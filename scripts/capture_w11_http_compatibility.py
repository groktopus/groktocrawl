#!/usr/bin/env python3
"""Capture a redacted live SearXNG-compatibility preflight for W11."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SEARCH_KEYS = {
    "answers",
    "corrections",
    "engines",
    "infoboxes",
    "number_of_results",
    "query",
    "results",
    "suggestions",
    "unresponsive_engines",
}
CONFIG_KEYS = {"categories", "engines", "locales", "safe_search", "version"}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _summary(name: str, response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    result: dict[str, Any] = {
        "name": name,
        "status": response.status_code,
        "content_type": content_type,
        "response_bytes": len(response.content),
        "response_sha256": _sha256(response.content),
    }
    if content_type == "application/json":
        payload = response.json()
        result["json_type"] = type(payload).__name__
        if isinstance(payload, dict):
            result["keys"] = sorted(payload)
            if isinstance(payload.get("results"), list):
                result["result_count"] = len(payload["results"])
    return result


def capture_compatibility(
    client: httpx.Client,
    *,
    expected_version: str,
    source_revision: str,
    image_digest: str,
    target_label: str,
) -> dict[str, Any]:
    query = {
        "q": "w11 compatibility probe",
        "format": "json",
        "engines": "wikipedia",
        "pageno": "1",
        "safesearch": "1",
    }
    responses = {
        "root_html_get": client.get("/"),
        "root_json_get": client.get("/", params=query),
        "search_json_get": client.get("/search", params=query),
        "root_json_post": client.post("/", data=query),
        "search_json_post": client.post("/search", data=query),
        "config_get": client.get("/config"),
        "health_get": client.get("/health"),
        "healthz_get": client.get("/healthz"),
        "invalid_pageno_get": client.get(
            "/search", params={**query, "pageno": "0"}
        ),
    }
    probes = {name: _summary(name, response) for name, response in responses.items()}
    issues: list[str] = []

    html = responses["root_html_get"]
    if html.status_code != 200 or "text/html" not in html.headers.get(
        "content-type", ""
    ):
        issues.append("GET / did not return the HTML compatibility surface")

    for name in (
        "root_json_get",
        "search_json_get",
        "root_json_post",
        "search_json_post",
    ):
        response = responses[name]
        if response.status_code != 200:
            issues.append(f"{name} returned HTTP {response.status_code}")
            continue
        try:
            payload = response.json()
        except ValueError:
            issues.append(f"{name} did not return JSON")
            continue
        if not isinstance(payload, dict) or not set(payload) >= SEARCH_KEYS:
            issues.append(f"{name} lacks required SearXNG search fields")
        elif payload.get("query") != query["q"]:
            issues.append(f"{name} did not preserve the submitted query")

    config = responses["config_get"]
    if config.status_code != 200:
        issues.append(f"GET /config returned HTTP {config.status_code}")
    else:
        try:
            config_payload = config.json()
        except ValueError:
            issues.append("GET /config did not return JSON")
        else:
            if not isinstance(config_payload, dict) or not set(
                config_payload
            ) >= CONFIG_KEYS:
                issues.append("GET /config lacks required SearXNG configuration fields")
            elif config_payload.get("version") != expected_version:
                issues.append("GET /config version differs from the frozen release")

    health = responses["health_get"]
    if health.status_code != 200:
        issues.append(f"GET /health returned HTTP {health.status_code}")
    healthz = responses["healthz_get"]
    if healthz.status_code != 200 or healthz.text.strip().casefold() != "ok":
        issues.append("GET /healthz did not return the readiness token")
    invalid = responses["invalid_pageno_get"]
    if invalid.status_code != 400:
        issues.append("invalid pagination did not use SearXNG-compatible HTTP 400")

    frozen = {
        "schema_version": "enterprise-evaluation/w11-http-compatibility/1",
        "target_label": target_label,
        "slopsearx": {
            "version": expected_version,
            "source_revision": source_revision,
            "image_digest": image_digest,
        },
        "query_scope": {"engines": ["wikipedia"], "result_content_retained": False},
        "probes": probes,
        "valid": not issues,
        "issues": issues,
    }
    return {
        **frozen,
        "configuration_sha256": _sha256(
            json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
        ),
        "captured_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--expected-version", default="0.5.0")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        record = capture_compatibility(
            client,
            expected_version=args.expected_version,
            source_revision=args.source_revision,
            image_digest=args.image_digest,
            target_label=args.target_label,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"captured W11 HTTP compatibility {record['configuration_sha256']} "
        f"valid={record['valid']}"
    )
    return 0 if record["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
