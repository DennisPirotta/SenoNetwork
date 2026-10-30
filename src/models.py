from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class REDCapField(BaseModel):
    """Represents a single field in a REDCap Data Dictionary."""

    field_name: str
    form_name: str = ""
    section_header: Optional[str] = None
    field_type: str = "text"  # text, notes, dropdown, radio, checkbox, yesno, truefalse, descriptive, calc, file, slider
    field_label: str = ""
    select_choices_or_calculations: Optional[str] = None
    field_note: Optional[str] = None
    text_validation_type_or_show_slider_number: Optional[str] = None
    text_validation_min: Optional[str] = None
    text_validation_max: Optional[str] = None
    identifier: Optional[str] = None
    branching_logic: Optional[str] = None
    required_field: Optional[str] = None
    field_annotation: Optional[str] = None

    @property
    def is_extractable(self) -> bool:
        """Determines if the field should be extracted by the LLM."""
        # Non-data fields or auto-calculated fields should be skipped
        if self.field_type in {"descriptive", "calc", "file"}:
            return False
        # Do not try to extract form completion status fields (e.g., form_name_complete)
        if self.field_name.endswith("_complete"):
            return False
        return True

    def parse_choices(self) -> Dict[str, str]:
        """Parses select_choices_or_calculations into a code -> label mapping.

        Examples:
            '1, Destro | 2, Sinistro | 3, Bilaterale' -> {'1': 'Destro', '2': 'Sinistro', '3': 'Bilaterale'}
            '0, No \\n 1, Si' -> {'0': 'No', '1': 'Si'}
        """
        if not self.select_choices_or_calculations:
            if self.field_type == "yesno":
                return {"1": "Si", "0": "No"}
            if self.field_type == "truefalse":
                return {"1": "Vero", "0": "Falso"}
            return {}

        raw = self.select_choices_or_calculations.replace("\r\n", "\n").replace("\r", "\n")
        # Choices can be separated by '|' or by newlines
        parts = []
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|") if p.strip()]
        else:
            parts = [p.strip() for p in raw.split("\n") if p.strip()]

        choices: Dict[str, str] = {}
        for part in parts:
            if "," in part:
                code, label = part.split(",", 1)
                choices[code.strip()] = label.strip()
            else:
                choices[part.strip()] = part.strip()
        return choices


class ExtractionResult(BaseModel):
    """Result of an extraction for a specific PDF file."""

    pdf_path: str
    record_id: Optional[str] = None
    raw_text: str = ""
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    redcap_record: Dict[str, Any] = Field(default_factory=dict)
    extraction_duration_sec: float = 0.0
    status: str = "success"  # success, error, partial
    error_message: Optional[str] = None
