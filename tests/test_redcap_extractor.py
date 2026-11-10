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


def test_list_all_records_and_summary():
    """Tests list_all_records and get_records_summary methods."""
    from unittest.mock import patch, MagicMock

    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")
    mock_records = [
        {
            "record_id": "PZ-01",
            "name": "Maria",
            "surname": "Rossi",
            "date_of_birth": "1965-01-01",
            "dati_del_paziente_complete": "2",
            "radiologia_complete": "2",
            "anatomia_patologica_complete": "0"
        },
        {
            "record_id": "PZ-02",
            "name": "Luisa",
            "surname": "Verdi",
            "date_of_birth": "1970-05-15",
            "dati_del_paziente_complete": "2",
            "radiologia_complete": "0",
            "anatomia_patologica_complete": "2"
        }
    ]

    with patch.object(service, "export_records_from_api", return_value=mock_records) as mock_export:
        records = service.list_all_records(include_demographics=True, include_completion_status=True)
        assert len(records) == 2
        assert records[0]["record_id"] == "PZ-01"
        assert mock_export.called

        # Test summary
        summary = service.get_records_summary()
        assert len(summary) == 2
        assert summary[0]["record_id"] == "PZ-01"
        assert summary[0]["name"] == "Maria"
        assert summary[0]["forms_completed_count"] == 2
        assert summary[0]["forms_status"]["dati_del_paziente"] == "Completato"
        assert summary[0]["forms_status"]["anatomia_patologica"] == "Incompleto"


def test_list_redcap_records_top_level_function():
    """Tests top-level helper function list_redcap_records."""
    from unittest.mock import patch
    from src.redcap_service import list_redcap_records

    mock_records = [{"record_id": "1", "name": "Anna", "surname": "Neri"}]
    with patch("src.redcap_service.REDCapService.export_records_from_api", return_value=mock_records):
        res = list_redcap_records(api_url="https://mock.url/api/", api_token="TOKEN")
        assert len(res) == 1
        assert res[0]["record_id"] == "1"


def test_delete_record_single():
    """Tests single record deletion via REDCapService.delete_record."""
    from unittest.mock import patch, MagicMock

    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")
    
    # Empty ID raises ValueError
    with pytest.raises(ValueError):
        service.delete_record(record_id="")

    # Mock successful POST response returning 1
    mock_resp = MagicMock()
    mock_resp.text = "1"
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_resp) as mock_post:
        success = service.delete_record(record_id="PZ-999")
        assert success is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["content"] == "record"
        assert call_kwargs["data"]["action"] == "delete"
        assert call_kwargs["data"]["records[0]"] == "PZ-999"


def test_delete_records_chunked():
    """Tests batch deletion with chunking in REDCapService.delete_records."""
    from unittest.mock import patch, MagicMock

    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")
    ids = ["PZ-01", "PZ-02", "PZ-03", "PZ-04", "PZ-05"]

    mock_resp = MagicMock()
    mock_resp.text = "2"
    mock_resp.status_code = 200

    with patch("requests.post", return_value=mock_resp) as mock_post:
        total = service.delete_records(record_ids=ids, chunk_size=2)
        # 5 items chunked at 2 -> 3 chunks (2, 2, 1)
        assert mock_post.call_count == 3
        # In our mock, each chunk returned text "2", so 3 * 2 = 6
        assert total == 6


def test_delete_all_records_safety_and_execution(tmp_path):
    """Tests that delete_all_records enforces confirm=True and deletes all records."""
    from unittest.mock import patch, MagicMock

    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")

    # Safety check: confirm=False must raise ValueError
    with pytest.raises(ValueError, match="confirm=True"):
        service.delete_all_records(confirm=False)

    # Empty project
    with patch.object(service, "get_all_patient_ids", return_value=[]):
        assert service.delete_all_records(confirm=True) == 0

    # With records and clean_local_state
    fake_state_dir = tmp_path / "state"
    fake_state_dir.mkdir()
    state_file1 = fake_state_dir / "PZ-01_state.json"
    state_file1.write_text("{}")

    with patch.object(service, "get_all_patient_ids", return_value=["PZ-01", "PZ-02"]), \
         patch.object(service, "delete_records", return_value=2) as mock_del, \
         patch("src.config.get_settings") as mock_settings:
        
        mock_cfg = MagicMock()
        mock_cfg.state_dir = fake_state_dir
        mock_settings.return_value = mock_cfg

        count = service.delete_all_records(confirm=True, clean_local_state=True)
        assert count == 2
        mock_del.assert_called_once_with(record_ids=["PZ-01", "PZ-02"], arm=None, chunk_size=100)
        assert not state_file1.exists()


def test_delete_redcap_records_helper():
    """Tests top-level convenience delete_redcap_records function."""
    from unittest.mock import patch
    from src.redcap_service import delete_redcap_records

    # Neither single record nor all_records
    with pytest.raises(ValueError, match="È necessario specificare"):
        delete_redcap_records()

    # Single record deletion
    with patch("src.redcap_service.REDCapService.delete_record", return_value=True) as mock_single:
        deleted = delete_redcap_records(record_id="PZ-01", api_url="https://m/api/", api_token="T")
        assert deleted == 1
        mock_single.assert_called_once_with(record_id="PZ-01", arm=None)

    # Mass deletion
    with patch("src.redcap_service.REDCapService.delete_all_records", return_value=5) as mock_all:
        deleted = delete_redcap_records(all_records=True, confirm=True, api_url="https://m/api/", api_token="T")
        assert deleted == 5
        mock_all.assert_called_once_with(confirm=True, arm=None, chunk_size=100, clean_local_state=False)


def test_api_server_delete_endpoints():
    """Tests FastAPI delete endpoints in src.api_server."""
    from unittest.mock import patch, MagicMock
    from fastapi import HTTPException
    from src.api_server import delete_single_redcap_record, delete_all_redcap_records

    mock_cfg = MagicMock()
    mock_cfg.redcap_api_url = "https://mock.redcap/api/"
    mock_cfg.redcap_api_token = "TOKEN"

    # 1. Single delete success
    with patch("src.redcap_service.delete_redcap_records", return_value=1) as mock_del:
        res = delete_single_redcap_record(record_id="PZ-01", clean_local_state=False, settings=mock_cfg)
        assert res["status"] == "deleted"
        assert res["record_id"] == "PZ-01"
        assert res["deleted_count"] == 1

    # 2. Single delete not found (returns 0) -> HTTPException 404
    with patch("src.redcap_service.delete_redcap_records", return_value=0):
        with pytest.raises(HTTPException) as exc_info:
            delete_single_redcap_record(record_id="PZ-NONEXIST", clean_local_state=False, settings=mock_cfg)
        assert exc_info.value.status_code == 404

    # 3. Mass delete without confirm -> HTTPException 400
    with pytest.raises(HTTPException) as exc_info:
        delete_all_redcap_records(confirm=False, settings=mock_cfg)
    assert exc_info.value.status_code == 400

    # 4. Mass delete with confirm=True -> success
    with patch("src.redcap_service.delete_redcap_records", return_value=12) as mock_mass:
        res = delete_all_redcap_records(confirm=True, clean_local_state=True, settings=mock_cfg)
        assert res["status"] == "deleted"
        assert res["deleted_count"] == 12
        mock_mass.assert_called_once_with(
            all_records=True,
            confirm=True,
            clean_local_state=True,
            api_url="https://mock.redcap/api/",
            api_token="TOKEN"
        )


