import difflib
import re
from typing import Any, Dict, List, Optional
from src.models import REDCapField


class REDCapNormalizer:
    """Normalizes and validates LLM-extracted data into standard REDCap flat record format."""

    @staticmethod
    def normalize_record(
        extracted_data: Dict[str, Any],
        fields: List[REDCapField],
        record_id: Optional[str] = None,
        default_record_id_field: str = "record_id",
        form_name: Optional[str] = None,
        mark_form_complete: bool = True
    ) -> Dict[str, Any]:
        """Converts extracted key-value dictionary into a valid REDCap record dictionary.

        Args:
            extracted_data: Dict returned by the LLM extraction.
            fields: List of REDCap fields metadata.
            record_id: Optional specific record ID.
            default_record_id_field: Name of the primary key field (usually 'record_id').
            form_name: Optional instrument name to set <form_name>_complete status.
            mark_form_complete: If True, sets <form_name>_complete to '2' (Complete).

        Returns:
            Flat dictionary ready for REDCap API or CSV import.
        """
        record: Dict[str, Any] = {}

        # Primary key handling
        if record_id:
            record[default_record_id_field] = str(record_id)
        elif default_record_id_field in extracted_data and extracted_data[default_record_id_field]:
            record[default_record_id_field] = str(extracted_data[default_record_id_field])

        field_map = {f.field_name: f for f in fields}

        for f in fields:
            if not f.is_extractable:
                continue

            field_name = f.field_name
            val = extracted_data.get(field_name)

            # Skip or set empty for None/null
            if val is None or val == "":
                if f.field_type == "checkbox":
                    # Checkbox fields must have 0 for each option if absent
                    choices = f.parse_choices()
                    for code in choices.keys():
                        record[f"{field_name}___{code}"] = "0"
                else:
                    record[field_name] = ""
                continue

            # Process by field type
            if f.field_type in {"dropdown", "radio"}:
                record[field_name] = REDCapNormalizer._normalize_choice(val, f.parse_choices())

            elif f.field_type in {"yesno", "truefalse"}:
                record[field_name] = REDCapNormalizer._normalize_boolean(val)

            elif f.field_type == "checkbox":
                choices = f.parse_choices()
                selected_codes = REDCapNormalizer._normalize_checkbox_selection(val, choices)
                for code in choices.keys():
                    record[f"{field_name}___{code}"] = "1" if code in selected_codes else "0"

            elif f.field_type == "text" and f.text_validation_type_or_show_slider_number:
                val_type = f.text_validation_type_or_show_slider_number
                if "date" in val_type:
                    record[field_name] = REDCapNormalizer._normalize_date(str(val))
                elif val_type in {"integer", "number", "number_1dp", "number_2dp"}:
                    record[field_name] = REDCapNormalizer._normalize_number(val)
                else:
                    record[field_name] = str(val).strip()
            else:
                record[field_name] = str(val).strip()

        # Enforce REDCap branching logic (field visibility and dependency rules):
        # If a field has branching_logic and that condition evaluates to False,
        # the field is hidden in REDCap. Any value must be cleared back to blank ("")
        # to prevent REDCap from showing:
        # "ERASE THE VALUE OF THE FIELD ... requires that field ... be hidden from view"
        # Multi-pass sweep (up to 3 passes) resolves cascading dependencies
        for _ in range(3):
            changed = False
            for f in fields:
                if not f.is_extractable or not f.branching_logic:
                    continue

                field_name = f.field_name
                is_visible = REDCapNormalizer.evaluate_branching_logic(f.branching_logic, record)
                if not is_visible:
                    if f.field_type == "checkbox":
                        choices = f.parse_choices()
                        for code in choices.keys():
                            chk_key = f"{field_name}___{code}"
                            if record.get(chk_key) not in (None, "", "0"):
                                record[chk_key] = "0"
                                changed = True
                    else:
                        if record.get(field_name) not in (None, ""):
                            record[field_name] = ""
                            changed = True
            if not changed:
                break

        # Mark form as complete in REDCap: 0=Incomplete, 1=Unverified, 2=Complete
        # Only mark complete if explicitly requested AND at least one non-empty clinical field was extracted
        has_clinical_values = any(
            v not in (None, "", [], {}, "0") or (k.endswith("___1"))
            for k, v in record.items()
            if k not in (default_record_id_field, f"{form_name}_complete") and not (k.endswith("___0") and v == "0")
        )
        if form_name and mark_form_complete and has_clinical_values:
            record[f"{form_name}_complete"] = "2"

        return record

    @staticmethod
    def evaluate_branching_logic(branching_logic: Optional[str], record_data: Dict[str, Any]) -> bool:
        """Evaluates a REDCap branching logic rule against current record values.

        Returns:
            True if the field should be visible (condition met or no branching logic),
            False if the field should be hidden from view.
        """
        if not branching_logic or not branching_logic.strip():
            return True

        expr = branching_logic.strip().replace("\r\n", " ").replace("\n", " ")

        # 1. Handle checkbox fields: [field_name(choice_code)]
        def replace_checkbox(match):
            field = match.group(1)
            code = match.group(2)
            chk_val = str(record_data.get(f"{field}___{code}", record_data.get(field, ""))).strip()
            return repr(chk_val)

        expr = re.sub(r"\[([a-zA-Z0-9_]+)\(([a-zA-Z0-9_\-]+)\)\]", replace_checkbox, expr)

        # 2. Handle standard fields: [field_name]
        def replace_field(match):
            field = match.group(1)
            val = str(record_data.get(field, "")).strip()
            return repr(val)

        expr = re.sub(r"\[([a-zA-Z0-9_]+)\]", replace_field, expr)

        # 3. Translate REDCap SQL-like operators to Python
        expr = re.sub(r"<>", r"!=", expr)
        expr = re.sub(r"(?<![!<>=])=(?!=)", r"==", expr)

        expr = re.sub(r"\bAND\b", "and", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "or", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "not", expr, flags=re.IGNORECASE)

        # Convert unquoted integer comparisons (e.g. == 1 -> == '1')
        expr = re.sub(r"([=!<>]=?\s*)(\d+)(?!\w|\'|\")", r'\1"\2"', expr)

        try:
            res = eval(expr, {"__builtins__": {}}, {})
            return bool(res)
        except Exception:
            return True

    @staticmethod
    def _normalize_choice(val: Any, choices: Dict[str, str]) -> str:
        """Matches a raw value or label to a valid REDCap choice code with high clinical precision."""
        str_val = str(val).strip()

        # 1. Exact code match
        if str_val in choices:
            return str_val

        # 2. Case-insensitive code match
        for code in choices.keys():
            if str_val.lower() == str(code).lower():
                return code

        # 3. Exact label match (case-insensitive)
        lower_val = str_val.lower()
        for code, label in choices.items():
            if lower_val == label.strip().lower():
                return code

        # 4. Token-level exact match (e.g. if choice is "Destro" and value is "seno destro")
        val_tokens = set(re.findall(r"\w+", lower_val))
        for code, label in choices.items():
            label_tokens = set(re.findall(r"\w+", label.strip().lower()))
            if label_tokens and label_tokens.issubset(val_tokens):
                return code

        # 5. Fuzzy match with high similarity threshold to prevent dangerous accidental substrings
        best_code: Optional[str] = None
        best_ratio = 0.0
        for code, label in choices.items():
            ratio = difflib.SequenceMatcher(None, lower_val, label.strip().lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_code = code

        if best_code is not None and best_ratio >= 0.80:
            return best_code

        # Fallback to raw string
        return str_val

    @staticmethod
    def _normalize_boolean(val: Any) -> str:
        """Normalizes boolean-like values to '1' (Yes/True) or '0' (No/False)."""
        if isinstance(val, bool):
            return "1" if val else "0"
        str_val = str(val).strip().lower()
        if str_val in {"1", "si", "sì", "yes", "true", "vero", "presente", "positivo"}:
            return "1"
        if str_val in {"0", "no", "false", "falso", "assente", "negativo"}:
            return "0"
        return "0"

    @staticmethod
    def _normalize_checkbox_selection(val: Any, choices: Dict[str, str]) -> List[str]:
        """Extracts list of matching codes from LLM output for checkbox fields."""
        items: List[str] = []
        if isinstance(val, list):
            items = [str(x).strip() for x in val]
        elif isinstance(val, str):
            items = [x.strip() for x in val.split(",") if x.strip()]
        else:
            items = [str(val).strip()]

        selected_codes: List[str] = []
        for item in items:
            code = REDCapNormalizer._normalize_choice(item, choices)
            if code in choices:
                selected_codes.append(code)

        return selected_codes

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """Attempts to normalize Italian/ISO dates to YYYY-MM-DD."""
        clean = date_str.strip()
        # YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
            return clean

        # DD/MM/YYYY or DD-MM-YYYY
        m = re.match(r"^(\d{1,2})[/\.-](\d{1,2})[/\.-](\d{4})$", clean)
        if m:
            day, month, year = m.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return clean

    @staticmethod
    def _normalize_number(val: Any) -> str:
        """Cleans and standardizes numeric values."""
        str_val = str(val).strip().replace(",", ".")
        match = re.search(r"[-+]?\d*\.?\d+", str_val)
        if match:
            return match.group(0)
        return str_val
