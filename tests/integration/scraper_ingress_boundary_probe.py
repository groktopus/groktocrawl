"""Verify the agent-facing bridge exposes only the existing scraper routes."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

BASE = "http://candidate-scraper-ingress:8010"
FIXTURE_ORIGIN = os.environ.get("CAPTURE_FIXTURE_ORIGIN", "http://flare-origin.test").rstrip("/")
FIXTURE_URL = f"{FIXTURE_ORIGIN}/scrape-fixture"
MAX_RESPONSE_BYTES = 256 * 1024
BROWSER_SOURCES = frozenset({"playwright", "browser-svc"})


def _post_json(path: str, payload: dict, *, timeout: int = 30) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) > 8192:
        raise RuntimeError("ingress test request exceeded its size bound")
    request = Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("scraper ingress response exceeded expected bounds")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("scraper ingress returned a non-object response")
        return result


def _validate_fixture_origin() -> None:
    parsed = urlsplit(FIXTURE_ORIGIN)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("capture fixture origin has an invalid port") from error
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or not parsed.hostname.endswith(".test")
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("capture fixture origin must be a plain HTTP .test origin")


def _require_browser_scrape(response: dict) -> None:
    """Require both fixture output and explicit browser-tier provenance."""
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("forced browser scrape returned no data object")
    markdown = data.get("markdown")
    if (
        response.get("success") is not True
        or not isinstance(markdown, str)
        or "INGRESS_SCRAPE_PIPELINE_OK" not in markdown
        or data.get("source") not in BROWSER_SOURCES
    ):
        raise RuntimeError("POST /scrape force_browser did not traverse browser capture")


def main() -> None:
    _validate_fixture_origin()
    with urlopen(f"{BASE}/health", timeout=5) as response:
        raw_health = response.read(4097)
        if len(raw_health) > 4096:
            raise RuntimeError("scraper ingress health response exceeded its bound")
        if response.status != 200 or json.loads(raw_health).get("status") != "ok":
            raise RuntimeError("scraper UDS ingress health is not fully ready")

    scraped = _post_json(
        "/scrape",
        {"url": FIXTURE_URL, "lightweight_only": True, "ignore_robots_txt": True},
    )
    markdown = scraped.get("data", {}).get("markdown", "")
    if (
        scraped.get("success") is not True
        or not isinstance(markdown, str)
        or "INGRESS_SCRAPE_PIPELINE_OK" not in markdown
    ):
        raise RuntimeError("POST /scrape did not return fixture content")

    browser_scrape = _post_json(
        "/scrape",
        {"url": FIXTURE_URL, "force_browser": True, "ignore_robots_txt": True},
        timeout=75,
    )
    _require_browser_scrape(browser_scrape)

    metadata = _post_json("/scrape/meta", {"url": FIXTURE_URL})
    if (
        metadata.get("success") is not True
        or metadata.get("title") != "Protected Ingress Fixture"
        or "Metadata returned through the scraper ingress pipeline."
        not in (metadata.get("description") or "")
    ):
        raise RuntimeError("POST /scrape/meta did not return fixture metadata")

    request = Request(f"{BASE}/admin", method="GET")
    try:
        urlopen(request, timeout=5)
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError("unexpected ingress rejection status") from error
    else:
        raise RuntimeError("scraper ingress exposed an unexpected route")
    print("scraper_http_ingress=pass scrape=pass meta=pass")


if __name__ == "__main__":
    main()
