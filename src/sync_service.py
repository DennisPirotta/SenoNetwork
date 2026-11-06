from datetime import date, datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field

from src.ai_extractor import AIExtractor, get_ai_extractor
from src.config import Settings, get_settings
from src.dotnet_client import DotNetApiClient
from src.form_classifier import AVAILABLE_FORMS, FormClassifier
from src.models import REDCapField
from src.pdf_extractor import PDFExtractor
from src.patient_pipeline import PatientPipeline, compute_file_hash
from src.redcap_service import REDCapService

logger = logging.getLogger(__name__)


class FieldUpdateDetail(BaseModel):
    """Details of a single REDCap field modification or enrichment."""
    field_name: str
    form_name: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    update_type: str = "new_field"  # "new_field", "overwrite", "status_change"


class PatientSyncResult(BaseModel):
    """Result of checking and syncing a single patient record."""
    patient_id: str
    name: Optional[str] = None
    surname: Optional[str] = None
    date_of_birth: Optional[str] = None
    status: str  # "updated", "up_to_date", "no_reports", "error", "skipped"
    forms_checked: List[str] = Field(default_factory=list)
    forms_updated: List[str] = Field(default_factory=list)
    new_reports_count: int = 0
    field_updates_count: int = 0
    field_updates: List[FieldUpdateDetail] = Field(default_factory=list)
    redcap_import_response: Optional[Any] = None
    error: Optional[str] = None
    duration_sec: float = 0.0


class SyncSummaryReport(BaseModel):
    """Global summary of a periodic synchronization and audit run."""
    timestamp: str
    patients_inspected: int = 0
    patients_updated: int = 0
    total_fields_updated: int = 0
    forms_affected: List[str] = Field(default_factory=list)
    results: List[PatientSyncResult] = Field(default_factory=list)
    duration_sec: float = 0.0
    dry_run: bool = False


