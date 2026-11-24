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
