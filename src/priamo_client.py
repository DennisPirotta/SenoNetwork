import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
from pydantic import BaseModel, Field
from requests import Response, Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

RETRYABLE = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RequestException),
    reraise=True,
)


class PriamoDocument(BaseModel):
    """Represents a medical document / report discovered in Priamo."""

    document_id: str
    event_id: str
    repository_id: Optional[str] = None
    exam_name: str = ""  # from 'primoEsame'
    event_type: str = ""  # from 'tipo'
    local_path: Optional[Path] = None


class PriamoPatient(BaseModel):
    """Represents patient identification and demographics in Priamo."""

    patient_id: str  # idAnagPaz1
    name: str
    surname: str
    date_of_birth: Optional[date] = None

    @property
    def slug(self) -> str:
        dob_str = self.date_of_birth.isoformat() if self.date_of_birth else "unknown"
        raw = f"{self.name}-{self.surname}-{dob_str}".lower().replace(" ", "-")
        return re.sub(r"[^a-z0-9\-]", "", raw)


class PriamoClient:
    """Client for the Valduce Priamo .NET Healthcare Information System.

    Implements:
    - Cloudflare Access authentication
    - ASP.NET MVC Form Authentication with __RequestVerificationToken
    - Session management and automatic re-authentication (.PRIAMOAUTH cookie)
    - Patient search (GetAnagrafica)
    - Event exploration (GetEvento)
    - Document lookup (GetDocumento)
    - PDF report download (GetReportTextToPdfToView)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cf_client_id: Optional[str] = None,
        cf_client_secret: Optional[str] = None,
        pool_maxsize: int = 30,
        max_workers: Optional[int] = None,
        settings: Optional[Settings] = None
    ):
        cfg = settings or get_settings()
        self.base_url = (base_url or cfg.priamo_api_url or os.getenv("PRIAMO_API_URL", "")).rstrip("/")
        self.username = username or cfg.priamo_username or os.getenv("PRIAMO_USERNAME", "")
        self.password = password or cfg.priamo_password or os.getenv("PRIAMO_PASSWORD", "")
        self.max_workers = max_workers or getattr(cfg, "priamo_max_workers", 8)
        self._auth_lock = threading.Lock()
        self.cf_client_id = (
            cf_client_id
            or getattr(cfg, "priamo_cloudflared_client_id", None)
            or cfg.cf_client_id
            or os.getenv("PRIAMO_CLOUDFLARED_CLIENT_ID", "")
            or os.getenv("CLOUDFLARED_CLIENT_ID", "")
        )
        self.cf_client_secret = (
            cf_client_secret
            or getattr(cfg, "priamo_cloudflared_client_secret", None)
            or cfg.cf_client_secret
            or os.getenv("PRIAMO_CLOUDFLARED_CLIENT_SECRET", "")
            or os.getenv("CLOUDFLARED_CLIENT_SECRET", "")
        )

        self.login_page_url = f"{self.base_url}/PriamoHOME/Account/Login"
        self.ces_url = f"{self.base_url}/PriamoCES"
        self.ces_api_url = f"{self.ces_url}/api"

        self.get_anagrafica_url = f"{self.ces_api_url}/Anagrafica/GetAnagrafica"
        self.get_evento_url = f"{self.ces_api_url}/Eventi/GetEvento"
        self.get_documento_url = f"{self.ces_api_url}/Documenti/GetDocumento"
        self.pdf_url = f"{self.ces_url}/Documento/GetReportTextToPdfToView"

        self.session = Session()
        if self.cf_client_id and self.cf_client_secret:
            self.session.headers.update({
                "CF-Access-Client-Id": self.cf_client_id,
                "CF-Access-Client-Secret": self.cf_client_secret,
            })

        effective_pool = max(pool_maxsize, self.max_workers * 2)
        adapter = HTTPAdapter(pool_maxsize=effective_pool, pool_connections=effective_pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._authenticated = False

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    @RETRYABLE
    def _request(self, method: str, url: str, **kwargs) -> Response:
        kwargs.setdefault("timeout", 15)
        response = self.session.request(method, url, **kwargs)

        # Handle session expiry and re-authenticate
        if response.status_code in (401, 403):
            logger.warning("Sessione Priamo scaduta o non autorizzata, ri-autenticazione in corso...")
            self.authenticate(force=True)
            response = self.session.request(method, url, **kwargs)

        response.raise_for_status()
        return response

    def authenticate(self, force: bool = False) -> None:
        """Authenticates with the Priamo ASP.NET MVC login endpoint (thread-safe)."""
        if self._authenticated and not force:
            return

        with self._auth_lock:
            if self._authenticated and not force:
                return

            if not self.is_configured:
                raise ValueError("Credenziali Priamo incomplete in configurazione o .env")

            logger.info(f"Autenticazione in corso su Priamo .NET ({self.login_page_url})...")
            response = self._request("GET", self.login_page_url)
            token = None
            if BeautifulSoup:
                soup = BeautifulSoup(response.text, "html.parser")
                token_input = soup.find("input", {"name": "__RequestVerificationToken"})
                if token_input:
                    token = str(token_input.get("value"))
            if not token:
                m = re.search(r'name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']', response.text)
                if not m:
                    m = re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']', response.text)
                if m:
                    token = m.group(1)

            if not token:
                raise ValueError("Impossibile trovare il token __RequestVerificationToken nella pagina di login Priamo.")

            payload = {
                "UserName": self.username,
                "Password": self.password,
                "__RequestVerificationToken": token,
            }

            login_response = self.session.post(
                self.login_page_url,
                data=payload,
                timeout=15,
                allow_redirects=True,
            )
            login_response.raise_for_status()

            # Extract .PRIAMOAUTH from Set-Cookie across redirect history or final response
            # (vital when accessed over Cloudflare Tunnel or reverse-proxy with domain mismatch)
            all_responses = list(login_response.history) + [login_response]
            for resp in all_responses:
                raw_headers = getattr(resp.raw, "headers", None)
                set_cookies = []
                if raw_headers and hasattr(raw_headers, "getlist"):
                    set_cookies = raw_headers.getlist("Set-Cookie")
                elif raw_headers and hasattr(raw_headers, "get_all"):
                    set_cookies = raw_headers.get_all("Set-Cookie")
                elif "Set-Cookie" in resp.headers:
                    set_cookies = [resp.headers["Set-Cookie"]]

                for sc in set_cookies:
                    match = re.search(r"(?i)\.PRIAMOAUTH=([^;]+)", sc)
                    if match:
                        priamo_auth_val = match.group(1).strip()
                        target_host = urlparse(self.base_url).hostname
                        if target_host:
                            self.session.cookies.set(".PRIAMOAUTH", priamo_auth_val, domain=target_host, path="/")
                        break

            if ".PRIAMOAUTH" not in self.session.cookies.get_dict():
                error_msg = ""
                if BeautifulSoup:
                    soup2 = BeautifulSoup(login_response.text, "html.parser")
                    for msg in soup2.find_all(class_=lambda c: c and ("error" in c or "validation" in c or "alert" in c)):
                        error_msg += msg.get_text(strip=True) + " "
                if not error_msg:
                    m = re.search(r'class=["\'][^"\']*(?:validation|alert|error)[^"\']*["\'][^>]*>([^<]+)<', login_response.text)
                    if m:
                        error_msg = m.group(1).strip()
                error_detail = error_msg.strip() or "Credenziali non valide o form modificato"
                raise ValueError(f"Autenticazione Priamo fallita: {error_detail}")

            self._authenticated = True
            logger.info("Autenticazione su Priamo avvenuta con successo (.PRIAMOAUTH acquisito).")

    def search_patient(
        self,
        name: str,
        surname: str,
        date_of_birth: Union[date, str]
    ) -> Optional[PriamoPatient]:
        """Searches for a patient in Priamo by Name, Surname, and Date of Birth."""
        self.authenticate()

        dob_str = date_of_birth.strftime("%Y-%m-%d") if isinstance(date_of_birth, date) else str(date_of_birth)
        params = {
            "Nome": name.strip(),
            "Cognome": surname.strip(),
            "DataNascita": dob_str,
        }

        response = self._request("GET", self.get_anagrafica_url, params=params)
        data = response.json()

        if not data or not isinstance(data, list):
            logger.warning(f"Paziente non trovato su Priamo: {name} {surname} ({dob_str})")
            return None

        # Look for consents containing patient ID
        first_match = data[0]
        consensi = first_match.get("consensi", [])
        if not consensi:
            logger.warning(f"Paziente trovato su Priamo ma senza consensi associati: {name} {surname}")
            return None

        patient_id = str(consensi[0].get("idAnagPaz1"))
        parsed_dob = date.fromisoformat(dob_str) if isinstance(date_of_birth, str) else date_of_birth

        return PriamoPatient(
            patient_id=patient_id,
            name=name.strip(),
            surname=surname.strip(),
            date_of_birth=parsed_dob
        )

    def get_patient_events(
        self,
        patient_id: str,
        start_date: Union[date, str] = date(2024, 1, 1)
    ) -> List[Dict[str, Any]]:
        """Retrieves clinical events for a given patient from Priamo."""
        self.authenticate()
        start_str = start_date.strftime("%Y-%m-%d") if isinstance(start_date, date) else str(start_date)

        params = {
            "idPaziente": str(patient_id),
            "DataInizio": start_str
        }

        response = self._request("GET", self.get_evento_url, params=params)
        res_json = response.json()
        return res_json.get("eventi", [])

    def get_event_documents(self, event_id: str) -> List[Dict[str, Any]]:
        """Retrieves documents linked to a specific event in Priamo."""
        self.authenticate()
        response = self._request("GET", self.get_documento_url, params={"idEvento": str(event_id)})
        res_json = response.json()
        return res_json.get("documenti", [])

    def download_document_pdf(
        self,
        document_id: Optional[str],
        event_id: str,
        repository_id: Optional[str] = None,
        destination_path: Optional[Path] = None,
        event_type: Optional[str] = None,
        preferred_stato: Optional[str] = None,
        versione: Union[int, str] = 0,
    ) -> Path:
        """Downloads the PDF report from Priamo and saves it locally.

        Supports both:
        1. Digitally signed repository documents:
           GET /PriamoCES/Documento/GetReportTextToPdfToView?stato=FIRMATO&idRepository=...&idEvento=...&repository=...&timeout=5000
        2. Outpatient text-to-pdf reports:
           GET /PriamoCES/Documento/GetReportTextToPdfToView?stato=REFERTATO&idEvento=...&tipoEvento=AMB&versione=0&repository=N&timeout=5000

        Automatically attempts fallback between REFERTATO and FIRMATO modes if one fails or returns non-PDF content.
        """
        self.authenticate()
        if not destination_path:
            raise ValueError("destination_path è obbligatorio.")

        # 1. Strategy REFERTATO: Outpatient text converted on the fly
        params_refertato: Dict[str, Any] = {
            "stato": "REFERTATO",
            "idEvento": str(event_id),
            "tipoEvento": str(event_type or "AMB"),
            "versione": str(versione),
            "repository": str(repository_id) if repository_id and repository_id != "None" else "N",
            "timeout": 5000,
        }
        if document_id and str(document_id).strip() and str(document_id) != str(event_id):
            params_refertato["idRepository"] = str(document_id)

        # 2. Strategy FIRMATO: Signed repository document
        params_firmato: Dict[str, Any] = {
            "stato": "FIRMATO",
            "idEvento": str(event_id),
            "timeout": 5000,
        }
        if document_id and str(document_id).strip():
            params_firmato["idRepository"] = str(document_id)
        if repository_id and str(repository_id).strip() and repository_id != "N":
            params_firmato["repository"] = str(repository_id)
        else:
            params_firmato["repository"] = ""
        if event_type:
            params_firmato["tipoEvento"] = str(event_type)

        # Determine order of attempts
        if preferred_stato == "REFERTATO" or repository_id == "N" or not document_id:
            strategies = [("REFERTATO", params_refertato), ("FIRMATO", params_firmato)]
        else:
            strategies = [("FIRMATO", params_firmato), ("REFERTATO", params_refertato)]

        last_error = None
        for stato_label, params in strategies:
            try:
                logger.debug(f"Tentativo download PDF evento {event_id} (stato={stato_label}, params={params})")
                response = self._request("GET", self.pdf_url, params=params, stream=True)
                content = response.content

                if content and content.startswith(b"%PDF"):
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(destination_path, "wb") as f:
                        f.write(content)
                    logger.info(f"PDF {destination_path.name} scaricato con successo con stato={stato_label}")
                    return destination_path
                else:
                    snippet = content[:150] if content else b"empty"
                    logger.warning(
                        f"Download per evento {event_id} (stato={stato_label}) non ha restituito un PDF valido "
                        f"(inizio risposta: {snippet}). Tento strategia alternativa..."
                    )
            except Exception as e:
                logger.warning(f"Download per evento {event_id} (stato={stato_label}) fallito: {e}. Tento fallback...")
                last_error = e

        raise ValueError(
            f"Impossibile scaricare un PDF valido per l'evento {event_id} (doc: {document_id}) "
            f"con nessuna delle modalità (FIRMATO/REFERTATO). Ultimo errore: {last_error}"
        )

    def fetch_all_patient_reports(
        self,
        patient: PriamoPatient,
        destination_dir: Path,
        max_workers: Optional[int] = None
    ) -> List[PriamoDocument]:
        """Discovers and downloads all clinical PDF reports available for a patient in Priamo.

        Uses multithreaded concurrent workers (ThreadPoolExecutor) with persistent HTTP
        connection pooling to maximize download speed and minimize latency.
        """
        events = self.get_patient_events(patient.patient_id)
        logger.info(f"Trovati {len(events)} eventi per il paziente {patient.name} {patient.surname} (ID: {patient.patient_id})")

        if destination_dir.name in (patient.slug, patient.patient_id):
            patient_dir = destination_dir
        else:
            patient_dir = destination_dir / patient.slug
        patient_dir.mkdir(parents=True, exist_ok=True)

        if not events:
            return []

        # Filter events that actually contain reports or documents
        active_events = [
            e for e in events
            if not (str(e.get("referto", "")).strip().upper() == "N" and str(e.get("pdf", "")).strip().upper() == "N")
        ]

        if not active_events:
            logger.info(f"Nessun referto/pdf disponibile tra gli eventi di {patient.name} {patient.surname}.")
            return []

        workers = max_workers or self.max_workers or 8

        # 1. Parallel retrieval of document lists for events
        event_docs_map: Dict[str, List[Dict[str, Any]]] = {}

        def _fetch_event_docs(ev: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
            ev_id = str(ev.get("idEvento"))
            try:
                return ev_id, self.get_event_documents(ev_id)
            except Exception as e:
                logger.warning(f"Errore recupero lista documenti per evento {ev_id}: {e}")
                return ev_id, []

        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(active_events)))) as executor:
            fut_to_ev = {executor.submit(_fetch_event_docs, ev): ev for ev in active_events}
            for fut in as_completed(fut_to_ev):
                ev_id, doc_list = fut.result()
                event_docs_map[ev_id] = doc_list

        # 2. Build download task queue
        download_tasks: List[Dict[str, Any]] = []

        for event in active_events:
            event_id = str(event.get("idEvento"))
            referto = str(event.get("referto", "")).strip().upper()
            pdf = str(event.get("pdf", "")).strip().upper()
            exam_name = str(event.get("primoEsame", "")).strip()
            event_type = (
                str(event.get("tipo", "")).strip()
                or str(event.get("tipoEvento", "")).strip()
                or "AMB"
            )

            doc_list = event_docs_map.get(event_id, [])

            # Case A: Documents found in GetDocumento
            if doc_list:
                for doc in doc_list:
                    doc_id = str(doc.get("idDocumento", ""))
                    repo_id = str(doc.get("repository", ""))
                    doc_stato = str(doc.get("stato", "")).strip().upper()
                    preferred_stato = doc_stato if doc_stato in ("FIRMATO", "REFERTATO") else ("REFERTATO" if repo_id == "N" else "FIRMATO")

                    safe_exam = re.sub(r"[^a-zA-Z0-9_\-]", "_", exam_name)[:30]
                    filename = f"{doc_id}_{safe_exam}.pdf" if safe_exam else f"{doc_id}.pdf"
                    dest_file = patient_dir / filename

                    download_tasks.append({
                        "document_id": doc_id,
                        "event_id": event_id,
                        "repository_id": repo_id,
                        "destination_path": dest_file,
                        "event_type": event_type,
                        "preferred_stato": preferred_stato,
                        "exam_name": exam_name,
                    })

            # Case B: No documents in GetDocumento, BUT event has referto='S' or pdf='S' (outpatient text report)
            elif referto == "S" or pdf == "S":
                safe_exam = re.sub(r"[^a-zA-Z0-9_\-]", "_", exam_name)[:30]
                filename = f"evento_{event_id}_{safe_exam}.pdf" if safe_exam else f"evento_{event_id}.pdf"
                dest_file = patient_dir / filename

                download_tasks.append({
                    "document_id": event_id,
                    "event_id": event_id,
                    "repository_id": "N",
                    "destination_path": dest_file,
                    "event_type": event_type,
                    "preferred_stato": "REFERTATO",
                    "exam_name": exam_name,
                })

        if not download_tasks:
            logger.info(f"Nessun file referto da scaricare per {patient.name} {patient.surname}.")
            return []

        logger.info(
            f"Avvio download concorrente di {len(download_tasks)} referti PDF per il paziente "
            f"{patient.name} {patient.surname} con pool di {workers} thread..."
        )

        documents: List[PriamoDocument] = []

        def _execute_download_task(task: Dict[str, Any]) -> Optional[PriamoDocument]:
            try:
                saved_path = self.download_document_pdf(
                    document_id=task["document_id"],
                    event_id=task["event_id"],
                    repository_id=task["repository_id"],
                    destination_path=task["destination_path"],
                    event_type=task["event_type"],
                    preferred_stato=task["preferred_stato"],
                )
                return PriamoDocument(
                    document_id=task["document_id"],
                    event_id=task["event_id"],
                    repository_id=task["repository_id"],
                    exam_name=task["exam_name"],
                    event_type=task["event_type"],
                    local_path=saved_path
                )
            except Exception as e:
                logger.error(f"Errore download concorrente PDF {task['document_id']} per evento {task['event_id']}: {e}")
                return None

        # 3. Execute all downloads in parallel
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(download_tasks)))) as executor:
            fut_to_task = {executor.submit(_execute_download_task, t): t for t in download_tasks}
            for fut in as_completed(fut_to_task):
                doc_res = fut.result()
                if doc_res:
                    documents.append(doc_res)

        logger.info(f"Download concorrente completato: {len(documents)}/{len(download_tasks)} referti acquisiti con successo per {patient.name} {patient.surname}.")
        return documents

    def close(self) -> None:
        if self.session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
