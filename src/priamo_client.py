import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from bs4 import BeautifulSoup
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
        pool_maxsize: int = 20,
        settings: Optional[Settings] = None
    ):
        cfg = settings or get_settings()
        self.base_url = (base_url or cfg.priamo_api_url or os.getenv("PRIAMO_API_URL", "")).rstrip("/")
        self.username = username or cfg.priamo_username or os.getenv("PRIAMO_USERNAME", "")
        self.password = password or cfg.priamo_password or os.getenv("PRIAMO_PASSWORD", "")
        self.cf_client_id = cf_client_id or cfg.cf_client_id or os.getenv("CLOUDFLARED_CLIENT_ID", "")
        self.cf_client_secret = cf_client_secret or cfg.cf_client_secret or os.getenv("CLOUDFLARED_CLIENT_SECRET", "")

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

        adapter = HTTPAdapter(pool_maxsize=pool_maxsize, pool_connections=pool_maxsize)
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
        """Authenticates with the Priamo ASP.NET MVC login endpoint."""
        if self._authenticated and not force:
            return

        if not self.is_configured:
            raise ValueError("Credenziali Priamo incomplete in configurazione o .env")

        logger.info(f"Autenticazione in corso su Priamo .NET ({self.login_page_url})...")
        response = self._request("GET", self.login_page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})

        if not token_input:
            raise ValueError("Impossibile trovare il token __RequestVerificationToken nella pagina di login Priamo.")

        token = str(token_input.get("value"))

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

        if ".PRIAMOAUTH" not in self.session.cookies.get_dict():
            # Check for error message in page
            soup2 = BeautifulSoup(login_response.text, "html.parser")
            error_msg = ""
            for msg in soup2.find_all(class_=lambda c: c and ("error" in c or "validation" in c or "alert" in c)):
                error_msg += msg.get_text(strip=True) + " "
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
        document_id: str,
        event_id: str,
        repository_id: Optional[str],
        destination_path: Path
    ) -> Path:
        """Downloads the signed PDF report from Priamo and saves it locally."""
        self.authenticate()
        params = {
            "stato": "FIRMATO",
            "idRepository": str(document_id),
            "idEvento": str(event_id),
            "repository": str(repository_id) if repository_id else "",
            "timeout": 5000,
        }

        response = self._request("GET", self.pdf_url, params=params, stream=True)
        content = response.content

        if not content.startswith(b"%PDF"):
            raise ValueError(f"La risposta da Priamo per il documento {document_id} non è un PDF valido.")

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with open(destination_path, "wb") as f:
            f.write(content)

        logger.info(f"PDF {document_id} salvato con successo in {destination_path}")
        return destination_path

    def fetch_all_patient_reports(
        self,
        patient: PriamoPatient,
        destination_dir: Path
    ) -> List[PriamoDocument]:
        """Discovers and downloads all clinical PDF reports available for a patient in Priamo."""
        events = self.get_patient_events(patient.patient_id)
        logger.info(f"Trovati {len(events)} eventi per il paziente {patient.name} {patient.surname} (ID: {patient.patient_id})")

        documents: List[PriamoDocument] = []
        patient_dir = destination_dir / patient.slug
        patient_dir.mkdir(parents=True, exist_ok=True)

        for event in events:
            event_id = str(event.get("idEvento"))
            referto = str(event.get("referto", "")).strip().upper()
            pdf = str(event.get("pdf", "")).strip().upper()
            exam_name = str(event.get("primoEsame", "")).strip()
            event_type = str(event.get("tipo", "")).strip()

            # Skip events with neither referto nor pdf available
            if referto == "N" and pdf == "N":
                continue

            try:
                doc_list = self.get_event_documents(event_id)
            except Exception as e:
                logger.error(f"Errore recupero documenti per evento {event_id}: {e}")
                continue

            for doc in doc_list:
                doc_id = str(doc.get("idDocumento"))
                repo_id = str(doc.get("repository", ""))

                # Build clean filename preserving exam type for form classification
                safe_exam = re.sub(r"[^a-zA-Z0-9_\-]", "_", exam_name)[:30]
                filename = f"{doc_id}_{safe_exam}.pdf" if safe_exam else f"{doc_id}.pdf"
                dest_file = patient_dir / filename

                try:
                    saved_path = self.download_document_pdf(
                        document_id=doc_id,
                        event_id=event_id,
                        repository_id=repo_id,
                        destination_path=dest_file
                    )
                    documents.append(
                        PriamoDocument(
                            document_id=doc_id,
                            event_id=event_id,
                            repository_id=repo_id,
                            exam_name=exam_name,
                            event_type=event_type,
                            local_path=saved_path
                        )
                    )
                except Exception as e:
                    logger.error(f"Errore download PDF {doc_id}: {e}")

        return documents

    def close(self) -> None:
        if self.session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
