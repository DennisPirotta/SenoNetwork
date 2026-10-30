import json
from unittest.mock import MagicMock, patch
import pytest

from src.ai_extractor import (
    LlamaCppExtractor,
    OllamaExtractor,
    get_ai_extractor,
    parse_json_response
)
from src.config import Settings
from src.models import REDCapField


@pytest.fixture
def sample_fields():
    return [
        REDCapField(
            field_name="record_id",
            form_name="radiologia",
            field_type="text",
            field_label="Record ID"
        ),
        REDCapField(
            field_name="result_of_mx_birads",
            form_name="radiologia",
            field_type="radio",
            field_label="Esito BIRADS",
            select_choices_or_calculations="1, 1 | 2, 2 | 3, 3 | 4, 4 | 5, 5"
        ),
        REDCapField(
            field_name="palpable_lesion",
            form_name="radiologia",
            field_type="dropdown",
            field_label="Lesione palpabile",
            select_choices_or_calculations="0, No | 1, Sì"
        )
    ]


def test_llamacpp_init():
    ext = LlamaCppExtractor(
        base_url="http://192.168.1.100:8080",
        api_mode="chat",
        temperature=0.0,
        timeout_seconds=120
    )
    assert ext.base_url == "http://192.168.1.100:8080"
    assert ext.api_mode == "chat"
    assert ext.temperature == 0.0


def test_llamacpp_is_available_mock():
    ext = LlamaCppExtractor(base_url="http://localhost:8080")
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"status": "ok"}
        assert ext.is_available() is True

    with patch("requests.get", side_effect=Exception("Connection refused")):
        assert ext.is_available() is False


def test_llamacpp_extract_chat_mode(sample_fields):
    ext = LlamaCppExtractor(base_url="http://localhost:8080", api_mode="chat")

    mock_llm_json = {
        "record_id": "PZ-999",
        "result_of_mx_birads": "4",
        "palpable_lesion": "1"
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(mock_llm_json)
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_response):
        result = ext.extract_from_text(
            report_text="Mammografia con lesione palpabile sospetta BIRADS 4.",
            fields=sample_fields,
            record_id="PZ-999",
            form_name="radiologia"
        )

    assert result.status == "success"
    assert result.extracted_data["result_of_mx_birads"] == "4"
    assert result.redcap_record["result_of_mx_birads"] == "4"
    assert result.redcap_record["palpable_lesion"] == "1"
    assert result.redcap_record["radiologia_complete"] == "2"


def test_llamacpp_extract_completion_mode(sample_fields):
    ext = LlamaCppExtractor(base_url="http://localhost:8080", api_mode="completion")

    mock_llm_json = {
        "record_id": "PZ-888",
        "result_of_mx_birads": "2",
        "palpable_lesion": "0"
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": json.dumps(mock_llm_json)
    }

    with patch("requests.post", return_value=mock_response):
        result = ext.extract_from_text(
            report_text="Reperto negativo o benigno BIRADS 2.",
            fields=sample_fields,
            record_id="PZ-888",
            form_name="radiologia"
        )

    assert result.status == "success"
    assert result.extracted_data["result_of_mx_birads"] == "2"
    assert result.redcap_record["result_of_mx_birads"] == "2"
    assert result.redcap_record["palpable_lesion"] == "0"


def test_get_ai_extractor_factory():
    # Explicit llama_cpp
    settings = Settings(ai_backend="llama_cpp", llama_cpp_base_url="http://localhost:8080")
    with patch.object(LlamaCppExtractor, "is_available", return_value=True):
        extractor = get_ai_extractor(settings)
        assert isinstance(extractor, LlamaCppExtractor)

    # Fallback to Ollama when llama.cpp is offline
    with patch.object(LlamaCppExtractor, "is_available", return_value=False):
        with patch.object(OllamaExtractor, "is_available", return_value=True):
            extractor = get_ai_extractor(settings)
            assert isinstance(extractor, OllamaExtractor)

    # Explicit ollama
    settings_ollama = Settings(ai_backend="ollama")
    extractor_ollama = get_ai_extractor(settings_ollama)
    assert isinstance(extractor_ollama, OllamaExtractor)
