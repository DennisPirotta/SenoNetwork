import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import requests

from src.config import Settings, get_settings
from src.priamo_client import PriamoClient, PriamoDocument, PriamoPatient

logger = logging.getLogger(__name__)


class DotNetApiClient:
    """Unified client for interacting with the hospital .NET system (Priamo / Valduce).

    Supports:
    1. Direct integration with the Priamo .NET system (PriamoHOME / PriamoCES).
    2. Batch loading from clinical Excel registers (e.g. interventi.xls).
    3. Generic REST endpoint fallback or local file simulation.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
        priamo_client: Optional[PriamoClient] = None,
        settings: Optional[Settings] = None
    ):
        self.settings = settings or get_settings()
        self.base_url = (base_url or self.settings.dotnet_api_base_url or os.getenv("DOTNET_API_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or self.settings.dotnet_api_key or os.getenv("DOTNET_API_KEY", "")
        self.timeout_seconds = timeout_seconds

        # Priamo client instance
        self.priamo = priamo_client or PriamoClient(settings=self.settings)

    @property
    def is_priamo_configured(self) -> bool:
        """Returns True if Priamo credentials and URL are configured."""
        return self.priamo.is_configured

    @property
    def is_generic_api_configured(self) -> bool:
        """Returns True if generic .NET REST API URL is configured."""
        return bool(self.base_url)

    def download_all_patient_pdfs(
        self,
        patient_id: str,
        destination_dir: Optional[Path] = None,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None
    ) -> List[Path]:
        """Downloads all PDFs available for a patient.
        Tries Priamo .NET first, then generic REST API, then local folder.
        """
        dest_dir = destination_dir or (self.settings.downloads_dir / patient_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 1. Priamo .NET API
        if self.is_priamo_configured:
            try:
                # If demographics are provided, search patient on Priamo
                if name and surname and date_of_birth:
                    patient = self.priamo.search_patient(name, surname, date_of_birth)
                else:
                    # Treat patient_id as Priamo ID or try slug parsing
                    patient = self._resolve_patient(patient_id)

                if patient:
                    docs = self.priamo.fetch_all_patient_reports(patient, dest_dir)
                    paths = [d.local_path for d in docs if d.local_path and d.local_path.exists()]
                    if paths:
                        logger.info(f"Scaricati {len(paths)} referti PDF da Priamo per {patient_id}")
                        return paths
            except Exception as e:
                logger.error(f"Errore download da Priamo per {patient_id}: {e}")

        # 2. Generic REST API
        if self.is_generic_api_configured:
            try:
                return self._download_from_generic_api(patient_id, dest_dir)
            except Exception as e:
                logger.error(f"Errore download da generic .NET API per {patient_id}: {e}")

        # 3. Local fallback mock / pre-downloaded files
        logger.info(f"Ricerca file locali per paziente {patient_id}...")
        return self._get_local_documents_mock(
            patient_id=patient_id,
            name=name,
            surname=surname,
            date_of_birth=date_of_birth
        )

    def get_patient_documents(
        self,
        patient_id: str,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None
    ) -> List[Dict[str, Any]]:
        """Returns metadata list of available documents for a patient."""
        pdfs = self._get_local_documents_mock(
            patient_id=patient_id,
            name=name,
            surname=surname,
            date_of_birth=date_of_birth
        )
        return [{"id": p.stem, "document_id": p.stem, "filename": p.name, "path": str(p)} for p in pdfs]

    def _resolve_patient(self, identifier: str) -> Optional[PriamoPatient]:
        """Attempts to resolve a patient identifier into a PriamoPatient object."""
        # Check if identifier is a slug: 'name-surname-YYYY-MM-DD'
        parts = identifier.split("-")
        if len(parts) >= 5:
            # Assuming 'name-surname-YYYY-MM-DD'
            dob_str = f"{parts[-3]}-{parts[-2]}-{parts[-1]}"
            try:
                dob = date.fromisoformat(dob_str)
                name = parts[0].title()
                surname = " ".join(parts[1:-3]).title()
                return self.priamo.search_patient(name, surname, dob)
            except Exception:
                pass

        # Treat identifier directly as idAnagPaz1
        return PriamoPatient(
            patient_id=identifier,
            name="Paziente",
            surname=identifier
        )

    def _download_from_generic_api(self, patient_id: str, destination_dir: Path) -> List[Path]:
        """Queries a generic REST .NET API using multithreaded download workers."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        url = f"{self.base_url}/patients/{patient_id}/documents"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        resp = requests.get(url, headers=headers, timeout=self.timeout_seconds)
        resp.raise_for_status()
        docs = resp.json()

        if not docs:
            return []

        def _download_single(doc: Dict[str, Any]) -> Optional[Path]:
            doc_id = str(doc.get("id") or doc.get("document_id"))
            fname = doc.get("filename") or f"{doc_id}.pdf"
            dest = destination_dir / fname
            dl_url = f"{self.base_url}/patients/{patient_id}/documents/{doc_id}/download"
            try:
                with requests.get(dl_url, headers=headers, stream=True, timeout=self.timeout_seconds) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                return dest
            except Exception as e:
                logger.error(f"Errore download doc {doc_id} da generic API: {e}")
                return None

        workers = getattr(self.settings, "priamo_max_workers", 8)
        downloaded = []
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(docs)))) as executor:
            futures = [executor.submit(_download_single, doc) for doc in docs]
            for fut in as_completed(futures):
                p = fut.result()
                if p:
                    downloaded.append(p)

        return downloaded

    def _get_local_documents_mock(
        self,
        patient_id: str,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None
    ) -> List[Path]:
        """Checks local directories for documents specifically belonging to patient_id.
        Preserves REDCap's native patient_id/record_id, while also checking candidate slug
        subfolders if demographics are known.
        """
        candidate_ids = [str(patient_id)]
        if name and surname:
            dob_str = ""
            if date_of_birth:
                dob_str = date_of_birth.isoformat() if isinstance(date_of_birth, date) else str(date_of_birth)
            raw_slug = f"{name}-{surname}-{dob_str}".lower().replace(" ", "-").strip("-")
            clean_slug = re.sub(r"[^a-z0-9\-]", "", raw_slug)
            if clean_slug and clean_slug not in candidate_ids:
                candidate_ids.append(clean_slug)

        for cid in candidate_ids:
            search_dirs = [
                Path("data/patient_repository") / cid,
                self.settings.downloads_dir / cid,
            ]
            if self.settings.priamo_files_dir:
                search_dirs.insert(0, Path(self.settings.priamo_files_dir) / cid)

            for sdir in search_dirs:
                if sdir and sdir.exists() and sdir.is_dir():
                    pdfs = sorted(list(sdir.glob("*.pdf")) + list(sdir.glob("*.PDF")))
                    if not pdfs:
                        pdfs = sorted(list(sdir.rglob("*.pdf")) + list(sdir.rglob("*.PDF")))
                    if pdfs:
                        return pdfs

        # Also check if files in data/input_pdf contain any candidate_id in their filename
        input_pdf_dir = Path("data/input_pdf")
        if input_pdf_dir.exists():
            for cid in candidate_ids:
                matched = [
                    p for p in sorted(list(input_pdf_dir.glob("*.pdf")) + list(input_pdf_dir.glob("*.PDF")))
                    if cid.lower() in p.stem.lower()
                ]
                if matched:
                    return matched

        return []

    @staticmethod
    def load_patients_from_excel(
        excel_path: Optional[Union[str, Path]] = None,
        header_row: int = 4,
        settings: Optional[Settings] = None
    ) -> List[Dict[str, Any]]:
        """Loads patient cohort from an Excel registry (such as Priamo's interventi.xls)."""
        import pandas as pd

        cfg = settings or get_settings()
        target_path = excel_path or cfg.patients_excel_path
        if not target_path:
            raise ValueError("Nessun percorso file Excel specificato e 'patients_excel_path' non configurato.")

        path = Path(target_path)
        if not path.exists():
            raise FileNotFoundError(f"File Excel non trovato: {path}")

        df = pd.read_excel(path, dtype=str, header=header_row)
        patients = []
        seen = set()

        for idx, row in df.iterrows():
            try:
                name = str(row.get("NOME") or row.get("Nome", "")).strip()
                surname = str(row.get("COGNOME") or row.get("Cognome", "")).strip()
                raw_dob = row.get("DATA_NASCITA") or row.get("DataNascita", "")

                if not name or not surname or not raw_dob or str(raw_dob).lower() == "nan":
                    continue

                # Parse date
                dob = pd.to_datetime(raw_dob, format="mixed", dayfirst=True).date()

                key = (name.lower(), surname.lower(), dob)
                if key in seen:
                    continue
                seen.add(key)

                # Patient identifier for REDCap
                patient_slug = f"{name}-{surname}-{dob.isoformat()}".lower().replace(" ", "-")
                patient_slug = re.sub(r"[^a-z0-9\-]", "", patient_slug)

                record = {
                    "patient_id": patient_slug,
                    "name": name,
                    "surname": surname,
                    "date_of_birth": dob,
                    "raw_row": row.to_dict()
                }
                patients.append(record)
            except Exception as e:
                logger.warning(f"Riga Excel {idx} scartata: {e}")

        logger.info(f"Caricati {len(patients)} pazienti univoci dal file Excel {path.name}")
        return patients
