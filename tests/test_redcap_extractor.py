import json
import pytest
from pathlib import Path
from src.models import REDCapField, ExtractionResult
from src.redcap_service import REDCapService
from src.schema_builder import SchemaBuilder
from src.normalizer import REDCapNormalizer
from src.pdf_extractor import PDFExtractor


@pytest.fixture
def sample_fields():
    return [
        REDCapField(
            field_name="record_id",
            form_name="scheda_paziente",
            field_type="text",
            field_label="Codice Paziente"
        ),
        REDCapField(
            field_name="data_visita",
            form_name="scheda_paziente",
            field_type="text",
            field_label="Data Visita",
            text_validation_type_or_show_slider_number="date_ymd"
        ),
        REDCapField(
            field_name="lateralita",
            form_name="scheda_paziente",
            field_type="radio",
            field_label="Lateralità lesione",
            select_choices_or_calculations="1, Destra | 2, Sinistra | 3, Bilaterale"
        ),
        REDCapField(
            field_name="linfonodi_positivi",
            form_name="scheda_paziente",
            field_type="yesno",
            field_label="Linfonodi positivi?"
        ),
        REDCapField(
            field_name="sintomi",
            form_name="scheda_paziente",
            field_type="checkbox",
            field_label="Sintomi clinici",
            select_choices_or_calculations="1, Dolore | 2, Nodulo | 3, Secrezione"
        ),
        REDCapField(
            field_name="dimensione_mm",
            form_name="scheda_paziente",
            field_type="text",
            field_label="Dimensione (mm)",
            text_validation_type_or_show_slider_number="number"
        ),
        REDCapField(
            field_name="note_esame",
            form_name="scheda_paziente",
            field_type="descriptive",
            field_label="Testo descrittivo informativo"
        ),
        REDCapField(
            field_name="calcolo_totale",
            form_name="scheda_paziente",
            field_type="calc",
            field_label="Totale calcolato"
        )
    ]


def test_field_is_extractable(sample_fields):
    field_map = {f.field_name: f for f in sample_fields}
    assert field_map["record_id"].is_extractable is True
    assert field_map["lateralita"].is_extractable is True
    assert field_map["sintomi"].is_extractable is True
    # Descriptive and calc fields should NOT be extracted
    assert field_map["note_esame"].is_extractable is False
    assert field_map["calcolo_totale"].is_extractable is False


def test_parse_choices(sample_fields):
    field_map = {f.field_name: f for f in sample_fields}
    choices = field_map["lateralita"].parse_choices()
    assert choices == {"1": "Destra", "2": "Sinistra", "3": "Bilaterale"}

    yesno_choices = field_map["linfonodi_positivi"].parse_choices()
    assert yesno_choices == {"1": "Si", "0": "No"}


def test_schema_builder(sample_fields):
    json_schema = SchemaBuilder.build_json_schema(sample_fields)
    assert json_schema["type"] == "object"
    assert "properties" in json_schema
    assert "lateralita" in json_schema["properties"]
    assert "sintomi" in json_schema["properties"]
    assert json_schema["properties"]["sintomi"]["type"] == ["array", "null"]
    # Descriptive and calc must not be present in schema
    assert "note_esame" not in json_schema["properties"]
    assert "calcolo_totale" not in json_schema["properties"]

    guidelines = SchemaBuilder.build_prompt_guidelines(sample_fields)
    assert "CAMPI DA ESTRARRE SECONDO I METADATA REDCAP" in guidelines
    assert "lateralita" in guidelines
    assert "sintomi" in guidelines


def test_redcap_normalizer(sample_fields):
    raw_llm_output = {
        "record_id": "PZ-100",
        "data_visita": "15/04/2024",
        "lateralita": "Sinistra",  # LLM returned the label instead of code '2'
        "linfonodi_positivi": "No",
        "sintomi": ["Nodulo", "3"],  # Mixed label and code for checkbox
        "dimensione_mm": "18.5 mm"
    }

    normalized = REDCapNormalizer.normalize_record(
        extracted_data=raw_llm_output,
        fields=sample_fields,
        form_name="scheda_paziente",
        mark_form_complete=True
    )

    assert normalized["record_id"] == "PZ-100"
    assert normalized["data_visita"] == "2024-04-15"  # Date normalized to YYYY-MM-DD
    assert normalized["lateralita"] == "2"  # Mapped from 'Sinistra' to code '2'
    assert normalized["linfonodi_positivi"] == "0"  # 'No' mapped to '0'
    assert normalized["dimensione_mm"] == "18.5"

    # REDCap Checkbox flat mapping test:
    # 1 (Dolore): absent -> 0
    # 2 (Nodulo): present -> 1
    # 3 (Secrezione): present -> 1
    assert normalized["sintomi___1"] == "0"
    assert normalized["sintomi___2"] == "1"
    assert normalized["sintomi___3"] == "1"

    # Completion field
    assert normalized["scheda_paziente_complete"] == "2"


def test_load_metadata_json_and_csv():
    service = REDCapService()
    json_path = Path("examples/redcap_metadata_senonetwork.json")
    csv_path = Path("examples/redcap_metadata_senonetwork.csv")

    fields_json = service.load_metadata_from_file(json_path)
    assert len(fields_json) > 10
    field_names_json = [f.field_name for f in fields_json]
    assert "record_id" in field_names_json
    assert "istotipo" in field_names_json
    assert "ki67" in field_names_json

    fields_csv = service.load_metadata_from_file(csv_path)
    assert len(fields_csv) == len(fields_json)
    field_names_csv = [f.field_name for f in fields_csv]
    assert "record_id" in field_names_csv
    assert "istotipo" in field_names_csv
