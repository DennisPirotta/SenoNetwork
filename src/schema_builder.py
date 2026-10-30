from typing import Any, Dict, List, Tuple
from src.models import REDCapField


class SchemaBuilder:
    """Builds JSON Schema and prompt documentation from REDCap fields."""

    @staticmethod
    def build_json_schema(fields: List[REDCapField]) -> Dict[str, Any]:
        """Generates a JSON Schema compatible with Ollama's structured outputs (`format` parameter).

        Args:
            fields: List of REDCap fields to include in the schema.

        Returns:
            Dictionary representing a JSON Schema object.
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for field in fields:
            if not field.is_extractable:
                continue

            field_desc = field.field_label or field.field_name
            if field.field_note:
                field_desc += f" (Nota: {field.field_note})"

            # Dropdown / Radio single choice
            if field.field_type in {"dropdown", "radio"}:
                choices = field.parse_choices()
                choices_str = ", ".join([f"'{c}'={lbl}" for c, lbl in choices.items()])
                allowed_codes = list(choices.keys())
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": allowed_codes + [None],
                    "description": f"{field_desc}. Scegli uno tra i codici ammessi: {choices_str}"
                }

            # Yes/No
            elif field.field_type == "yesno":
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": ["1", "0", None],
                    "description": f"{field_desc}. Usa '1' per Sì/Presente, '0' per No/Assente."
                }

            # True/False
            elif field.field_type == "truefalse":
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "enum": ["1", "0", None],
                    "description": f"{field_desc}. Usa '1' per Vero, '0' per Falso."
                }

            # Checkbox multiple choice
            elif field.field_type == "checkbox":
                choices = field.parse_choices()
                choices_str = ", ".join([f"'{c}'={lbl}" for c, lbl in choices.items()])
                properties[field.field_name] = {
                    "type": ["array", "null"],
                    "items": {
                        "type": "string",
                        "enum": list(choices.keys())
                    },
                    "description": (
                        f"{field_desc}. Array contenente SOLO i codici delle opzioni ACCERTATE "
                        f"nel referto tra: {choices_str}. NON includere sintomi o condizioni assenti."
                    )
                }

            # Numeric fields
            elif field.field_type == "text" and field.text_validation_type_or_show_slider_number in {
                "integer", "number", "number_1dp", "number_2dp"
            }:
                properties[field.field_name] = {
                    "type": ["number", "string", "null"],
                    "description": f"{field_desc}. Valore numerico."
                }

            # Date fields
            elif field.field_type == "text" and (
                field.text_validation_type_or_show_slider_number and "date" in field.text_validation_type_or_show_slider_number
            ):
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "description": f"{field_desc}. Formato data standard: YYYY-MM-DD (es. 2024-03-15)."
                }

            # Notes / Long text / Standard text
            else:
                properties[field.field_name] = {
                    "type": ["string", "null"],
                    "description": field_desc
                }

            # We can mark fields as required in the JSON schema or let all be optional/nullable
            required.append(field.field_name)

        schema = {
            "type": "object",
            "properties": properties,
            "required": required
        }
        return schema

    @staticmethod
    def build_prompt_guidelines(fields: List[REDCapField]) -> str:
        """Constructs human/LLM-readable field guidelines in Italian for prompt context."""
        lines = []
        lines.append("### CAMPI DA ESTRARRE SECONDO I METADATA REDCAP:")
        lines.append(
            "ISTRUZIONI RIGOROSE:\n"
            "- Se un dato non è presente nel referto, restituisci null.\n"
            "- Per radio/dropdown: restituisci SOLO la stringa del codice ammesso (es. \"1\", \"2\"). Non restituire il testo.\n"
            "- Per i campi checkbox (a scelta multipla): includi nell'array SOLO i codici delle opzioni ACCERTATE/PRESENTI nel referto. Non includere tutte le opzioni!\n"
            "- Per record_id: se nel referto è presente un ID paziente, cartella o codice (es. SENO-2024-0891), estrailo.\n"
        )

        current_form = None
        for f in fields:
            if not f.is_extractable:
                continue

            if f.form_name and f.form_name != current_form:
                current_form = f.form_name
                lines.append(f"\n[Strumento / Form: {current_form}]")

            label = f.field_label.strip() if f.field_label else f.field_name
            line = f"- `{f.field_name}` ({f.field_type}): {label}"

            if f.field_note:
                line += f" [Note: {f.field_note}]"

            if f.field_type in {"dropdown", "radio", "yesno", "truefalse"}:
                choices = f.parse_choices()
                choices_repr = " | ".join([f"{k} = {v}" for k, v in choices.items()])
                line += f" -> Restituisci il CODICE corrispondente tra: [{choices_repr}]"
            elif f.field_type == "checkbox":
                choices = f.parse_choices()
                choices_repr = " | ".join([f"{k} = {v}" for k, v in choices.items()])
                line += f" -> Restituisci un array con i CODICI delle opzioni presenti nel referto tra: [{choices_repr}]"
            elif f.text_validation_type_or_show_slider_number:
                line += f" -> Validazione: {f.text_validation_type_or_show_slider_number}"

            lines.append(line)

        return "\n".join(lines)
