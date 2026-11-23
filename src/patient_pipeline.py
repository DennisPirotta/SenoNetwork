from datetime import date, datetime
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union
import urllib.parse
from pydantic import BaseModel, Field
import requests

from src.ai_extractor import (
    AIExtractor,
    LlamaCppExtractor,
    OllamaExtractor,
    get_ai_extractor
)
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.form_classifier import FormClassifier
from src.models import ExtractionResult, REDCapField
from src.pdf_extractor import PDFExtractor
from src.redcap_service import REDCapService

logger = logging.getLogger(__name__)


def is_safe_webhook_url(
    url: str,
    allowed_hosts: Optional[List[str]] = None,
    allow_local: bool = False
) -> bool:
    """Validates webhook URL to protect against Server-Side Request Forgery (SSRF)."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # If explicit whitelist is configured
        if allowed_hosts:
            return hostname.lower() in [h.lower() for h in allowed_hosts]

        if allow_local:
            return True

        lower_host = hostname.lower()
        if lower_host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            return False

        # Check if hostname is an IP address
        try:
            ip = ipaddress.ip_address(lower_host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            # Standard domain name
            pass

        return True
    except Exception:
        return False


def compute_file_hash(path: Path) -> str:
    """Computes SHA-256 hash of a file for change tracking and idempotence."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


class DocumentProcessingResult(BaseModel):
    filename: str
    classified_form: str
    fields_extracted_count: int
    duration_sec: float
    status: str
    error: Optional[str] = None
    extracted_record: Dict[str, Any] = Field(default_factory=dict)
    file_sha256: Optional[str] = None


class PatientPipelineResult(BaseModel):
    patient_id: str
    total_pdfs: int
    successful_pdfs: int
    forms_populated: List[str]
    documents: List[DocumentProcessingResult]
    redcap_upload_status: str  # "uploaded", "skipped", "error"
    redcap_upload_response: Optional[Any] = None
    total_duration_sec: float = 0.0


