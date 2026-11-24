"""Regression tests verifying resolution of critical issues from Code Review Audit (November 2026).
"""
from datetime import date
import io
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.normalizer import REDCapNormalizer
from src.pdf_extractor import PDFExtractor
from src.form_classifier import FormClassifier
from src.dotnet_client import DotNetApiClient
from src.redcap_service import REDCapService
from src.patient_pipeline import PatientPipeline
from src.config import Settings
from src.api_server import app


# ==============================================================================
# 1. Normalizer: Branching Logic Numeric Comparison & Safety Fallbacks
# ==============================================================================

def test_branching_logic_numeric_comparison():
    """Verify that numeric inequalities compare numerically rather than lexically.
    Previously, '100' > '50' evaluated to False because of lexical string comparison.
    """
    rule = "[tumor_size] > 50"
    # 100 > 50 must be True
    assert REDCapNormalizer.evaluate_branching_logic(rule, {"tumor_size": "100"}) is True
    # 25 > 50 must be False
    assert REDCapNormalizer.evaluate_branching_logic(rule, {"tumor_size": "25"}) is False
    # Empty value in inequality must evaluate to False (not satisfied)
    assert REDCapNormalizer.evaluate_branching_logic(rule, {"tumor_size": ""}) is False

    # Compound rule
    compound_rule = "[ki67] >= 20 and [recidiva] = 1"
    assert REDCapNormalizer.evaluate_branching_logic(compound_rule, {"ki67": "25", "recidiva": "1"}) is True
    assert REDCapNormalizer.evaluate_branching_logic(compound_rule, {"ki67": "15", "recidiva": "1"}) is False


def test_normalize_choice_fallback_to_empty():
    """Verify that unrecognized choices return empty string to prevent REDCap API batch rejection."""
    choices = {"1": "Destro", "2": "Sinistro", "3": "Bilaterale"}
    # Known choices match properly
    assert REDCapNormalizer._normalize_choice("Destro", choices) == "1"
    assert REDCapNormalizer._normalize_choice("2", choices) == "2"
    assert REDCapNormalizer._normalize_choice("seno sinistro", choices) == "2"

    # Unknown value returns "" instead of raw text
    assert REDCapNormalizer._normalize_choice("Valore Non Riconosciuto", choices) == ""
    assert REDCapNormalizer._normalize_choice("Centrale", choices) == ""


def test_normalize_boolean_undetermined():
    """Verify that ambiguous or undetermined values return empty string, not '0'."""
    assert REDCapNormalizer._normalize_boolean("si") == "1"
    assert REDCapNormalizer._normalize_boolean("positivo") == "1"
    assert REDCapNormalizer._normalize_boolean(True) == "1"

    assert REDCapNormalizer._normalize_boolean("no") == "0"
    assert REDCapNormalizer._normalize_boolean("assente") == "0"
    assert REDCapNormalizer._normalize_boolean(False) == "0"

    # Ambiguous findings must NOT be mapped to '0' (which means absent/negative)
    assert REDCapNormalizer._normalize_boolean("non valutabile") == ""
    assert REDCapNormalizer._normalize_boolean("n.d.") == ""
    assert REDCapNormalizer._normalize_boolean("dubbio") == ""
    assert REDCapNormalizer._normalize_boolean("sconosciuto") == ""
    assert REDCapNormalizer._normalize_boolean(None) == ""
    assert REDCapNormalizer._normalize_boolean("") == ""


# ==============================================================================
# 2. Form Classifier: Biopsy vs Post-Op Histopathology Word Boundary
# ==============================================================================

def test_classifier_agobioptici_not_matched_as_post_op():
    """Verify that 'agobioptici' does not trigger 'pt' substring matching for post-op pathology."""
    text = (
        "ISTITUTO ONCOLOGICO - Servizio di Anatomia Patologica\n"
        "REFERTO ISTOLOGICO DI BIOPSIA MAMMARIA\n"
        "Materiale inviato: Prelievi agobioptici multipli mammella destra.\n"
        "Diagnosi: B5b - Carcinoma duttale infiltrante."
    )
    res = FormClassifier.classify_report(text=text)
    # Must be pre-operative biopsy, NOT post-operative histopathology
    assert res == "anatomia_patologica_preoperatoria"


# ==============================================================================
# 3. PDF Extractor: Date Prioritization and Multi-Year Support
# ==============================================================================

