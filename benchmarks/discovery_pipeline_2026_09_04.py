"""Compare streamed discovery with the pre-#622 gather barrier.

The benchmark uses the real discovery functions and deterministic async
clients.  Pass ``--baseline-worktree`` to run the same fixture in the #624
worktree in a subprocess, keeping the comparison on the pre-streaming module.
No network or service process is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

QUERY_DELAYS = {"q0": 0.04, "q1": 0.12, "q2": 0.08}
SCRAPE_DELAY = 0.05
URL_COUNT = 3


class FixtureSearch:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, query: str, **_kwargs: Any) -> tuple[list[dict], str]:
        self.calls.append(query)
        await asyncio.sleep(QUERY_DELAYS[query])
        return (
            [
                {
                    "url": f"https://{query}.example/source-{index}",
                    "title": f"{query} source {index}",
                    "description": f"fixture result {index}",
                }
                for index in range(URL_COUNT)
            ],
            "healthy",
        )


class FixtureScraper:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scrape_with_fallback(self, url: str, **_kwargs: Any) -> dict:
        self.calls.append(url)
        await asyncio.sleep(SCRAPE_DELAY)
        return {
            "success": True,
            "data": {"markdown": f"fixture markdown for {url}", "source": "fixture"},
        }


async def run_fixture(mode: str) -> dict[str, Any]:
    from agent.research import discovery

    search = FixtureSearch()
    scraper = FixtureScraper()
    start = time.perf_counter()
    if mode == "streamed":
        result = await discovery._run_multi_query_discover_and_scrape(
            queries=list(QUERY_DELAYS),
            urls=None,
            searxng=search,
            scraper=scraper,
        )
    elif mode == "gather":
        # This is the pre-#622 shape from the base #624 worktree: all searches
        # settle before the shared scraper helper receives their union.
        search_results = await asyncio.gather(
            *(
                search.search(query, limit=10, raise_on_rate_limit=True)
                for query in QUERY_DELAYS
            )
        )
        results = [item for result, _health in search_results for item in result]
        result = {
            "artifacts": await discovery._scrape_urls(
                [item["url"] for item in results], scraper, min_sources=3
            )
        }
    else:
        raise ValueError(f"unknown benchmark mode: {mode}")
    return {
        "mode": mode,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        "search_calls": len(search.calls),
        "scrape_calls": len(scraper.calls),
        "artifacts": len(result["artifacts"]),
    }


def _run_current(runs: int) -> list[dict[str, Any]]:
    return [asyncio.run(run_fixture("streamed")) for _ in range(runs)]


def _run_baseline_subprocess(worktree: Path, runs: int) -> list[dict[str, Any]]:
    script = Path(__file__).resolve()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(worktree / "agent-svc"), str(worktree / "scraper-svc"), str(worktree)]
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--mode",
            "baseline-child",
            "--runs",
            str(runs),
            "--json",
        ],
        cwd=worktree,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-worktree", type=Path)
    parser.add_argument(
        "--mode", choices=["streamed", "baseline-child"], default="streamed"
    )
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.mode == "baseline-child":
        output = [asyncio.run(run_fixture("gather")) for _ in range(args.runs)]
    else:
        streamed = _run_current(args.runs)
        baseline = (
            _run_baseline_subprocess(args.baseline_worktree, args.runs)
            if args.baseline_worktree
            else []
        )
        output = {
            "streamed": streamed,
            "baseline_gather": baseline,
            "median_ms": {
                "streamed": round(
                    statistics.median(item["elapsed_ms"] for item in streamed), 2
                ),
                "baseline_gather": (
                    round(statistics.median(item["elapsed_ms"] for item in baseline), 2)
                    if baseline
                    else None
                ),
            },
        }
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else output)


if __name__ == "__main__":
    main()
