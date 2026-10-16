"""API key authentication for GroktoCrawl."""

import logging

from fastapi import HTTPException, Request

from .settings import load_settings

logger = logging.getLogger(__name__)

SECURITY_WARNING_HEADER = "X-Security-Warning"
SECURITY_WARNING_BODY = (
    "No API key configured. API is publicly accessible without authentication. "
    "Set API_KEY in your .env file to enable authentication."
)

_settings = load_settings()
AUTH_ENABLED = _settings.api_key != ""
API_KEY = _settings.api_key


def request_credential(request: Request) -> str | None:
    """Select the same credential for authentication and request identity.

    A configured key must match before it can select an ownership/cache scope.
    Without authentication, supplied keys are partition hints, not identities.
    """
    authorization = request.headers.get("Authorization", "")
    bearer = authorization[7:] if authorization.startswith("Bearer ") else None
    api_key = request.headers.get("X-API-Key")
    if not AUTH_ENABLED:
        return bearer or api_key or None
    if bearer == API_KEY:
        return bearer
    if api_key == API_KEY:
        return api_key
    return None


async def verify_api_key(request: Request) -> None:
    if not AUTH_ENABLED or request_credential(request) is not None:
        return
    raise HTTPException(status_code=403, detail="Invalid or missing API key.")
