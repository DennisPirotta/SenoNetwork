from unittest.mock import MagicMock, patch

from scripts.test_remote_llamacpp import LlamaRemoteTester


def test_llama_remote_tester_health_mock():
    tester = LlamaRemoteTester(base_url="http://remote-llama:8080")

    # Mock success on /health
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "OK"
        assert tester.test_health() is True
        assert tester.results["connected"] is True
        assert tester.results["health_status"] == "OK"

    # Mock failure
    with patch("requests.get", side_effect=Exception("Connection refused")):
        tester_offline = LlamaRemoteTester(base_url="http://remote-llama:8080")
        assert tester_offline.test_health() is False
        assert tester_offline.results["connected"] is False


def test_llama_remote_tester_props_mock():
    tester = LlamaRemoteTester(base_url="http://remote-llama:8080")
    mock_props = {
        "default_generation_settings": {
            "n_ctx": 8192,
            "model": "qwen2.5-14b-instruct.Q4_K_M.gguf"
        },
        "total_slots": 4
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_props

    with patch("requests.get", return_value=mock_resp):
        info = tester.get_server_props()
        assert info["n_ctx"] == 8192
        assert "qwen2.5" in info["model_alias"]
        assert tester.results["context_size"] == 8192


def test_llama_remote_tester_json_schema_mock():
    tester = LlamaRemoteTester(base_url="http://remote-llama:8080")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"stato_server": "operativo", "cluster_rpc": true, "num_schede_gpu": 2}'
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_resp):
        assert tester.test_json_schema_gbnf() is True
        assert tester.results["json_schema_supported"] is True
        assert tester.results["preferred_api_mode"] == "chat"


def test_llama_remote_tester_clinical_extraction_mock():
    tester = LlamaRemoteTester(base_url="http://remote-llama:8080")
    tester.results["preferred_api_mode"] = "chat"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"result_of_mx_birads": "5", "side": "1", "linfonodi_ascellari_sospetti": "1"}'
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_resp):
        assert tester.test_clinical_extraction() is True
        assert tester.results["clinical_test_passed"] is True