class PatientPipeline:
    """Orchestrates the entire patient workflow:
    1. Extract PDFs from .NET API
    2. Process with Local AI (Form classification + Structured extraction)
    3. Upload to REDCap
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        redcap_service: Optional[REDCapService] = None,
        dotnet_client: Optional[DotNetApiClient] = None,
        ai_extractor: Optional[Union[AIExtractor, LlamaCppExtractor, OllamaExtractor]] = None
    ):
        self.settings = settings or get_settings()
        self.redcap_service = redcap_service or REDCapService(
            api_url=self.settings.redcap_api_url,
            api_token=self.settings.redcap_api_token
        )
        self.dotnet_client = dotnet_client or DotNetApiClient(settings=self.settings)
        self.ai_extractor = ai_extractor or get_ai_extractor(self.settings)

        # Cache for REDCap metadata fields per form to avoid repeated API requests
        self._cached_fields_by_form: Dict[str, List[REDCapField]] = {}
        self._all_metadata_fields: List[REDCapField] = []

    def load_all_metadata(self):
        """Pre-loads the full REDCap project data dictionary."""
        if not self._all_metadata_fields:
            logger.info("Caricamento del data dictionary completo da REDCap...")
            # Try loading from configured local cache file if exists, otherwise fetch from API
            live_cache = self.settings.redcap_cache_file
            if live_cache and live_cache.exists():
                self._all_metadata_fields = self.redcap_service.load_metadata_from_file(live_cache)
                logger.info(f"-> Data dictionary caricato dalla cache locale ({len(self._all_metadata_fields)} campi totali).")
            else:
                self._all_metadata_fields = self.redcap_service.fetch_metadata_from_api()
                logger.info(f"-> Data dictionary scaricato via API ({len(self._all_metadata_fields)} campi totali).")

    def get_fields_for_form(self, form_name: str) -> List[REDCapField]:
        """Returns the extractable fields for a specific form, always including record_id."""
        if form_name in self._cached_fields_by_form:
            return self._cached_fields_by_form[form_name]

        self.load_all_metadata()
        filtered = self.redcap_service.filter_fields_by_form(self._all_metadata_fields, form_name)
        self._cached_fields_by_form[form_name] = filtered
        return filtered

    def process_patient(
        self,
        patient_id: str,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None,
        pdf_paths: Optional[List[Union[str, Path]]] = None,
        auto_upload: bool = True,
        output_dir: Optional[Path] = None,
        webhook_url: Optional[str] = None
    ) -> PatientPipelineResult:
        """Executes the complete pipeline for a single patient."""
        start_time = time.time()

        # Mantenimento o generazione automatica del record_id numerico di REDCap:
        # Se il paziente esiste già su REDCap con un record_id, lo mantiene per evitare duplicati.
        # Se l'identificatore è lo slug nome-cognome-dob, genera automaticamente il prossimo record_id numerico da REDCap.
        try:
            if self.redcap_service.api_url and self.redcap_service.api_token:
                matched_redcap_id = None
                if name and surname:
                    matched_redcap_id = self.redcap_service.find_record_id_by_demographics(
                        name=name, surname=surname, date_of_birth=date_of_birth
                    )
                if matched_redcap_id:
                    if matched_redcap_id != str(patient_id):
                        logger.info(
                            f"-> Mantenuto record_id nativo di REDCap: '{matched_redcap_id}' "
                            f"(invece dell'identificatore transitorio '{patient_id}')"
                        )
                        patient_id = matched_redcap_id
                elif not patient_id:
                    next_id = self.redcap_service.generate_next_record_name()
                    if next_id:
                        patient_id = next_id
                else:
                    # Controlla se patient_id è lo slug generato da nome-cognome-data_nascita
                    slug_candidate = ""
                    if name and surname:
                        d_str = date_of_birth.strftime("%Y-%m-%d") if isinstance(date_of_birth, date) else str(date_of_birth or "")
                        slug_candidate = re.sub(r"[^a-z0-9\-]", "", f"{name}-{surname}-{d_str}".lower().replace(" ", "-"))

                    if slug_candidate and str(patient_id).lower() == slug_candidate:
                        next_id = self.redcap_service.generate_next_record_name()
                        if next_id:
                            logger.info(
                                f"-> Assegnato record_id numerico da REDCap: '{next_id}' "
                                f"(sostituisce slug '{patient_id}')"
                            )
                            patient_id = next_id
        except Exception as e:
            logger.debug(f"Verifica o generazione record_id REDCap non riuscita: {e}")

        logger.info(f"=== INIZIO PIPELINE PER PAZIENTE: {patient_id} ({name or ''} {surname or ''}) ===")

        # Step 1: Estrazione PDF da API .NET (Priamo o REST) se non forniti manualmente
        download_dir = self.settings.downloads_dir / patient_id
        if pdf_paths:
            pdfs_to_process = [Path(p) for p in pdf_paths]
        else:
            logger.info(f"1. Estrazione PDF da API .NET per paziente {patient_id}...")
            pdfs_to_process = self.dotnet_client.download_all_patient_pdfs(
                patient_id=patient_id,
                destination_dir=download_dir,
                name=name,
                surname=surname,
                date_of_birth=date_of_birth
            )

        total_pdfs = len(pdfs_to_process)
        logger.info(f"-> Trovati {total_pdfs} PDF di referti per il paziente {patient_id}.")
        if total_pdfs == 0:
            logger.warning(f"-> Nessun PDF di referto associato al paziente {patient_id}. Salto l'estrazione clinica IA.")

        # Step 2: Elaborazione IA
        doc_results: List[DocumentProcessingResult] = []
        patient_records_to_upload: List[Dict[str, Any]] = []
        forms_populated = set()

        # Se sono disponibili i dati anagrafici, prepariamo il record per il form 'dati_del_paziente'
        if name and surname:
            dob_str = date_of_birth.strftime("%Y-%m-%d") if isinstance(date_of_birth, date) else str(date_of_birth or "")
            anagrafica_record = {
                "record_id": str(patient_id),
                "name": name,
                "surname": surname,
                "date_of_birth": dob_str,
                "dati_del_paziente_complete": "2"
            }
            patient_records_to_upload.append(anagrafica_record)
            forms_populated.add("dati_del_paziente")

        for idx, pdf_path in enumerate(pdfs_to_process, 1):
            logger.info(f"2.[{idx}/{total_pdfs}] Elaborazione IA su referto: {pdf_path.name}")
            t_doc_start = time.time()
            file_hash = None
            try:
                if pdf_path.exists():
                    file_hash = compute_file_hash(pdf_path)
            except Exception:
                pass

            try:
                # 2.1 Estrazione testo
                report_text = PDFExtractor.extract_text(pdf_path)

                # 2.2 Classificazione automatica del form REDCap di destinazione
                classified_form = FormClassifier.classify_report(
                    text=report_text,
                    hint=pdf_path.name,
                    settings=self.settings
                )
                logger.info(f"-> Referto '{pdf_path.name}' classificato per il form REDCap: '{classified_form}'")

                # Se il documento è non senologico o da saltare, non invochiamo l'IA
                if classified_form in ("skip", "irrelevant", "unknown"):
                    logger.info(f"-> Salto referto '{pdf_path.name}' (classificato come '{classified_form}').")
                    doc_results.append(
                        DocumentProcessingResult(
                            filename=pdf_path.name,
                            classified_form=classified_form,
                            fields_extracted_count=0,
                            duration_sec=round(time.time() - t_doc_start, 2),
                            status="skipped",
                            extracted_record={},
                            file_sha256=file_hash
                        )
                    )
                    continue

                # 2.3 Estrazione strutturata dei campi del form
                target_fields = self.get_fields_for_form(classified_form)
                logger.info(
                    f"-> Invio prompt al motore IA per estrazione di {len(target_fields)} campi "
                    f"sul form '{classified_form}' (backend: {self.settings.ai_backend})..."
                )
                extraction_res = self.ai_extractor.extract_from_text(
                    report_text=report_text,
                    fields=target_fields,
                    pdf_path=str(pdf_path),
                    record_id=patient_id,
                    form_name=classified_form
                )

                if extraction_res.status == "success":
                    record = extraction_res.redcap_record
                    record["record_id"] = str(patient_id)

                    # Conta i campi clinici effettivamente estratti ed esclude record_id e _complete
                    clinical_fields = [
                        k for k, v in extraction_res.extracted_data.items()
                        if k != "record_id" and v not in ("", None, "0", [], {})
                    ]
                    extracted_count = len(clinical_fields)

                    if extracted_count > 0:
                        patient_records_to_upload.append(record)
                        forms_populated.add(classified_form)
                        logger.info(f"-> Estratti {extracted_count} campi clinici per '{classified_form}'. Accodato per upload.")
                    else:
                        logger.info(f"-> Nessun campo clinico utile estratto da '{pdf_path.name}' per '{classified_form}'. Record vuoto non accodato.")

                    doc_results.append(
                        DocumentProcessingResult(
                            filename=pdf_path.name,
                            classified_form=classified_form,
                            fields_extracted_count=extracted_count,
                            duration_sec=round(time.time() - t_doc_start, 2),
                            status="success",
                            extracted_record=record if extracted_count > 0 else {},
                            file_sha256=file_hash
                        )
                    )
                else:
                    doc_results.append(
                        DocumentProcessingResult(
                            filename=pdf_path.name,
                            classified_form=classified_form,
                            fields_extracted_count=0,
                            duration_sec=round(time.time() - t_doc_start, 2),
                            status="error",
                            error=extraction_res.error_message,
                            file_sha256=file_hash
                        )
                    )

            except Exception as e:
                logger.error(f"Errore elaborazione PDF {pdf_path.name}: {e}")
                doc_results.append(
                    DocumentProcessingResult(
                        filename=pdf_path.name,
                        classified_form="unknown",
                        fields_extracted_count=0,
                        duration_sec=round(time.time() - t_doc_start, 2),
                        status="error",
                        error=str(e),
                        file_sha256=file_hash
                    )
                )

        # Step 3: Caricamento su REDCap
        upload_status = "skipped"
        upload_response = None

        if auto_upload and patient_records_to_upload:
            merged_to_upload = REDCapService.merge_flat_records(patient_records_to_upload)
            logger.info(
                f"3. Caricamento dati estratti su REDCap ({len(patient_records_to_upload)} record estratti "
                f"consolidati in {len(merged_to_upload)} record paziente)..."
            )
            try:
                upload_response = self.redcap_service.import_records_to_api(merged_to_upload)
                upload_status = "uploaded"
                logger.info(f"-> Caricamento REDCap completato con successo: {upload_response}")
            except Exception as e:
                upload_status = "error"
                upload_response = str(e)
                logger.error(f"-> Errore durante il caricamento REDCap: {e}")

        # Save results locally
        target_out_dir = output_dir or (self.settings.output_dir / "patients")
        target_out_dir.mkdir(parents=True, exist_ok=True)
        summary_file = target_out_dir / f"patient_{patient_id}_result.json"

        result = PatientPipelineResult(
            patient_id=patient_id,
            total_pdfs=total_pdfs,
            successful_pdfs=len([d for d in doc_results if d.status == "success"]),
            forms_populated=sorted(list(forms_populated)),
            documents=doc_results,
            redcap_upload_status=upload_status,
            redcap_upload_response=upload_response,
            total_duration_sec=round(time.time() - start_time, 2)
        )

        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=2))

        # Idempotence: Save processing state
        try:
            self.settings.state_dir.mkdir(parents=True, exist_ok=True)
            state_file = self.settings.state_dir / f"{patient_id}_state.json"
            state_payload = {
                "patient_id": patient_id,
                "last_run": datetime.now().isoformat(),
                "status": "completed" if result.successful_pdfs == result.total_pdfs and result.total_pdfs > 0 else "partial",
                "total_pdfs": total_pdfs,
                "successful_pdfs": result.successful_pdfs,
                "forms_populated": result.forms_populated,
                "redcap_upload_status": upload_status,
                "files": [
                    {
                        "filename": d.filename,
                        "sha256": d.file_sha256,
                        "form": d.classified_form,
                        "status": d.status
                    }
                    for d in doc_results
                ]
            }
            with open(state_file, "w", encoding="utf-8") as sf:
                json.dump(state_payload, sf, indent=2)
        except Exception as e:
            logger.warning(f"Impossibile salvare il file di stato per {patient_id}: {e}")

        # Step 4: Invio risultati tramite API Webhook (se configurato e validato)
        target_webhook = webhook_url or self.settings.webhook_callback_url
        if target_webhook:
            if not is_safe_webhook_url(
                target_webhook,
                allowed_hosts=self.settings.allowed_webhook_hosts,
                allow_local=self.settings.allow_local_webhooks
            ):
                logger.error(
                    f"-> Invio webhook annullato per motivi di sicurezza (URL non valido o non autorizzato): {target_webhook}"
                )
            else:
                logger.info(f"4. Invio automatico dei risultati all'API di destinazione: {target_webhook}")
                try:
                    cb_payload = json.loads(result.model_dump_json())
                    cb_resp = requests.post(target_webhook, json=cb_payload, timeout=30)
                    logger.info(f"-> Risultati inviati all'API con esito: HTTP {cb_resp.status_code}")
                except Exception as e:
                    logger.error(f"-> Errore durante l'invio dei risultati all'API esterna ({target_webhook}): {e}")

        logger.info(f"=== PIPELINE COMPLETATA PER {patient_id} IN {result.total_duration_sec}s ===")
        return result
