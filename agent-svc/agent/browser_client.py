"""Fixed browser-controller HTTP client for TCP or a trusted local socket."""

from __future__ import annotations

import httpx

DEFAULT_BROWSER_URL = "http://browser-svc:8012"
_UDS_BASE_URL = "http://browser-control"


def create_browser_client(
    *,
    socket_path: str | None = None,
    timeout: float = 10.0,
) -> httpx.AsyncClient:
    """Create a browser client, using only the configured transport.

    When ``socket_path`` is set, HTTPX is bound to that Unix socket and the
    fixed local base URL. There is no TCP fallback if the socket is missing or
    unavailable. With no socket path, retain the legacy HTTP client behavior.
    """
    if socket_path:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path, retries=0),
            base_url=_UDS_BASE_URL,
            trust_env=False,
            timeout=timeout,
        )
    return httpx.AsyncClient(timeout=timeout)


def browser_request_url(
    path: str,
    *,
    socket_path: str | None,
    legacy_base_url: str = DEFAULT_BROWSER_URL,
) -> str:
    """Build the request target without permitting UDS mode to use a host URL."""
    if socket_path:
        return path
    return f"{legacy_base_url.rstrip('/')}{path}"
