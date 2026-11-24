"""Security and API Key Authentication module for SenoNetwork."""

import secrets
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from src.config import Settings, get_settings

API_KEY_HEADER_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

router = APIRouter(tags=["Security & Auth"])


def generate_api_key(prefix: str = "sn_") -> str:
    """Generates a cryptographically secure API key."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def verify_api_key(
    header_key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Query(None, alias="api_key"),
    settings: Settings = Depends(get_settings)
) -> Optional[str]:
    """Validates the incoming API key against the configured settings.

    If `settings.api_key` is not configured (None or empty), authentication is bypassed
    to facilitate open local development.
    If `settings.api_key` is configured, requests must provide matching header `X-API-Key`
    or query parameter `api_key`.
    """
    configured_key = settings.api_key
    if not configured_key or not configured_key.strip():
        # Open access in local development
        return None

    incoming_key = header_key or query_key
    if not incoming_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Autenticazione richiesta: specificare l'header '{API_KEY_HEADER_NAME}' o il parametro '?api_key='."
        )

    # Constant time comparison to prevent timing attacks
    if not secrets.compare_digest(incoming_key.strip(), configured_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key non valida. Accesso negato."
        )

    return incoming_key


@router.post("/api/security/generate-key")
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
