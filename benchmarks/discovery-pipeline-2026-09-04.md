# Discovery pipeline benchmark (2026-09-04)

The benchmark compares the current bounded streamed acquisition loop with the
pre-#622 gather barrier from `codex/perf-624-source-reuse`. Run it with:

```bash
PYTHONPATH=agent-svc:scraper-svc:. \
  /Volumes/tank01/magnus/git/groktocrawl/.venv/bin/python \
  benchmarks/discovery_pipeline_2026_09_04.py \
  --baseline-worktree /private/tmp/groktocrawl-perf-624 --runs 7 --json
```

The fixture runs three concurrent searches with delays of 40 ms, 120 ms, and
80 ms. Each search returns three URLs, and each scrape takes 50 ms. The
streamed loop admits the first resolved query in plan order and overlaps its
scrapes with the remaining searches. The baseline waits for all searches,
then starts scraping.

| Mode | Median elapsed | Scrape calls | Returned artifacts |
| --- | ---: | ---: | ---: |
| Streamed acquisition | 121.90 ms | 3 | 3 |
| Gather barrier baseline | 172.87 ms | 5 | 3 |

This fixture shows a 50.97 ms (29.5%) median reduction while retaining
deterministic query-order admission. The tradeoff is deliberate: a fast later
query cannot spend the bounded acquisition budget until earlier planned query
positions resolve. The result is therefore bounded and reproducible, but a
slow first query can still delay admission even when later search results are
ready.

The measurement is synthetic and in-process. It excludes network variance,
scraper service startup, browser rendering, provider rate limits, and LLM
latency. It demonstrates overlap and admission behavior rather than a
production throughput or tail-latency guarantee.
