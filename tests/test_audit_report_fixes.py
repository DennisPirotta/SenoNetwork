"""Comprehensive regression tests verifying all fixes from the codebase audit report."""

from datetime import date
import json
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from src.ai_extractor import parse_json_response
from src.api_server import app
from src.config import Settings, get_settings
from src.form_classifier import AVAILABLE_FORMS, FormClassifier
from src.models import ExtractionResult, REDCapField
from src.normalizer import REDCapNormalizer
from src.patient_pipeline import PatientPipeline, compute_file_hash, is_safe_webhook_url
from src.pdf_extractor import PDFExtractor
from src.priamo_cup_client import PriamoCupClient
from src.redcap_service import REDCapService, _PROJECT_CF_LOCK, _PROJECT_SUPPORTS_CF
from src.routers.pipeline import (
    STALE_ACTIVE_TTL_SECONDS,
    _cleanup_old_tasks_locked,
    _tasks_lock,
    _tasks_store,
)
from src.sync_service import RedCapSyncService
from src.utils import (
    atomic_write_bytes,
    atomic_write_text,
    build_patient_slug,
    llm_slot,
    sanitize_csv_cell,
    sanitize_identifier,
)


def _sync_settings(tmp_path) -> Settings:
    return Settings(
        redcap_api_url="https://mock-redcap.org/api/",
        redcap_api_token="MOCK_TOKEN_AUDIT",
        downloads_dir=tmp_path / "downloads",
        state_dir=tmp_path / "state",
        sync_audit_dir=tmp_path / "sync_audit",
        output_dir=tmp_path / "output",
        cup_dir=tmp_path / "cup",
        redcap_cache_file=tmp_path / "redcap_cache.json",
    )


def test_utils_security_slug_concurrency_and_atomic_io(tmp_path):
    """Verifies CSV formula neutralization, NFKD slugging, reentrant llm_slot, and atomic I/O."""
    assert sanitize_csv_cell("=CMD|'/C calc'!A0") == "'=CMD|'/C calc'!A0"
    assert sanitize_csv_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert sanitize_csv_cell("@HYPERLINK") == "'@HYPERLINK"
    assert sanitize_csv_cell("-1+1") == "'-1+1"
    assert sanitize_csv_cell("-12.5") == "-12.5"
    assert sanitize_csv_cell("Carcinoma duttale") == "Carcinoma duttale"

    assert build_patient_slug("Nicolò", "Salvadè", date(1965, 5, 1)) == "nicolo-salvade-1965-05-01"
    assert sanitize_identifier("../../etc/passwd", max_len=32) == "passwd"

    text_file = tmp_path / "nested" / "out.txt"
    bin_file = tmp_path / "nested" / "out.bin"
    atomic_write_text(text_file, "contenuto clinico")
    atomic_write_bytes(bin_file, b"%PDF-1.4 mock")
    assert text_file.read_text(encoding="utf-8") == "contenuto clinico"
    assert bin_file.read_bytes() == b"%PDF-1.4 mock"

    # Reentrant same-thread acquisition must never deadlock when concurrency limit is 1
    with llm_slot(1):
        with llm_slot(1):
            entered = True
    assert entered is True


def test_normalizer_decimal_branching_and_choice_specificity():
    """Verifies decimal constants in branching logic and longest-match token specificity."""
    # Previously `22.5` was corrupted into `"22".5`, failing AST parsing and returning True blindly
    assert REDCapNormalizer.evaluate_branching_logic("[bmi] = 22.5", {"bmi": "22.5"}) is True
    assert REDCapNormalizer.evaluate_branching_logic("[bmi] = 22.5", {"bmi": "999"}) is False

    choices = {"1": "Carcinoma", "2": "Carcinoma duttale"}
    assert REDCapNormalizer._normalize_choice("Carcinoma duttale invasivo", choices) == "2"


