from abc import ABC, abstractmethod
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import Settings
from src.models import ExtractionResult, REDCapField
from src.normalizer import REDCapNormalizer
from src.schema_builder import SchemaBuilder

logger = logging.getLogger(__name__)


@runtime_checkable
class AIExtractor(Protocol):
    """Protocol defining the standard interface for all AI extractor backends."""

    def is_available(self) -> bool:
        """Checks if the underlying LLM engine/server is reachable."""
        ...

    def extract_from_text(
        self,
        report_text: str,
        fields: List[REDCapField],
        pdf_path: str = "",
        record_id: Optional[str] = None,
        form_name: Optional[str] = None
    ) -> ExtractionResult:
        """Extracts structured clinical data according to REDCap fields."""
        ...


class BaseAIExtractor(ABC):
    """Abstract base class consolidating common extraction logic, prompt construction,
    retry mechanisms, and response normalization across different LLM backends.
    """

    SYSTEM_PROMPT = (
        "Sei un assistente medico esperto in senologia e oncologia specializzato nell'estrazione "
        "strutturata di dati clinici per registri REDCap.\n"
        "Il tuo compito è analizzare con precisione il testo del referto medico ed estrarre le variabili cliniche.\n\n"
        "REGOLE FONDAMENTALI:\n"
        "1. Basati ESCLUSIVAMENTE sulle informazioni contenute nel testo. Non inventare o allucinare alcun dato.\n"
        "2. Se un campo non è presente o non è menzionato nel referto, imposta il suo valore su null.\n"
        "3. Per i campi a scelta singola (dropdown, radio, yesno, truefalse), assegna il CODICE indicato nelle linee guida.\n"
        "4. Per i campi a scelta multipla (checkbox), fornisci un array con i codici selezionati.\n"
        "5. Per le date, usa il formato YYYY-MM-DD quando possibile.\n"
        "6. Restituisci ESCLUSIVAMENTE un JSON valido conforme allo schema specificato."
    )

    def __init__(self, timeout_seconds: int = 120, max_retries: int = 2):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @abstractmethod
    def is_available(self) -> bool:
        """Subclasses must implement engine availability check."""
        pass

    @abstractmethod
    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any]
    ) -> str:
        """Subclasses must implement actual HTTP communication with their specific LLM backend."""
        pass

    def build_prompts(
        self,
        fields: List[REDCapField],
        report_text: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Constructs system prompt, user prompt, and JSON Schema."""
        json_schema = SchemaBuilder.build_json_schema(fields)
        guidelines = SchemaBuilder.build_prompt_guidelines(fields)

        user_prompt = (
            f"Di seguito trovi le linee guida dei campi REDCap da estrarre:\n"
            f"{guidelines}\n\n"
            f"--- TESTO DEL REFERTO MEDICO ---\n"
            f"{report_text}\n"
            f"--- FINE REFERTO ---\n\n"
            f"Estrai tutti i dati clinici presenti nel referto secondo i campi richiesti e restituisci il JSON:"
        )

        return self.SYSTEM_PROMPT, user_prompt, json_schema

    def _call_llm_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calls the LLM backend with retries on malformed JSON responses or transient HTTP errors."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                raw_response = self._call_llm(system_prompt, user_prompt, json_schema)
                return parse_json_response(raw_response)
            except (json.JSONDecodeError, requests.RequestException) as e:
                last_error = e
                logger.warning(f"Tentativo LLM {attempt}/{self.max_retries} fallito ({e}). Riprovo...")
                if attempt < self.max_retries:
                    time.sleep(1.0 * attempt)

        if last_error:
            raise last_error
        raise RuntimeError("Estrazione LLM fallita senza eccezione specifica.")

    def extract_from_text(
        self,
        report_text: str,
        fields: List[REDCapField],
        pdf_path: str = "",
        record_id: Optional[str] = None,
        form_name: Optional[str] = None
    ) -> ExtractionResult:
        """Executes full structured clinical data extraction pipeline with schema enforcement."""
        start_time = time.time()
        result = ExtractionResult(pdf_path=pdf_path, raw_text=report_text, record_id=record_id)

        if not report_text.strip():
            result.status = "error"
            result.error_message = "Il testo del referto è vuoto o non estraibile."
            result.extraction_duration_sec = round(time.time() - start_time, 2)
            return result

        try:
            system_prompt, user_prompt, json_schema = self.build_prompts(fields, report_text)
            extracted_dict = self._call_llm_with_retry(system_prompt, user_prompt, json_schema)

            # C-11: Validate and filter keys to only recognized extractable fields
            valid_field_names = {f.field_name for f in fields if f.is_extractable}
            valid_field_names.add("record_id")
            filtered_dict = {k: v for k, v in extracted_dict.items() if k in valid_field_names}

            result.extracted_data = filtered_dict

            # Normalize to valid REDCap record format
            normalized_record = REDCapNormalizer.normalize_record(
                extracted_data=filtered_dict,
                fields=fields,
                record_id=record_id,
                form_name=form_name
            )
            result.redcap_record = normalized_record
            result.status = "success"

        except Exception as e:
            logger.error(f"Errore durante l'estrazione IA per {pdf_path}: {e}")
            result.status = "error"
            result.error_message = str(e)

        result.extraction_duration_sec = round(time.time() - start_time, 2)
        return result


class LlamaCppExtractor(BaseAIExtractor):
    """Uses llama.cpp server (with distributed multi-GPU inference via RPC protocol)
    to extract structured REDCap data from clinical report text.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_mode: str = "chat",  # 'chat' (/v1/chat/completions) or 'completion' (/completion)
        model: str = "",
        temperature: float = 0.0,
        timeout_seconds: int = 180,
        max_retries: int = 2
    ):
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries)
        self.base_url = base_url.rstrip("/")
        self.api_mode = api_mode.lower()
        self.model = model or "llama-cpp-rpc"
        self.temperature = temperature

    def is_available(self) -> bool:
        """Checks if the llama.cpp server is active and reachable."""
        # 1. Check /health
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass

        # 2. Check /v1/models
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass

        # 3. Check /props
        try:
            r = requests.get(f"{self.base_url}/props", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass

        return False

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any]
    ) -> str:
        """Communicates with llama.cpp using either /v1/chat/completions or /completion endpoint."""
        if self.api_mode == "completion":
            endpoint = f"{self.base_url}/completion"
            full_prompt = (
                f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
                f"<|im_start|>assistant\n"
            )
            payload = {
                "prompt": full_prompt,
                "temperature": self.temperature,
                "json_schema": json_schema,
                "n_predict": 4096,
                "stream": False
            }
            response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            res_json = response.json()
            return res_json.get("content", "")

        # OpenAI-compatible /v1/chat/completions
        endpoint = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temperature,
            "response_format": {
                "type": "json_object",
                "schema": json_schema
            },
            "stream": False
        }
        response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)
        if response.status_code != 200:
            # Fallback to general json_object if schema format argument unsupported
            payload["response_format"] = {"type": "json_object"}
            response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)

        response.raise_for_status()
        res_json = response.json()
        return res_json["choices"][0]["message"]["content"]


