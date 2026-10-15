"""Network-facing browser API controller.

The controller deliberately contains no Playwright code. It forwards the
existing browser API over a fixed Unix-domain socket to the renderer process;
the caller cannot choose a renderer URL or network destination.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .cookie_rpc import DEFAULT_COOKIE_RPC_SOCKET, CookieRPCServer
from .settings import load_settings

DEFAULT_RENDERER_SOCKET = "/run/browser/renderer.sock"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
_ALLOWED_PATH = re.compile(
    r"^/(?:health|metrics|browsers|browsers/[A-Za-z0-9_-]+(?:/execute)?)$"
)
_REQUEST_HOP_HEADERS = {
    "connection",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_RESPONSE_HOP_HEADERS = _REQUEST_HOP_HEADERS | {"content-length"}


def _renderer_socket_path(value: str | None = None) -> str:
    path = value if value is not None else os.environ.get(
        "BROWSER_RENDERER_SOCKET", DEFAULT_RENDERER_SOCKET
    )
    if not Path(path).is_absolute() or "\x00" in path:
        raise ValueError("renderer socket must be an absolute filesystem path")
    return path


def create_app(socket_path: str | None = None) -> FastAPI:
    """Create a controller fixed to one trusted local renderer socket."""
    uds_path = _renderer_socket_path(socket_path)
    app = FastAPI(title="GroktoCrawl Browser Controller", version="0.1.0")

    @app.on_event("startup")
    async def start_cookie_rpc() -> None:
        controller_socket = os.environ.get("BROWSER_CONTROLLER_SOCKET")
        if controller_socket and os.path.exists(controller_socket):
            socket_stat = os.lstat(controller_socket)
            if not stat.S_ISSOCK(socket_stat.st_mode):
                raise RuntimeError("browser controller endpoint is not a Unix socket")
            os.chown(controller_socket, -1, 20000)
            os.chmod(controller_socket, 0o660)
        settings = load_settings()
        redis_client = None
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.Redis(
                host=settings.valkey_host,
                port=settings.valkey_port,
                decode_responses=True,
            )
            await redis_client.ping()
        except Exception:
            if redis_client is not None:
                with suppress(Exception):
                    await redis_client.aclose()
            redis_client = None
        rpc = CookieRPCServer(
            os.environ.get("BROWSER_COOKIE_RPC_SOCKET", DEFAULT_COOKIE_RPC_SOCKET),
            redis_client,
        )
        await rpc.start()
        app.state.cookie_rpc = rpc
        app.state.redis = redis_client

    @app.on_event("shutdown")
    async def stop_cookie_rpc() -> None:
        rpc = getattr(app.state, "cookie_rpc", None)
        if rpc is not None:
            await rpc.close()
        redis_client = getattr(app.state, "redis", None)
        if redis_client is not None:
            await redis_client.aclose()

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "DELETE"], include_in_schema=False
    )
    async def forward(path: str, request: Request):
        del path  # The original path is taken from the request, never a caller URL.
        raw_path = request.url.path
        if not _ALLOWED_PATH.fullmatch(raw_path):
            return JSONResponse({"detail": "not found"}, status_code=404)

        declared_length = request.headers.get("content-length")
        if declared_length and declared_length.isdecimal():
            if len(declared_length) > len(str(MAX_REQUEST_BYTES)) or int(
                declared_length
            ) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    {"detail": "request too large"}, status_code=413
                )
        body_buffer = bytearray()
        async for chunk in request.stream():
            if len(body_buffer) + len(chunk) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    {"detail": "request too large"}, status_code=413
                )
            body_buffer.extend(chunk)
        body = bytes(body_buffer)

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _REQUEST_HOP_HEADERS
        }
        url = httpx.URL(
            "http://renderer.invalid"
            + raw_path
            + (f"?{request.url.query}" if request.url.query else "")
        )
        client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=uds_path),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(300.0, connect=2.0),
        )
        try:
            upstream_request = client.build_request(
                request.method, url, headers=headers, content=body
            )
            upstream = await client.send(upstream_request, stream=True)
        except (httpx.HTTPError, OSError):
            await client.aclose()
            return JSONResponse(
                {"detail": "renderer unavailable"},
                status_code=503,
                headers={"x-browser-error": "renderer-unavailable"},
            )

        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in _RESPONSE_HOP_HEADERS
        }

        async def stream_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return app


app = create_app()
