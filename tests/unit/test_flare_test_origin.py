"""Local HTTP contract tests for the protected Flare origin fixture."""

from __future__ import annotations

import http.client
import importlib.util
import io
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest


def _load_fixture_module():
    path = Path(__file__).resolve().parents[2] / "flare-test-origin" / "server.py"
    spec = importlib.util.spec_from_file_location("flare_test_origin_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load local origin fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _markdown_from_fixture(html: bytes) -> str:
    scraper_root = str(Path(__file__).resolve().parents[2] / "scraper-svc")
    sys.path.insert(0, scraper_root)
    try:
        from scraper.fetch_quality import html_to_markdown

        return html_to_markdown(html.decode("utf-8"))
    finally:
        sys.path.remove(scraper_root)


@pytest.fixture
def origin_url():
    fixture = _load_fixture_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_head_returns_get_metadata_without_body(origin_url: str) -> None:
    url = f"{origin_url}/scrape-fixture"
    with urlopen(url, timeout=2) as response:
        get_body = response.read()
        get_headers = response.headers

    request = Request(url, method="HEAD")
    with urlopen(request, timeout=2) as response:
        assert response.status == 200
        assert response.read() == b""
        assert response.headers["Content-Type"] == get_headers["Content-Type"]
        assert response.headers["Content-Length"] == str(len(get_body))
    assert b"INGRESS_SCRAPE_PIPELINE_OK" in get_body
    assert get_body == _load_fixture_module().SCRAPE_FIXTURE_HTML
    markdown = _markdown_from_fixture(get_body)
    assert "INGRESS_SCRAPE_PIPELINE_OK" in markdown


def test_head_preserves_redirect_metadata_without_body(origin_url: str) -> None:
    host, port = origin_url.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=2)
    try:
        connection.request("HEAD", "/redirect")
        response = connection.getresponse()
        assert response.status == 302
        assert response.getheader("Location") == "http://flare-origin.test/get"
        assert response.read() == b""
    finally:
        connection.close()


def test_lab_fixture_host_and_private_peer_are_explicitly_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPTURE_FIXTURE_HOST", "capture-origin.example.test")
    monkeypatch.setenv(
        "CAPTURE_PRIVATE_SENTINEL_URL", "http://172.31.253.250:19001/probe"
    )
    fixture = _load_fixture_module()
    handler = fixture.FixtureHandler.__new__(fixture.FixtureHandler)
    headers: dict[str, str] = {}
    statuses: list[int] = []
    handler.send_response = statuses.append
    handler.send_header = headers.__setitem__
    handler.end_headers = lambda: None
    handler.path = "/redirect"
    fixture.FixtureHandler.do_GET(handler)
    assert statuses == [302]
    assert headers["Location"] == "http://capture-origin.example.test/get"

    headers.clear()
    statuses.clear()
    handler.path = "/get"
    handler.headers = {}
    handler.command = "GET"
    handler.wfile = io.BytesIO()
    fixture.FixtureHandler.do_GET(handler)
    body = handler.wfile.getvalue().decode("utf-8")
    assert statuses == [200]
    assert 'fetch("http://172.31.253.250:19001/probe/" + privateNonce,' in body
    assert "fetch(`$http://172.31.253.250:19001/probe/" not in body


def test_private_fetch_fixture_requires_cors_validated_sentinel_response(origin_url: str) -> None:
    with urlopen(f"{origin_url}/get", timeout=2) as response:
        body = response.read().decode("utf-8")
    assert "crypto.getRandomValues(nonceBytes)" in body
    private_fetch = body.split("const privateProbe", 1)[1]
    assert "mode:'cors'" in private_fetch
    assert "response.status === 200" in private_fetch
    assert "response.headers.get('content-type') === 'text/plain'" in private_fetch
    assert "PRIVATE_SENTINEL_RESPONSE_V1:${privateNonce}" in private_fetch
    assert "mode:'no-cors'" not in private_fetch
