import json
from unittest.mock import MagicMock, patch
from fastapi import BackgroundTasks

from src.ai_extractor import (
    AIExtractor,
    BaseAIExtractor,
    LlamaCppExtractor,
    OllamaExtractor,
)
from src.api_server import (
    health_check,
    process_patient_async,
    get_task_status,
    _tasks_store
)
from src.config import Settings
from src.models import ExtractionResult, REDCapField
from src.normalizer import REDCapNormalizer
from src.patient_pipeline import (
    PatientPipeline,
    is_safe_webhook_url,
)


def test_ai_extractor_protocol_conformance():
    """Verifies that both extractor classes strictly conform to the runtime AIExtractor protocol."""
    llama_ext = LlamaCppExtractor(base_url="http://localhost:8080")
    ollama_ext = OllamaExtractor(base_url="http://localhost:11434")

    assert isinstance(llama_ext, AIExtractor)
    assert isinstance(ollama_ext, AIExtractor)
    assert isinstance(llama_ext, BaseAIExtractor)
    assert isinstance(ollama_ext, BaseAIExtractor)


def test_safe_choice_normalization():
    """Tests that REDCapNormalizer._normalize_choice avoids dangerous false substring matches."""
    choices = {
        "1": "Invasivo",
        "2": "In situ"
    }

    # Exact label
    assert REDCapNormalizer._normalize_choice("In situ", choices) == "2"
    assert REDCapNormalizer._normalize_choice("Invasivo", choices) == "1"

    # Token-level match
    assert REDCapNormalizer._normalize_choice("Carcinoma in situ", choices) == "2"
    assert REDCapNormalizer._normalize_choice("Carcinoma invasivo mammario", choices) == "1"

    # Code direct match
    assert REDCapNormalizer._normalize_choice("1", choices) == "1"
    assert REDCapNormalizer._normalize_choice("2", choices) == "2"


def test_webhook_ssrf_validator():
    """Ensures internal IP addresses and dangerous metadata endpoints are rejected."""
    # Loopback / internal IPs blocked when allow_local=False
    assert is_safe_webhook_url("http://127.0.0.1:8080/callback", allow_local=False) is False
    assert is_safe_webhook_url("http://localhost:8000/api", allow_local=False) is False
    assert is_safe_webhook_url("http://169.254.169.254/latest/meta-data", allow_local=False) is False
    assert is_safe_webhook_url("http://10.0.0.1/hook", allow_local=False) is False
    assert is_safe_webhook_url("http://192.168.1.10/hook", allow_local=False) is False

    # External safe domains allowed
    assert is_safe_webhook_url("https://api.valduce.it/webhooks/senonetwork", allow_local=False) is True

    # Allowed hosts whitelist check
    whitelist = ["webhook.example.org"]
    assert is_safe_webhook_url("https://webhook.example.org/hook", allowed_hosts=whitelist) is True
    assert is_safe_webhook_url("https://evil.example.com/hook", allowed_hosts=whitelist) is False

    # Allow local if explicitly enabled in config
    assert is_safe_webhook_url("http://localhost:8000/api", allow_local=True) is True


def test_patient_pipeline_idempotence_and_state(tmp_path):
    """Verifies that patient pipeline records execution state and SHA256 hashes."""
    settings = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
        downloads_dir=tmp_path / "downloads",
        redcap_api_url="",
        redcap_api_token="",
    )

    dummy_pdf = tmp_path / "report_mammografia.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")

    mock_ai = MagicMock(spec=LlamaCppExtractor)
    mock_ai.extract_from_text.return_value = ExtractionResult(
        pdf_path=str(dummy_pdf),
        record_id="PZ-IDEMPOTENT-1",
        status="success",
        extracted_data={"result_of_mx_birads": "2"},
        redcap_record={"record_id": "PZ-IDEMPOTENT-1", "result_of_mx_birads": "2"}
    )

    from src.models import REDCapField
    pipeline = PatientPipeline(settings=settings, ai_extractor=mock_ai)
    pipeline._cached_fields_by_form["radiologia"] = [
        REDCapField(field_name="record_id", form_name="dati_del_paziente", field_type="text"),
        REDCapField(field_name="result_of_mx_birads", form_name="radiologia", field_type="radio"),
    ]

    with patch("src.pdf_extractor.PDFExtractor.extract_text", return_value="Mammografia birads 2"):
        res = pipeline.process_patient(
            patient_id="PZ-IDEMPOTENT-1",
            name="Laura",
            surname="Rossi",
            date_of_birth="1980-05-12",
            pdf_paths=[dummy_pdf],
            auto_upload=False
        )

    assert res.successful_pdfs == 1
    state_file = settings.state_dir / "PZ-IDEMPOTENT-1_state.json"
    assert state_file.exists()

    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    assert state_data["patient_id"] == "PZ-IDEMPOTENT-1"
    assert len(state_data["files"]) == 1
    assert state_data["files"][0]["sha256"] is not None


def test_api_server_endpoints_unit():
    """Directly tests health_check and process_patient_async handler functions."""
    settings = Settings()
    health_data = health_check(settings=settings)
    assert "llama_cpp_rpc_available" in health_data
    assert "ollama_available" in health_data
    assert "status" in health_data

    # Async task enqueueing
    mock_pipeline = MagicMock(spec=PatientPipeline)
    bg = BackgroundTasks()

    async_res = process_patient_async(
        patient_id="PZ-ASYNC-UNIT",
        background_tasks=bg,
        request=None,
        pipeline=mock_pipeline
    )
    assert async_res.patient_id == "PZ-ASYNC-UNIT"
    assert async_res.status == "queued"
    assert async_res.task_id in _tasks_store

    status_data = get_task_status(async_res.task_id)
    assert status_data["patient_id"] == "PZ-ASYNC-UNIT"
    assert status_data["status"] == "queued"
