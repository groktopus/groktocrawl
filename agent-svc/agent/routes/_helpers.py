"""Shared helper functions used across route domain files."""

import hashlib
import logging
import os
from typing import Any

from fastapi import Request

from agent.browser_client import browser_request_url, create_browser_client

logger = logging.getLogger(__name__)

# ── Browser service URL ───────────────────────────────────────
BROWSER_SVC_URL = "http://browser-svc:8012"


def _get_client_ip(request: Request) -> str:
    """Extract the client IP address from the request.

    Respects the ``X-Forwarded-For`` header for reverse-proxy deployments.
    Falls back to ``request.client.host`` when the header is absent.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _resolve_output_schema(
    output_schema: dict[str, Any] | None,
    schema_alias: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve the effective output schema from request fields.

    - ``output_schema`` takes priority over ``schema`` alias.
    - Empty dicts (``{}``) are treated as ``None`` (no schema).
    - ``None`` means "not set" — falls back to ``schema`` alias.
    - Returns ``None`` when no valid schema is provided.
    """
    effective = output_schema if output_schema is not None else schema_alias
    if effective is not None and not any(effective):
        return None
    return effective


def _derive_user_id(request: Request) -> str | None:
    """Derive a user identifier from the request for cache scoping.

    When RESEARCH_MEMORY_SCOPE=per_user, the user_id is derived from
    the credential accepted from ``Authorization: Bearer`` or ``X-API-Key``. When no
    API key is present, the client IP is used as a fallback.

    Returns ``None`` for global scope (no per-user isolation).
    """
    scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
    if scope != "per_user":
        return None

    from ..auth import request_credential

    api_key = request_credential(request)
    if api_key:
        # Hash the key for privacy — we only need a stable identifier
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    # Fall back to client IP
    return _get_client_ip(request)


def _get_redis_url(request: Request) -> str:
    """Build a Redis/Valkey URL from the app settings."""
    from agent.settings import load_settings

    settings = load_settings()
    return f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"


async def _browser_proxy(
    path: str, method: str = "POST", json_data: dict[str, Any] | None = None
) -> Any:
    """Proxy a request to the browser service over its configured transport."""
    socket_path = os.environ.get("BROWSER_CONTROL_SOCKET") or None
    target = browser_request_url(
        path,
        socket_path=socket_path,
        legacy_base_url=BROWSER_SVC_URL,
    )
    async with create_browser_client(socket_path=socket_path, timeout=120) as client:
        if method == "GET":
            resp = await client.get(target)
        elif method == "DELETE":
            resp = await client.delete(target)
        else:
            resp = await client.post(target, json=json_data or {})
        try:
            return resp.json()
        except Exception:
            return {"success": False, "error": resp.text[:200]}


async def _index_scrape(url: str, title: str, content: str, request: Request) -> None:
    """Fire-and-forget index a scraped page in the vector index."""
    semantic = None
    try:
        from agent.semantic_client import SemanticClient

        semantic = SemanticClient(request.app.state.semantic_url)
        await semantic.index_page(url, title, content[:2000])
    except Exception:
        logger.warning(
            "Semantic indexing failed for %s — page will not appear in vector search",
            url,
            exc_info=True,
        )
    finally:
        if semantic is not None:
            await semantic.close()
