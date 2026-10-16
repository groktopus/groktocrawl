"""Tests for the redacted W11 live HTTP compatibility capture."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "capture_w11_http_compatibility",
    ROOT / "scripts" / "capture_w11_http_compatibility.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _handler(*, invalid_status: int = 400):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = dict(request.url.params)
        if request.method == "POST":
            query = dict(httpx.QueryParams(request.content.decode()))
        if path == "/config":
            return httpx.Response(
                200,
                json={
                    "categories": [],
                    "engines": [],
                    "locales": {},
                    "safe_search": 1,
                    "version": "0.5.0",
                },
            )
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/healthz":
            return httpx.Response(200, text="ok")
        if query.get("pageno") == "0":
            return httpx.Response(invalid_status, json={"error": "invalid"})
        if query.get("format") == "json":
            return httpx.Response(
                200,
                json={
                    "answers": [],
                    "corrections": [],
                    "engines": ["wikipedia"],
                    "infoboxes": [],
                    "number_of_results": 0,
                    "query": "w11 compatibility probe",
                    "results": [],
                    "suggestions": [],
                    "unresponsive_engines": [],
                },
            )
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    return handle


def _capture(handler) -> dict:
    with httpx.Client(
        base_url="http://isolated.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        return module.capture_compatibility(
            client,
            expected_version="0.5.0",
            source_revision="a" * 40,
            image_digest="sha256:" + "b" * 64,
            target_label="isolated-test",
        )


def test_compatibility_capture_covers_all_routes_without_retaining_endpoint():
    record = _capture(_handler())

    assert record["valid"]
    assert record["issues"] == []
    assert set(record["probes"]) == {
        "config_get",
        "health_get",
        "healthz_get",
        "invalid_pageno_get",
        "root_html_get",
        "root_json_get",
        "root_json_post",
        "search_json_get",
        "search_json_post",
    }
    encoded = json.dumps(record)
    assert "isolated.invalid" not in encoded
    assert "w11 compatibility probe" not in encoded
    assert record["query_scope"]["result_content_retained"] is False


def test_compatibility_capture_fails_invalid_status_drift():
    record = _capture(_handler(invalid_status=422))

    assert not record["valid"]
    assert record["issues"] == [
        "invalid pagination did not use SearXNG-compatible HTTP 400"
    ]
