"""POST /v2/agent ``max_credits`` must bound the research loop.

Regression tests for the accepted-but-ignored ``max_credits`` parameter
found during mission validation of issues #587/#588/#589: the request
model accepted it and echoed it into the job payload, but nothing in
the worker or research code consumed it, so a job could scrape an
unbounded number of sources regardless of the requested budget.

Semantics (1 credit ≈ one successfully scraped source page, matching
the crawl engine's per-page credit accounting): when ``max_credits``
is set, discovery stops admitting new scrapes once the credit count is
reached. The default (None) keeps today's unbounded behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_scraper(markdown: str = "content") -> MagicMock:
    scraper = MagicMock()
    scraper.base_url = "http://scraper"

    async def _scrape(url: str, **kwargs) -> dict:
        return {"success": True, "data": {"markdown": markdown, "source": "test"}}

    scraper.scrape_with_fallback = AsyncMock(side_effect=_scrape)
    scraper.scrape = AsyncMock(side_effect=_scrape)
    scraper.close = AsyncMock()
    return scraper


class TestRunResearchMaxCredits:
    """run_research honors max_credits as a scrape-count budget."""

    @pytest.mark.asyncio
    async def test_max_credits_bounds_scraped_sources(self):
        """A 2-credit budget stops discovery at 2 scraped sources."""
        from agent.research.loop import run_research

        searxng = MagicMock()

        async def _search(query: str, **kwargs):
            results = [
                {"url": f"https://s{i}.com", "title": f"S{i}", "description": "d"}
                for i in range(10)
            ]
            return results, MagicMock()

        searxng.search = AsyncMock(side_effect=_search)
        searxng.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(return_value="answer [1]")
        llm.close = AsyncMock()

        scraper = _make_scraper()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="question",
                llm_model="m",
                max_searches_per_request=1,
                max_credits=2,
            )

        assert result["result"] == "answer [1]"
        # Budget honored: exactly 2 pages scraped even though 10 candidates
        # were discovered.
        assert len(result["source_details"]) == 2

    @pytest.mark.asyncio
    async def test_default_unbounded_keeps_full_discovery(self):
        """Without max_credits the pipeline scrapes as before."""
        from agent.research.loop import run_research

        searxng = MagicMock()

        async def _search(query: str, **kwargs):
            results = [
                {"url": f"https://s{i}.com", "title": f"S{i}", "description": "d"}
                for i in range(6)
            ]
            return results, MagicMock()

        searxng.search = AsyncMock(side_effect=_search)
        searxng.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(return_value="answer [1]")
        llm.close = AsyncMock()

        scraper = _make_scraper()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="question",
                llm_model="m",
                max_searches_per_request=1,
            )

        assert result["result"] == "answer [1]"
        # Unchanged default behavior: discovery fills its min_sources quota
        # (plus speculative in-flight scrapes), never fewer.
        assert len(result["source_details"]) >= 3
        assert len(scraper.scrape_with_fallback.call_args_list) >= 3


class TestAgentWorkerMaxCreditsWiring:
    """The agent route/worker must thread max_credits to run_research."""

    @pytest.mark.asyncio
    async def test_route_passes_max_credits_to_worker(self):
        """POST /v2/agent forwards body.max_credits into the background job."""
        from unittest.mock import MagicMock

        from agent.models import AgentRequest
        from agent.routes.agent import create_agent

        request = MagicMock()
        rate_limiter = MagicMock()
        rate_limiter.limit = 100
        rate_limiter.window = 60
        rate_limiter.check = AsyncMock(return_value=(True, 100))
        state = MagicMock()
        state.rate_limiter = rate_limiter
        state.max_searches_per_request = 5
        state.research_memory = MagicMock()
        state.research_memory.query = AsyncMock(return_value={"hit": False})
        state.job_store = MagicMock()
        state.job_store.create_job.return_value = "job-credits"
        state.task_tracker = MagicMock()
        request.app.state = state
        request.headers = MagicMock()
        request.headers.get.return_value = None
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        body = AgentRequest(prompt="hello", stream=False, max_credits=7)
        response = MagicMock()
        response.headers = {}

        with (
            patch(
                "agent.routes.agent._lookup_agent_cache",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "agent.routes.agent._handle_agent_streaming",
                new=AsyncMock(return_value=None),
            ),
            patch("agent.worker._process_agent_async") as process,
        ):
            await create_agent(request, body, response)

        assert process.call_args.kwargs["max_credits"] == 7

    @pytest.mark.asyncio
    async def test_worker_passes_max_credits_to_run_research(self):
        """_process_agent_async forwards max_credits into run_research."""
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(
            return_value={"result": "ok", "sources": [], "source_details": []}
        )
        mock_research_memory = MagicMock()
        mock_research_memory.query = AsyncMock(return_value={"hit": False})
        mock_research_memory.store = AsyncMock(return_value="mem-1")
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", AsyncMock()),
            patch("agent.worker.METRICS", mock_metrics),
            patch(
                "agent.worker.load_settings",
                return_value=MagicMock(
                    valkey_host="valkey",
                    valkey_port=6379,
                    valkey_db=0,
                    crawl_max_duration_seconds=1800,
                    crawl_idle_timeout_seconds=300,
                ),
            ),
        ):
            await _process_agent_async(
                job_id="job-credits",
                prompt="p",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="k",
                llm_model="m",
                searxng_url="http://searxng",
                scraper_url="http://scraper",
                max_credits=4,
            )

        assert mock_run_research.call_args.kwargs["max_credits"] == 4

    @pytest.mark.asyncio
    async def test_swr_refresh_closure_honors_max_credits(self):
        """The sync-path stale-while-revalidate refresh threads max_credits.

        Without this, a background refresh launched after serving the stale
        artifact would run with an unbounded budget, defeating the cap the
        request explicitly set (PR #597 review finding).
        """
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(
            return_value={"result": "ok", "sources": [], "source_details": []}
        )
        mock_research_memory = MagicMock()
        mock_research_memory.query = AsyncMock(
            return_value={
                "hit": True,
                "freshness": "stale",
                "swr_eligible": True,
                "artifact": {"artifact": "stale text", "sources": []},
                "age_hours": 8.0,
                "similarity": 0.9,
                "memory_id": "mem-stale",
            }
        )
        started_refresh: dict = {}

        def _start_refresh(key, factory):
            started_refresh["factory"] = factory
            return MagicMock(add_done_callback=MagicMock())

        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", AsyncMock()),
            patch("agent.worker.METRICS", mock_metrics),
            patch(
                "agent.worker.load_settings",
                return_value=MagicMock(
                    valkey_host="valkey",
                    valkey_port=6379,
                    valkey_db=0,
                    crawl_max_duration_seconds=1800,
                    crawl_idle_timeout_seconds=300,
                ),
            ),
            patch.object(
                mock_research_memory, "start_refresh", side_effect=_start_refresh
            ),
            patch(
                "agent.worker.refresh_research_memory",
                new=AsyncMock(
                    return_value={
                        "result": "ok",
                        "sources": [],
                        "source_details": [],
                    }
                ),
            ) as refresh_mem,
        ):
            await _process_agent_async(
                job_id="job-swr",
                prompt="p",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="k",
                llm_model="m",
                searxng_url="http://searxng",
                scraper_url="http://scraper",
                stale_while_revalidate=True,
                research_memory=mock_research_memory,
                max_credits=6,
            )

            assert "factory" in started_refresh

            # Invoke the captured factory INSIDE the patch context: the
            # closure resolves refresh_research_memory at call time, so a
            # late invocation must still observe the patched binding and
            # thread max_credits into it — the request's budget must not
            # be silently dropped to None (worker.py binds
            # refresh_research_memory at module scope; that is the name
            # the closure resolves).
            await started_refresh["factory"]()

        assert refresh_mem.await_count == 1
        assert refresh_mem.await_args.kwargs["max_credits"] == 6


class TestMaxCreditsGuardRemoval:
    """loop.py must not defend against max_credits <= 0.

    The request model enforces ``max_credits: ge=1`` (0/negative are 422s),
    so the in-loop ``max_credits is not None and max_credits <= 0`` guards
    are unreachable defensive branches that imply a reachable zero-budget
    state. Their presence invites callers to rely on a contract the API
    does not offer; removal keeps loop.py trusting its validated inputs.
    """

    def test_run_research_events_has_no_zero_credit_guard(self):
        import inspect

        from agent.research import loop

        source = inspect.getsource(loop._run_research_events)
        assert "max_credits <= 0" not in source, (
            "_run_research_events still carries an unreachable max_credits<=0 "
            "guard — the API model enforces ge=1, so drop the dead branch"
        )


class TestRunResearchBudgetExhaustion:
    """An exhausted credit budget skips pass-2 discovery and re-synthesis."""

    @pytest.mark.asyncio
    async def test_exhausted_budget_skips_gap_detection_and_pass2(self):
        """Pass 1 consuming the full budget ends the loop without pass 2."""
        from agent.research.loop import run_research

        searxng = MagicMock()

        async def _search(query: str, **kwargs):
            results = [
                {"url": f"https://s{i}.com", "title": f"S{i}", "description": "d"}
                for i in range(10)
            ]
            return results, MagicMock()

        searxng.search = AsyncMock(side_effect=_search)
        searxng.close = AsyncMock()

        llm = MagicMock()
        calls: list[str] = []

        async def _generate(**kwargs) -> str:
            stage = kwargs.get("stage") or kwargs.get("user_prompt", "")
            if kwargs.get("system_prompt") and "gap" in str(stage).lower():
                calls.append("gap")
            else:
                calls.append(f"gen:{stage}")
            return (
                '{"focused_queries": ["q1"], "research_strategy": "deep"}'
                if not calls[:-1] and "plan" in str(kwargs.get("stage", ""))
                else "answer [1]"
            )

        llm.generate = AsyncMock(side_effect=_generate)
        llm.close = AsyncMock()

        scraper = _make_scraper()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch(
                "agent.research.loop._detect_gaps",
                new=AsyncMock(return_value=["gap-topic"]),
            ) as detect_gaps,
        ):
            result = await run_research(
                prompt="question",
                llm_model="m",
                max_searches_per_request=1,
                search_type="deep",
                max_credits=3,
            )

        assert result["result"] == "answer [1]"
        # Budget fully consumed by pass 1 (min_sources=3 of 3 candidates):
        # no gap-detection LLM call and exactly ONE synthesis call — pass 2
        # must not run redundant searches/synthesis on an exhausted budget.
        detect_gaps.assert_not_awaited()
        synthesis_calls = [c for c in calls if c.startswith("gen:") and "plan" not in c]
        assert len(synthesis_calls) >= 1
        assert len(calls) <= 2  # planning + single synthesis only
