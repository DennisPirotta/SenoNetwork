import csv
from datetime import date, datetime
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import requests

from src.models import REDCapField

logger = logging.getLogger(__name__)


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

        response = requests.post(self.api_url, data=payload, timeout=30)
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

        REDCap rejects imports if the same (record_id, event, repeat_instance) occurs more
        than once in the payload with HTTP 400 'The records listed below occur more than once'.
        This method consolidates fields across multiple reports or forms for each record.
        """
        merged_by_key: Dict[str, Dict[str, Any]] = {}
        for r in records:
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
                if val is None or val == "":
                    continue
                # For checkbox fields ('___'), '1' takes precedence over '0'
                if "___" in field:
                    current_val = str(target.get(field, "")).strip()
                    if current_val == "1":
                        continue
                    target[field] = str(val).strip()
                else:
                    target[field] = val

        return list(merged_by_key.values())

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

        response = requests.post(self.api_url, data=payload, timeout=60)
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

        response = requests.post(self.api_url, data=payload, timeout=60)
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

    def find_record_id_by_demographics(
        self,
        name: Optional[str] = None,
        surname: Optional[str] = None,
        date_of_birth: Optional[Union[date, str]] = None
    ) -> Optional[str]:
        """Searches REDCap for an existing patient by name, surname and date of birth.

        Returns the native REDCap 'record_id' if found, preserving the original project
        primary key and avoiding accidental creation of duplicate patient records.
        """
        if not name or not surname:
            return None

        clean_name = name.strip().lower()
        clean_surname = surname.strip().lower()
        dob_str = ""
        if date_of_birth:
            if isinstance(date_of_birth, date):
                dob_str = date_of_birth.strftime("%Y-%m-%d")
            else:
                s = str(date_of_birth).strip()
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
                    try:
                        dob_str = datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                if not dob_str:
                    dob_str = s

        try:
            records = self.export_records_from_api(
                fields=["record_id", "name", "surname", "date_of_birth"]
            )
            for r in records:
                r_name = str(r.get("name") or "").strip().lower()
                r_surname = str(r.get("surname") or "").strip().lower()
                r_dob = str(r.get("date_of_birth") or "").strip()

                if r_name == clean_name and r_surname == clean_surname:
                    if dob_str and r_dob:
                        if r_dob == dob_str:
                            return str(r.get("record_id"))
                    else:
                        return str(r.get("record_id"))
        except Exception as e:
            logger.warning(f"Errore ricerca record_id su REDCap tramite anagrafica: {e}")

        return None

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

        if include_completion_status:
            for frm in [
                "dati_del_paziente",
                "radiologia",
                "anatomia_patologica_preoperatoria",
                "struttura_della_lesione",
                "anatomia_patologica",
                "chemioterapia",
                "radioterapia",
                "recidiva",
            ]:
                flag_key = f"{frm}_complete"
                if flag_key not in target_fields:
                    target_fields.append(flag_key)

        query_fields = target_fields if (fields or include_completion_status or (include_demographics and not forms)) else None

        return self.export_records_from_api(
            fields=query_fields,
            forms=forms,
            filter_logic=filter_logic
        )

    def get_records_summary(self) -> List[Dict[str, Any]]:
        """Returns a high-level summary list of all records with demographics and form status flags."""
        records = self.list_all_records(include_demographics=True, include_completion_status=True)
        summary = []
        for r in records:
            forms_status = {}
            completed_count = 0
            for frm in [
                "dati_del_paziente",
                "radiologia",
                "anatomia_patologica_preoperatoria",
                "struttura_della_lesione",
                "anatomia_patologica",
                "chemioterapia",
                "radioterapia",
                "recidiva",
            ]:
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

            response = requests.post(self.api_url, data=payload, timeout=60)
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
                sf = state_dir / f"{pid}_state.json"
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
    service = REDCapService(
        api_url=api_url or cfg.redcap_api_url,
        api_token=api_token or cfg.redcap_api_token
    )

    if record_id and not all_records:
        success = service.delete_record(record_id=record_id, arm=arm)
        if success and clean_local_state:
            state_file = cfg.state_dir / f"{record_id}_state.json"
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