def test_extract_document_date_prioritizes_explicit_header():
    """Verify that explicit clinical headers take priority over incidental preposition dates."""
    text = (
        "Relazione clinica pregressa del 01/01/2015.\n"
        "Data Referto: 14/10/2026\n"
        "Firma del medico."
    )
    extracted = PDFExtractor.extract_document_date("sample.pdf", text=text)
    assert extracted == "2026-10-14"


def test_extract_document_date_supports_prior_years():
    """Verify that dates prior to 2020 are correctly extracted and not ignored."""
    text = "Esame istologico eseguito in data 15/04/2018."
    extracted = PDFExtractor.extract_document_date("sample.pdf", text=text)
    assert extracted == "2018-04-15"


# ==============================================================================
# 4. DotNet & Local Mock: Token Boundary Matching
# ==============================================================================

def test_dotnet_local_mock_token_boundaries(tmp_path):
    """Verify that candidate ID matching in data/input_pdf respects token boundaries."""
    input_dir = tmp_path / "data" / "input_pdf"
    input_dir.mkdir(parents=True)

    f1 = input_dir / "referto_01_istologia.pdf"
    f2 = input_dir / "SENO9999990.pdf"
    f3 = input_dir / "paziente_999999_controllo.pdf"
    for f in (f1, f2, f3):
        f.write_bytes(b"%PDF-test")

    client = DotNetApiClient(settings=Settings())

    with patch("src.dotnet_client.Path") as mock_path_cls:
        def side_effect(arg):
            if arg == "data/input_pdf":
                return input_dir
            return Path(arg)

        mock_path_cls.side_effect = side_effect

        matched = client._get_local_documents_mock("999999")
        # Only 'paziente_999999_controllo.pdf' should match, NOT 'referto_01' or 'SENO9999990'
        matched_names = [p.name for p in matched]
        assert "paziente_999999_controllo.pdf" in matched_names
        assert "referto_01_istologia.pdf" not in matched_names
        assert "SENO9999990.pdf" not in matched_names


# ==============================================================================
# 5. REDCap Service: Strict Demographics and Safe Record Generation
# ==============================================================================

def test_find_record_id_strict_dob():
    """Verify that demographic matching strictly requires non-empty matching date of birth."""
    service = REDCapService(api_url="http://fake.api", api_token="faketoken")
    fake_records = [
        {"record_id": "101", "name": "Maria", "surname": "Rossi", "date_of_birth": "1965-03-20"},
        {"record_id": "102", "name": "Anna", "surname": "Verdi", "date_of_birth": "1972-11-05"},
    ]

    with patch.object(service, "export_records_from_api", return_value=fake_records):
        # Matching demographics with correct DOB finds record
        assert service.find_record_id_by_demographics("Maria", "Rossi", "1965-03-20") == "101"

        # Same name/surname with DIFFERENT DOB must NOT match
        assert service.find_record_id_by_demographics("Maria", "Rossi", "1980-01-01") is None

        # Missing date of birth must NOT match any record
        assert service.find_record_id_by_demographics("Maria", "Rossi", None) is None
        assert service.find_record_id_by_demographics("Maria", "Rossi", "") is None


def test_generate_next_record_name_fails_safely():
    """Verify that if API and ID retrieval fail, RuntimeError is raised instead of returning '1'."""
    service = REDCapService(api_url="http://fake.api", api_token="faketoken")

    with patch("requests.post", side_effect=Exception("Network error")):
        with patch.object(service, "get_all_patient_ids", side_effect=Exception("Database error")):
            with pytest.raises(RuntimeError, match="Impossibile generare record_id REDCap"):
                service.generate_next_record_name()


# ==============================================================================
# 6. Pipeline: Path Traversal Sanitization & Legitimate '0' Value Retention
# ==============================================================================

def test_pipeline_patient_id_path_traversal_sanitized(tmp_path):
    """Verify that PatientPipeline.process_patient sanitizes traversal sequences on disk paths."""
    out_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    pipe = PatientPipeline(settings=Settings(output_dir=out_dir, state_dir=state_dir))
    res = pipe.process_patient(patient_id="../../etc/passwd", pdf_paths=[], auto_upload=False)
    assert ".." not in res.patient_id
    assert "/" not in res.patient_id
    assert res.patient_id == "passwd"
    assert (out_dir / "patients" / "patient_passwd_result.json").exists()
    assert (state_dir / "passwd_state.json").exists()


