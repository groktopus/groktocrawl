"""Only the existing scraper HTTP surface crosses the private ingress bridge."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx
from scraper import ingress

_PROBE_PATH = (
    Path(__file__).parents[1] / "integration" / "scraper_ingress_boundary_probe.py"
)
_PROBE_SPEC = importlib.util.spec_from_file_location(
    "scraper_ingress_boundary_probe", _PROBE_PATH
)
assert _PROBE_SPEC is not None and _PROBE_SPEC.loader is not None
_PROBE = importlib.util.module_from_spec(_PROBE_SPEC)
_PROBE_SPEC.loader.exec_module(_PROBE)


def test_ingress_forwards_fixed_api_routes_and_rejects_arbitrary_paths():
    observed: list[tuple[str, str, bytes]] = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"forwarded":"/scrape"}'

        async def aclose(self):
            return None

    async def handle(request: httpx.Request) -> httpx.Response:
        observed.append((request.method, request.url.path, await request.aread()))
        return httpx.Response(
            201, headers={"content-type": "application/json"}, stream=Stream()
        )

    async def run():
        ingress.app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="http://scraper.internal",
            trust_env=False,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ingress.app),
            base_url="http://ingress.internal",
        ) as client:
            accepted = await client.post("/scrape", json={"url": "https://example.test"})
            rejected = await client.post("/admin/flush", json={})
            query = await client.get("/health?target=http://example.test")
        await ingress.app.state.client.aclose()
        return accepted, rejected, query

    accepted, rejected, query = asyncio.run(run())
    assert accepted.status_code == 201
    assert accepted.json() == {"forwarded": "/scrape"}
    assert rejected.status_code == 404
    assert query.status_code == 404
    assert len(observed) == 1
    method, path, body = observed[0]
    assert (method, path) == ("POST", "/scrape")
    assert b"https://example.test" in body


def test_forced_browser_probe_requires_browser_source_provenance():
    response = {
        "success": True,
        "data": {
            "source": "browser-svc",
            "markdown": "fixture INGRESS_SCRAPE_PIPELINE_OK",
        },
    }
    _PROBE._require_browser_scrape(response)

    for source in ("content-negotiation", "llms.txt", None):
        response["data"]["source"] = source
        try:
            _PROBE._require_browser_scrape(response)
        except RuntimeError as exc:
            assert "browser capture" in str(exc)
        else:
            raise AssertionError(f"non-browser source was accepted: {source!r}")
