from typing import Any, Dict, List, Optional, Tuple
from src.models import REDCapField


class SchemaBuilder:
    """Builds JSON Schema and prompt documentation from REDCap fields with integrated clinical guidelines."""

    @staticmethod
    def build_json_schema(
        fields: List[REDCapField],
        enforce_all_required: bool = True,
        form_name: Optional[str] = None,
        guidelines_mgr: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Generates a JSON Schema compatible with Ollama and llama.cpp structured outputs (`format`/`json_schema`).

        Args:
            fields: List of REDCap fields to include in the schema.
            enforce_all_required: If True (default), marks every extractable field as required in the schema.
                Since each field property allows null (e.g. `['string', 'null']`), this ensures grammar-constrained
                decoding (GBNF) forces the LLM to output all variables rather than short-circuiting to `{}`.
            form_name: Optional name of the active REDCap form.
            guidelines_mgr: Optional ClinicalGuidelinesManager instance.

        Returns:
            Dictionary representing a JSON Schema object.
        """
        if guidelines_mgr is None:
            try:
                from src.clinical_guidelines import ClinicalGuidelinesManager
                guidelines_mgr = ClinicalGuidelinesManager.get_instance()
            except Exception:
                guidelines_mgr = None

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for field in fields:
            if not field.is_extractable:
                continue

            field_desc = field.field_label.strip().replace("\n", " ")
            if field.branching_logic:
                clean_dep = field.branching_logic.strip().replace("\r\n", " ").replace("\n", " ")
                field_desc = f"{field_desc} [DIPENDENZA: visibile solo se {clean_dep}. Se non soddisfatta, DEVE essere null]"

            if guidelines_mgr:
                fg = guidelines_mgr.get_field_guideline(field.field_name, form_name or field.form_name)
                if fg:
                    field_desc = f"{field_desc} [GUIDA CLINICA: {fg}]"

            # Dropdown / Radio single choice
            if field.field_type in {"dropdown", "radio"}:
                choices = field.parse_choices()
                choices_str = ", ".join([f"'{c}'={lbl[:20]}" for c, lbl in list(choices.items())[:10]])
                allowed_codes = list(choices.keys())
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": allowed_codes + [None],
                    "description": f"{field_desc} [{choices_str}]"
                }

            # Yes/No
            elif field.field_type == "yesno":
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": ["1", "0", None],
                    "description": f"{field_desc} ['1'=Sì, '0'=No]"
                }

            # True/False
            elif field.field_type == "truefalse":
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": ["1", "0", None],
                    "description": f"{field_desc} ['1'=Vero, '0'=Falso]"
                }

            # Checkbox multiple choice
            elif field.field_type == "checkbox":
                choices = field.parse_choices()
                choices_str = ", ".join([f"'{c}'={lbl[:18]}" for c, lbl in list(choices.items())[:8]])
                properties[field.field_name] = {
                    "type": ["array", "null"],
                    "items": {
                        "type": "string",
                        "enum": list(choices.keys())
                    },
                    "description": f"{field_desc} (array codici presenti tra: {choices_str})"
                }

            # Numeric fields
            elif field.field_type == "text" and field.text_validation_type_or_show_slider_number in {
                "integer", "number", "number_1dp", "number_2dp"
            }:
                properties[field.field_name] = {
                    "type": ["number", "string", "null"],
                    "description": f"{field_desc} (numero)"
                }

            # Date fields
            elif field.field_type == "text" and (
                field.text_validation_type_or_show_slider_number and "date" in field.text_validation_type_or_show_slider_number
            ):
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "description": f"{field_desc} (data: YYYY-MM-DD)"
                }

            # Notes / Long text / Standard text
            else:
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "description": field_desc
                }

            # Mark as required when enforce_all_required is True (default) or if required in REDCap dictionary.
            # Crucial: Enforcing all extractable fields in the schema ensures GBNF grammar forces the LLM
            # to evaluate every field (preventing lazy short-circuiting or omission of dependent fields like
            # surgery details, pTNM, margins).
            # Because every property allows null (e.g. ["string", "null"]), fields whose branching condition
            # is not met or not mentioned in the text can be legally emitted as null by the LLM,
            # and any residual values for hidden fields are deterministically sanitized by REDCapNormalizer.
            if enforce_all_required or (field.required_field and field.required_field.strip().lower() == "y"):
                required.append(field.field_name)

        schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties
        }
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def build_prompt_guidelines(
        fields: List[REDCapField],
        form_name: Optional[str] = None,
        guidelines_mgr: Optional[Any] = None
    ) -> str:
        """Constructs concise, high-density field guidelines in Italian for prompt context.
        Optimized to fit within prompt token budgets while clearly communicating allowed codes
        for single/multi-choice fields, visibility rules, and domain clinical guidelines.
        """
        if guidelines_mgr is None:
            try:
                from src.clinical_guidelines import ClinicalGuidelinesManager
                guidelines_mgr = ClinicalGuidelinesManager.get_instance()
            except Exception:
                guidelines_mgr = None

        lines = []
        lines.append("### CAMPI DA ESTRARRE SECONDO I METADATA REDCAP:")
        lines.append(
            "ISTRUZIONI:\n"
            "- Estrai SOLO i dati clinici esplicitamente menzionati nel referto (imposta a null i campi non citati).\n"
            "- Per radio/dropdown/yesno/truefalse: usa ESCLUSIVAMENTE la stringa del CODICE indicato tra parentesi quadre (es. \"1\", \"2\"). Non restituire testo descrittivo.\n"
            "- Per checkbox (multipli): restituisci un array JSON con i SOLI codici delle opzioni accertate nel referto.\n"
            "- Per record_id: se nel referto è presente un ID paziente o cartella, estrailo, altrimenti null.\n"
            "- REGOLE DI DIPENDENZA E VISIBILITÀ (BRANCHING LOGIC): Se un campo indica [DIPENDE DA: ...], può avere un valore SOLO SE la condizione sul campo padre è pienamente verificata nel referto. Se la condizione non è verificata o il campo padre ha un valore diverso, il campo dipendente DEVE RIGOROSAMENTE ESSERE null (non inserire valori dummy come '0' o '9', altrimenti REDCap solleverà errori bloccanti di campo nascosto con valore).\n"
        )

        # Inject general form clinical guidelines if available
        active_forms = set()
        if form_name:
            active_forms.add(form_name)
        for f in fields:
            if f.form_name:
                active_forms.add(f.form_name)

        if guidelines_mgr and active_forms:
            has_any_general = False
            general_lines = ["### LINEE GUIDA CLINICHE SPECIFICHE:"]
            for fn in sorted(list(active_forms)):
                rules = guidelines_mgr.get_form_general_guidelines(fn)
                if rules:
                    has_any_general = True
                    title = guidelines_mgr.get_form_title(fn)
                    general_lines.append(f"• {title} ({fn}):")
                    for r in rules:
                        general_lines.append(f"  - {r}")
            if has_any_general:
                lines.extend(general_lines)
                lines.append("")

        current_form = None
        for f in fields:
            if not f.is_extractable:
                continue

            if f.form_name and f.form_name != current_form:
                current_form = f.form_name
                lines.append(f"\n[Strumento / Form: {current_form}]")

            label = (f.field_label or f.field_name).strip().replace("\n", " ")
            if len(label) > 55:
                label = label[:52] + "..."

            if f.branching_logic:
                clean_dep = f.branching_logic.strip().replace("\r\n", " ").replace("\n", " ")
                label = f"{label} [DIPENDE DA: {clean_dep}]"

            field_guide = None
            if guidelines_mgr:
                field_guide = guidelines_mgr.get_field_guideline(f.field_name, form_name or f.form_name)

            if f.field_type in {"dropdown", "radio", "yesno", "truefalse"}:
                choices = f.parse_choices()
                ch_items = []
                for k, v in list(choices.items())[:10]:
                    clean_v = v.split(" - ")[-1].strip() if " - " in v else v.strip()
                    if len(clean_v) > 18:
                        clean_v = clean_v[:16] + ".."
                    ch_items.append(f'"{k}"={clean_v}')
                line = f"- `{f.field_name}` ({f.field_type}): {label} [{', '.join(ch_items)}]"

            elif f.field_type == "checkbox":
                choices = f.parse_choices()
                ch_items = []
                for k, v in list(choices.items())[:8]:
                    clean_v = v.split(" - ")[-1].strip() if " - " in v else v.strip()
                    if len(clean_v) > 16:
                        clean_v = clean_v[:14] + ".."
                    ch_items.append(f'"{k}"={clean_v}')
                line = f"- `{f.field_name}` ({f.field_type}): {label} [{', '.join(ch_items)}]"

            elif f.field_type == "text" and f.text_validation_type_or_show_slider_number:
                vt = f.text_validation_type_or_show_slider_number
                if "date" in vt:
                    line = f"- `{f.field_name}` ({f.field_type}): {label} (data: YYYY-MM-DD)"
                elif "int" in vt or "num" in vt:
                    line = f"- `{f.field_name}` ({f.field_type}): {label} (numero)"
                else:
                    line = f"- `{f.field_name}` ({f.field_type}): {label}"
            else:
                line = f"- `{f.field_name}` ({f.field_type}): {label}"

            if field_guide:
                line = f"{line}\n  -> [GUIDA CLINICA: {field_guide}]"
            lines.append(line)

        return "\n".join(lines)
