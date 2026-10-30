import csv
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