class RedCapSyncService:
    """Performs periodic audits and reconciliations of patient records in REDCap:
    1. Reads what is currently stored in REDCap.
    2. Identifies missing/incomplete forms (e.g. post-operative histopathology).
    3. Fetches newly arrived or unread clinical reports (from Priamo/.NET or local repo).
    4. Extracts structured data with local AI.
    5. Computes field-level differences (diff).
    6. Updates REDCap with enriched data and updates audit tracking state.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        redcap_service: Optional[REDCapService] = None,
        dotnet_client: Optional[DotNetApiClient] = None,
        ai_extractor: Optional[AIExtractor] = None,
        patient_pipeline: Optional[PatientPipeline] = None
    ):
        self.settings = settings or get_settings()
        self.redcap_service = redcap_service or REDCapService(
            api_url=self.settings.redcap_api_url,
            api_token=self.settings.redcap_api_token
        )
        self.dotnet_client = dotnet_client or DotNetApiClient(settings=self.settings)
        self.ai_extractor = ai_extractor or get_ai_extractor(self.settings)
        self.pipeline = patient_pipeline or PatientPipeline(
            settings=self.settings,
            redcap_service=self.redcap_service,
            dotnet_client=self.dotnet_client,
            ai_extractor=self.ai_extractor
        )

        # Pre-load metadata via pipeline
        self._all_metadata: List[REDCapField] = []

    def load_metadata(self) -> List[REDCapField]:
        """Loads REDCap data dictionary fields."""
        if not self._all_metadata:
            self.pipeline.load_all_metadata()
            self._all_metadata = self.pipeline._all_metadata_fields
        return self._all_metadata

    def get_patients_from_redcap(
        self,
        filter_incomplete_forms: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Exports patient records from REDCap and optionally filters those with incomplete forms.

        Args:
            filter_incomplete_forms: Optional list of form names to check (e.g. ['anatomia_patologica']).
                                     If provided, returns only records where at least one form is not complete ("2").
        """
        # We query identifying fields and all form complete status flags
        forms_list = list(AVAILABLE_FORMS.keys())
        query_fields = ["record_id", "name", "surname", "date_of_birth"]
        for frm in forms_list:
            query_fields.append(f"{frm}_complete")

        records = self.redcap_service.export_records_from_api(fields=query_fields)
        if not records:
            return []

        if not filter_incomplete_forms:
            return records

        target_forms = [f.lower().strip() for f in filter_incomplete_forms]
        incomplete_records = []
        for r in records:
            needs_update = False
            for frm in target_forms:
                status_val = str(r.get(f"{frm}_complete") or "").strip()
                if status_val != "2":
                    needs_update = True
                    break
            if needs_update:
                incomplete_records.append(r)

        return incomplete_records

    def detect_incomplete_forms(
        self,
        redcap_record: Dict[str, Any],
        candidate_forms: Optional[List[str]] = None
    ) -> List[str]:
        """Returns the list of forms that are currently incomplete or missing for a patient."""
        forms_to_check = candidate_forms or list(AVAILABLE_FORMS.keys())
        incomplete = []
        for frm in forms_to_check:
            flag_key = f"{frm}_complete"
            status = str(redcap_record.get(flag_key) or "").strip()
            if status != "2":
                incomplete.append(frm)
        return incomplete

    def load_patient_state(self, patient_id: str) -> Dict[str, Any]:
        """Loads state tracking json for patient if present."""
        state_file = self.settings.state_dir / f"{patient_id}_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Errore lettura state file per {patient_id}: {e}")
        return {"patient_id": patient_id, "files": [], "forms_populated": []}

    def save_patient_state(self, patient_id: str, state_data: Dict[str, Any]):
        """Persists state tracking json for patient."""
        try:
            self.settings.state_dir.mkdir(parents=True, exist_ok=True)
            state_file = self.settings.state_dir / f"{patient_id}_state.json"
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Impossibile salvare lo state file per {patient_id}: {e}")

    def check_and_update_patient(
        self,
        patient_id: str,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        dob: Optional[Union[date, str]] = None,
        target_forms: Optional[List[str]] = None,
        auto_upload: bool = True,
        dry_run: bool = False,
        force_reprocess: bool = False,
        overwrite_mode: Optional[str] = None
    ) -> PatientSyncResult:
        """Audits a single patient in REDCap, inspects reports, and updates missing or updated data.

        Args:
            patient_id: Patient identifier in REDCap.
            name: Patient first name.
            surname: Patient last name.
            dob: Patient date of birth.
            target_forms: List of specific forms to check (e.g. ['anatomia_patologica']). Defaults to all incomplete forms.
            auto_upload: If True and not dry_run, imports updates directly to REDCap API.
            dry_run: If True, only computes and reports changes without writing to REDCap.
            force_reprocess: If True, re-processes existing PDF reports even if their hash was already logged.
            overwrite_mode: 'fill_missing' (default: only update empty/0 fields) or 'overwrite' (replace existing).
        """
        start_time = time.time()
        mode = overwrite_mode or self.settings.sync_overwrite_mode
        logger.info(f"--- [SYNC] Controllo paziente REDCap: {patient_id} (mode={mode}, dry_run={dry_run}) ---")

        # Step 1: Query existing REDCap record
        existing_record: Optional[Dict[str, Any]] = None
        try:
            existing_record = self.redcap_service.get_record(patient_id)
        except Exception as e:
            logger.error(f"Errore recupero record REDCap per {patient_id}: {e}")
            return PatientSyncResult(
                patient_id=patient_id,
                status="error",
                error=f"Errore connessione REDCap: {e}",
                duration_sec=round(time.time() - start_time, 2)
            )

        # Se non trovato direttamente con l'ID fornito, prova a cercare il record_id nativo di REDCap
        # per dati anagrafici, garantendo di mantenere sempre il patient_id / record_id di REDCap.
        if not existing_record:
            cand_name = name
            cand_surname = surname
            cand_dob = dob
            if not cand_name or not cand_surname:
                resolved = self.dotnet_client._resolve_patient(patient_id)
                if resolved:
                    cand_name = cand_name or resolved.name
                    cand_surname = cand_surname or resolved.surname
                    cand_dob = cand_dob or resolved.date_of_birth

            if cand_name and cand_surname:
                matched_id = self.redcap_service.find_record_id_by_demographics(
                    cand_name, cand_surname, cand_dob
                )
                if matched_id:
                    logger.info(
                        f"-> Mantenuto record_id nativo di REDCap: '{matched_id}' (risolto da '{patient_id}')"
                    )
                    patient_id = matched_id
                    try:
                        existing_record = self.redcap_service.get_record(patient_id)
                    except Exception:
                        pass

        if not existing_record:
            logger.warning(f"Paziente {patient_id} non trovato su REDCap.")
            return PatientSyncResult(
                patient_id=patient_id,
                status="error",
                error="Paziente non presente nel database REDCap.",
                duration_sec=round(time.time() - start_time, 2)
            )

        # Assicura che l'identificatore sia esattamente il record_id presente su REDCap
        if existing_record and "record_id" in existing_record:
            patient_id = str(existing_record["record_id"])

        # Extract demographics if not provided
        p_name = name or existing_record.get("name")
        p_surname = surname or existing_record.get("surname")
        p_dob = dob or existing_record.get("date_of_birth")

        # If still missing, try slug resolution
        if not p_name or not p_surname:
            resolved = self.dotnet_client._resolve_patient(patient_id)
            if resolved:
                p_name = p_name or resolved.name
                p_surname = p_surname or resolved.surname
                p_dob = p_dob or resolved.date_of_birth

        # Step 2: Determine target forms
        forms_to_audit = target_forms or self.detect_incomplete_forms(existing_record)
        logger.info(f"-> Form da verificare per {patient_id}: {forms_to_audit}")

        # Step 3: Fetch available reports (Priamo / .NET or local directories)
        download_dir = self.settings.downloads_dir / patient_id
        download_dir.mkdir(parents=True, exist_ok=True)
        available_pdfs = self.dotnet_client.download_all_patient_pdfs(
            patient_id=patient_id,
            destination_dir=download_dir,
            name=p_name,
            surname=p_surname,
            date_of_birth=p_dob
        )

        if not available_pdfs:
            logger.info(f"-> Nessun PDF di referto trovato per {patient_id}.")
            return PatientSyncResult(
                patient_id=patient_id,
                name=p_name,
                surname=p_surname,
                date_of_birth=str(p_dob or ""),
                status="no_reports",
                forms_checked=forms_to_audit,
                duration_sec=round(time.time() - start_time, 2)
            )

        # Step 4: Check state file to identify new or relevant reports
        patient_state = self.load_patient_state(patient_id)
        known_hashes: Set[str] = {f.get("sha256") for f in patient_state.get("files", []) if f.get("sha256")}

        field_updates: List[FieldUpdateDetail] = []
        updates_payload: Dict[str, Any] = {"record_id": str(patient_id)}
        forms_updated: Set[str] = set()
        new_reports_count = 0

        # Pre-load metadata
        self.load_metadata()

        for pdf_path in available_pdfs:
            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                continue

            file_hash = compute_file_hash(pdf_path)
            is_new_file = file_hash not in known_hashes
            if is_new_file:
                new_reports_count += 1

            # Decide whether to inspect this report:
            # - If it's a brand new file
            # - Or if force_reprocess is True
            # - Or if one of our forms_to_audit is still incomplete
            should_process = is_new_file or force_reprocess or bool(forms_to_audit)
            if not should_process:
                continue

            try:
                # 4.1 Extract text
                report_text = PDFExtractor.extract_text(pdf_path)
                if not report_text.strip():
                    continue

                # 4.2 Classify form
                classified_form = FormClassifier.classify_report(
                    text=report_text,
                    hint=pdf_path.name,
                    settings=self.settings
                )

                # Check if this form is relevant to audit
                if forms_to_audit and classified_form not in forms_to_audit:
                    # Also check if this form in REDCap is currently incomplete
                    if existing_record.get(f"{classified_form}_complete") == "2" and not force_reprocess:
                        continue

                logger.info(f"-> Analisi referto '{pdf_path.name}' classificato come '{classified_form}'...")

                # 4.3 AI Extraction
                target_fields = self.pipeline.get_fields_for_form(classified_form)
                extraction_res = self.ai_extractor.extract_from_text(
                    report_text=report_text,
                    fields=target_fields,
                    pdf_path=str(pdf_path),
                    record_id=patient_id,
                    form_name=classified_form
                )

                if extraction_res.status != "success":
                    logger.warning(f"Estrazione fallita per {pdf_path.name}: {extraction_res.error_message}")
                    continue

                extracted_data = extraction_res.redcap_record

                # 4.4 Compute diff with existing REDCap record
                form_has_updates = False
                for k, new_v in extracted_data.items():
                    if k in ("record_id",):
                        continue

                    # Skip empty/None values in extracted data unless checkbox code '0'
                    if new_v in (None, ""):
                        continue

                    old_v = existing_record.get(k)
                    old_str = str(old_v).strip() if old_v is not None else ""
                    new_str = str(new_v).strip()

                    # Completion field logic
                    if k == f"{classified_form}_complete":
                        if old_str != "2" and new_str == "2":
                            field_updates.append(
                                FieldUpdateDetail(
                                    field_name=k,
                                    form_name=classified_form,
                                    old_value=old_v,
                                    new_value=new_v,
                                    update_type="status_change"
                                )
                            )
                            updates_payload[k] = new_v
                            form_has_updates = True
                        continue

                    # Checkbox fields: format {field}___{code}
                    if "___" in k:
                        if old_str != new_str:
                            if mode == "overwrite" or (mode == "fill_missing" and old_str in ("", "0") and new_str == "1"):
                                field_updates.append(
                                    FieldUpdateDetail(
                                        field_name=k,
                                        form_name=classified_form,
                                        old_value=old_v,
                                        new_value=new_v,
                                        update_type="new_field" if old_str in ("", "0") else "overwrite"
                                    )
                                )
                                updates_payload[k] = new_v
                                form_has_updates = True
                        continue

                    # Standard fields (radio, dropdown, text, number, date)
                    if old_str == "" or old_str is None:
                        # New field enrichment
                        field_updates.append(
                            FieldUpdateDetail(
                                field_name=k,
                                form_name=classified_form,
                                old_value=old_v,
                                new_value=new_v,
                                update_type="new_field"
                            )
                        )
                        updates_payload[k] = new_v
                        form_has_updates = True
                    elif old_str != new_str and mode == "overwrite":
                        # Overwrite existing
                        field_updates.append(
                            FieldUpdateDetail(
                                field_name=k,
                                form_name=classified_form,
                                old_value=old_v,
                                new_value=new_v,
                                update_type="overwrite"
                            )
                        )
                        updates_payload[k] = new_v
                        form_has_updates = True

                if form_has_updates:
                    forms_updated.add(classified_form)
                    # Ensure form completion is marked
                    updates_payload[f"{classified_form}_complete"] = "2"

                # Update in-memory state file entry
                patient_state.setdefault("files", []).append({
                    "filename": pdf_path.name,
                    "sha256": file_hash,
                    "form": classified_form,
                    "status": "success",
                    "processed_at": datetime.now().isoformat()
                })

            except Exception as e:
                logger.error(f"Errore elaborazione referto {pdf_path.name}: {e}")

        # Step 5: Upload updates to REDCap if not dry_run and updates exist
        import_response = None
        has_changes = len(field_updates) > 0

        if has_changes and auto_upload and not dry_run:
            logger.info(f"Caricamento {len(field_updates)} campi aggiornati su REDCap per {patient_id}...")
            try:
                import_response = self.redcap_service.import_records_to_api([updates_payload])
                logger.info(f"-> Aggiornamento REDCap riuscito: {import_response}")
            except Exception as e:
                logger.error(f"-> Fallimento importazione aggiornamenti su REDCap: {e}")
                return PatientSyncResult(
                    patient_id=patient_id,
                    name=p_name,
                    surname=p_surname,
                    date_of_birth=str(p_dob or ""),
                    status="error",
                    forms_checked=forms_to_audit,
                    forms_updated=sorted(list(forms_updated)),
                    new_reports_count=new_reports_count,
                    field_updates_count=len(field_updates),
                    field_updates=field_updates,
                    error=f"Errore caricamento REDCap: {e}",
                    duration_sec=round(time.time() - start_time, 2)
                )

        # Step 6: Save state file
        if not dry_run:
            all_forms = set(patient_state.get("forms_populated", []))
            all_forms.update(forms_updated)
            patient_state["forms_populated"] = sorted(list(all_forms))
            patient_state["last_sync"] = datetime.now().isoformat()
            self.save_patient_state(patient_id, patient_state)

        status_result = "updated" if has_changes else "up_to_date"
        logger.info(
            f"=== SYNC COMPLETATO PER {patient_id}: {status_result.upper()} "
            f"({len(field_updates)} modifiche su form: {sorted(list(forms_updated))}) ==="
        )

        return PatientSyncResult(
            patient_id=patient_id,
            name=p_name,
            surname=p_surname,
            date_of_birth=str(p_dob or ""),
            status=status_result,
            forms_checked=forms_to_audit,
            forms_updated=sorted(list(forms_updated)),
            new_reports_count=new_reports_count,
            field_updates_count=len(field_updates),
            field_updates=field_updates,
            redcap_import_response=import_response,
            duration_sec=round(time.time() - start_time, 2)
        )

    def sync_all(
        self,
        target_forms: Optional[List[str]] = None,
        auto_upload: bool = True,
        dry_run: bool = False,
        force_reprocess: bool = False,
        limit: Optional[int] = None,
        only_incomplete: bool = True
    ) -> SyncSummaryReport:
        """Executes a complete audit and synchronization cycle across REDCap patients.

        Args:
            target_forms: Optional list of forms to audit (e.g. ['anatomia_patologica']).
            auto_upload: If True and not dry_run, uploads changes to REDCap.
            dry_run: If True, only computes changes without modifying REDCap.
            force_reprocess: If True, re-evaluates reports even if unchanged.
            limit: Optional maximum number of patients to process.
            only_incomplete: If True, only inspects patients who have incomplete forms.
        """
        start_time = time.time()
        logger.info(f"=== INIZIO AUDIT & SYNC REDCAP (target_forms={target_forms}, dry_run={dry_run}) ===")

        # Step 1: Discover patients from REDCap
        records = self.get_patients_from_redcap(
            filter_incomplete_forms=target_forms if only_incomplete else None
        )
        if limit:
            records = records[:limit]

        logger.info(f"Trovati {len(records)} pazienti da verificare su REDCap.")

        results: List[PatientSyncResult] = []
        all_forms_affected: Set[str] = set()
        total_fields_updated = 0
        patients_updated = 0

        for idx, rec in enumerate(records, 1):
            pid = str(rec.get("record_id", "")).strip()
            if not pid:
                continue

            logger.info(f"[{idx}/{len(records)}] Controllo paziente: {pid}...")
            res = self.check_and_update_patient(
                patient_id=pid,
                name=rec.get("name"),
                surname=rec.get("surname"),
                dob=rec.get("date_of_birth"),
                target_forms=target_forms,
                auto_upload=auto_upload,
                dry_run=dry_run,
                force_reprocess=force_reprocess
            )
            results.append(res)

            if res.status == "updated":
                patients_updated += 1
                total_fields_updated += res.field_updates_count
                all_forms_affected.update(res.forms_updated)

        duration = round(time.time() - start_time, 2)
        report = SyncSummaryReport(
            timestamp=datetime.now().isoformat(),
            patients_inspected=len(results),
            patients_updated=patients_updated,
            total_fields_updated=total_fields_updated,
            forms_affected=sorted(list(all_forms_affected)),
            results=results,
            duration_sec=duration,
            dry_run=dry_run
        )

        # Save audit report locally
        audit_dir = self.settings.sync_audit_dir
        audit_dir.mkdir(parents=True, exist_ok=True)
        report_filename = f"sync_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(audit_dir / report_filename, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

        logger.info(
            f"=== AUDIT & SYNC COMPLETATO IN {duration}s: "
            f"{patients_updated}/{len(results)} pazienti aggiornati ({total_fields_updated} campi) ==="
        )
        return report

    def run_periodic(
        self,
        interval_seconds: int = 3600,
        target_forms: Optional[List[str]] = None,
        auto_upload: bool = True,
        dry_run: bool = False,
        stop_event: Optional[Any] = None
    ):
        """Runs the synchronization loop periodically at specified interval."""
        logger.info(f"Avvio ciclo periodico di sincronizzazione REDCap ogni {interval_seconds}s...")
        while True:
            if stop_event and stop_event.is_set():
                logger.info("Segnale di stop ricevuto. Arresto del loop di sincronizzazione.")
                break

            try:
                self.sync_all(
                    target_forms=target_forms,
                    auto_upload=auto_upload,
                    dry_run=dry_run
                )
            except Exception as e:
                logger.error(f"Errore durante l'esecuzione del ciclo di sync: {e}")

            logger.info(f"In attesa del prossimo ciclo ({interval_seconds}s)...")
            if stop_event:
                if stop_event.wait(timeout=interval_seconds):
                    break
            else:
                time.sleep(interval_seconds)
