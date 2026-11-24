from datetime import date
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi import BackgroundTasks

from src.ai_extractor import BaseAIExtractor
from src.api_server import (
    SyncTriggerRequest,
    _tasks_store,
    get_incomplete_patients,
    sync_check_updates_async,
    sync_check_updates_sync
)
from src.config import Settings
from src.models import ExtractionResult
from src.redcap_service import REDCapService
from src.sync_service import (
    PatientSyncResult,
    RedCapSyncService,
)


@pytest.fixture
def mock_settings(tmp_path):
    return Settings(
        redcap_api_url="https://mock-redcap.org/api/",
        redcap_api_token="MOCK_TOKEN_123",
        downloads_dir=tmp_path / "downloads",
        state_dir=tmp_path / "state",
        sync_audit_dir=tmp_path / "sync_audit",
        output_dir=tmp_path / "output",
        redcap_cache_file=Path("examples/redcap_metadata_senonetwork.json")
    )


@pytest.fixture
def mock_redcap_service():
    service = REDCapService(api_url="https://mock-redcap.org/api/", api_token="MOCK_TOKEN_123")
    return service


def test_redcap_service_export_records_and_helpers(mock_redcap_service):
    """Verifies that REDCapService.export_records_from_api constructs the correct POST payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "record_id": "PZ-001",
            "name": "Maria",
            "surname": "Rossi",
            "date_of_birth": "1965-04-12",
            "anatomia_patologica_complete": "0"
        },
        {
            "record_id": "PZ-002",
            "name": "Laura",
            "surname": "Bianchi",
            "date_of_birth": "1970-11-23",
            "anatomia_patologica_complete": "2"
        }
    ]

    with patch("requests.post", return_value=mock_response) as mock_post:
        records = mock_redcap_service.export_records_from_api(
            records=["PZ-001", "PZ-002"],
            fields=["record_id", "name", "surname", "anatomia_patologica_complete"],
            filter_logic="[dati_del_paziente_complete] = '2'"
        )

        assert len(records) == 2
        assert records[0]["record_id"] == "PZ-001"

        called_data = mock_post.call_args[1]["data"]
        assert called_data["token"] == "MOCK_TOKEN_123"
        assert called_data["content"] == "record"
        assert called_data["format"] == "json"
        assert called_data["type"] == "flat"
        assert called_data["records[0]"] == "PZ-001"
        assert called_data["records[1]"] == "PZ-002"
        assert called_data["fields[0]"] == "record_id"
        assert called_data["filterLogic"] == "[dati_del_paziente_complete] = '2'"

    # Test get_record helper
    with patch.object(mock_redcap_service, "export_records_from_api", return_value=[{"record_id": "PZ-001"}]):
        rec = mock_redcap_service.get_record("PZ-001")
        assert rec is not None
        assert rec["record_id"] == "PZ-001"

    # Test get_all_patient_ids
    with patch.object(mock_redcap_service, "export_records_from_api", return_value=[{"record_id": "PZ-001"}, {"record_id": "PZ-002"}]):
        ids = mock_redcap_service.get_all_patient_ids()
        assert ids == ["PZ-001", "PZ-002"]


def test_redcap_find_record_id_by_demographics(mock_redcap_service):
    """Verifies that REDCapService.find_record_id_by_demographics preserves the existing REDCap record_id."""
    mock_records = [
        {"record_id": "105", "name": "Maria", "surname": "Rossi", "date_of_birth": "1965-04-12"},
        {"record_id": "208", "name": "Laura", "surname": "Bianchi", "date_of_birth": "1970-11-23"}
    ]

    with patch.object(mock_redcap_service, "export_records_from_api", return_value=mock_records):
        # Match exact
        matched_id = mock_redcap_service.find_record_id_by_demographics(
            name="Maria",
            surname="Rossi",
            date_of_birth=date(1965, 4, 12)
        )
        assert matched_id == "105"

        # Match case-insensitive
        matched_case = mock_redcap_service.find_record_id_by_demographics(
            name="laura",
            surname="BIANCHI",
            date_of_birth="23/11/1970"
        )
        assert matched_case == "208"

        # No match
        no_match = mock_redcap_service.find_record_id_by_demographics(
            name="Giulia",
            surname="Verdi",
            date_of_birth="1980-01-01"
        )
        assert no_match is None


def test_patient_pipeline_preserves_redcap_record_id(mock_settings, tmp_path):
    """Verifies that PatientPipeline preserves existing REDCap record_id instead of generating a new slug."""
    from src.patient_pipeline import PatientPipeline

    mock_redcap_svc = MagicMock(spec=REDCapService)
    mock_redcap_svc.api_url = "https://mock-redcap.org/api/"
    mock_redcap_svc.api_token = "MOCK_TOKEN"
    # Patient already exists in REDCap with ID '42'
    mock_redcap_svc.find_record_id_by_demographics.return_value = "42"
    mock_redcap_svc.import_records_to_api.return_value = {"count": 1}

    mock_dotnet = MagicMock()
    mock_dotnet.download_all_patient_pdfs.return_value = []

    mock_ai = MagicMock(spec=BaseAIExtractor)

    pipeline = PatientPipeline(
        settings=mock_settings,
        redcap_service=mock_redcap_svc,
        dotnet_client=mock_dotnet,
        ai_extractor=mock_ai
    )

    # Process called with temporary slug
    res = pipeline.process_patient(
        patient_id="maria-rossi-1965-04-12",
        name="Maria",
        surname="Rossi",
        date_of_birth=date(1965, 4, 12),
        auto_upload=True
    )

    # Must preserve REDCap's native '42'
    assert res.patient_id == "42"
    assert mock_redcap_svc.import_records_to_api.called
    imported_records = mock_redcap_svc.import_records_to_api.call_args[0][0]
    assert any(r.get("record_id") == "42" for r in imported_records)
    assert not any(r.get("record_id") == "maria-rossi-1965-04-12" for r in imported_records)


def test_sync_service_detect_incomplete_forms(mock_settings, mock_redcap_service):
    """Verifies that detect_incomplete_forms identifies forms with missing/incomplete status."""
    sync_svc = RedCapSyncService(settings=mock_settings, redcap_service=mock_redcap_service)

    record_partially_complete = {
        "record_id": "PZ-10",
        "dati_del_paziente_complete": "2",
        "radiologia_complete": "2",
        "anatomia_patologica_preoperatoria_complete": "2",
        "struttura_della_lesione_complete": "0",
        "anatomia_patologica_complete": "0",  # Post-operative histology not complete!
        "chemioterapia_complete": "",
    }

    incomplete = sync_svc.detect_incomplete_forms(
        record_partially_complete,
        candidate_forms=["anatomia_patologica", "radiologia", "struttura_della_lesione"]
    )
    assert "anatomia_patologica" in incomplete
    assert "struttura_della_lesione" in incomplete
    assert "radiologia" not in incomplete


def test_sync_service_get_patients_from_redcap_filtering(mock_settings, mock_redcap_service):
    """Verifies filtering of patients on REDCap needing updates for specific forms."""
    sync_svc = RedCapSyncService(settings=mock_settings, redcap_service=mock_redcap_service)

    mock_records = [
        {
            "record_id": "PZ-A",
            "name": "Anna",
            "surname": "Verdi",
            "anatomia_patologica_complete": "0"  # Incomplete
        },
        {
            "record_id": "PZ-B",
            "name": "Beatrice",
            "surname": "Neri",
            "anatomia_patologica_complete": "2"  # Complete
        }
    ]

    with patch.object(mock_redcap_service, "export_records_from_api", return_value=mock_records):
        # Filter specifically for anatomia_patologica
        filtered = sync_svc.get_patients_from_redcap(filter_incomplete_forms=["anatomia_patologica"])
        assert len(filtered) == 1
        assert filtered[0]["record_id"] == "PZ-A"


def test_sync_patient_post_operative_histology_update(tmp_path, mock_settings):
    """Simulates the core clinical requirement:
    A patient already inserted in REDCap undergoes surgery later.
    A new post-operative histology report appears.
    RedCapSyncService identifies the report, extracts data, diffs, and updates REDCap.
    """
    patient_id = "giulia-rossi-1972-03-15"

    # 1. Mock existing REDCap state: patient was registered earlier, biopsy was done, but NO surgery/post-op yet
    existing_redcap_record = {
        "record_id": patient_id,
        "name": "Giulia",
        "surname": "Rossi",
        "date_of_birth": "1972-03-15",
        "dati_del_paziente_complete": "2",
        "radiologia_complete": "2",
        "anatomia_patologica_preoperatoria_complete": "2",
        "anatomia_patologica_complete": "0",  # Post-op histology missing
        "invasive_histological_type": "",
        "grade": "",
        "pt": "",
        "pn": ""
    }

    mock_redcap_svc = MagicMock(spec=REDCapService)
    mock_redcap_svc.get_record.return_value = existing_redcap_record
    mock_redcap_svc.import_records_to_api.return_value = {"count": 1}

    # 2. Create mock PDF report for post-op histology
    patient_dir = mock_settings.downloads_dir / patient_id
    patient_dir.mkdir(parents=True, exist_ok=True)
    postop_pdf = patient_dir / "2026_ESAME_ISTOLOGICO_DEFINITIVO_QUADRANTECTOMIA.pdf"
    postop_pdf.write_bytes(b"%PDF-1.4 Mock Post-Operative Histology Report")

    # 3. Mock DotNet client returning the new PDF
    mock_dotnet = MagicMock()
    mock_dotnet.download_all_patient_pdfs.return_value = [postop_pdf]

    # 4. Mock AI extractor returning extracted fields for anatomia_patologica
    mock_ai = MagicMock(spec=BaseAIExtractor)
    mock_extraction_res = ExtractionResult(
        pdf_path=str(postop_pdf),
        record_id=patient_id,
        raw_text="Carcinoma duttale invasivo NST, grado 2, pT1c pN0",
        extracted_data={
            "invasive_histological_type": "1",  # 1 = Duttale NAS
            "grade": "2",  # Grado 2
            "pt": "1c",
            "pn": "0"
        },
        redcap_record={
            "record_id": patient_id,
            "invasive_histological_type": "1",
            "grade": "2",
            "pt": "1c",
            "pn": "0",
            "anatomia_patologica_complete": "2"
        },
        status="success"
    )
    mock_ai.extract_from_text.return_value = mock_extraction_res

    sync_svc = RedCapSyncService(
        settings=mock_settings,
        redcap_service=mock_redcap_svc,
        dotnet_client=mock_dotnet,
        ai_extractor=mock_ai
    )

    # Mock PDFExtractor and FormClassifier
    with patch("src.sync_service.PDFExtractor.extract_text", return_value="Carcinoma duttale infiltrante pezzo operatorio"):
        with patch("src.sync_service.FormClassifier.classify_report_targets", return_value=["anatomia_patologica"]):
            # Execute sync
            result = sync_svc.check_and_update_patient(
                patient_id=patient_id,
                target_forms=["anatomia_patologica"],
                auto_upload=True,
                dry_run=False
            )

    # 5. Assertions
    assert result.status == "updated"
    assert "anatomia_patologica" in result.forms_updated
    assert result.field_updates_count == 4  # invasive_histological_type, grade, pt, pn

    # Verify REDCap import was called with the updated fields
    assert mock_redcap_svc.import_records_to_api.called
    imported_payload = mock_redcap_svc.import_records_to_api.call_args[0][0][0]
    assert imported_payload["record_id"] == patient_id
    assert imported_payload["invasive_histological_type"] == "1"
    assert imported_payload["grade"] == "2"
    assert imported_payload["pt"] == "1c"
    assert imported_payload["pn"] == "0"
    assert imported_payload["anatomia_patologica_complete"] == "1"

    # Verify state file was created and contains the updated form
    state_file = mock_settings.state_dir / f"{patient_id}_state.json"
    assert state_file.exists()
    state_content = json.loads(state_file.read_text(encoding="utf-8"))
    assert "anatomia_patologica" in state_content["forms_populated"]
    assert len(state_content["files"]) >= 1


def test_sync_patient_dry_run_no_redcap_upload(tmp_path, mock_settings):
    """Verifies that in dry-run mode, changes are computed and reported but NOT uploaded to REDCap."""
    patient_id = "test-dryrun-patient"
    existing_record = {
        "record_id": patient_id,
        "name": "Elena",
        "surname": "Santi",
        "anatomia_patologica_complete": "0",
        "invasive_histological_type": ""
    }

    mock_redcap_svc = MagicMock(spec=REDCapService)
    mock_redcap_svc.get_record.return_value = existing_record

    pdf_file = tmp_path / "histology.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 Mock")

    mock_dotnet = MagicMock()
    mock_dotnet.download_all_patient_pdfs.return_value = [pdf_file]

    mock_ai = MagicMock(spec=BaseAIExtractor)
    mock_ai.extract_from_text.return_value = ExtractionResult(
        pdf_path=str(pdf_file),
        record_id=patient_id,
        extracted_data={"invasive_histological_type": "2"},
        redcap_record={"record_id": patient_id, "invasive_histological_type": "2", "anatomia_patologica_complete": "2"},
        status="success"
    )

    sync_svc = RedCapSyncService(
        settings=mock_settings,
        redcap_service=mock_redcap_svc,
        dotnet_client=mock_dotnet,
        ai_extractor=mock_ai
    )

    with patch("src.sync_service.PDFExtractor.extract_text", return_value="Pezzo operatorio"):
        with patch("src.sync_service.FormClassifier.classify_report_targets", return_value=["anatomia_patologica"]):
            res = sync_svc.check_and_update_patient(
                patient_id=patient_id,
                target_forms=["anatomia_patologica"],
                auto_upload=True,
                dry_run=True  # Dry run!
            )

    assert res.status == "updated"
    assert res.field_updates_count > 0
    # Must NOT call import_records_to_api
    assert not mock_redcap_svc.import_records_to_api.called


def test_sync_fill_missing_preserves_existing_values_and_deduplicates_state(tmp_path, mock_settings):
    """Ensures fill_missing mode never overwrites populated fields and state file deduplicates by sha256."""
    patient_id = "test-fill-missing"
    existing_record = {
        "record_id": patient_id,
        "name": "Elena",
        "surname": "Santi",
        "anatomia_patologica_complete": "1",
        "grade": "1",  # Already populated -> must NOT be overwritten in fill_missing mode
        "pt": "",      # Empty -> should be enriched
    }

    mock_redcap_svc = MagicMock(spec=REDCapService)
    mock_redcap_svc.get_record.return_value = existing_record

    pdf_file = tmp_path / "histology.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 Mock Identical")

    mock_dotnet = MagicMock()
    mock_dotnet.download_all_patient_pdfs.return_value = [pdf_file]

    mock_ai = MagicMock(spec=BaseAIExtractor)
    mock_ai.extract_from_text.return_value = ExtractionResult(
        pdf_path=str(pdf_file),
        record_id=patient_id,
        extracted_data={"grade": "3", "pt": "2"},
        redcap_record={"record_id": patient_id, "grade": "3", "pt": "2", "anatomia_patologica_complete": "1"},
        status="success"
    )

    sync_svc = RedCapSyncService(
        settings=mock_settings,
        redcap_service=mock_redcap_svc,
        dotnet_client=mock_dotnet,
        ai_extractor=mock_ai
    )

    with patch("src.sync_service.PDFExtractor.extract_text", return_value="Pezzo operatorio"):
        with patch("src.sync_service.FormClassifier.classify_report_targets", return_value=["anatomia_patologica"]):
            res = sync_svc.check_and_update_patient(
                patient_id=patient_id,
                target_forms=["anatomia_patologica"],
                auto_upload=True,
                dry_run=False,
                overwrite_mode="fill_missing",
            )
            # Run a second time with force_reprocess=True -> state files list must still deduplicate by sha256
            sync_svc.check_and_update_patient(
                patient_id=patient_id,
                target_forms=["anatomia_patologica"],
                auto_upload=False,
                dry_run=False,
                force_reprocess=True,
                overwrite_mode="fill_missing",
            )

    imported_payload = mock_redcap_svc.import_records_to_api.call_args[0][0][0]
    assert "grade" not in imported_payload
    assert imported_payload["pt"] == "2"
    assert res.field_updates_count >= 1
    state_content = json.loads((mock_settings.state_dir / f"{patient_id}_state.json").read_text(encoding="utf-8"))
    assert len(state_content["files"]) == 1


def test_sync_all_summary_report(tmp_path, mock_settings):
    """Tests sync_all orchestrating multiple patients and saving the audit report."""
    mock_redcap_svc = MagicMock(spec=REDCapService)
    sync_svc = RedCapSyncService(settings=mock_settings, redcap_service=mock_redcap_svc)

    with patch.object(sync_svc, "get_patients_from_redcap", return_value=[{"record_id": "PZ-1"}, {"record_id": "PZ-2"}]):
        with patch.object(
            sync_svc,
            "check_and_update_patient",
            side_effect=[
                PatientSyncResult(
                    patient_id="PZ-1",
                    status="updated",
                    forms_updated=["anatomia_patologica"],
                    field_updates_count=3
                ),
                PatientSyncResult(
                    patient_id="PZ-2",
                    status="up_to_date",
                    forms_updated=[],
                    field_updates_count=0
                )
            ]
        ):
            report = sync_svc.sync_all(target_forms=["anatomia_patologica"])
            assert report.patients_inspected == 2
            assert report.patients_updated == 1
            assert report.total_fields_updated == 3
            assert "anatomia_patologica" in report.forms_affected

            # Verify report file was saved
            saved_reports = list(mock_settings.sync_audit_dir.glob("sync_report_*.json"))
            assert len(saved_reports) == 1


def test_api_server_sync_endpoints():
    """Tests the new FastAPI sync endpoint handler functions directly."""
    mock_sync_svc = MagicMock(spec=RedCapSyncService)

    # 1. Test get_incomplete_patients handler
    mock_sync_svc.get_patients_from_redcap.return_value = [{"record_id": "PZ-TEST"}]
    res = get_incomplete_patients(form="anatomia_patologica", sync_service=mock_sync_svc)
    assert len(res) == 1
    assert res[0]["record_id"] == "PZ-TEST"
    mock_sync_svc.get_patients_from_redcap.assert_called_with(filter_incomplete_forms=["anatomia_patologica"])

    # 2. Test sync_check_updates_sync handler (single patient)
    mock_sync_res = PatientSyncResult(
        patient_id="PZ-TEST",
        status="updated",
        forms_updated=["anatomia_patologica"],
        field_updates_count=2
    )
    mock_sync_svc.check_and_update_patient.return_value = mock_sync_res
    req = SyncTriggerRequest(patient_id="PZ-TEST", target_forms=["anatomia_patologica"], dry_run=False)
    res_sync = sync_check_updates_sync(req=req, sync_service=mock_sync_svc)
    assert res_sync.patient_id == "PZ-TEST"
    assert res_sync.status == "updated"
    assert res_sync.field_updates_count == 2

    # 3. Test sync_check_updates_async handler
    bg = BackgroundTasks()
    res_async = sync_check_updates_async(background_tasks=bg, req=req, sync_service=mock_sync_svc)
    assert res_async.patient_id == "PZ-TEST"
    assert res_async.status == "queued"
    assert res_async.task_id in _tasks_store
