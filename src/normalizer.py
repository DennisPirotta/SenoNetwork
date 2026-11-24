import ast
from datetime import datetime
import difflib
import logging
import operator
import re
from typing import Any, Dict, List, Optional
from src.models import REDCapField

logger = logging.getLogger(__name__)

_ITALIAN_MONTHS = {
    "gennaio": 1, "gen": 1,
    "febbraio": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "aprile": 4, "apr": 4,
    "maggio": 5, "mag": 5,
    "giugno": 6, "giu": 6,
    "luglio": 7, "lug": 7,
    "agosto": 8, "ago": 8,
    "settembre": 9, "set": 9, "sett": 9,
    "ottobre": 10, "ott": 10,
    "novembre": 11, "nov": 11,
    "dicembre": 12, "dic": 12,
}


def _safe_eval_ast(node: ast.AST) -> Any:
    """Safely evaluates a restricted boolean/comparison AST without calling eval()."""
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")
    if isinstance(node, ast.UnaryOp):
        val = _safe_eval_ast(node.operand)
        if isinstance(node.op, ast.Not):
            return not bool(val)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise ValueError(f"Unsupported unary op: {node.op}")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not bool(_safe_eval_ast(v)):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for v in node.values:
                if bool(_safe_eval_ast(v)):
                    return True
            return False
        raise ValueError(f"Unsupported bool op: {node.op}")
    if isinstance(node, ast.Compare):
        ops_map = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
        }
        left = _safe_eval_ast(node.left)
        for op, comp_node in zip(node.ops, node.comparators):
            fn = ops_map.get(type(op))
            if fn is None:
                raise ValueError(f"Unsupported comparison op: {op}")
            right = _safe_eval_ast(comp_node)
            if not fn(left, right):
                return False
            left = right
        return True
    raise ValueError(f"Unsupported AST node in branching logic: {type(node).__name__}")


