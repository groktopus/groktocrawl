"""Narrow HTTP ingress bridge to the scraper's private Unix socket."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .source_http import scraper_api_uds_client

SCRAPER_SOCKET = os.environ.get("SCRAPER_API_SOCKET", "/run/scraper/app.sock")
_ALLOWED = {("GET", "/health"), ("POST", "/scrape"), ("POST", "/scrape/meta")}
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not Path(SCRAPER_SOCKET).is_absolute():
        raise RuntimeError("scraper API socket must be absolute")
    app.state.client = scraper_api_uds_client(SCRAPER_SOCKET)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="Scraper API ingress", lifespan=lifespan)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def forward(path: str, request: Request):
    del path
    if (request.method, request.url.path) not in _ALLOWED or request.url.query:
        return JSONResponse({"detail": "not found"}, status_code=404)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP | {"host", "content-length"}
    }
    try:
        upstream_request = app.state.client.build_request(
            request.method,
            request.url.path,
            headers=headers,
            content=request.stream() if request.method == "POST" else None,
        )
        upstream = await app.state.client.send(upstream_request, stream=True)
    except (httpx.HTTPError, OSError):
        return JSONResponse({"detail": "scraper unavailable"}, status_code=503)

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP
    }

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(), status_code=upstream.status_code, headers=response_headers
    )