class OllamaExtractor(BaseAIExtractor):
    """Uses a local Ollama LLM to extract structured REDCap data from clinical report text."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2:3b",
        temperature: float = 0.0,
        timeout_seconds: int = 120,
        max_retries: int = 2
    ):
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

    def is_available(self) -> bool:
        """Checks if the local Ollama instance is reachable."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_local_models(self) -> List[str]:
        """Returns the list of downloaded local model names."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code == 200:
                data = r.json()
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            pass
        return []

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any]
    ) -> str:
        """Calls the Ollama /api/chat endpoint."""
        endpoint = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": json_schema,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": 4096
            }
        }
        response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)
        if response.status_code != 200:
            payload["format"] = "json"
            response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)

        response.raise_for_status()
        res_json = response.json()
        return res_json.get("message", {}).get("content", "")


def parse_json_response(text: str) -> Dict[str, Any]:
    """Safely parses JSON string, stripping any surrounding markdown markers."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned)
        cleaned = re.sub(r"```$", "", cleaned)
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]

    return json.loads(cleaned)


def get_ai_extractor(settings: Optional[Settings] = None) -> Union[LlamaCppExtractor, OllamaExtractor]:
    """Factory that returns the configured AI extractor.
    Defaults to LlamaCppExtractor (with distributed RPC inference),
    falling back to Ollama if explicitly requested or if llama.cpp is not responding.
    """
    cfg = settings or Settings()

    if cfg.ai_backend == "llama_cpp":
        extractor = LlamaCppExtractor(
            base_url=cfg.llama_cpp_base_url,
            api_mode=cfg.llama_cpp_api_mode,
            model=cfg.llama_cpp_model,
            temperature=cfg.llama_cpp_temperature,
            timeout_seconds=cfg.llama_cpp_timeout_seconds
        )
        if extractor.is_available():
            logger.info(f"Utilizzo motore IA locale: llama.cpp RPC Server ({cfg.llama_cpp_base_url})")
            return extractor

        logger.warning(
            f"llama.cpp non risponde all'indirizzo {cfg.llama_cpp_base_url}. "
            "Verifico disponibilità di Ollama come fallback..."
        )
        ollama_fallback = OllamaExtractor(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            temperature=cfg.ollama_temperature,
            timeout_seconds=cfg.ollama_timeout_seconds
        )
        if ollama_fallback.is_available():
            logger.info(f"Fallback su Ollama attivo ({cfg.ollama_base_url})")
            return ollama_fallback

        # If neither is responding, return configured extractor anyway
        return extractor

    # If backend explicitly set to ollama
    return OllamaExtractor(
        base_url=cfg.ollama_base_url,
        model=cfg.ollama_model,
        temperature=cfg.ollama_temperature,
        timeout_seconds=cfg.ollama_timeout_seconds
    )