def _extract_code_tokens(text: str) -> List[str]:
    """Extracts alphanumeric tokens containing digits (e.g. '3', '4a', 'b5', 'g2')."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if any(ch.isdigit() for ch in t)]


class REDCapNormalizer:
    """Normalizes and validates LLM-extracted data into standard REDCap flat record format."""

    @staticmethod
    def normalize_record(
        extracted_data: Dict[str, Any],
        fields: List[REDCapField],
        record_id: Optional[str] = None,
        default_record_id_field: str = "record_id",
        form_name: Optional[str] = None,
        mark_form_complete: bool = True,
        form_completion_status: str = "2",
        include_unchecked_checkboxes: bool = True,
    ) -> Dict[str, Any]:
        """Converts extracted key-value dictionary into a valid REDCap record dictionary.

        Args:
            extracted_data: Dict returned by the LLM extraction.
            fields: List of REDCap fields metadata.
            record_id: Optional specific record ID.
            default_record_id_field: Name of the primary key field (usually 'record_id').
            form_name: Optional instrument name to set <form_name>_complete status.
            mark_form_complete: If True, sets <form_name>_complete to form_completion_status.
            form_completion_status: Form completion code: '0' (Incomplete), '1' (Unverified), '2' (Complete).
            include_unchecked_checkboxes: If False, omits '0' entries for unmentioned checkbox fields
                so incremental REDCap uploads do not clear previously ticked checkboxes.

        Returns:
            Flat dictionary ready for REDCap API or CSV import.
        """
        record: Dict[str, Any] = {}

        # Primary key handling
        if record_id:
            record[default_record_id_field] = str(record_id)
        elif default_record_id_field in extracted_data and extracted_data[default_record_id_field]:
            record[default_record_id_field] = str(extracted_data[default_record_id_field])

        for f in fields:
            if not f.is_extractable:
                continue

            field_name = f.field_name
            val = extracted_data.get(field_name)

            # Skip or set empty for None/null
            if val is None or val == "":
                if f.field_type == "checkbox":
                    if include_unchecked_checkboxes:
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
                    if code in selected_codes:
                        record[f"{field_name}___{code}"] = "1"
                    elif include_unchecked_checkboxes or selected_codes:
                        if include_unchecked_checkboxes:
                            record[f"{field_name}___{code}"] = "0"

            elif f.field_type == "text" and f.text_validation_type_or_show_slider_number:
                val_type = f.text_validation_type_or_show_slider_number
                if "date" in val_type:
                    record[field_name] = REDCapNormalizer._normalize_date(str(val))
                elif val_type in {"integer", "number", "number_1dp", "number_2dp"}:
                    record[field_name] = REDCapNormalizer._normalize_number(
                        val,
                        min_val=f.text_validation_min,
                        max_val=f.text_validation_max,
                        integer_only=(val_type == "integer"),
                    )
                else:
                    record[field_name] = str(val).strip()
            else:
                record[field_name] = str(val).strip()

        # Enforce REDCap branching logic (field visibility and dependency rules):
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
                            if chk_key in record and record.get(chk_key) not in (None, "", "0"):
                                record[chk_key] = "0" if include_unchecked_checkboxes else ""
                                changed = True
                    else:
                        if record.get(field_name) not in (None, ""):
                            record[field_name] = ""
                            changed = True
            if not changed:
                break

        # Mark form status only if explicitly requested AND at least one meaningful clinical field was extracted.
        # Non-checkbox fields with legitimate clinical value "0" (e.g. pN=0, margins=0, BI-RADS=0) count as data;
        # checkbox fields count only when checked ("1").
        has_clinical_values = any(
            (v == "1" if "___" in k else v not in (None, "", [], {}))
            for k, v in record.items()
            if k not in (default_record_id_field, f"{form_name}_complete")
        )
        if form_name and mark_form_complete and has_clinical_values:
            record[f"{form_name}_complete"] = str(form_completion_status)

        return record

    @staticmethod
    def evaluate_branching_logic(branching_logic: Optional[str], record_data: Dict[str, Any]) -> bool:
        """Evaluates a REDCap branching logic rule safely using AST evaluation (no eval())."""
        if not branching_logic or not branching_logic.strip():
            return True

        if "[" not in branching_logic:
            return False

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

        # 3. Translate REDCap SQL-like operators to Python syntax
        expr = re.sub(r"<>", r"!=", expr)
        expr = re.sub(r"(?<![!<>=])=(?!=)", r"==", expr)

        expr = re.sub(r"\bAND\b", "and", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "or", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "not", expr, flags=re.IGNORECASE)

        # Convert unquoted integer comparisons (e.g. == 1 -> == "1")
        expr = re.sub(r"([=!<>]=?\s*)(\d+)(?!\w|\'|\")", r'\1"\2"', expr)

        # Convert inequality comparisons with numeric operands to float comparison
        def replace_inequality(m):
            left = m.group(1).strip("'\"")
            op = m.group(2)
            right = m.group(3).strip("'\"")
            try:
                left_f = float(left)
                right_f = float(right)
                return f"({left_f} {op} {right_f})"
            except (ValueError, TypeError):
                return "False"

        expr = re.sub(
            r"(\'[^\']*\'|\"[^\"]*\"|-?\d+(?:\.\d+)?)\s*(<=|>=|<|>)\s*(\'[^\']*\'|\"[^\"]*\"|-?\d+(?:\.\d+)?)",
            replace_inequality,
            expr
        )

        try:
            tree = ast.parse(expr, mode="eval")
            return bool(_safe_eval_ast(tree))
        except Exception as e:
            logger.debug(f"Branching logic non valutabile in modo sicuro ({branching_logic}): {e}")
            return True

    @staticmethod
    def _normalize_choice(val: Any, choices: Dict[str, str]) -> str:
        """Matches a raw value or label to a valid REDCap choice code with high clinical precision."""
        str_val = str(val).strip()
        if not str_val:
            return ""

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

        # 5. Fuzzy match guarded against differing numeric/alphanumeric clinical codes
        # Prevents dangerous collisions like "Grado 3" -> "Grado 1" or "BI-RADS 4A" -> "BI-RADS 4B"
        val_code_tokens = _extract_code_tokens(lower_val)
        best_code: Optional[str] = None
        best_ratio = 0.0
        for code, label in choices.items():
            clean_label = label.strip().lower()
            label_code_tokens = _extract_code_tokens(clean_label)
            if (val_code_tokens or label_code_tokens) and sorted(val_code_tokens) != sorted(label_code_tokens):
                continue
            ratio = difflib.SequenceMatcher(None, lower_val, clean_label).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_code = code

        if best_code is not None and best_ratio >= 0.80:
            return best_code

        # Fallback: return empty string instead of raw unmapped text to prevent REDCap API batch rejection
        logger.debug(f"Valore '{str_val}' non corrisponde ad alcuna scelta valida in REDCap. Valore azzerato.")
        return ""

    @staticmethod
    def _normalize_boolean(val: Any) -> str:
        """Normalizes boolean-like values to '1' (Yes/True) or '0' (No/False).
        Ambiguous, undetermined, or empty clinical findings return '' to avoid falsifying clinical records.
        """
        if val is None:
            return ""
        if isinstance(val, bool):
            return "1" if val else "0"
        str_val = str(val).strip().lower()
        if str_val in {"", "none", "null", "n.d.", "nd", "non pervenuto", "non disponibile", "non valutabile", "non determinabile", "dubbio", "sconosciuto", "inconcludente", "non eseguito"}:
            return ""
        if str_val in {"1", "si", "sì", "yes", "true", "vero", "presente", "positivo"}:
            return "1"
        if str_val in {"0", "no", "false", "falso", "assente", "negativo"}:
            return "0"
        return ""

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
        """Normalizes and validates Italian/ISO dates to YYYY-MM-DD; returns '' if invalid."""
        clean = str(date_str or "").strip()
        if not clean:
            return ""

        # YYYY-MM-DD or YYYY/MM/DD
        m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", clean)
        if m_iso:
            y, mth, d = m_iso.groups()
            try:
                return datetime(int(y), int(mth), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                return ""

        # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
        m_it = re.match(r"^(\d{1,2})[/\.-](\d{1,2})[/\.-](\d{4})$", clean)
        if m_it:
            day, month, year = m_it.groups()
            try:
                return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
            except ValueError:
                return ""

        # Italian textual month: "12 aprile 2024"
        m_txt = re.match(r"^(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})$", clean)
        if m_txt:
            day, month_name, year = m_txt.groups()
            mth_num = _ITALIAN_MONTHS.get(month_name.lower())
            if mth_num:
                try:
                    return datetime(int(year), mth_num, int(day)).strftime("%Y-%m-%d")
                except ValueError:
                    return ""

        return ""

    @staticmethod
    def _normalize_number(
        val: Any,
        min_val: Optional[str] = None,
        max_val: Optional[str] = None,
        integer_only: bool = False,
    ) -> str:
        """Cleans, standardizes, and validates numeric values against optional min/max bounds."""
        str_val = str(val).strip().replace(",", ".")
        match = re.search(r"[-+]?\d*\.?\d+", str_val)
        if not match:
            return ""
        raw_num = match.group(0)
        try:
            num = float(raw_num)
            if min_val is not None and str(min_val).strip() != "":
                if num < float(min_val):
                    return ""
            if max_val is not None and str(max_val).strip() != "":
                if num > float(max_val):
                    return ""
            if integer_only and num.is_integer():
                return str(int(num))
            return raw_num
        except ValueError:
            return ""