def test_pipeline_retains_zero_clinical_value(tmp_path):
    """Verify that extracted clinical fields with value '0' are preserved and uploaded via PatientPipeline."""
    from src.models import ExtractionResult, REDCapField

    pdf_file = tmp_path / "mammografia.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")

    mock_redcap = MagicMock(spec=REDCapService)
    mock_redcap.api_url = "https://mock.redcap/api/"
    mock_redcap.api_token = "TOKEN"
    mock_redcap.find_record_id_by_demographics.return_value = "123"
    mock_redcap.get_record.return_value = None
    mock_redcap.import_records_to_api.return_value = {"count": 1}

    mock_ai = MagicMock()
    mock_ai.extract_from_text.return_value = ExtractionResult(
        pdf_path=str(pdf_file),
        record_id="123",
        extracted_data={"metastasi": "0", "dimensione_mm": "15"},
        redcap_record={
            "record_id": "123",
            "metastasi": "0",
            "dimensione_mm": "15",
            "radiologia_complete": "1",
        },
        status="success",
    )

    pipe = PatientPipeline(
        settings=Settings(output_dir=tmp_path / "out", state_dir=tmp_path / "state"),
        redcap_service=mock_redcap,
        ai_extractor=mock_ai,
    )
    pipe._cached_fields_by_form["radiologia"] = [
        REDCapField(field_name="record_id", form_name="dati_del_paziente", field_type="text"),
        REDCapField(field_name="metastasi", form_name="radiologia", field_type="yesno"),
        REDCapField(field_name="dimensione_mm", form_name="radiologia", field_type="text"),
    ]

    with patch("src.patient_pipeline.PDFExtractor.extract_text", return_value="Mammografia negativa BI-RADS 1"):
        with patch("src.patient_pipeline.FormClassifier.classify_report_targets", return_value=["radiologia"]):
            res = pipe.process_patient(
                patient_id="123",
                name="Anna",
                surname="Verdi",
                date_of_birth="1970-01-01",
                pdf_paths=[pdf_file],
                auto_upload=True,
            )

    assert res.redcap_upload_status == "uploaded"
    uploaded = mock_redcap.import_records_to_api.call_args[0][0][0]
    assert uploaded["metastasi"] == "0"
    assert uploaded["dimensione_mm"] == "15"
    assert uploaded["dati_del_paziente_complete"] == "1"


# ==============================================================================
# 7. API Security: Health Privacy & SSE Endpoint Authentication
# ==============================================================================

def test_cup_export_xlsx_openpyxl(tmp_path):
    """Verify that CUP export format='xlsx' uses openpyxl engine successfully without calamine error."""
    from scripts.extract_patients_from_shared_file import Paziente

    dummy_patient = Paziente(
        progressivo=1,
        cognome="Rossi",
        nome="Maria",
        data_nascita="1970-01-01",
        codice_fiscale="RSSMRA70A41C933A",
        cartella="12345",
        telefono="031000000"
    )
    client = TestClient(app)
    with patch("src.routers.cup._find_target_cup_file", return_value=tmp_path / "cup.xlsx"):
        with patch("src.routers.cup.estrai_pazienti_univoci", return_value=[dummy_patient]):
            resp = client.get("/api/cup/export?format=xlsx")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            # Verify Excel file signature (PK zip header for openpyxl .xlsx)
            assert resp.content.startswith(b"PK")


def test_sync_async_background_tasks_endpoint():
    """Verify that POST /api/sync/check-updates-async accepts BackgroundTasks without FastAPI 422 error."""
    client = TestClient(app)
    # Mock _run_background_sync to avoid running full heavy sync in unit test
    with patch("src.routers.sync._run_background_sync") as mock_bg:
        resp = client.post("/api/sync/check-updates-async", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"


def test_health_endpoint_does_not_leak_internal_urls():
    """Verify that /health does not expose internal hostnames/URLs to unauthenticated clients."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "llama_cpp_base_url" not in data
    assert "priamo_cup_url" not in data
    assert "status" in data


def test_sse_events_endpoint_requires_auth():
    """Verify that /api/tasks/{task_id}/events is protected with API key authentication."""
    from src.config import get_settings
    from src.routers.pipeline import _tasks_store

    _tasks_store["fake-task-1"] = {"task_id": "fake-task-1", "status": "completed"}
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="secret-token-123")
    try:
        client = TestClient(app)

        # Unauthenticated request
        resp = client.get("/api/tasks/fake-task-1/events")
        assert resp.status_code == 401

        # Authenticated request
        resp_auth = client.get(
            "/api/tasks/fake-task-1/events",
            headers={"X-API-Key": "secret-token-123"}
        )
        assert resp_auth.status_code == 200
    finally:
        app.dependency_overrides.clear()
        _tasks_store.pop("fake-task-1", None)
