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

    def import_records_to_api(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Directly uploads extracted records to the REDCap project.

        Args:
            records: List of record dictionaries in REDCap flat format.

        Returns:
            API response (typically count of imported records).
        """
        if not self.api_url or not self.api_token:
            raise ValueError("REDCap API URL and Token are required to import records.")

        payload = {
            "token": self.api_token,
            "content": "record",
            "format": "json",
            "type": "flat",
            "overwriteBehavior": "normal",
            "data": json.dumps(records),
            "returnContent": "count",
            "returnFormat": "json"
        }

        response = requests.post(self.api_url, data=payload, timeout=60)
        response.raise_for_status()
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
