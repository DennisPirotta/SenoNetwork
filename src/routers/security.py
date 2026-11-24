"""Security and API Key Authentication module for SenoNetwork."""

import secrets
import threading
import time
from typing import Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from src.config import Settings, get_settings

API_KEY_HEADER_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SSE_TOKEN_TTL_SECONDS = 60.0

_SSE_TOKENS_LOCK = threading.Lock()
_SSE_TOKENS: Dict[str, float] = {}

router = APIRouter(tags=["Security & Auth"])


def generate_api_key(prefix: str = "sn_") -> str:
    """Generates a cryptographically secure API key."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def _prune_expired_sse_tokens(now: float) -> None:
    expired = [tok for tok, exp in _SSE_TOKENS.items() if exp <= now]
    for tok in expired:
        _SSE_TOKENS.pop(tok, None)


def _issue_sse_token() -> str:
    now = time.monotonic()
    token = f"sse_{secrets.token_urlsafe(24)}"
    with _SSE_TOKENS_LOCK:
        _prune_expired_sse_tokens(now)
        # Bound token cache to avoid unbounded memory growth under repeated requests
        if len(_SSE_TOKENS) >= 512:
            oldest_key = min(_SSE_TOKENS, key=lambda k: _SSE_TOKENS[k])
            _SSE_TOKENS.pop(oldest_key, None)
        _SSE_TOKENS[token] = now + SSE_TOKEN_TTL_SECONDS
    return token


def _validate_sse_token(token: str) -> bool:
    now = time.monotonic()
    clean = token.strip()
    if not clean:
        return False
    with _SSE_TOKENS_LOCK:
        _prune_expired_sse_tokens(now)
        for existing, exp in _SSE_TOKENS.items():
            if secrets.compare_digest(clean, existing) and exp > now:
                return True
    return False


def verify_api_key(
    header_key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Query(None, alias="api_key"),
    sse_token: Optional[str] = Query(None, alias="sse_token"),
    settings: Settings = Depends(get_settings)
) -> Optional[str]:
    """Validates the incoming API key or short-lived SSE token against the configured settings.

    Fails closed when bound to a non-loopback host without an API key (unless
    `allow_insecure_local_dev=True`). When `settings.api_key` is configured,
    accepts matching `X-API-Key` header, short-lived `sse_token`, or `api_key`.
    """
    configured_key = (settings.api_key or "").strip()
    if not configured_key:
        host_val = (settings.api_host or "").strip().lower()
        if host_val and host_val not in LOOPBACK_HOSTS and not settings.allow_insecure_local_dev:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Configurazione sicurezza incompleta: impostare SENONETWORK_API_KEY su host non-loopback."
            )
        return None

    if sse_token is not None:
        if _validate_sse_token(sse_token):
            return configured_key
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token SSE non valido o scaduto."
        )

    incoming_key = header_key or query_key
    if not incoming_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Autenticazione richiesta: specificare l'header '{API_KEY_HEADER_NAME}'."
        )

    # Constant time comparison to prevent timing attacks
    if not secrets.compare_digest(incoming_key.strip(), configured_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key non valida. Accesso negato."
        )

    return incoming_key


@router.post("/api/security/sse-token", dependencies=[Depends(verify_api_key)])
def create_sse_token() -> Dict[str, object]:
    """Issues a short-lived token (60s TTL) for authenticating EventSource SSE connections."""
    token = _issue_sse_token()
    return {
        "sse_token": token,
        "expires_in": int(SSE_TOKEN_TTL_SECONDS),
    }


@router.post("/api/security/generate-key", dependencies=[Depends(verify_api_key)])
def generate_new_api_key():
    """Generates a new cryptographically secure API Key that can be saved in .env (SENONETWORK_API_KEY)."""
    new_key = generate_api_key()
    return {
        "status": "success",
        "api_key": new_key,
        "env_variable": "SENONETWORK_API_KEY",
        "header": "X-API-Key",
        "instructions": f"Aggiungi '{new_key}' al tuo file .env come 'SENONETWORK_API_KEY={new_key}'."
    }
