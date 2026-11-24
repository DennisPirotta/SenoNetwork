"""Comprehensive Unit & Integration Tests for SenoNetwork FastAPI Backend.

Covers:
- Root redirect and /health check
- CORS middleware & preflight requests
- API Key security (bypass in dev, enforcement, 401 missing, 403 invalid)
- Key generation (/api/security/generate-key and CLI)
- Dashboard overview endpoint
- Priamo CUP endpoints (files, patients pagination, export, download)
- Priamo HIS endpoints (patient verification)
- AI Extraction Pipeline (sync, async, task tracking, SSE)
- REDCap records management & deletion endpoints
- CLI --generate-key
"""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api_server import app, main as api_main
from src.config import get_settings
from src.patient_pipeline import PatientPipelineResult
from src.routers.redcap import get_redcap_service


@pytest.fixture(autouse=True)
def ensure_cup_sample_file(tmp_path):
    """Assicura la presenza di un file Excel CUP sintetico in tmp_path senza toccare data/cup."""
    cup_dir = tmp_path / "cup"
    state_dir = tmp_path / "state"
    cup_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    sample_file = cup_dir / "test_sample_cup.xlsx"
    df = pd.DataFrame([{
        "PAZ_COGNOME": "ROSSI",
        "PAZ_NOME": "MARIA",
        "PAZ_DATA_NASCITA": "1975-06-15",
        "PAZ_CODICE_FISCALE": "RSSMRA75H55C933A",
        "PAZ_SESSO": "F",
        "PAZ_COMUNE_NASCITA": "COMO",
        "PAZ_PROGRESSIVO_PAZ1": "123456",
        "EROG_CODICE": "3004",
        "SLOT_DATA": "2026-11-12",
        "SLOT_ORA": "1130",
        "PREST_DESCRIZIONE": "Medicazione",
    }])
    df.to_excel(sample_file, index=False, engine="openpyxl")

    base_settings = get_settings().model_copy(update={"cup_dir": cup_dir, "state_dir": state_dir})
    app.dependency_overrides[get_settings] = lambda: base_settings
    try:
        yield base_settings
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def client():
    """Returns a test client for FastAPI."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def override_settings(ensure_cup_sample_file):
    """Helper fixture to temporarily override app settings."""
    orig_settings = ensure_cup_sample_file

    def _override(**kwargs):
        new_settings = orig_settings.model_copy(update=kwargs)
        app.dependency_overrides[get_settings] = lambda: new_settings
        return new_settings

    yield _override
    app.dependency_overrides[get_settings] = lambda: orig_settings


# ---------------------------------------------------------------------------
# 1. Health & Root Redirect
# ---------------------------------------------------------------------------

def test_root_serves_dashboard(client):
    """GET / and /dashboard should serve the SenoNetwork Web Dashboard."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "SenoNetwork" in resp.text

    resp_dash = client.get("/dashboard")
    assert resp_dash.status_code == 200
    assert "text/html" in resp_dash.headers["content-type"]
    assert "SenoNetwork" in resp_dash.text


