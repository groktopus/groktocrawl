#!/usr/bin/env python3
"""Freeze one real search and scrape every returned URL for a Jev holdout.

This is an experiment collector, not a product path. One invocation spends one
search request; ``scrape`` only replays the frozen result list.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests


def _base_url() -> str:
    direct = os.getenv("GROKTOCRAWL_API_URL") or os.getenv("FIRECRAWL_API_URL")
    if direct:
        return direct.rstrip("/")
    raise RuntimeError("Set GROKTOCRAWL_API_URL or FIRECRAWL_API_URL")


def _headers() -> dict[str, str]:
    key = os.getenv("GROKTOCRAWL_API_KEY") or os.getenv("API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _write_new(path: Path, payload: dict) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")


def search(query: str, output: Path) -> None:
    response = requests.post(
        _base_url() + "/v2/search",
        json={"query": query, "limit": 40},
        headers=_headers(),
        timeout=90,
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    hits = data.get("web", []) if isinstance(data, dict) else []
    seen = set()
    results = []
    for hit in hits:
        url = hit.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "id": f"url-{len(results) + 1:02}",
                "url": url,
                "title": hit.get("title", ""),
                "description": hit.get("description", ""),
                "rank": len(results) + 1,
            }
        )
    _write_new(output, {"research_question": query, "results": results})
    print(f"search_requests=1 unique_results={len(results)}")


def scrape(index_path: Path, output_dir: Path) -> None:
    index = json.loads(index_path.read_text())
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    headers = _headers()
    for hit in index["results"]:
        path = output_dir / f"large-scrape-{hit['id']}.json"
        if path.exists():
            continue
        try:
            response = requests.post(
                _base_url() + "/v2/scrape",
                json={"url": hit["url"], "formats": ["markdown"]},
                headers=headers,
                timeout=90,
            )
            payload = {
                "id": hit["id"],
                "url": hit["url"],
                "http_status": response.status_code,
                "result": response.json(),
            }
        except (requests.RequestException, ValueError) as exc:
            payload = {
                "id": hit["id"],
                "url": hit["url"],
                "http_status": None,
                "transport_error": type(exc).__name__,
            }
        _write_new(path, payload)
        result = payload.get("result") or {}
        print(
            hit["id"], payload["http_status"], bool(result.get("success")), flush=True
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    search_cmd = commands.add_parser("search")
    search_cmd.add_argument("--query", required=True)
    search_cmd.add_argument("--output", type=Path, required=True)
    scrape_cmd = commands.add_parser("scrape")
    scrape_cmd.add_argument("--index", type=Path, required=True)
    scrape_cmd.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "search":
        search(args.query, args.output)
    else:
        scrape(args.index, args.output_dir)


if __name__ == "__main__":
    main()
