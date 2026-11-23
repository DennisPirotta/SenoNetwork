import json
from unittest.mock import MagicMock, patch
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


def test_merge_flat_records():
    """Tests REDCapService.merge_flat_records consolidates duplicate record entries."""
    raw_records = [
        # Patient 1, Report 1 (Radiology)
        {
            "record_id": "PZ-01",
            "date_of_mx": "2025-01-20",
            "result_of_mx_birads": "5",
            "radiologia_complete": "2",
            "lesion_type___1": "1",
            "lesion_type___2": "0",
        },
        # Patient 1, Report 2 (Biopsy)
        {
            "record_id": "PZ-01",
            "date_of_cb": "2025-01-28",
            "result_cb": "B5",
            "anatomia_patologica_preoperatoria_complete": "2",
            "lesion_type___1": "0",  # Checkbox 1 should remain '1' due to precedence
            "lesion_type___2": "1",
        },
        # Patient 2 (Single record)
        {
            "record_id": "PZ-02",
            "date_of_mx": "2025-02-01",
        }
    ]

    merged = REDCapService.merge_flat_records(raw_records)
    assert len(merged) == 2

    p1 = next(r for r in merged if r["record_id"] == "PZ-01")
    assert p1["date_of_mx"] == "2025-01-20"
    assert p1["result_of_mx_birads"] == "5"
    assert p1["date_of_cb"] == "2025-01-28"
    assert p1["result_cb"] == "B5"
    assert p1["radiologia_complete"] == "2"
    assert p1["anatomia_patologica_preoperatoria_complete"] == "2"
    assert p1["lesion_type___1"] == "1"
    assert p1["lesion_type___2"] == "1"

    p2 = next(r for r in merged if r["record_id"] == "PZ-02")
    assert p2["date_of_mx"] == "2025-02-01"


def test_import_records_to_api_merges_and_formats_errors():
    """Tests that import_records_to_api automatically merges duplicate entries and extracts error messages."""
    import requests
    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")

    raw_records = [
        {"record_id": "PZ-01", "name": "Anna"},
        {"record_id": "PZ-01", "surname": "Rossi"}
    ]

    # Test success path: payload received by requests.post must have only 1 merged record
    mock_resp_ok = MagicMock()
    mock_resp_ok.ok = True
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = {"count": 1}

    with patch("requests.post", return_value=mock_resp_ok) as mock_post:
        res = service.import_records_to_api(raw_records)
        assert res["count"] == 1
        sent_data_str = mock_post.call_args[1]["data"]["data"]
        sent_json = json.loads(sent_data_str)
        assert len(sent_json) == 1
        assert sent_json[0]["name"] == "Anna"
        assert sent_json[0]["surname"] == "Rossi"

    # Test error path: 400 with REDCap error details
    mock_resp_err = MagicMock()
    mock_resp_err.ok = False
    mock_resp_err.status_code = 400
    mock_resp_err.text = '{"error": "Field xyz does not exist"}'
    mock_resp_err.json.return_value = {"error": "Field xyz does not exist"}

    with patch("requests.post", return_value=mock_resp_err):
        with pytest.raises(requests.exceptions.HTTPError, match="Field xyz does not exist"):
            service.import_records_to_api(raw_records)


def test_generate_next_record_name_api_and_session_tracking():
    """Tests that generate_next_record_name calls REDCap API and allocates unique sequential numbers."""
    from unittest.mock import patch, MagicMock

    service = REDCapService(api_url="https://mock.redcap/api/", api_token="TOKEN")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "1"

    with patch("requests.post", return_value=mock_resp):
        id1 = service.generate_next_record_name()
        assert id1 == "1"

        # Successive call in same session increments to avoid collisions before upload
        id2 = service.generate_next_record_name()
        assert id2 == "2"

        id3 = service.generate_next_record_name()
        assert id3 == "3"


