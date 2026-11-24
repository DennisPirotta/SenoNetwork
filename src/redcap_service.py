import csv
from datetime import date
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Union
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.models import REDCapField
from src.utils import normalize_person_name, parse_flexible_date, sanitize_identifier

logger = logging.getLogger(__name__)

_ALLOCATED_IDS_LOCK = threading.Lock()
_ALLOCATED_IDS_BY_PROJECT: Dict[str, set[str]] = {}
_PROJECT_CF_LOCK = threading.Lock()
_PROJECT_SUPPORTS_CF: Dict[str, bool] = {}

_REDCAP_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    reraise=True,
)


class REDCapService:
    """Handles communication with REDCap API and reading/parsing REDCap Data Dictionaries."""

    def __init__(self, api_url: Optional[str] = None, api_token: Optional[str] = None):
        raw_url = api_url.strip() if api_url else ""
        if raw_url:
            clean = raw_url.rstrip("/")
            if not clean.endswith("/api"):
                clean = clean + "/api/"
            else:
                clean = clean + "/"
            self.api_url = clean
        else:
            self.api_url = ""
        self.api_token = api_token.strip() if api_token else ""
        self._allocated_record_ids: set[str] = set()

    @_REDCAP_RETRY
    def _post_redcap(self, data: Dict[str, Any], timeout: int = 60) -> requests.Response:
        """Executes a REDCap API HTTP POST with automatic retries on transient connection/timeout failures."""
        return requests.post(self.api_url, data=data, timeout=timeout)

    @staticmethod
    def _default_form_names() -> List[str]:
        """Returns the canonical REDCap instrument names from FormClassifier."""
        from src.form_classifier import AVAILABLE_FORMS
        return list(AVAILABLE_FORMS.keys())

    def fetch_metadata_from_api(self, forms: Optional[List[str]] = None) -> List[REDCapField]:
        """Fetches project metadata (data dictionary) directly from REDCap API.

        Args:
            forms: Optional list of form names to retrieve. If None, retrieves all forms.

        Returns:
            List of parsed REDCapField objects.
        """
        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and Token are required to fetch metadata from API.")

        payload = {
            "token": self.api_token,
            "content": "metadata",
            "format": "json",
            "returnFormat": "json"
        }
        if forms:
            for idx, form in enumerate(forms):
                payload[f"forms[{idx}]"] = form

        response = self._post_redcap(payload, timeout=30)
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected response from REDCap API: {data}")

        return [REDCapField(**item) for item in data]

    def load_metadata_from_file(self, file_path: Union[str, Path]) -> List[REDCapField]:
        """Loads REDCap data dictionary from a local CSV or JSON file.

        Args:
            file_path: Path to the metadata file (.json or .csv).

        Returns:
            List of parsed REDCapField objects.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Metadata file not found: {path}")

        if path.suffix.lower() == ".json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [REDCapField(**item) for item in data]

        elif path.suffix.lower() == ".csv":
            fields: List[REDCapField] = []
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                # Normalize column headers (REDCap CSV headers often contain human labels or underscores)
                for row in reader:
                    normalized_row = self._normalize_csv_row(row)
                    fields.append(REDCapField(**normalized_row))
            return fields
        else:
            raise ValueError(f"Unsupported metadata file format: {path.suffix}. Use .json or .csv")

    def _normalize_csv_row(self, row: Dict[str, str]) -> Dict[str, Any]:
        """Normalizes standard REDCap Data Dictionary CSV column headers to REDCapField attributes."""
        # Standard REDCap header mapping
        mapping = {
            "variable / field name": "field_name",
            "variable / field_name": "field_name",
            "field_name": "field_name",
            "form name": "form_name",
            "form_name": "form_name",
            "section header": "section_header",
            "section_header": "section_header",
            "field type": "field_type",
            "field_type": "field_type",
            "field label": "field_label",
            "field_label": "field_label",
            "choices, calculations, or slider labels": "select_choices_or_calculations",
            "select_choices_or_calculations": "select_choices_or_calculations",
            "field note": "field_note",
            "field_note": "field_note",
            "text validation type or show slider number": "text_validation_type_or_show_slider_number",
            "text_validation_type_or_show_slider_number": "text_validation_type_or_show_slider_number",
            "text validation min": "text_validation_min",
            "text_validation_min": "text_validation_min",
            "text validation max": "text_validation_max",
            "text_validation_max": "text_validation_max",
            "identifier?": "identifier",
            "identifier": "identifier",
            "branching logic (show field only if...)": "branching_logic",
            "branching_logic": "branching_logic",
            "required field?": "required_field",
            "required_field": "required_field",
            "field annotation": "field_annotation",
            "field_annotation": "field_annotation"
        }

        normalized = {}
        for k, v in row.items():
            clean_k = k.strip().lower()
            if clean_k in mapping:
                normalized[mapping[clean_k]] = v.strip() if v else None
            else:
                # Keep direct match if snake_case
                clean_snake = clean_k.replace(" ", "_")
                normalized[clean_snake] = v.strip() if v else None

        # Ensure required field_name exists
        if "field_name" not in normalized or not normalized["field_name"]:
            normalized["field_name"] = row.get(list(row.keys())[0], "").strip()

        return normalized

    def filter_fields_by_form(
        self, fields: List[REDCapField], form_name: Optional[str] = None
    ) -> List[REDCapField]:
        """Filters fields to only extractable fields for a specific form (or all forms).
        Ensures the project primary key (first field in data dictionary) is always included.
        """
        if not fields:
            return []
        primary_key_field = fields[0]

        if not form_name:
            return [f for f in fields if f.is_extractable]

        form_fields = [f for f in fields if f.form_name.lower() == form_name.lower() and f.is_extractable]
        if primary_key_field.is_extractable and primary_key_field.field_name not in [f.field_name for f in form_fields]:
            form_fields.insert(0, primary_key_field)
        return form_fields

    def get_form_names(self, fields: List[REDCapField]) -> List[str]:
        """Returns unique list of form names present in metadata."""
        seen = set()
        forms = []
        for f in fields:
            if f.form_name and f.form_name not in seen:
                seen.add(f.form_name)
                forms.append(f.form_name)
        return forms

    @staticmethod
    def merge_flat_records(records: List[Dict[str, Any]], primary_key: str = "record_id") -> List[Dict[str, Any]]:
        """Merges multiple record dictionaries belonging to the same entity/event.

        Clinical Merging Rule:
        - Records are processed in chronological order (or sorted by '_document_date').
        - If a field has a valid, non-empty value in a more recent record, it updates/overwrites
          the previous value (as newer medical documents represent updates of clinical status).
        - If a newer record has an empty/null value for a field, the previous established clinical
          value is preserved and not erased.
        - For checkbox fields, confirmed findings ('1') are maintained.
        - Completed form flags ('2') are preserved.
        """
        # Sort chronologically parsing ISO/Italian dates properly
        sorted_records = sorted(
            records,
            key=lambda r: (
                parse_flexible_date(
                    r.get("_document_date") or r.get("date_of_follow_up_update") or r.get("data_visita")
                )
                or date(1970, 1, 1)
            )
            if isinstance(r, dict)
            else date(1970, 1, 1),
        )

        merged_by_key: Dict[str, Dict[str, Any]] = {}
        for r in sorted_records:
            if not r or not isinstance(r, dict):
                continue
            key = str(r.get(primary_key, "")).strip()
            if not key:
                continue
            event = str(r.get("redcap_event_name", "")).strip()
            repeat_inst = str(r.get("redcap_repeat_instrument", "")).strip()
            repeat_instance = str(r.get("redcap_repeat_instance", "")).strip()
            compound_key = f"{key}||{event}||{repeat_inst}||{repeat_instance}"

            if compound_key not in merged_by_key:
                merged_by_key[compound_key] = {}

            target = merged_by_key[compound_key]
            for field, val in r.items():
                if field.startswith("_"):
                    # Internal metadata like _document_date: track latest
                    target[field] = val
                    continue

                if val is None or val == "":
                    # Empty value from newer document does NOT erase previous valid data
                    continue

                # Form completion status: '2' (complete) is preserved
                if field.endswith("_complete"):
                    current_status = str(target.get(field, "")).strip()
                    if current_status == "2":
                        continue
                    target[field] = str(val).strip()
                    continue

                # For checkbox fields ('___'), '1' takes precedence over '0'
                if "___" in field:
                    current_val = str(target.get(field, "")).strip()
                    if current_val == "1" and str(val).strip() != "1":
                        continue
                    target[field] = str(val).strip()
                else:
                    # Valid value from more recent document updates older value
                    target[field] = val

        # Strip internal temporary metadata keys before returning
        result = []
        for rec in merged_by_key.values():
            clean_rec = {k: v for k, v in rec.items() if not k.startswith("_")}
            result.append(clean_rec)

        return result

    @staticmethod
    def merge_with_existing_record(
        existing: Optional[Dict[str, Any]],
        updates: Dict[str, Any],
        overwrite: bool = False,
        primary_key: str = "record_id",
    ) -> Dict[str, Any]:
        """Safely merges new extracted updates against an existing REDCap record.

        When `overwrite=False`, existing populated REDCap fields are preserved and
        checked checkboxes ('1') are never cleared to '0'.
        """
        if not existing or overwrite:
            return dict(updates)

        filtered: Dict[str, Any] = {primary_key: str(updates.get(primary_key) or existing.get(primary_key) or "")}
        for k, new_v in updates.items():
            if k == primary_key or k.startswith("_"):
                continue
            if new_v is None or new_v == "":
                continue

            old_v = existing.get(k)
            old_str = str(old_v).strip() if old_v is not None else ""
            new_str = str(new_v).strip()

            if k.endswith("_complete"):
                if old_str == "2":
                    continue
                if old_str == "1" and new_str == "0":
                    continue
                filtered[k] = new_str
            elif "___" in k:
                # Never overwrite a checked box ('1') with '0' in safe fill_missing mode;
                # only emit '1' for newly detected options.
                if new_str == "1" and old_str != "1":
                    filtered[k] = "1"
            else:
                if not old_str:
                    filtered[k] = new_v

        return filtered

    def import_records_to_api(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Directly uploads extracted records to the REDCap project.

        Args:
            records: List of record dictionaries in REDCap flat format.

        Returns:
            API response (typically count of imported records).
        """
        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and Token are required to import records.")

        if not records:
            return {"count": 0}

        cleaned_records = self.merge_flat_records(records)
        if not cleaned_records:
            return {"count": 0}

        payload = {
            "token": self.api_token,
            "content": "record",
            "format": "json",
            "type": "flat",
            "overwriteBehavior": "normal",
            "data": json.dumps(cleaned_records),
            "returnContent": "count",
            "returnFormat": "json"
        }

        response = self._post_redcap(payload, timeout=60)
        if not response.ok:
            error_detail = response.text.strip()
            try:
                err_json = response.json()
                if isinstance(err_json, dict) and "error" in err_json:
                    error_detail = err_json["error"]
            except Exception:
                pass
            raise requests.exceptions.HTTPError(
                f"Errore REDCap API {response.status_code} su importazione record: {error_detail}",
                response=response
            )

        return response.json()

    def export_records_from_api(
        self,
        records: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        forms: Optional[List[str]] = None,
        events: Optional[List[str]] = None,
        filter_logic: Optional[str] = None,
        raw_or_label: str = "raw",
        export_checkbox_label: bool = False,
    ) -> List[Dict[str, Any]]:
        """Exports records from REDCap API in flat JSON format.

        Args:
            records: Optional list of record IDs to retrieve.
            fields: Optional list of specific field names.
            forms: Optional list of specific form/instrument names.
            events: Optional list of event names (for longitudinal projects).
            filter_logic: Optional REDCap filter logic string (e.g. "[anatomia_patologica_complete] != '2'").
            raw_or_label: 'raw' (default) or 'label'.
            export_checkbox_label: Whether to export checkbox labels instead of 0/1.

        Returns:
            List of records in REDCap flat format.
        """
        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and Token are required to export records.")

        payload: Dict[str, Any] = {
            "token": self.api_token,
            "content": "record",
            "format": "json",
            "type": "flat",
            "rawOrLabel": raw_or_label,
            "rawOrLabelHeaders": "raw",
            "exportCheckboxLabel": "true" if export_checkbox_label else "false",
            "exportSurveyFields": "false",
            "exportDataAccessGroups": "false",
            "returnFormat": "json"
        }
        if records:
            for idx, rec in enumerate(records):
                payload[f"records[{idx}]"] = str(rec)
        if fields:
            for idx, f in enumerate(fields):
                payload[f"fields[{idx}]"] = str(f)
        if forms:
            for idx, frm in enumerate(forms):
                payload[f"forms[{idx}]"] = str(frm)
        if events:
            for idx, evt in enumerate(events):
                payload[f"events[{idx}]"] = str(evt)
        if filter_logic:
            payload["filterLogic"] = filter_logic

        response = self._post_redcap(payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected response from REDCap API: {data}")
        return data

    def get_record(
        self,
        record_id: str,
        fields: Optional[List[str]] = None,
        forms: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetches a single record by record_id. Returns None if not found."""
        records = self.export_records_from_api(
            records=[str(record_id)],
            fields=fields,
            forms=forms
        )
        if records and len(records) > 0:
            return records[0]
        return None

    def get_all_patient_ids(self) -> List[str]:
        """Returns all record_ids currently present in the REDCap project."""
        records = self.export_records_from_api(fields=["record_id"])
        ids = []
        for r in records:
            rec_id = str(r.get("record_id", "")).strip()
            if rec_id and rec_id not in ids:
                ids.append(rec_id)
        return ids

    def _export_demographic_records(self, extra_fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Exports demographic lookup records, including `codice_fiscale` when supported by the REDCap schema."""
        base_fields = ["record_id", "name", "surname", "date_of_birth"]
        for ef in extra_fields or []:
            if ef not in base_fields:
                base_fields.append(ef)

        proj_key = self._project_alloc_key()
        with _PROJECT_CF_LOCK:
            cf_supported = _PROJECT_SUPPORTS_CF.get(proj_key, True)

        if cf_supported and "codice_fiscale" not in base_fields:
            try:
                return self.export_records_from_api(fields=base_fields + ["codice_fiscale"])
            except requests.exceptions.HTTPError as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                body_lower = str(getattr(getattr(e, "response", None), "text", "") or e).lower()
                if status_code == 400 and "codice_fiscale" in body_lower:
                    with _PROJECT_CF_LOCK:
                        _PROJECT_SUPPORTS_CF[proj_key] = False
                    return self.export_records_from_api(fields=base_fields)
                raise
        return self.export_records_from_api(fields=base_fields)

    def find_record_id_by_demographics(
        self,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None,
        numeric_only: bool = False,
        codice_fiscale: Optional[str] = None,
        records_cache: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Searches REDCap for an existing patient by Codice Fiscale or (name, surname, DOB).

        Strictly requires date_of_birth (or Codice Fiscale) to match and rejects ambiguous
        multiple matches to prevent cross-contamination of homonyms.
        """
        cf_norm = str(codice_fiscale or "").strip().upper()
        clean_name = normalize_person_name(name)
        clean_surname = normalize_person_name(surname)
        dob_parsed = parse_flexible_date(date_of_birth)
        dob_str = dob_parsed.strftime("%Y-%m-%d") if dob_parsed else ""

        if not cf_norm and (not clean_name or not clean_surname or not dob_str):
            return None

        try:
            records = records_cache
            if records is None:
                records = self._export_demographic_records()

            cf_matches: List[str] = []
            demo_matches: List[str] = []

            for r in records:
                rid = str(r.get("record_id") or "").strip()
                if not rid:
                    continue
                if numeric_only and not rid.isdigit():
                    continue

                if cf_norm:
                    r_cf = str(r.get("codice_fiscale") or "").strip().upper()
                    if r_cf and r_cf == cf_norm:
                        cf_matches.append(rid)

                if clean_name and clean_surname and dob_str:
                    r_name = normalize_person_name(r.get("name"))
                    r_surname = normalize_person_name(r.get("surname"))
                    r_dob_parsed = parse_flexible_date(r.get("date_of_birth"))
                    r_dob = r_dob_parsed.strftime("%Y-%m-%d") if r_dob_parsed else str(r.get("date_of_birth") or "").strip()

                    if r_name == clean_name and r_surname == clean_surname and r_dob and r_dob == dob_str:
                        demo_matches.append(rid)

            if len(cf_matches) == 1:
                return cf_matches[0]
            if len(cf_matches) > 1:
                logger.error(f"Rilevati più record REDCap duplicati con stesso CF ({len(cf_matches)} match).")
                return None

            if len(demo_matches) == 1:
                return demo_matches[0]
            if len(demo_matches) > 1:
                logger.error(f"Rilevati più record REDCap omonimi con stessa anagrafica/DOB ({len(demo_matches)} match).")
                return None
        except Exception as e:
            logger.warning(f"Errore ricerca record_id su REDCap tramite anagrafica: {e}")

        return None

    def _project_alloc_key(self) -> str:
        token_hash = hashlib.sha256(self.api_token.encode("utf-8")).hexdigest()[:12] if self.api_token else "local"
        return f"{self.api_url}::{token_hash}"

    def generate_next_record_name(self) -> str:
        """Generates the next available numeric record ID using REDCap's native API.

        Uses process-wide locking and allocation tracking per REDCap project to avoid
        concurrent ID collisions before upload.
        """
        proj_key = self._project_alloc_key()
        with _ALLOCATED_IDS_LOCK:
            proj_allocated = _ALLOCATED_IDS_BY_PROJECT.setdefault(proj_key, set())
            candidate_num = None
            if self.api_url and self.api_token:
                try:
                    payload = {
                        "token": self.api_token,
                        "content": "generateNextRecordName",
                        "format": "json"
                    }
                    resp = self._post_redcap(payload, timeout=15)
                    if resp.status_code == 200:
                        raw_val = resp.text.strip().strip('"').strip("'")
                        if raw_val and raw_val.isdigit():
                            candidate_num = int(raw_val)
                except Exception as e:
                    logger.warning(f"Chiamata REDCap generateNextRecordName fallita: {e}")

            if candidate_num is None:
                try:
                    existing_ids = self.get_all_patient_ids()
                    numeric_ids = [int(rid) for rid in existing_ids if rid and str(rid).isdigit()]
                    candidate_num = max(numeric_ids) + 1 if numeric_ids else 1
                except Exception as e:
                    logger.error(f"Errore critico nel calcolo del prossimo record_id da REDCap: {e}")
                    raise RuntimeError(
                        "Impossibile generare record_id REDCap: chiamata API generateNextRecordName "
                        f"e recupero degli ID esistenti falliti ({e})."
                    ) from e

            while str(candidate_num) in self._allocated_record_ids or str(candidate_num) in proj_allocated:
                candidate_num += 1

            result_str = str(candidate_num)
            self._allocated_record_ids.add(result_str)
            proj_allocated.add(result_str)
            return result_str

    def list_all_records(
        self,
        fields: Optional[List[str]] = None,
        forms: Optional[List[str]] = None,
        include_demographics: bool = True,
        include_completion_status: bool = False,
        filter_logic: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists all records from the REDCap project.

        Args:
            fields: Specific list of fields to retrieve. If None, retrieves all project fields or default summary fields.
            forms: Optional list of form names to retrieve.
            include_demographics: If True, ensures record_id, name, surname, date_of_birth are included.
            include_completion_status: If True, ensures {form}_complete fields are included.
            filter_logic: Optional REDCap filter logic expression.

        Returns:
            List of record dictionaries from REDCap.
        """
        target_fields = list(fields) if fields else []
        if include_demographics:
            for f in ["record_id", "name", "surname", "date_of_birth"]:
                if f not in target_fields:
                    target_fields.append(f)

        form_names = self._default_form_names()
        if include_completion_status:
            for frm in form_names:
                flag_key = f"{frm}_complete"
                if flag_key not in target_fields:
                    target_fields.append(flag_key)

        if not fields and include_demographics and not forms:
            extra = [f for f in target_fields if f not in {"record_id", "name", "surname", "date_of_birth"}]
            if filter_logic:
                return self.export_records_from_api(
                    fields=target_fields,
                    forms=forms,
                    filter_logic=filter_logic
                )
            return self._export_demographic_records(extra_fields=extra)

        query_fields = target_fields if (fields or include_completion_status or (include_demographics and not forms)) else None

        return self.export_records_from_api(
            fields=query_fields,
            forms=forms,
            filter_logic=filter_logic
        )

    def get_records_summary(self) -> List[Dict[str, Any]]:
        """Returns a high-level summary list of all records with demographics and form status flags."""
        records = self.list_all_records(include_demographics=True, include_completion_status=True)
        form_names = self._default_form_names()
        summary = []
        for r in records:
            forms_status = {}
            completed_count = 0
            for frm in form_names:
                st = str(r.get(f"{frm}_complete") or "").strip()
                label = "Completato" if st == "2" else ("Non verificato" if st == "1" else ("Incompleto" if st == "0" else "Non iniziato"))
                forms_status[frm] = label
                if st == "2":
                    completed_count += 1

            summary.append({
                "record_id": str(r.get("record_id", "")),
                "name": r.get("name", ""),
                "surname": r.get("surname", ""),
                "date_of_birth": r.get("date_of_birth", ""),
                "codice_fiscale": r.get("codice_fiscale", ""),
                "forms_completed_count": completed_count,
                "forms_status": forms_status,
                "raw_record": r
            })
        return summary

    def delete_records(
        self,
        record_ids: List[str],
        arm: Optional[str] = None,
        chunk_size: int = 100
    ) -> int:
        """Deletes a list of records from the REDCap project.

        Args:
            record_ids: List of record_id strings to delete.
            arm: Optional arm number (for longitudinal projects).
            chunk_size: Number of records to delete per API request (default: 100).

        Returns:
            Total count of deleted records.
        """
        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and Token are required to delete records.")

        if not record_ids:
            return 0

        clean_ids = [str(rid).strip() for rid in record_ids if str(rid).strip()]
        if not clean_ids:
            return 0

        total_deleted = 0
        step = max(1, chunk_size)

        for i in range(0, len(clean_ids), step):
            chunk = clean_ids[i:i + step]
            payload: Dict[str, Any] = {
                "token": self.api_token,
                "content": "record",
                "action": "delete"
            }
            if arm:
                payload["arm"] = str(arm)

            for idx, rid in enumerate(chunk):
                payload[f"records[{idx}]"] = str(rid)

            response = self._post_redcap(payload, timeout=60)
            if response.status_code == 403:
                raw_text = response.text.strip()
                # Estrae il messaggio di errore da XML/JSON se presente
                clean_msg = raw_text
                if "<error>" in raw_text and "</error>" in raw_text:
                    clean_msg = raw_text.split("<error>")[1].split("</error>")[0].strip()
                raise PermissionError(
                    f"Errore 403 Forbidden da REDCap API: '{clean_msg}'. "
                    "L'account REDCap associato al token non possiede i permessi per eliminare i record. "
                    "Per abilitarlo: accedi al progetto REDCap -> 'User Rights' -> seleziona l'utente -> abilita la spunta 'Delete Records'."
                )
            response.raise_for_status()

            # Parse deleted count from REDCap response
            chunk_deleted = 0
            try:
                txt = response.text.strip()
                if txt.isdigit():
                    chunk_deleted = int(txt)
                else:
                    data = response.json()
                    if isinstance(data, int):
                        chunk_deleted = data
                    elif isinstance(data, dict) and "count" in data:
                        chunk_deleted = int(data["count"])
                    else:
                        chunk_deleted = len(chunk)
            except Exception:
                chunk_deleted = len(chunk)

            total_deleted += chunk_deleted

        return total_deleted

    def delete_record(
        self,
        record_id: str,
        arm: Optional[str] = None
    ) -> bool:
        """Deletes a single record from REDCap.

        Args:
            record_id: Record identifier to delete.
            arm: Optional arm number.

        Returns:
            True if deleted, False otherwise.
        """
        if not record_id or not str(record_id).strip():
            raise ValueError("record_id non specificato o vuoto.")

        count = self.delete_records(record_ids=[str(record_id).strip()], arm=arm)
        return count > 0

    def delete_all_records(
        self,
        confirm: bool = False,
        arm: Optional[str] = None,
        chunk_size: int = 100,
        clean_local_state: bool = False
    ) -> int:
        """Deletes all records currently present in the REDCap project.

        Args:
            confirm: Safety confirmation flag. Must be explicitly True.
            arm: Optional arm number.
            chunk_size: Number of records per API chunk.
            clean_local_state: If True, also unlinks corresponding local data/state/{id}_state.json.

        Returns:
            Total count of deleted records.
        """
        if not confirm:
            raise ValueError(
                "Operazione ad alto rischio: per eliminare tutti i record presenti su REDCap "
                "è obbligatorio impostare confirm=True."
            )

        all_ids = self.get_all_patient_ids()
        if not all_ids:
            logger.info("Nessun record presente su REDCap da eliminare.")
            return 0

        logger.warning(f"Avvio eliminazione massiva di {len(all_ids)} record da REDCap...")
        deleted_count = self.delete_records(record_ids=all_ids, arm=arm, chunk_size=chunk_size)

        if clean_local_state:
            from src.config import get_settings
            state_dir = get_settings().state_dir
            for pid in all_ids:
                safe_pid = sanitize_identifier(pid, default="")
                if not safe_pid:
                    continue
                sf = state_dir / f"{safe_pid}_state.json"
                if sf.exists():
                    try:
                        sf.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Impossibile rimuovere file di stato locale {sf}: {e}")

        logger.info(f"Eliminati con successo {deleted_count} record da REDCap.")
        return deleted_count


def list_redcap_records(
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    fields: Optional[List[str]] = None,
    forms: Optional[List[str]] = None,
    include_demographics: bool = True,
    include_completion_status: bool = True,
    filter_logic: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Convenience top-level function to list all records from REDCap.

    If api_url and api_token are omitted, they are automatically read from environment/.env.
    """
    from src.config import get_settings
    cfg = get_settings()
    service = REDCapService(
        api_url=api_url or cfg.redcap_api_url,
        api_token=api_token or cfg.redcap_api_token
    )
    return service.list_all_records(
        fields=fields,
        forms=forms,
        include_demographics=include_demographics,
        include_completion_status=include_completion_status,
        filter_logic=filter_logic
    )


def delete_redcap_records(
    record_id: Optional[str] = None,
    all_records: bool = False,
    confirm: bool = False,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    arm: Optional[str] = None,
    chunk_size: int = 100,
    clean_local_state: bool = False
) -> int:
    """Convenience top-level function to delete records from REDCap.

    Supports either single record deletion (`record_id="ID"`) or mass deletion (`all_records=True`).
    For mass deletion, `confirm=True` is strictly required to prevent accidental project wipe.

    Args:
        record_id: Specific record_id to delete (single record mode).
        all_records: Flag to delete all records currently present in the REDCap project.
        confirm: Confirmation flag required when all_records=True.
        api_url: Optional REDCap API URL (falls back to config).
        api_token: Optional REDCap API Token (falls back to config).
        arm: Optional longitudinal arm number.
        chunk_size: Chunk size for batch API requests.
        clean_local_state: If True, also unlinks corresponding local data/state/{id}_state.json.

    Returns:
        Number of deleted records.
    """
    from src.config import get_settings
    cfg = get_settings()
    effective_token = api_token or cfg.redcap_delete_token or cfg.redcap_api_token
    service = REDCapService(
        api_url=api_url or cfg.redcap_api_url,
        api_token=effective_token
    )

    if record_id and not all_records:
        success = service.delete_record(record_id=record_id, arm=arm)
        if success and clean_local_state:
            safe_id = sanitize_identifier(record_id, default="")
            if safe_id:
                state_file = cfg.state_dir / f"{safe_id}_state.json"
                if state_file.exists():
                    try:
                        state_file.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Impossibile rimuovere file di stato locale {state_file}: {e}")
        return 1 if success else 0

    if all_records:
        return service.delete_all_records(
            confirm=confirm,
            arm=arm,
            chunk_size=chunk_size,
            clean_local_state=clean_local_state
        )

    raise ValueError(
        "È necessario specificare 'record_id' per eliminare un singolo record, "
        "oppure 'all_records=True' (con 'confirm=True') per eliminare tutti i record."
    )