def test_health_check(client):
    """GET /health returns 200 with online status and system components."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "ai_backend_configured" in data
    assert "api_key_required" in data


# ---------------------------------------------------------------------------
# 2. CORS Headers & Preflight
# ---------------------------------------------------------------------------

def test_cors_headers_and_preflight(client):
    """OPTIONS preflight with Origin header should return CORS allow headers."""
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "X-API-Key, Content-Type",
    }
    resp = client.options("/api/dashboard/overview", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in ("*", "http://localhost:5173")
    assert "x-api-key" in resp.headers.get("access-control-allow-headers", "").lower()


# ---------------------------------------------------------------------------
# 3. Security: API Key Enforcement & Dev Mode Bypass
# ---------------------------------------------------------------------------

def test_api_key_security_enforcement(client, override_settings):
    """When api_key is configured:
    - Missing header -> 401 Unauthorized
    - Wrong header -> 403 Forbidden
    - Correct header -> 200 OK
    """
    secret_key = "sn_test_secret_1234567890abcdef"
    override_settings(api_key=secret_key)

    # Missing API key -> 401
    resp_missing = client.get("/api/dashboard/overview")
    assert resp_missing.status_code == 401
    assert "Autenticazione richiesta" in resp_missing.json()["detail"]

    # Invalid API key -> 403
    resp_wrong = client.get("/api/dashboard/overview", headers={"X-API-Key": "wrong_key"})
    assert resp_wrong.status_code == 403
    assert "API Key non valida" in resp_wrong.json()["detail"]

    # Valid API key -> 200
    resp_valid = client.get("/api/dashboard/overview", headers={"X-API-Key": secret_key})
    assert resp_valid.status_code == 200


def test_api_key_security_dev_bypass(client, override_settings):
    """When api_key is None (default development mode), access is permitted without key."""
    override_settings(api_key=None)
    resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200


def test_generate_api_key_endpoint(client, override_settings):
    """POST /api/security/generate-key works in local dev and enforces X-API-Key when configured."""
    override_settings(api_key=None)
    resp = client.post("/api/security/generate-key")
    assert resp.status_code == 200
    data = resp.json()
    assert "api_key" in data
    assert data["api_key"].startswith("sn_")
    assert "SENONETWORK_API_KEY=" in data["instructions"]

    secret_key = "sn_test_guard_1234567890"
    override_settings(api_key=secret_key)
    resp_unauth = client.post("/api/security/generate-key")
    assert resp_unauth.status_code == 401
    resp_auth = client.post("/api/security/generate-key", headers={"X-API-Key": secret_key})
    assert resp_auth.status_code == 200


def test_sse_token_flow_and_non_loopback_fail_closed(client, override_settings):
    """Verifies short-lived SSE tokens and fail-closed enforcement on non-loopback host without key."""
    # Non-loopback without API key -> 503 Service Unavailable
    override_settings(api_key=None, api_host="0.0.0.0", allow_insecure_local_dev=False)
    resp_fail_closed = client.get("/api/dashboard/overview")
    assert resp_fail_closed.status_code == 503

    # Short-lived SSE token minted via X-API-Key authenticates SSE stream without leaking master key
    secret_key = "sn_master_key_for_sse"
    override_settings(api_key=secret_key, api_host="127.0.0.1")
    with patch("src.routers.pipeline._background_process_single"):
        resp_post = client.post(
            "/api/pipeline/process-patient-async",
            json={"patient_id": "PZ-SSE-TOK"},
            headers={"X-API-Key": secret_key},
        )
        task_id = resp_post.json()["task_id"]

        resp_tok = client.post("/api/security/sse-token", headers={"X-API-Key": secret_key})
        assert resp_tok.status_code == 200
        sse_tok = resp_tok.json()["sse_token"]
        assert sse_tok.startswith("sse_")

        # Bad sse_token -> 403
        resp_bad = client.get(f"/api/tasks/{task_id}/events?sse_token=invalid_tok")
        assert resp_bad.status_code == 403

        # Valid sse_token -> 200 streaming
        with client.stream("GET", f"/api/tasks/{task_id}/events?sse_token={sse_tok}") as sse_stream:
            assert sse_stream.status_code == 200
            assert "text/event-stream" in sse_stream.headers["content-type"]


# ---------------------------------------------------------------------------
# 4. Dashboard Overview
# ---------------------------------------------------------------------------

def test_dashboard_overview_data(client, override_settings):
    override_settings(api_key=None)
    resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "cup_metrics" in data
    assert "redcap_metrics" in data
    assert "storage_metrics" in data


# ---------------------------------------------------------------------------
# 5. Priamo CUP Endpoints
# ---------------------------------------------------------------------------

def test_cup_files_endpoint(client, override_settings):
    override_settings(api_key=None)
    resp = client.get("/api/cup/files")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        first = data[0]
        assert "name" in first
        assert "size_bytes" in first


def test_cup_patients_pagination_and_search(client, override_settings):
    override_settings(api_key=None)
    resp = client.get("/api/cup/patients?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_items" in data
    assert "page" in data
    assert "page_size" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_cup_export_csv_and_json(client, override_settings):
    override_settings(api_key=None)
    # JSON export
    resp_json = client.get("/api/cup/export?format=json")
    assert resp_json.status_code == 200
    assert "application/json" in resp_json.headers["content-type"]

    # CSV export
    resp_csv = client.get("/api/cup/export?format=csv")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["content-type"]


def test_cup_download_endpoint_mocked(client, override_settings, tmp_path):
    override_settings(
        api_key=None,
        priamo_username="test_user",
        priamo_password="test_password"
    )
    fake_file = tmp_path / "fogli_lavoro_fake.xlsx"
    fake_file.write_text("fake excel content")

    with patch("src.routers.cup.PriamoCupClient") as mock_cup_cls, \
         patch("src.routers.cup.estrai_pazienti_univoci") as mock_estrai:
        mock_instance = MagicMock()
        mock_instance.download_updated_worksheets.return_value = fake_file
        mock_cup_cls.return_value = mock_instance

        mock_patient = MagicMock()
        mock_estrai.return_value = [mock_patient, mock_patient]

        payload = {
            "agendas": ["3000", "3003"],
            "ytd": True,
            "year": 2026
        }
        resp = client.post("/api/cup/download", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["total_unique_patients"] == 2
        assert data["file_name"] == fake_file.name


# ---------------------------------------------------------------------------
# 6. Priamo HIS Endpoints
# ---------------------------------------------------------------------------

def test_priamo_verify_patient_mocked(client, override_settings):
    override_settings(
        api_key=None,
        priamo_api_url="http://mock.priamo",
        priamo_username="user",
        priamo_password="pwd"
    )
    mock_patient_info = MagicMock()
    mock_patient_info.patient_id = "PZ-001"
    mock_patient_info.name = "MARIA"
    mock_patient_info.surname = "ROSSI"
    mock_patient_info.date_of_birth = "1980-01-01"
    mock_patient_info.codice_fiscale = "RSSMRA80A41C933A"
    mock_patient_info.comune_nascita = "COMO"
    mock_patient_info.sesso = "F"
    mock_patient_info.telefono = "3331234567"
    mock_patient_info.indirizzo = "Via Roma 1"

    with patch("src.dotnet_client.DotNetApiClient.verify_patient_in_priamo", return_value=mock_patient_info):
        payload = {
            "name": "Maria",
            "surname": "Rossi",
            "date_of_birth": "1980-01-01"
        }
        resp = client.post("/api/priamo/verify-patient", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["patient_id"] == "PZ-001"
        assert data["name"] == "MARIA"
        assert data["codice_fiscale"] == "RSSMRA80A41C933A"


# ---------------------------------------------------------------------------
# 7. AI Pipeline & Async Task Endpoints
# ---------------------------------------------------------------------------

def test_pipeline_process_patient_sync_mocked(client, override_settings):
    override_settings(api_key=None)
    mock_result = PatientPipelineResult(
        patient_id="PZ-TEST",
        total_pdfs=2,
        successful_pdfs=2,
        forms_populated=["radiologia", "anatomia_patologica"],
        documents=[],
        redcap_upload_status="skipped",
        total_duration_sec=1.5
    )

    with patch("src.patient_pipeline.PatientPipeline.process_patient", return_value=mock_result):
        payload = {
            "patient_id": "PZ-TEST",
            "auto_upload": False,
            "dry_run": True
        }
        resp = client.post("/api/pipeline/process-patient", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["patient_id"] == "PZ-TEST"
        assert data["total_pdfs"] == 2
        assert "radiologia" in data["forms_populated"]


def test_pipeline_process_patient_async_and_task_polling(client, override_settings):
    override_settings(api_key=None)
    with patch("src.routers.pipeline._background_process_single"):
        payload = {
            "patient_id": "PZ-ASYNC-01",
            "name": "Anna",
            "surname": "Verdi",
            "dry_run": True
        }
        resp = client.post("/api/pipeline/process-patient-async", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        task_id = data["task_id"]

        # Poll task status
        resp_task = client.get(f"/api/tasks/{task_id}")
        assert resp_task.status_code == 200
        task_data = resp_task.json()
        assert task_data["task_id"] == task_id
        assert task_data["status"] == "queued"
        assert task_data["patient_id"] == "PZ-ASYNC-01"


def test_pipeline_task_events_sse(client, override_settings):
    """GET /api/tasks/{task_id}/events should stream text/event-stream."""
    override_settings(api_key=None)
    with patch("src.routers.pipeline._background_process_single"):
        resp_post = client.post("/api/pipeline/process-patient-async", json={"patient_id": "PZ-SSE-01"})
        task_id = resp_post.json()["task_id"]

        with client.stream("GET", f"/api/tasks/{task_id}/events") as sse_stream:
            assert sse_stream.status_code == 200
            assert "text/event-stream" in sse_stream.headers["content-type"]
            first_chunk = next(sse_stream.iter_text())
            assert "data:" in first_chunk
            assert task_id in first_chunk


# ---------------------------------------------------------------------------
# 8. REDCap Endpoints
# ---------------------------------------------------------------------------

def test_redcap_records_list_and_single_mocked(client, override_settings):
    override_settings(api_key=None)
    fake_records = [
        {"record_id": "1", "name": "Maria", "surname": "Rossi", "dati_del_paziente_complete": 2},
        {"record_id": "2", "name": "Luigi", "surname": "Bianchi", "dati_del_paziente_complete": 0}
    ]

    mock_svc = MagicMock()
    mock_svc.list_all_records.return_value = fake_records
    mock_svc.get_record.side_effect = lambda rid: next((r for r in fake_records if r["record_id"] == rid), None)
    app.dependency_overrides[get_redcap_service] = lambda: mock_svc

    try:
        # List
        resp_list = client.get("/api/redcap/records")
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 2

        # Single found
        resp_single = client.get("/api/redcap/records/1")
        assert resp_single.status_code == 200
        assert resp_single.json()["record_id"] == "1"

        # Single not found -> 404
        resp_not_found = client.get("/api/redcap/records/999")
        assert resp_not_found.status_code == 404
    finally:
        app.dependency_overrides.pop(get_redcap_service, None)


def test_redcap_delete_endpoints_mocked(client, override_settings):
    override_settings(api_key=None, allow_mass_delete=False, redcap_delete_token="DEL_TOK")
    with patch("src.redcap_service.delete_redcap_records") as mock_del:
        mock_del.return_value = 1

        # Single record delete (uses redcap_delete_token if configured)
        resp_del = client.delete("/api/redcap/records/PZ-01")
        assert resp_del.status_code == 200
        assert resp_del.json()["deleted_count"] == 1
        assert resp_del.json()["record_id"] == "PZ-01"
        assert mock_del.call_args.kwargs["api_token"] == "DEL_TOK"

        # Mass delete blocked by default when allow_mass_delete=False -> 403
        resp_blocked = client.delete("/api/redcap/records?confirm=true")
        assert resp_blocked.status_code == 403

        # Enable allow_mass_delete explicitly
        override_settings(api_key=None, allow_mass_delete=True, redcap_delete_token=None)

        # Mass delete without confirm -> 400
        resp_all_no_confirm = client.delete("/api/redcap/records")
        assert resp_all_no_confirm.status_code == 400

        # Mass delete with confirm=true -> 200
        mock_del.return_value = 10
        resp_all_confirm = client.delete("/api/redcap/records?confirm=true")
        assert resp_all_confirm.status_code == 200
        assert resp_all_confirm.json()["deleted_count"] == 10


# ---------------------------------------------------------------------------
# 9. CLI --generate-key
# ---------------------------------------------------------------------------

def test_cli_generate_key(capsys):
    """Running main(['--generate-key']) should print the generated key and exit."""
    with patch("sys.argv", ["api_server", "--generate-key"]):
        with pytest.raises(SystemExit) as exc:
            api_main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "sn_" in captured.out
        assert "SENONETWORK_API_KEY=" in captured.out