def test_form_classifier_score_vs_core_longest_key_and_phi_redaction():
    """Verifies boundary matching for 'core', longest-first form key resolution, and PHI stripping."""
    # 'score' must not falsely trigger the 'core' needle biopsy rule
    form = FormClassifier.classify_report(
        text="Referto visita chirurgica senologica programmata.",
        hint="score_clinico_senologia.pdf",
    )
    assert form == "struttura_della_lesione"

    # Prefix collision must resolve full form name rather than 'anatomia_patologica'
    assert FormClassifier._match_form_key("anatomia_patologica_preoperatoria") == "anatomia_patologica_preoperatoria"
    assert FormClassifier._match_form_key("skip") == "skip"

    stripped = FormClassifier._strip_phi_headers(
        "Paziente: ROSSI MARIA\n"
        "Codice Fiscale: RSSMRA75H55C933A\n"
        "Referto: Mammografia bilaterale negativa per lesioni focali."
    )
    assert "ROSSI MARIA" not in stripped
    assert "RSSMRA75H55C933A" not in stripped
    assert "Mammografia bilaterale negativa" in stripped


def test_patient_pipeline_header_identity_and_https_webhook():
    """Verifies header-first identity check and HTTPS enforcement on external webhook URLs."""
    # Even if 'Rossi' appears later as a signing physician, mismatched patient header must warn
    report_with_signer = (
        "Paziente: BIANCHI LAURA\n"
        "Esame: Mammografia bilaterale.\n"
        "Referto firmato dal Dott. Rossi\n"
    )
    warn = PatientPipeline._verify_pdf_patient_identity(report_with_signer, name="Maria", surname="Rossi")
    assert warn is not None
    assert "mismatch" in warn.lower()

    assert is_safe_webhook_url("http://external.example.org/hook", allow_local=False) is False
    with patch("src.patient_pipeline.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        assert is_safe_webhook_url("https://external.example.org/hook", allow_local=False) is True
    assert is_safe_webhook_url("http://127.0.0.1:8080/hook", allow_local=True) is True


def test_parse_json_response_extracts_first_balanced_object():
    """Verifies balanced JSON brace extraction ignoring trailing brace commentary."""
    raw = 'Risultato estratto:\n{"birads": "4", "nested": {"k": "v"}} note aggiuntive {non valido}'
    parsed = parse_json_response(raw)
    assert parsed == {"birads": "4", "nested": {"k": "v"}}


def test_priamo_cup_start_date_with_weeks_and_invalid_month(tmp_path):
    """Verifies start_date + weeks combination is honored and invalid months raise ValueError."""
    client = PriamoCupClient(username="u", password="p", verify_ssl=True)
    with patch.object(client, "export_weeks", return_value=[tmp_path / "w1.xlsx"]) as mock_weeks:
        res = client.download_updated_worksheets(start_date="2026-10-01", weeks=2, output_dir=str(tmp_path))
        assert res == tmp_path / "w1.xlsx"
        assert mock_weeks.call_args.kwargs["start_date"] == "2026-10-01"
        assert mock_weeks.call_args.kwargs["num_weeks"] == 2

    with pytest.raises(ValueError, match="Formato mese"):
        client.download_updated_worksheets(month="not-a-month", output_dir=str(tmp_path))

    with pytest.raises(ValueError, match="Numero mese"):
        client.download_updated_worksheets(month="13", output_dir=str(tmp_path))


def test_redcap_merge_flat_records_chronological_and_cf_fallback():
    """Verifies chronological merging using parsed dates and HTTP 400 fallback caching on missing CF field."""
    merged = REDCapService.merge_flat_records([
        {"record_id": "10", "_document_date": "10/01/2026", "birads": "5"},
        {"record_id": "10", "_document_date": "2025-12-01", "birads": "2"},
    ])
    assert len(merged) == 1
    assert merged[0]["birads"] == "5"

    svc = REDCapService(api_url="https://mock-cf-fallback.org/api/", api_token="TOK_CF_TEST")
    proj_key = svc._project_alloc_key()
    with _PROJECT_CF_LOCK:
        _PROJECT_SUPPORTS_CF.pop(proj_key, None)

    import requests as req_lib
    mock_http_err = req_lib.exceptions.HTTPError("400 Bad Request")
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = '{"error": "The field codice_fiscale is not valid"}'
    mock_http_err.response = mock_resp

    expected_rows = [{"record_id": "1", "name": "Maria", "surname": "Rossi", "date_of_birth": "1975-01-01"}]
    try:
        with patch.object(svc, "export_records_from_api", side_effect=[mock_http_err, expected_rows, expected_rows]) as mock_exp:
            rows1 = svc._export_demographic_records()
            rows2 = svc._export_demographic_records()
            assert rows1 == expected_rows
            assert rows2 == expected_rows
            assert mock_exp.call_count == 3
            assert "codice_fiscale" not in mock_exp.call_args_list[-1].kwargs["fields"]
    finally:
        with _PROJECT_CF_LOCK:
            _PROJECT_SUPPORTS_CF.pop(proj_key, None)


def test_sync_all_only_incomplete_defaults_all_forms_and_avoids_redundant_reprocessing(tmp_path):
    """Verifies sync_all incomplete form scoping, unchanged hash skip, error retry, and multi-PDF dedup."""
    settings = _sync_settings(tmp_path)
    redcap_svc = REDCapService(api_url=settings.redcap_api_url, api_token=settings.redcap_api_token)
    sync_svc = RedCapSyncService(settings=settings, redcap_service=redcap_svc)

    with patch.object(sync_svc, "get_patients_from_redcap", return_value=[]) as mock_get:
        sync_svc.sync_all(target_forms=None, only_incomplete=True)
        assert mock_get.call_args.kwargs["filter_incomplete_forms"] == list(AVAILABLE_FORMS.keys())

        sync_svc.sync_all(target_forms=None, only_incomplete=False)
        assert mock_get.call_args.kwargs["filter_incomplete_forms"] is None

    # Create patient PDFs in downloads dir
    dl_dir = settings.downloads_dir / "PZ-SYNC"
    dl_dir.mkdir(parents=True, exist_ok=True)
    pdf_a = dl_dir / "a_mammografia.pdf"
    pdf_b = dl_dir / "b_mammografia.pdf"
    pdf_a.write_bytes(b"%PDF-1.4 Mammografia A")
    pdf_b.write_bytes(b"%PDF-1.4 Mammografia B")

    hash_a = compute_file_hash(pdf_a)
    hash_b = compute_file_hash(pdf_b)

    mock_ai = MagicMock()
    sync_svc.ai_extractor = mock_ai

    existing_record = {
        "record_id": "PZ-SYNC",
        "name": "Maria",
        "surname": "Rossi",
        "date_of_birth": "1975-06-15",
        "radiologia_complete": "0",
    }

    # 1. Pre-existing succeeded state must skip AI extraction without calling extract_from_text
    mock_fields = [REDCapField(field_name="birads", form_name="radiologia", field_type="text")]
    sync_svc.save_patient_state("PZ-SYNC", {
        "redcap_upload_status": "uploaded",
        "files": [
            {"filename": pdf_a.name, "sha256": hash_a, "status": "success"},
            {"filename": pdf_b.name, "sha256": hash_b, "status": "success"},
        ],
    })
    with patch.object(sync_svc, "load_metadata", return_value=mock_fields):
        with patch.object(sync_svc.dotnet_client, "download_all_patient_pdfs", return_value=[pdf_a, pdf_b]):
            res_skip = sync_svc.check_and_update_patient(
                patient_id="PZ-SYNC",
                existing_record_cache=dict(existing_record),
                auto_upload=False,
            )
    assert res_skip.status == "up_to_date"
    assert mock_ai.extract_from_text.call_count == 0

    # 2. Previous upload failure + overwrite across 2 PDFs must reprocess and deduplicate FieldUpdateDetail
    sync_svc.save_patient_state("PZ-SYNC", {
        "redcap_upload_status": "error",
        "files": [
            {"filename": pdf_a.name, "sha256": hash_a, "status": "success"},
            {"filename": pdf_b.name, "sha256": hash_b, "status": "success"},
        ],
    })
    mock_ai.extract_from_text.side_effect = [
        ExtractionResult(pdf_path=str(pdf_a), status="success", redcap_record={"birads": "4"}),
        ExtractionResult(pdf_path=str(pdf_b), status="success", redcap_record={"birads": "5"}),
    ]

    with patch.object(PDFExtractor, "sort_pdfs_with_text", return_value=[
        (pdf_a, "Esame: Mammografia bilaterale 01/01/2026 BI-RADS 4", date(2026, 1, 1)),
        (pdf_b, "Esame: Mammografia bilaterale 02/01/2026 BI-RADS 5", date(2026, 1, 2)),
    ]):
        with patch.object(PDFExtractor, "extract_text") as mock_extra_read:
            with patch.object(sync_svc, "load_metadata", return_value=mock_fields):
                with patch.object(sync_svc.pipeline, "get_fields_for_form", return_value=mock_fields):
                    with patch.object(sync_svc.dotnet_client, "download_all_patient_pdfs", return_value=[pdf_a, pdf_b]):
                        res_upd = sync_svc.check_and_update_patient(
                            patient_id="PZ-SYNC",
                            existing_record_cache=dict(existing_record),
                            target_forms=["radiologia"],
                            auto_upload=False,
                            overwrite_mode="overwrite",
                        )

    assert res_upd.status == "updated"
    assert mock_extra_read.call_count == 0
    assert res_upd.field_updates_count == 1
    assert len(res_upd.field_updates) == 1
    assert res_upd.field_updates[0].field_name == "birads"
    assert res_upd.field_updates[0].new_value == "5"


def test_sync_check_and_update_patient_propagates_doc_error(tmp_path):
    """Verifies AI extraction failures surface with status='error' instead of false up_to_date."""
    settings = _sync_settings(tmp_path)
    sync_svc = RedCapSyncService(settings=settings)
    dl_dir = settings.downloads_dir / "PZ-ERR"
    dl_dir.mkdir(parents=True, exist_ok=True)
    pdf_err = dl_dir / "mammografia_err.pdf"
    pdf_err.write_bytes(b"%PDF-1.4 err")

    mock_ai = MagicMock()
    mock_ai.extract_from_text.return_value = ExtractionResult(
        pdf_path=str(pdf_err), status="error", error_message="LLM timeout"
    )
    sync_svc.ai_extractor = mock_ai
    mock_fields = [REDCapField(field_name="birads", form_name="radiologia", field_type="text")]

    with patch.object(PDFExtractor, "sort_pdfs_with_text", return_value=[
        (pdf_err, "Referto mammografia bilaterale BI-RADS 4", date(2026, 2, 1)),
    ]):
        with patch.object(sync_svc, "load_metadata", return_value=mock_fields):
            with patch.object(sync_svc.pipeline, "get_fields_for_form", return_value=mock_fields):
                with patch.object(sync_svc.dotnet_client, "download_all_patient_pdfs", return_value=[pdf_err]):
                    res = sync_svc.check_and_update_patient(
                        patient_id="PZ-ERR",
                        existing_record_cache={"record_id": "PZ-ERR", "radiologia_complete": "0"},
                        target_forms=["radiologia"],
                        auto_upload=False,
                    )

    assert res.status == "error"
    assert res.error and "LLM timeout" in res.error


def test_security_query_auth_rejected_sse_token_scoped_and_headers_present(tmp_path):
    """Verifies REST endpoints reject ?api_key= query secrets, SSE tokens stay scoped, and security headers exist."""
    cup_dir = tmp_path / "cup"
    cup_dir.mkdir(parents=True, exist_ok=True)
    sec_settings = get_settings().model_copy(update={
        "api_key": "audit_secret_key_value",
        "cup_dir": cup_dir,
        "state_dir": tmp_path / "state",
    })
    app.dependency_overrides[get_settings] = lambda: sec_settings
    client = TestClient(app, raise_server_exceptions=False)
    tid = "audit-sse-delta-task"
    try:
        # Query-string api_key must be rejected on REST endpoints
        r_query = client.get("/api/cup/files?api_key=audit_secret_key_value")
        assert r_query.status_code == 401

        # Security headers present on responses
        assert r_query.headers.get("X-Content-Type-Options") == "nosniff"
        assert r_query.headers.get("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in (r_query.headers.get("Content-Security-Policy") or "")

        # Issue short-lived SSE token with X-API-Key header
        tok_resp = client.post("/api/security/sse-token", headers={"X-API-Key": "audit_secret_key_value"})
        assert tok_resp.status_code == 200
        sse_token = tok_resp.json()["sse_token"]

        # SSE token must NOT authorize regular REST endpoints
        assert client.get(f"/api/cup/files?sse_token={sse_token}").status_code == 401

        # SSE token authorizes incremental event streaming
        with _tasks_lock:
            _tasks_store[tid] = {
                "task_id": tid,
                "status": "completed",
                "progress_pct": 100,
                "current_step": "Done",
                "events_log": [{"time": "10:00:00", "message": "Step 1", "pct": 100}],
                "processed_documents": [],
                "result": {"ok": True},
                "error": None,
            }
        sse_resp = client.get(f"/api/tasks/{tid}/events?sse_token={sse_token}")
        assert sse_resp.status_code == 200
        data_lines = [line for line in sse_resp.text.splitlines() if line.startswith("data: ")]
        assert len(data_lines) == 1
        frame = json.loads(data_lines[0].replace("data: ", ""))
        assert frame["incremental"] is False
        assert len(frame["events_log"]) == 1
    finally:
        with _tasks_lock:
            _tasks_store.pop(tid, None)
        app.dependency_overrides.pop(get_settings, None)


def test_sse_incremental_deltas_and_stale_task_eviction():
    """Verifies SSE stream sends only delta logs/docs across frames and stale tasks get pruned."""
    client = TestClient(app, raise_server_exceptions=False)
    base_settings = get_settings().model_copy(update={"api_key": None})
    app.dependency_overrides[get_settings] = lambda: base_settings

    tid = "audit-delta-stream"
    stale_tid = "audit-stale-stuck"
    try:
        import threading
        with _tasks_lock:
            _tasks_store[stale_tid] = {
                "task_id": stale_tid,
                "status": "processing",
                "created_at": time.time() - STALE_ACTIVE_TTL_SECONDS - 60,
            }
            _cleanup_old_tasks_locked()
            assert stale_tid not in _tasks_store

            _tasks_store[tid] = {
                "task_id": tid,
                "status": "processing",
                "progress_pct": 25,
                "current_step": "Step 1",
                "events_log": [{"time": "10:00:01", "message": "Init", "pct": 25}],
                "processed_documents": [{"filename": "d1.pdf", "status": "success"}],
                "result": None,
                "error": None,
            }

        def _advance_and_finish():
            with _tasks_lock:
                _tasks_store[tid]["events_log"].append({"time": "10:00:02", "message": "Done", "pct": 100})
                _tasks_store[tid]["processed_documents"].append({"filename": "d2.pdf", "status": "success"})
                _tasks_store[tid]["progress_pct"] = 100
                _tasks_store[tid]["status"] = "completed"

        timer = threading.Timer(0.2, _advance_and_finish)
        timer.start()
        resp = client.get(f"/api/tasks/{tid}/events")
        timer.join()

        assert resp.status_code == 200
        frames = [json.loads(l.replace("data: ", "")) for l in resp.text.splitlines() if l.startswith("data: ")]
        assert len(frames) >= 2
        assert frames[0]["incremental"] is False
        assert [e["message"] for e in frames[0]["events_log"]] == ["Init"]
        assert frames[-1]["incremental"] is True
        assert [e["message"] for e in frames[-1]["events_log"]] == ["Done"]
        assert [d["filename"] for d in frames[-1]["processed_documents"]] == ["d2.pdf"]
    finally:
        with _tasks_lock:
            _tasks_store.pop(tid, None)
            _tasks_store.pop(stale_tid, None)
        app.dependency_overrides.pop(get_settings, None)
