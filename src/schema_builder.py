from typing import Any, Dict, List, Tuple
from src.models import REDCapField


class SchemaBuilder:
    """Builds JSON Schema and prompt documentation from REDCap fields."""

    @staticmethod
    def build_json_schema(fields: List[REDCapField], enforce_all_required: bool = False) -> Dict[str, Any]:
        """Generates a JSON Schema compatible with Ollama and llama.cpp structured outputs (`format`/`json_schema`).

        Args:
            fields: List of REDCap fields to include in the schema.
            enforce_all_required: If True, marks every extractable field as required in the schema.
                If False (default), only mandatory fields (e.g. required_field='y') are marked as required.
                This avoids forcing the LLM to output dozens of redundant null entries, dramatically
                cutting token decoding time and preventing gateway timeouts (Cloudflare 524).

        Returns:
            Dictionary representing a JSON Schema object.
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for field in fields:
            if not field.is_extractable:
                continue

            field_desc = (field.field_label or field.field_name).strip().replace("\n", " ")
            if len(field_desc) > 60:
                field_desc = field_desc[:57] + "..."

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

            # Only mark as required if explicitly enforced or flagged required in REDCap dictionary
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
    def build_prompt_guidelines(fields: List[REDCapField]) -> str:
        """Constructs concise, high-density field guidelines in Italian for prompt context.
        Optimized to fit within prompt token budgets while clearly communicating allowed codes
        for single/multi-choice fields.
        """
        lines = []
        lines.append("### CAMPI DA ESTRARRE SECONDO I METADATA REDCAP:")
        lines.append(
            "ISTRUZIONI:\n"
            "- Estrai SOLO i dati clinici esplicitamente menzionati nel referto (imposta a null i campi non citati).\n"
            "- Per radio/dropdown/yesno/truefalse: usa ESCLUSIVAMENTE la stringa del CODICE indicato tra parentesi quadre (es. \"1\", \"2\"). Non restituire testo descrittivo.\n"
            "- Per checkbox (multipli): restituisci un array JSON con i SOLI codici delle opzioni accertate nel referto.\n"
            "- Per record_id: se nel referto è presente un ID paziente o cartella, estrailo, altrimenti null.\n"
        )

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

            if f.field_type in {"dropdown", "radio", "yesno", "truefalse"}:
                choices = f.parse_choices()
                ch_items = []
                for k, v in list(choices.items())[:10]:
                    clean_v = v.split(" - ")[-1].strip() if " - " in v else v.strip()
                    if len(clean_v) > 18:
                        clean_v = clean_v[:16] + ".."
                    ch_items.append(f'"{k}"={clean_v}')
                lines.append(f"- `{f.field_name}` ({f.field_type}): {label} [{', '.join(ch_items)}]")

            elif f.field_type == "checkbox":
                choices = f.parse_choices()
                ch_items = []
                for k, v in list(choices.items())[:8]:
                    clean_v = v.split(" - ")[-1].strip() if " - " in v else v.strip()
                    if len(clean_v) > 16:
                        clean_v = clean_v[:14] + ".."
                    ch_items.append(f'"{k}"={clean_v}')
                lines.append(f"- `{f.field_name}` ({f.field_type}): {label} [{', '.join(ch_items)}]")

            elif f.field_type == "text" and f.text_validation_type_or_show_slider_number:
                vt = f.text_validation_type_or_show_slider_number
                if "date" in vt:
                    lines.append(f"- `{f.field_name}` ({f.field_type}): {label} (data: YYYY-MM-DD)")
                elif "int" in vt or "num" in vt:
                    lines.append(f"- `{f.field_name}` ({f.field_type}): {label} (numero)")
                else:
                    lines.append(f"- `{f.field_name}` ({f.field_type}): {label}")
            else:
                lines.append(f"- `{f.field_name}` ({f.field_type}): {label}")

        return "\n".join(lines)
