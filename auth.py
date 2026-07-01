"""Doctor API-key authentication.

Authentication can be disabled only with the explicit
NERON_DOCTOR_AUTH_DEV_MODE=true environment variable. Production defaults to
fail-closed when no key is configured.
"""

import hmac
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from doctor.config import cfg

api_key_header = APIKeyHeader(name="X-Doctor-Key", auto_error=False)


def require_api_key(key: str | None = Security(api_key_header)):
    """
    Dependency FastAPI.
    Le mode sans clé est réservé au dev explicite. Sinon une configuration
    absente produit 503 et une clé absente/invalide produit 401.
    """
    if not cfg.API_KEY:
        if cfg.AUTH_DEV_MODE:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Doctor API authentication is not configured.",
        )

    if key is None or not hmac.compare_digest(key, cfg.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Set X-Doctor-Key header.",
        )
