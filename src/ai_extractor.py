from abc import ABC, abstractmethod
import contextlib
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

import requests

from src.config import Settings
from src.models import ExtractionResult, REDCapField
from src.normalizer import REDCapNormalizer
from src.schema_builder import SchemaBuilder

logger = logging.getLogger(__name__)

_IT_MONTHS_LIST = [
    "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]
_IT_MONTHS_SHORT = [
    "", "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
]


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
        form_name: Optional[str] = None,
        mark_form_complete: bool = True,
        form_completion_status: str = "2",
        include_unchecked_checkboxes: bool = True,
    ) -> ExtractionResult:
        """Extracts structured clinical data according to REDCap fields."""
        ...


class BaseAIExtractor(ABC):
    """Abstract base class consolidating common extraction logic, prompt construction,
    retry mechanisms, deterministic validation, and response normalization across LLM backends.
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

    backend_name: str = "unknown"

    def __init__(self, timeout_seconds: int = 120, max_retries: int = 2, model: str = ""):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.model = model
        self._fallback_used = False

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
        report_text: str,
        form_name: Optional[str] = None,
        include_record_id: bool = True,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Constructs system prompt, user prompt, and JSON Schema with clinical guidelines."""
        json_schema = SchemaBuilder.build_json_schema(
            fields,
            enforce_all_required=True,
            form_name=form_name,
            include_record_id=include_record_id,
        )
        guidelines = SchemaBuilder.build_prompt_guidelines(
            fields,
            form_name=form_name,
            include_record_id=include_record_id,
        )

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
            except (json.JSONDecodeError, requests.RequestException, KeyError, IndexError, TypeError, ValueError) as e:
                last_error = e
                logger.warning(f"Tentativo LLM {attempt}/{self.max_retries} fallito ({e}). Riprovo...")
                if attempt < self.max_retries:
                    time.sleep(1.0 * attempt)

        if last_error:
            raise last_error
        raise RuntimeError("Estrazione LLM fallita senza eccezione specifica.")

    @staticmethod
    def _date_appears_in_text(iso_date: str, report_text: str) -> bool:
        """Verifies deterministically whether an extracted YYYY-MM-DD date appears in source text."""
        try:
            dt = datetime.strptime(iso_date, "%Y-%m-%d")
        except ValueError:
            return False

        y, m, d = dt.year, dt.month, dt.day
        yy = str(y)[2:]
        candidates = [
            f"{y:04d}-{m:02d}-{d:02d}",
            f"{y:04d}/{m:02d}/{d:02d}",
            f"{d:02d}/{m:02d}/{y:04d}",
            f"{d:02d}-{m:02d}-{y:04d}",
            f"{d:02d}.{m:02d}.{y:04d}",
            f"{d}/{m}/{y:04d}",
            f"{d}-{m}-{y:04d}",
            f"{d}.{m}.{y:04d}",
            f"{d:02d}/{m:02d}/{yy}",
            f"{d:02d}-{m:02d}-{yy}",
            f"{d:02d}.{m:02d}.{yy}",
        ]
        lower_text = report_text.lower()
        if any(c in lower_text for c in candidates):
            return True

        m_long = _IT_MONTHS_LIST[m]
        m_short = _IT_MONTHS_SHORT[m]
        textual_pat = rf"\b0?{d}\s+(?:{m_long}|{m_short})\.?\s+{y:04d}\b"
        return bool(re.search(textual_pat, lower_text))

    @staticmethod
    def _apply_deterministic_validation(
        extracted: Dict[str, Any],
        report_text: str,
        fields: List[REDCapField],
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Applies deterministic post-extraction checks to reject hallucinated dates or out-of-range numbers."""
        warnings: List[str] = []
        validated = dict(extracted)
        field_map = {f.field_name: f for f in fields}

        for k, val in list(validated.items()):
            if val in (None, "", [], {}):
                continue
            f = field_map.get(k)
            if not f:
                continue

            vt = (f.text_validation_type_or_show_slider_number or "").lower()
            # 1. Verify extracted clinical dates actually appear in report text
            if "date" in vt and k != "date_of_birth":
                iso = REDCapNormalizer._normalize_date(str(val))
                if not iso:
                    warnings.append(f"Campo '{k}': data non valida '{val}' rimossa.")
                    validated[k] = None
                elif not BaseAIExtractor._date_appears_in_text(iso, report_text):
                    warnings.append(f"Campo '{k}': data '{iso}' non riscontrata nel testo del referto (rimossa).")
                    validated[k] = None

            # 2. Verify percentage fields are within 0..100
            elif vt in {"integer", "number", "number_1dp", "number_2dp"}:
                is_pct = ("%" in (f.field_label or "")) or ("positive_cells" in k) or ("ki67" in k)
                num_str = REDCapNormalizer._normalize_number(
                    val,
                    min_val="0" if is_pct else f.text_validation_min,
                    max_val="100" if is_pct else f.text_validation_max,
                    integer_only=(vt == "integer"),
                )
                if not num_str:
                    warnings.append(f"Campo '{k}': valore numerico '{val}' fuori range o non valido (rimosso).")
                    validated[k] = None

        return validated, warnings

    def extract_from_text(
        self,
        report_text: str,
        fields: List[REDCapField],
        pdf_path: str = "",
        record_id: Optional[str] = None,
        form_name: Optional[str] = None,
        mark_form_complete: bool = True,
        form_completion_status: str = "2",
        include_unchecked_checkboxes: bool = True,
    ) -> ExtractionResult:
        """Executes full structured clinical data extraction pipeline with schema enforcement."""
        start_time = time.time()
        result = ExtractionResult(
            pdf_path=pdf_path,
            raw_text=report_text,
            record_id=record_id,
            ai_backend=self.backend_name,
            ai_model=self.model or None,
            fallback_used=self._fallback_used,
        )

        if not report_text.strip():
            result.status = "error"
            result.error_message = "Il testo del referto è vuoto o non estraibile."
            result.extraction_duration_sec = round(time.time() - start_time, 2)
            return result

        try:
            system_prompt, user_prompt, json_schema = self.build_prompts(
                fields,
                report_text,
                form_name=form_name,
                include_record_id=(record_id is None),
            )
            result.prompt_chars = len(system_prompt) + len(user_prompt)
            if result.prompt_chars > 24000:
                logger.warning(
                    f"Prompt molto esteso ({result.prompt_chars} caratteri) per form '{form_name}': "
                    "rischio superamento finestra contesto LLM."
                )

            extracted_dict = self._call_llm_with_retry(system_prompt, user_prompt, json_schema)

            # C-11: Validate and filter keys to only recognized extractable fields
            valid_field_names = {f.field_name for f in fields if f.is_extractable}
            valid_field_names.add("record_id")
            filtered_dict = {k: v for k, v in extracted_dict.items() if k in valid_field_names}

            # Deterministic post-extraction sanity verification
            filtered_dict, val_warnings = self._apply_deterministic_validation(
                filtered_dict, report_text, fields
            )
            result.validation_warnings = val_warnings
            for w in val_warnings:
                logger.warning(f"Validazione post-estrazione ({form_name}): {w}")

            result.extracted_data = filtered_dict

            # Normalize to valid REDCap record format
            normalized_record = REDCapNormalizer.normalize_record(
                extracted_data=filtered_dict,
                fields=fields,
                record_id=record_id,
                form_name=form_name,
                mark_form_complete=mark_form_complete,
                form_completion_status=form_completion_status,
                include_unchecked_checkboxes=include_unchecked_checkboxes,
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
    """Uses llama.cpp server (with distributed multi-GPU inference via RPC protocol
    or remote server behind Cloudflare Access) to extract structured REDCap data
    from clinical report text.
    """

    backend_name = "llama_cpp"

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_mode: str = "chat",
        model: str = "",
        temperature: float = 0.0,
        timeout_seconds: int = 180,
        max_retries: int = 2,
        cf_client_id: Optional[str] = None,
        cf_client_secret: Optional[str] = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, model=model or "llama-cpp-rpc")
        self.base_url = base_url.rstrip("/")
        self.api_mode = api_mode.lower()
        self.temperature = temperature
        self.cf_client_id = cf_client_id or os.getenv("AI_CLOUDFLARED_CLIENT_ID", "")
        self.cf_client_secret = cf_client_secret or os.getenv("AI_CLOUDFLARED_CLIENT_SECRET", "")

        self.headers: Dict[str, str] = {}
        if self.cf_client_id and self.cf_client_secret:
            self.headers["CF-Access-Client-Id"] = self.cf_client_id
            self.headers["CF-Access-Client-Secret"] = self.cf_client_secret

    def is_available(self) -> bool:
        """Checks if the llama.cpp server is active and reachable."""
        for path in ("/health", "/v1/models", "/props"):
            try:
                r = requests.get(f"{self.base_url}{path}", headers=self.headers, timeout=5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
        return False

    def _read_stream_or_json(self, response: requests.Response, mode: str) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            collected = []
            for line in response.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if decoded.startswith("data: "):
                    raw_data = decoded[6:].strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw_data)
                        if mode == "completion":
                            c = chunk.get("content", "")
                        else:
                            c = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if c:
                            collected.append(c)
                    except Exception:
                        pass
            return "".join(collected)
        res_json = response.json()
        if mode == "completion":
            return res_json.get("content", "")
        return res_json["choices"][0]["message"]["content"]

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any]
    ) -> str:
        """Communicates with llama.cpp using /v1/chat/completions or /completion with streaming SSE."""
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
                "stream": True
            }
            with contextlib.closing(
                requests.post(endpoint, json=payload, headers=self.headers, stream=True, timeout=self.timeout_seconds)
            ) as response:
                response.raise_for_status()
                return self._read_stream_or_json(response, "completion")

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
            "stream": True
        }
        with contextlib.closing(
            requests.post(endpoint, json=payload, headers=self.headers, stream=True, timeout=self.timeout_seconds)
        ) as response:
            if response.status_code == 200:
                return self._read_stream_or_json(response, "chat")
            err_body = response.text[:500]
            status_code = response.status_code

        # Only retry without schema if server rejected schema formatting (400/422) and not context overflow
        if status_code in (400, 422) and "context" not in err_body.lower() and "n_ctx" not in err_body.lower():
            logger.warning(f"llama.cpp ha rifiutato response_format con schema (HTTP {status_code}); fallback a json_object.")
            payload["response_format"] = {"type": "json_object"}
            with contextlib.closing(
                requests.post(endpoint, json=payload, headers=self.headers, stream=True, timeout=self.timeout_seconds)
            ) as fallback_resp:
                fallback_resp.raise_for_status()
                return self._read_stream_or_json(fallback_resp, "chat")

        raise requests.HTTPError(f"Errore llama.cpp HTTP {status_code}: {err_body}")


class OllamaExtractor(BaseAIExtractor):
    """Uses a local Ollama LLM to extract structured REDCap data from clinical report text."""

    backend_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2:3b",
        temperature: float = 0.0,
        timeout_seconds: int = 120,
        max_retries: int = 2
    ):
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, model=model)
        self.base_url = base_url.rstrip("/")
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
        if response.status_code in (400, 422):
            logger.warning(f"Ollama ha rifiutato format json_schema (HTTP {response.status_code}); fallback a format='json'.")
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
            timeout_seconds=cfg.llama_cpp_timeout_seconds,
            cf_client_id=cfg.ai_cloudflared_client_id,
            cf_client_secret=cfg.ai_cloudflared_client_secret,
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
            ollama_fallback._fallback_used = True
            logger.warning(f"Fallback su Ollama attivo ({cfg.ollama_base_url}, model={cfg.ollama_model})")
            return ollama_fallback

        return extractor

    # If backend explicitly set to ollama
    return OllamaExtractor(
        base_url=cfg.ollama_base_url,
        model=cfg.ollama_model,
        temperature=cfg.ollama_temperature,
        timeout_seconds=cfg.ollama_timeout_seconds
    )
