"""Compare the production loop against the pre-optimization loop with delayed I/O.

PYTHONPATH=agent-svc:scraper-svc:. python benchmarks/research_final_synthesis.py
Loads only the committed baseline loop, with every external dependency replaced.
"""

import asyncio
import json
import statistics
import subprocess
import time
import types
from unittest.mock import AsyncMock

from agent.research import loop

BASE = "ca591199cfd05144e45d2372afd659f2e86e6327"


async def probe(module, streaming):
    calls = 0
    first_token = None

    class Search:
        async def search(self, query, **kwargs):
            await asyncio.sleep(0.01)
            return ([{"url": f"https://example.test/{query}"}], None)

        close = AsyncMock()

    class Scraper:
        async def scrape_with_fallback(self, url, **kwargs):
            await asyncio.sleep(0.01)
            return {"success": True, "data": {"markdown": f"evidence {url}"}}

        close = AsyncMock()

    class LLM:
        async def generate(self, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.06)
            return "final answer [1] [2]"

        async def generate_stream(self, **kwargs):
            value = await self.generate(**kwargs)
            yield {"type": "token", "content": value}
            yield {"type": "done", "full_content": value}

        close = AsyncMock()

    async def gaps(*args, **kwargs):
        await asyncio.sleep(0.02)
        return ["followup"]

    module.SearXNGClient = lambda *a, **k: Search()
    module.ScraperClient = lambda *a, **k: Scraper()
    module.LLMClient = lambda *a, **k: LLM()
    module._generate_research_plan = AsyncMock(
        return_value={"focused_queries": ["initial"], "research_strategy": "focused"}
    )
    module._detect_gaps = gaps
    start = time.perf_counter()
    events = []
    async for event in module._run_research_events(
        "q", llm_model="fixture", stream_tokens=streaming
    ):
        events.append(event)
        if event["type"] == "token" and first_token is None:
            first_token = (time.perf_counter() - start) * 1000
    final = events[-1]
    assert set(final["sources"]) == {
        "https://example.test/initial",
        "https://example.test/followup",
    }
    return {
        "total_ms": (time.perf_counter() - start) * 1000,
        "first_token_ms": first_token,
        "synthesis_calls": calls,
        "tokens_match_final": "".join(
            e["content"] for e in events if e["type"] == "token"
        )
        == final["result"],
    }


async def main():
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:agent-svc/agent/research/loop.py"], text=True
    )
    baseline = types.ModuleType("agent.research.baseline_loop")
    exec(compile(source, "baseline_loop.py", "exec"), baseline.__dict__)
    output = []
    for streaming in (False, True):
        for name, module in (("baseline", baseline), ("final_only", loop)):
            samples = [await probe(module, streaming) for _ in range(10)]
            timings = sorted(s["total_ms"] for s in samples)
            output.append(
                {
                    "mode": name,
                    "streaming": streaming,
                    "p50_ms": round(statistics.median(timings), 2),
                    "p95_ms": round(timings[-1], 2),
                    "first_token_p50_ms": round(
                        statistics.median(s["first_token_ms"] for s in samples), 2
                    )
                    if streaming
                    else None,
                    "synthesis_calls": samples[0]["synthesis_calls"],
                    "tokens_match_final": samples[0]["tokens_match_final"]
                    if streaming
                    else None,
                }
            )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