def test_schema_builder_branching_logic():
    fields = [
        REDCapField(
            field_name="genetic_assessment",
            form_name="dati_del_paziente",
            field_type="radio",
            field_label="Valutazione genetica",
            select_choices_or_calculations="1, Eseguita | 2, In corso | 9, Non eseguita"
        ),
        REDCapField(
            field_name="brca1",
            form_name="dati_del_paziente",
            field_type="radio",
            field_label="BRCA 1",
            select_choices_or_calculations="1, Mutato | 2, Wild type | 9, Non disponibile",
            branching_logic="[genetic_assessment] = '1'"
        )
    ]

    schema = SchemaBuilder.build_json_schema(fields, enforce_all_required=True)
    # Both fields must be in required to force GBNF grammar to inspect every field (preventing data loss)
    assert "genetic_assessment" in schema.get("required", [])
    assert "brca1" in schema.get("required", [])
    # brca1 explicitly allows null in schema so the LLM can output null when hidden
    assert None in schema["properties"]["brca1"]["enum"]
    # Dependency is documented in property description
    assert "DIPENDENZA" in schema["properties"]["brca1"]["description"]

    # When enforce_all_required=False, only fields marked required_field='y' are in required
    schema_optional = SchemaBuilder.build_json_schema(fields, enforce_all_required=False)
    assert schema_optional.get("required") is None or "brca1" not in schema_optional.get("required", [])

    # Guidelines include branching logic instruction and tag
    guidelines = SchemaBuilder.build_prompt_guidelines(fields)
    assert "BRANCHING LOGIC" in guidelines
    assert "[DIPENDE DA: [genetic_assessment] = '1']" in guidelines


def test_normalizer_branching_logic_erases_hidden_values():
    fields = [
        REDCapField(
            field_name="genetic_assessment",
            field_type="radio",
            select_choices_or_calculations="1, Eseguita | 9, Non eseguita"
        ),
        REDCapField(
            field_name="brca1",
            field_type="radio",
            select_choices_or_calculations="1, Mutato | 2, Wild type | 9, Non disponibile",
            branching_logic="[genetic_assessment] = '1'"
        ),
        REDCapField(
            field_name="presence_of_distant_metastases",
            field_type="radio",
            select_choices_or_calculations="1, Si | 0, No"
        ),
        REDCapField(
            field_name="bone_metastases",
            field_type="radio",
            select_choices_or_calculations="1, Si | 0, No",
            branching_logic="[presence_of_distant_metastases] = '1'"
        ),
        REDCapField(
            field_name="histopathological_diagnosis",
            field_type="radio",
            select_choices_or_calculations="0, Benigno | 2, Invasivo"
        ),
        REDCapField(
            field_name="benign_type",
            field_type="text",
            branching_logic="[histopathological_diagnosis] = '0'"
        )
    ]

    # Case 1: Extracted data contains values for fields whose parent conditions are FALSE
    raw_extracted = {
        "record_id": "100",
        "genetic_assessment": "9",
        "brca1": "9",  # Should be erased because genetic_assessment != '1'
        "presence_of_distant_metastases": "0",
        "bone_metastases": "0",  # Should be erased because presence_of_distant_metastases != '1'
        "histopathological_diagnosis": "2",
        "benign_type": "Fibroadenoma"  # Should be erased because diagnosis != '0'
    }

    norm = REDCapNormalizer.normalize_record(raw_extracted, fields, record_id="100")

    assert norm["genetic_assessment"] == "9"
    assert norm["brca1"] == "", "brca1 must be blanked because genetic_assessment is 9"
    assert norm["presence_of_distant_metastases"] == "0"
    assert norm["bone_metastases"] == "", "bone_metastases must be blanked because presence_of_distant_metastases is 0"
    assert norm["histopathological_diagnosis"] == "2"
    assert norm["benign_type"] == "", "benign_type must be blanked because diagnosis is 2"

    # Case 2: Parent conditions are TRUE -> values must be preserved
    raw_valid = {
        "record_id": "101",
        "genetic_assessment": "1",
        "brca1": "1",
        "presence_of_distant_metastases": "1",
        "bone_metastases": "1",
        "histopathological_diagnosis": "0",
        "benign_type": "Fibroadenoma"
    }

    norm_valid = REDCapNormalizer.normalize_record(raw_valid, fields, record_id="101")
    assert norm_valid["genetic_assessment"] == "1"
    assert norm_valid["brca1"] == "1"
    assert norm_valid["presence_of_distant_metastases"] == "1"
    assert norm_valid["bone_metastases"] == "1"
    assert norm_valid["histopathological_diagnosis"] == "0"
    assert norm_valid["benign_type"] == "Fibroadenoma"





