"""Measure cold launches against isolated contexts on a reused browser.

Uses the production stealth helpers and an intercepted, in-memory page. No
origin network calls are made. This isolates setup cost, not production scrape
latency; domain-keyed reuse is required by CloakBrowser fingerprint settings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper-svc"))

HTML = (
    "<html><body><article><h1>Fixture</h1>"
    + "Evidence paragraph. " * 100
    + "</article></body></html>"
)
URL = "https://lifecycle-fixture.example/article"


def summary(samples: list[float]) -> dict:
    ordered = sorted(samples)

    def percentile(fraction):
        rank = fraction * (len(ordered) - 1)
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 6)

    return {"p50": percentile(0.5), "p95": percentile(0.95), "samples": samples}


async def measure(runs: int) -> dict:
    from playwright.async_api import async_playwright
    from scraper.stealth import create_stealth_browser, create_stealth_context

    async def visit(browser, cloakbrowser):
        context = await create_stealth_context(browser, cloakbrowser=cloakbrowser)
        try:
            await context.route(
                "**/*", lambda route: route.fulfill(body=HTML, content_type="text/html")
            )
            page = await context.new_page()
            await page.goto(URL, wait_until="domcontentloaded")
            content = await page.locator("article").inner_text()
            return hashlib.sha256(content.encode()).hexdigest()
        finally:
            await context.close()

    output: dict = {"runs": runs, "fixture": "intercepted-static-page", "modes": {}}
    hashes: set[str] = set()
    for mode in ("cold", "reuse"):
        total, setup = [], []
        engines = set()
        if mode == "cold":
            for _ in range(runs):
                started = time.monotonic()
                async with async_playwright() as p:
                    browser, cloak = await create_stealth_browser(p, URL)
                    setup.append(time.monotonic() - started)
                    engines.add("cloakbrowser" if cloak else "stock")
                    try:
                        hashes.add(await visit(browser, cloak))
                    finally:
                        await browser.close()
                total.append(time.monotonic() - started)
        else:
            async with async_playwright() as p:
                started = time.monotonic()
                browser, cloak = await create_stealth_browser(p, URL)
                output["reuse_initial_launch_seconds"] = time.monotonic() - started
                engines.add("cloakbrowser" if cloak else "stock")
                try:
                    for _ in range(runs):
                        started = time.monotonic()
                        hashes.add(await visit(browser, cloak))
                        total.append(time.monotonic() - started)
                finally:
                    await browser.close()
        output["modes"][mode] = {
            "engines": sorted(engines),
            "total_seconds": summary(total),
        }
        if setup:
            output["modes"][mode]["setup_seconds"] = summary(setup)
    output["identical_content"] = len(hashes) == 1
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    result = json.dumps(asyncio.run(measure(args.runs)), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result)


if __name__ == "__main__":
    main()
