"""Unit and Integration tests for SenoNetwork Web Dashboard & Granular SSE Stream."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.api_server import app
from src.patient_pipeline import PatientPipelineResult, DocumentProcessingResult
from src.routers.pipeline import _tasks_store


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_static_and_root_endpoints(client):
    # 1. Root / serves HTML dashboard
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert "SenoNetwork" in res_root.text
    assert 'role="tablist"' in res_root.text

    # 2. /dashboard serves HTML dashboard
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert "text/html" in res_dash.headers.get("content-type", "")

    # 3. /static/css/dashboard.css
    res_css = client.get("/static/css/dashboard.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")
    assert "--primary" in res_css.text

    # 4. /static/js/app.js
    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    assert any(ct in res_js.headers.get("content-type", "") for ct in ("javascript", "text/plain"))
    assert "APIClient" in res_js.text
    assert "EventSource" in res_js.text


def test_granular_sse_stream_events(client):
    task_id = "test-sse-task-granular-123"
    _tasks_store[task_id] = {
        "task_id": task_id,
        "type": "single_patient",
        "status": "processing",
        "progress_pct": 45,
        "current_step": "Estrazione [1/2] MAMMOGRAFIA.pdf...",
        "items_total": 1,
        "items_done": 0,
        "events_log": [
            {"time": "09:00:01", "message": "Avvio pipeline...", "pct": 5},
            {"time": "09:00:03", "message": "Download referti completato", "pct": 20}
        ],
        "processed_documents": [
            {
                "filename": "MAMMOGRAFIA.pdf",
                "classified_form": "radiologia",
                "fields_extracted_count": 3,
                "duration_sec": 1.5,
                "status": "success"
            }
        ],
        "result": None,
        "error": None
    }

    # Simulate immediate completion on next step
    def complete_task():
        _tasks_store[task_id]["status"] = "completed"
        _tasks_store[task_id]["progress_pct"] = 100
        _tasks_store[task_id]["result"] = {"patient_id": "test-paz", "status": "success"}

    import threading
    timer = threading.Timer(0.3, complete_task)
    timer.start()

    resp = client.get(f"/api/tasks/{task_id}/events")
    timer.join()

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    lines = resp.text.split("\n")
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) >= 1

    payload = json.loads(data_lines[0].replace("data: ", ""))
    assert payload["task_id"] == task_id
    assert "progress_pct" in payload
    assert "events_log" in payload
    assert "processed_documents" in payload


def test_cup_download_latest_endpoint(client, tmp_path):
    mock_file = tmp_path / "fogli_lavoro_unita_37002_YTD_20260101_20261124.xlsx"
    # Create a small valid excel or copy existing
    existing = list(Path("data/cup").glob("*.xlsx"))
    assert len(existing) > 0, "Almeno un file CUP deve esistere in data/cup"
    import shutil
    shutil.copy(existing[0], mock_file)

    with patch("src.routers.cup.PriamoCupClient") as MockClientClass:
        mock_instance = MagicMock()
        mock_instance.download_updated_worksheets.return_value = mock_file
        MockClientClass.return_value = mock_instance

        # Call POST /api/cup/download-latest with no body
        resp = client.post("/api/cup/download-latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["file_name"] == mock_file.name
        assert "total_unique_patients" in data
        assert "size_formatted" in data
        assert mock_instance.download_updated_worksheets.called
        assert mock_instance.download_updated_worksheets.call_args[1]["ytd"] is True


def test_cup_files_enriched_and_raw_download(client):
    resp = client.get("/api/cup/files")
    assert resp.status_code == 200
    files = resp.json()
    assert isinstance(files, list)
    assert len(files) > 0

    first = files[0]
    assert "name" in first
    assert "size_bytes" in first
    assert "size_formatted" in first
    assert "modified_at" in first
    assert "total_patients" in first
    assert "records_estimate" in first

    # Test downloading the raw xlsx file
    download_resp = client.get(f"/api/cup/files/{first['name']}/download")
    assert download_resp.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in download_resp.headers["content-type"]
    assert f'filename="{first["name"]}"' in download_resp.headers["content-disposition"]


def test_cup_patients_compatibility_and_local_state(client):
    # Test with cup_file query alias
    files_resp = client.get("/api/cup/files")
    first_file = files_resp.json()[0]["name"]

    resp = client.get(f"/api/cup/patients?cup_file={first_file}&page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_name"] == first_file
    assert "total_items" in data
    assert "total" in data
    assert data["total"] == data["total_items"]
    assert len(data["items"]) <= 5
    if data["items"]:
        first_patient = data["items"][0]
        assert "has_local_state" in first_patient
        assert isinstance(first_patient["has_local_state"], bool)
        assert "in_redcap" in first_patient
        assert isinstance(first_patient["in_redcap"], bool)


def test_cup_patients_redcap_matching(client):
    files_resp = client.get("/api/cup/files")
    first_file = files_resp.json()[0]["name"]

    # First get the first patient from this file
    sample_resp = client.get(f"/api/cup/patients?cup_file={first_file}&page=1&page_size=1")
    items = sample_resp.json()["items"]
    assert len(items) > 0
    p = items[0]
    cognome = p["cognome"]
    nome = p["nome"]
    dob = p["data_nascita"]

    mock_summary = [
        {
            "record_id": "42",
            "name": nome,
            "surname": cognome,
            "date_of_birth": dob,
            "forms_completed_count": 5,
            "forms_status": {"dati_del_paziente": "Completato"}
        }
    ]

    with patch("src.routers.cup.REDCapService") as MockServiceClass:
        mock_svc = MagicMock()
        mock_svc.get_records_summary.return_value = mock_summary
        MockServiceClass.return_value = mock_svc

        # Force refresh redcap lookup
        resp = client.get(f"/api/cup/patients?cup_file={first_file}&page=1&page_size=1&refresh_redcap=true")
        assert resp.status_code == 200
        res_items = resp.json()["items"]
        assert len(res_items) > 0
        matched = res_items[0]
        assert matched["in_redcap"] is True
        assert matched["redcap_record_id"] == "42"
        assert matched["redcap_forms_completed"] == 5


def test_dashboard_html_contains_cup_files_table(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "cup-files-tbody" in html
    assert "cup-files-count" in html
    assert "Fogli di Lavoro Archiviati" in html
