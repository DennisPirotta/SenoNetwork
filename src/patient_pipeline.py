from datetime import date, datetime
import hashlib
import inspect
import ipaddress
import json
import logging
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
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
from src.models import REDCapField
from src.pdf_extractor import PDFExtractor
from src.redcap_service import REDCapService
from src.utils import atomic_write_text, build_patient_slug, llm_slot, mask_phi, normalize_person_name, sanitize_identifier

logger = logging.getLogger(__name__)

_ID_RESOLUTION_LOCK = threading.Lock()
_PENDING_IDS_BY_PATIENT: Dict[Tuple[str, str], str] = {}


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
        if parsed.scheme != "https" and not allow_local:
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        lower_host = hostname.lower().rstrip(".")

        # Explicit whitelist check
        if allowed_hosts:
            allowed = {h.lower().rstrip(".") for h in allowed_hosts}
            if lower_host not in allowed:
                return False
            if allow_local:
                return True

        if allow_local:
            return True

        if lower_host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or lower_host.endswith(
            (".local", ".internal", ".localhost")
        ):
            return False

        def _is_disallowed_ip(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
            return (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
                or ip_obj.is_unspecified
            )

        # Literal IP address check
        try:
            ip = ipaddress.ip_address(lower_host)
            return not _is_disallowed_ip(ip)
        except ValueError:
            pass

        # Reject bare internal service names (e.g. 'http://redis:6379')
        if "." not in lower_host:
            return False

        # Resolve DNS where available to block loopback/private targets behind public-looking hostnames
        try:
            infos = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP,
            )
            for info in infos:
                ip_str = info[4][0].split("%")[0]
                resolved_ip = ipaddress.ip_address(ip_str)
                if _is_disallowed_ip(resolved_ip):
                    return False
        except socket.gaierror:
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
        self._ai_extractor = ai_extractor

        # Cache for REDCap metadata fields per form to avoid repeated API requests
        self._cached_fields_by_form: Dict[str, List[REDCapField]] = {}
        self._all_metadata_fields: List[REDCapField] = []

    @property
    def ai_extractor(self) -> Union[AIExtractor, LlamaCppExtractor, OllamaExtractor]:
        """Lazily instantiates the configured AI extractor on first actual extraction use."""
        if self._ai_extractor is None:
            self._ai_extractor = get_ai_extractor(self.settings)
        return self._ai_extractor

    @ai_extractor.setter
    def ai_extractor(self, value: Optional[Union[AIExtractor, LlamaCppExtractor, OllamaExtractor]]) -> None:
        self._ai_extractor = value

    @staticmethod
    def _verify_pdf_patient_identity(
        report_text: str,
        name: Optional[str],
        surname: Optional[str],
    ) -> Optional[str]:
        """Detects explicit header identity mismatches between a PDF report and the target patient."""
        if not name or not surname:
            return None
        norm_surname = normalize_person_name(surname)
        if len(norm_surname) < 3 or not report_text:
            return None

        # Check explicit patient header line first (even if signer/doctor in body shares a surname)
        header_sample = report_text[:1000]
        match = re.search(
            r"(?:paziente|nominativo|assistit[oa]|cognome(?:\s+e\s+nome)?)\s*[:\-]\s*([A-Za-zÀ-ÿ'`\-\s]{3,60})",
            header_sample,
            re.IGNORECASE,
        )
        if match:
            header_identity = normalize_person_name(match.group(1))
            if header_identity and norm_surname not in header_identity:
                return (
                    f"Possibile mismatch anagrafico nel referto PDF: intestazione non corrispondente "
                    f"al paziente {mask_phi(surname)} {mask_phi(name)}."
                )
            if header_identity and norm_surname in header_identity:
                return None

        norm_text = normalize_person_name(report_text)
        if norm_surname in norm_text:
            return None

        return None

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
        webhook_url: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int, Optional[Dict[str, Any]]], None]] = None,
        overwrite: bool = False,
        codice_fiscale: Optional[str] = None,
    ) -> PatientPipelineResult:
        """Executes the complete pipeline for a single patient."""
        start_time = time.time()
        if progress_callback:
            progress_callback(f"Inizializzazione pipeline per paziente {patient_id}...", 5, None)

        # Sanitizzazione rigorosa di patient_id per prevenire Path Traversal
        patient_id = sanitize_identifier(patient_id)
        pending_key: Optional[Tuple[str, str]] = None

        # Mantenimento o generazione atomica del record_id numerico di REDCap
        if self.redcap_service.api_url and self.redcap_service.api_token:
            slug_candidate = (
                build_patient_slug(name, surname, date_of_birth, default="")
                if (name and surname)
                else ""
            )
            proj_key = str(self.redcap_service.api_url or "").strip()
            cf_norm = str(codice_fiscale or "").strip().upper()
            identity_key = cf_norm or slug_candidate or str(patient_id).lower()

            with _ID_RESOLUTION_LOCK:
                matched_redcap_id = None
                try:
                    if cf_norm or (name and surname):
                        matched_redcap_id = self.redcap_service.find_record_id_by_demographics(
                            name=name,
                            surname=surname,
                            date_of_birth=date_of_birth,
                            codice_fiscale=codice_fiscale,
                        )
                except Exception as e:
                    logger.warning(f"Verifica anagrafica su REDCap non riuscita per {patient_id}: {e}")

                if matched_redcap_id:
                    _PENDING_IDS_BY_PATIENT.pop((proj_key, identity_key), None)
                    if matched_redcap_id != str(patient_id):
                        logger.info(
                            f"-> Mantenuto record_id nativo di REDCap: '{matched_redcap_id}' "
                            f"(invece dell'identificatore transitorio '{patient_id}')"
                        )
                        patient_id = matched_redcap_id
                elif auto_upload and (
                    not patient_id or (slug_candidate and str(patient_id).lower() == slug_candidate)
                ):
                    pending_key = (proj_key, identity_key)
                    if pending_key in _PENDING_IDS_BY_PATIENT:
                        patient_id = _PENDING_IDS_BY_PATIENT[pending_key]
                    else:
                        try:
                            next_id = self.redcap_service.generate_next_record_name()
                            if isinstance(next_id, str) and next_id.strip():
                                resolved_next = next_id.strip()
                                if slug_candidate and str(patient_id).lower() == slug_candidate:
                                    logger.info(
                                        f"-> Assegnato record_id numerico da REDCap: '{resolved_next}' "
                                        f"(sostituisce slug '{patient_id}')"
                                    )
                                patient_id = resolved_next
                                _PENDING_IDS_BY_PATIENT[pending_key] = resolved_next
                            else:
                                raise RuntimeError("Impossibile ottenere il prossimo record_id numerico da REDCap.")
                        except Exception as e:
                            logger.error(f"Generazione record_id REDCap fallita ({patient_id}): {e}")
                            raise

        try:
            return self._execute_patient_workflow(
                patient_id=patient_id,
                name=name,
                surname=surname,
                date_of_birth=date_of_birth,
                pdf_paths=pdf_paths,
                auto_upload=auto_upload,
                output_dir=output_dir,
                webhook_url=webhook_url,
                progress_callback=progress_callback,
                overwrite=overwrite,
                codice_fiscale=codice_fiscale,
                start_time=start_time,
            )
        finally:
            if pending_key is not None:
                with _ID_RESOLUTION_LOCK:
                    _PENDING_IDS_BY_PATIENT.pop(pending_key, None)

    def _execute_patient_workflow(
        self,
        patient_id: str,
        name: Optional[str],
        surname: Optional[str],
        date_of_birth: Optional[Union[date, str]],
        pdf_paths: Optional[List[Union[str, Path]]],
        auto_upload: bool,
        output_dir: Optional[Path],
        webhook_url: Optional[str],
        progress_callback: Optional[Callable[[str, int, Optional[Dict[str, Any]]], None]],
        overwrite: bool,
        codice_fiscale: Optional[str],
        start_time: float,
    ) -> PatientPipelineResult:
        """Internal execution body after record_id resolution."""
        logger.info(
            f"=== INIZIO PIPELINE PER PAZIENTE: {patient_id} ({mask_phi(name)} {mask_phi(surname)}) ==="
        )

        # Step 1: Estrazione PDF da API .NET (Priamo o REST) se non forniti manualmente
        download_dir = self.settings.downloads_dir / patient_id
        if pdf_paths:
            pdfs_to_process = [Path(p) for p in pdf_paths]
            if progress_callback:
                progress_callback(f"Caricati {len(pdfs_to_process)} referti PDF per {patient_id}.", 15, None)
        else:
            logger.info(f"1. Estrazione PDF da API .NET per paziente {patient_id}...")
            if progress_callback:
                progress_callback(f"Download referti da Priamo .NET per {patient_id}...", 10, None)
            pdfs_to_process = self.dotnet_client.download_all_patient_pdfs(
                patient_id=patient_id,
                destination_dir=download_dir,
                name=name,
                surname=surname,
                date_of_birth=date_of_birth,
                codice_fiscale=codice_fiscale,
            )

        total_pdfs = len(pdfs_to_process)
        logger.info(f"-> Trovati {total_pdfs} PDF di referti per il paziente {patient_id}.")
        if progress_callback:
            progress_callback(f"Trovati {total_pdfs} referti PDF per {patient_id}.", 20, None)

        pdf_text_cache: Dict[Path, str] = {}
        doc_date_cache: Dict[Path, str] = {}
        if total_pdfs == 0:
            logger.warning(f"-> Nessun PDF di referto associato al paziente {patient_id}. Salto l'estrazione clinica IA.")
        else:
            # Ordinamento cronologico singolo-pass (evita doppia lettura integrale di ogni PDF)
            sorted_entries = PDFExtractor.sort_pdfs_with_text(pdfs_to_process)
            pdfs_to_process = []
            for p_item, txt_item, dt_item in sorted_entries:
                pdfs_to_process.append(p_item)
                if txt_item:
                    pdf_text_cache[p_item] = txt_item
                doc_date_cache[p_item] = dt_item
            logger.info("-> Referti ordinati cronologicamente per preservare aggiornamenti clinici.")

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
                "dati_del_paziente_complete": "1"
            }
            patient_records_to_upload.append(anagrafica_record)
            forms_populated.add("dati_del_paziente")

        for idx, pdf_path in enumerate(pdfs_to_process, 1):
            logger.info(f"2.[{idx}/{total_pdfs}] Elaborazione IA su referto: {pdf_path.name}")
            t_doc_start = time.time()
            file_hash = None
            pct_base = 20 + int(((idx - 1) / max(total_pdfs, 1)) * 65)
            if progress_callback:
                progress_callback(f"[{idx}/{total_pdfs}] Analisi referto: {pdf_path.name}...", pct_base, None)
            try:
                if pdf_path.exists():
                    file_hash = compute_file_hash(pdf_path)
            except Exception:
                pass

            try:
                # 2.1 Estrazione testo (riusa testo già estratto durante l'ordinamento)
                report_text = pdf_text_cache.get(pdf_path) or PDFExtractor.extract_text(pdf_path)

                # 2.1b Verifica identità paziente se il referto espone un'intestazione esplicita
                identity_error = self._verify_pdf_patient_identity(report_text, name, surname)
                if identity_error:
                    logger.error(f"-> Scartato referto '{pdf_path.name}': {identity_error}")
                    doc_results.append(
                        DocumentProcessingResult(
                            filename=pdf_path.name,
                            classified_form="unknown",
                            fields_extracted_count=0,
                            duration_sec=round(time.time() - t_doc_start, 2),
                            status="error",
                            error=identity_error,
                            file_sha256=file_hash,
                        )
                    )
                    continue

                # 2.2 Rilevazione dei form REDCap di destinazione (primario ed eventuali secondari)
                target_forms = FormClassifier.classify_report_targets(
                    text=report_text,
                    hint=pdf_path.name,
                    settings=self.settings
                )
                logger.info(f"-> Referto '{pdf_path.name}' classificato per i form REDCap: {target_forms}")

                # Se il documento è non senologico o da saltare, non invochiamo l'IA
                if not target_forms or all(f in ("skip", "irrelevant", "unknown") for f in target_forms):
                    logger.info(f"-> Salto referto '{pdf_path.name}' (nessun form senologico target).")
                    doc_results.append(
                        DocumentProcessingResult(
                            filename=pdf_path.name,
                            classified_form=target_forms[0] if target_forms else "skip",
                            fields_extracted_count=0,
                            duration_sec=round(time.time() - t_doc_start, 2),
                            status="skipped",
                            extracted_record={},
                            file_sha256=file_hash
                        )
                    )
                    continue

                # 2.3 Estrazione strutturata iterativa per ciascun form target (Multi-Pass)
                doc_extracted_record: Dict[str, Any] = {}
                doc_total_clinical_count = 0
                doc_status = "success"
                doc_error = None
                doc_date = doc_date_cache.get(pdf_path) or PDFExtractor.extract_document_date(pdf_path, report_text)

                valid_target_forms = [f for f in target_forms if f not in ("skip", "irrelevant", "unknown")]
                extract_sig_params = set()
                try:
                    extract_sig_params = set(inspect.signature(self.ai_extractor.extract_from_text).parameters.keys())
                except (TypeError, ValueError):
                    extract_sig_params = set()

                for form_idx, target_form in enumerate(valid_target_forms):
                    is_primary = (form_idx == 0)
                    target_fields = self.get_fields_for_form(target_form)
                    logger.info(
                        f"-> [{form_idx + 1}/{len(valid_target_forms)}] Estrazione IA di {len(target_fields)} campi "
                        f"per '{target_form}' ({'primario' if is_primary else 'secondario'})..."
                    )

                    extract_kwargs: Dict[str, Any] = {
                        "report_text": report_text,
                        "fields": target_fields,
                        "pdf_path": str(pdf_path),
                        "record_id": patient_id,
                        "form_name": target_form,
                        "mark_form_complete": is_primary,
                        "form_completion_status": "1" if is_primary else "0",
                    }
                    if "include_unchecked_checkboxes" in extract_sig_params:
                        extract_kwargs["include_unchecked_checkboxes"] = bool(overwrite)

                    with llm_slot(getattr(self.settings, "ai_max_concurrent_requests", 1)):
                        extraction_res = self.ai_extractor.extract_from_text(**extract_kwargs)

                    if extraction_res.status == "success":
                        record = extraction_res.redcap_record
                        record["record_id"] = str(patient_id)
                        record["_document_date"] = doc_date

                        clinical_fields = [
                            k for k, v in record.items()
                            if k != "record_id"
                            and not k.startswith("_")
                            and not k.endswith("_complete")
                            and (v == "1" if "___" in k else v not in ("", None, [], {}))
                        ]
                        extracted_count = len(clinical_fields)

                        if extracted_count > 0:
                            patient_records_to_upload.append(record)
                            forms_populated.add(target_form)
                            doc_total_clinical_count += extracted_count
                            doc_extracted_record.update({k: v for k, v in record.items() if not k.startswith("_")})
                            logger.info(
                                f"-> Estratti {extracted_count} campi clinici per '{target_form}'. Accodato per upload."
                            )
                        else:
                            logger.info(
                                f"-> Nessun campo clinico utile estratto da '{pdf_path.name}' per '{target_form}'."
                            )
                    else:
                        doc_status = "error"
                        doc_error = extraction_res.error_message
                        logger.warning(
                            f"-> Estrazione non riuscita per '{target_form}' su '{pdf_path.name}': {extraction_res.error_message}"
                        )

                doc_results.append(
                    DocumentProcessingResult(
                        filename=pdf_path.name,
                        classified_form=", ".join(valid_target_forms),
                        fields_extracted_count=doc_total_clinical_count,
                        duration_sec=round(time.time() - t_doc_start, 2),
                        status=doc_status if doc_total_clinical_count > 0 or doc_status == "success" else "error",
                        error=doc_error,
                        extracted_record=doc_extracted_record,
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

            if progress_callback and doc_results:
                last_doc = doc_results[-1]
                pct_doc_done = 20 + int((idx / max(total_pdfs, 1)) * 65)
                progress_callback(
                    f"[{idx}/{total_pdfs}] Referto '{pdf_path.name}' completato: {last_doc.fields_extracted_count} campi ({last_doc.classified_form}).",
                    pct_doc_done,
                    last_doc.model_dump()
                )

        # Step 3: Caricamento su REDCap
        upload_status = "skipped"
        upload_response = None

        if auto_upload and patient_records_to_upload:
            if progress_callback:
                progress_callback(f"Consolidamento e caricamento dati su REDCap per {patient_id}...", 90, None)
            merged_to_upload = REDCapService.merge_flat_records(patient_records_to_upload)

            if self.redcap_service.api_url and self.redcap_service.api_token:
                existing_record: Optional[Dict[str, Any]] = None
                try:
                    existing_record = self.redcap_service.get_record(str(patient_id))
                except Exception as e:
                    logger.debug(f"Recupero record esistente REDCap per safe-diff fallito ({patient_id}): {e}")

                if isinstance(existing_record, dict) and existing_record:
                    if not overwrite:
                        safe_records: List[Dict[str, Any]] = []
                        for rec in merged_to_upload:
                            safe_rec = REDCapService.merge_with_existing_record(
                                existing=existing_record,
                                updates=rec,
                                overwrite=False,
                            )
                            if any(k != "record_id" for k in safe_rec.keys()):
                                safe_records.append(safe_rec)
                        merged_to_upload = safe_records
                    elif str(existing_record.get("dati_del_paziente_complete") or "").strip() == "2":
                        # Never downgrade an already verified demographics form status
                        for rec in merged_to_upload:
                            if "dati_del_paziente_complete" in rec:
                                rec["dati_del_paziente_complete"] = "2"

            if not merged_to_upload:
                upload_status = "skipped"
                upload_response = {"count": 0, "reason": "no_new_fields"}
                logger.info(f"3. Nessun nuovo campo da arricchire su REDCap per {patient_id} (dati esistenti preservati).")
            else:
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

        if progress_callback:
            progress_callback(
                f"Pipeline completata per {patient_id}: {result.successful_pdfs}/{result.total_pdfs} referti elaborati.",
                100,
                {"patient_id": patient_id, "forms_populated": result.forms_populated, "upload_status": upload_status}
            )

        atomic_write_text(summary_file, result.model_dump_json(indent=2))

        # Idempotence: Save processing state
        try:
            self.settings.state_dir.mkdir(parents=True, exist_ok=True)
            state_file = self.settings.state_dir / f"{patient_id}_state.json"
            clean_docs_count = len([d for d in doc_results if d.status in ("success", "skipped")])
            if upload_status == "error":
                state_status = "error"
            elif total_pdfs > 0 and clean_docs_count == total_pdfs:
                state_status = "completed"
            else:
                state_status = "partial"
            state_payload = {
                "patient_id": patient_id,
                "last_run": datetime.now().isoformat(),
                "status": state_status,
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
            atomic_write_text(state_file, json.dumps(state_payload, indent=2))
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
                    cb_resp = requests.post(target_webhook, json=cb_payload, timeout=30, allow_redirects=False)
                    cb_resp.raise_for_status()
                    logger.info(f"-> Risultati inviati all'API con esito: HTTP {cb_resp.status_code}")
                except Exception as e:
                    logger.error(f"-> Errore durante l'invio dei risultati all'API esterna ({target_webhook}): {e}")

        logger.info(f"=== PIPELINE COMPLETATA PER {patient_id} IN {result.total_duration_sec}s ===")
        return result
