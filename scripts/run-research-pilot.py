#!/usr/bin/env python3
"""Run bounded real research through configured search, scraper and LiteLLM services."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from agent.barrier_guard import is_block_flagged
from agent.experimental.model_transport import ReviewTransport
from agent.experimental.query_construction import CapturedSource
from agent.experimental.query_runner import run_query
from agent.scraper_client import ScraperClient
from agent.searxng_client import SearXNGClient


async def main(query: str, output: Path) -> None:
    # Require explicit service configuration and reserve a new output directory
    # before any network/provider work. Never overwrite a prior result.
    llm_url = os.environ["LLM_BASE_URL"]
    search_url = os.environ["SEARXNG_URL"]
    scraper_url = os.environ["SCRAPER_URL"]
    output.mkdir(parents=True, exist_ok=False)
    search_client = SearXNGClient(search_url)
    scraper = ScraperClient(scraper_url)
    usage = []

    async def search(question, limit):
        rows, health = await search_client.search(
            question, limit=limit, raise_on_rate_limit=True
        )
        if health.empty_result:
            raise ValueError("search returned no usable sources")
        return tuple(dict.fromkeys(row["url"] for row in rows))[:limit]

    async def acquire(url):
        value = await scraper.scrape(url, lightweight_only=True)
        if not value.get("success") or is_block_flagged(value) or value.get("warning"):
            raise ValueError("source acquisition failed or was qualified")
        text = (value.get("data") or {}).get("markdown")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("source acquisition returned no text")
        return CapturedSource(
            url, text, datetime.now(UTC).isoformat().replace("+00:00", "Z")
        )

    try:
        async with httpx.AsyncClient() as client:
            transport = ReviewTransport(
                client, base_url=llm_url, api_key=os.environ.get("LLM_API_KEY", "")
            )

            async def complete(request):
                if len(usage) >= 64:
                    raise ValueError("model call budget exhausted")
                receipt = {
                    "requested_model": request.requested_model,
                    "status": "pending",
                    "resolved_model": None,
                    "input_tokens": None,
                    "output_tokens": None,
                }
                usage.append(receipt)
                try:
                    reply = await transport(request)
                except BaseException:
                    receipt["status"] = "failed_or_cancelled"
                    raise
                receipt.update(
                    status="received",
                    resolved_model=reply.resolved_model,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    raw_content_digest=reply.raw_content_digest,
                )
                return reply

            result = await run_query(
                query,
                search=search,
                acquire=acquire,
                complete=complete,
                scope_id="local-pilot",
                model="local",
            )
        for report in result.reports:
            (output / f"{report.artifact.layer}.md").write_bytes(report.body)
        (output / "knowledge.json").write_bytes(result.knowledge_bytes)
        # Manifest is written last; partial writes never create a complete result.
        (output / "manifest.json").write_bytes(result.manifest_bytes)
        print(f"Model-reviewed experimental reports: {output.resolve()}")
        print(
            "Local files only; not a retained publication or comparative-quality result."
        )
    finally:
        (output / "usage.json").write_text(json.dumps(usage, indent=2) + "\n")
        await search_client.close()
        await scraper.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("output", type=Path, help="New directory for this run")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.query, args.output))
    except Exception:
        parser.exit(
            1,
            "Research did not complete; no successful result is claimed. Check configured services and retained usage metadata.\n",
        )
