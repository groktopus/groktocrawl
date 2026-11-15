"""Tests for agent-svc/agent/worker.py — async job processing functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestProcessAgentAsync:
    """Test _process_agent_async — the main agent job handler."""

    async def _run_worker_with(
        self, result: dict, *, job_id: str = "test-job-helper"
    ) -> dict:
        """Run _process_agent_async with a canned *result* from run_research.

        Returns mocks keyed by name: ``store``, ``research_memory``,
        ``deliver_webhook``.
        """
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(return_value=result)
        mock_research_memory = MagicMock()
        mock_research_memory.query = AsyncMock(return_value={"hit": False})
        mock_research_memory.store = AsyncMock(return_value="mem-helper")
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
                job_id=job_id,
                prompt="Test prompt",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="test-key",
                llm_model="gpt-4o-mini",
                searxng_url="http://searxng:8080",
                scraper_url="http://scraper:8001",
                research_memory=mock_research_memory,
            )

        return {
            "store": mock_store,
            "research_memory": mock_research_memory,
            "deliver_webhook": mock_deliver_webhook,
        }

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify success path: run_research returns result, job completed,
        webhook delivered, metrics incremented."""
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(
            return_value={"result": "research result", "sources": ["https://a.com"]}
        )
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
                job_id="test-job-1",
                prompt="Test prompt",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="test-key",
                llm_model="gpt-4o-mini",
                searxng_url="http://searxng:8080",
                scraper_url="http://scraper:8001",
            )

        mock_store.complete_job.assert_called_once()
        call_args = mock_store.complete_job.call_args
        assert call_args[0][0] == "test-job-1"
        payload = call_args[0][1]
        assert payload["result"] == "research result"
        assert payload["sources"] == ["https://a.com"]
        mock_deliver_webhook.assert_called_once()
        # Check that metrics were recorded
        assert mock_metrics.counter.call_count >= 2
        assert mock_metrics.histogram.call_count >= 1

    @pytest.mark.asyncio
    async def test_failure(self):
        """Verify failure path: run_research raises, job failed, webhook with error."""
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(side_effect=Exception("Processing error"))
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
                job_id="test-job-fail",
                prompt="Test prompt",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="test-key",
                llm_model="gpt-4o-mini",
                searxng_url="http://searxng:8080",
                scraper_url="http://scraper:8001",
            )

        mock_store.fail_job.assert_called_once_with("test-job-fail", "Processing error")
        mock_deliver_webhook.assert_called_once()
        # Verify webhook was called with "failed" event
        call_args = mock_deliver_webhook.call_args[0]
        assert call_args[1] == "failed"
        assert "error" in call_args[3]

    @pytest.mark.asyncio
    async def test_provider_output_failure_persists_safe_diagnostics(self):
        from agent.exceptions import OutputTruncatedError
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(
            side_effect=OutputTruncatedError(
                details={
                    "finish_reason": "length",
                    "refusal_present": False,
                    "prompt_tokens": 123,
                    "completion_tokens": 8192,
                    "recovery_attempts": 1,
                }
            )
        )
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
                job_id="test-job-provider-truncated",
                prompt="Test prompt",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="test-key",
                llm_model="fixture-model",
                searxng_url="http://searxng:8080",
                scraper_url="http://scraper:8001",
            )

        call = mock_store.fail_job.call_args
        assert call.args == (
            "test-job-provider-truncated",
            "LLM provider stopped before completing the response",
        )
        assert call.kwargs["error_code"] == "LLM_OUTPUT_TRUNCATED"
        assert call.kwargs["error_details"] == {
            "finish_reason": "length",
            "refusal_present": False,
            "prompt_tokens": 123,
            "completion_tokens": 8192,
            "recovery_attempts": 1,
        }
        # Provider diagnostics are retained for status inspection, not sent
        # with the failure webhook payload.
        assert mock_deliver_webhook.call_args.args[3] == {
            "error": "LLM provider stopped before completing the response"
        }

    @pytest.mark.asyncio
    async def test_llm_error_not_cached(self):
        """LLM error results must not pollute the research memory cache (#432)."""
        mocks = await self._run_worker_with(
            {
                "result": "Error: LLM call failed: Connection error",
                "sources": ["https://a.com"],
                "source_details": [
                    {"url": "https://a.com", "title": "A", "source": "..."}
                ],
            },
            job_id="test-job-llm-error",
        )
        mocks["research_memory"].store.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_result_cached(self):
        """Valid results are stored in research memory cache (#432)."""
        mocks = await self._run_worker_with(
            {
                "result": "This is a valid research result.",
                "sources": ["https://a.com"],
                "source_details": [
                    {"url": "https://a.com", "title": "A", "source": "..."}
                ],
            },
            job_id="test-job-success-cache",
        )
        mocks["research_memory"].store.assert_called_once()
        call_args = mocks["research_memory"].store.call_args
        assert call_args.kwargs["artifact"] == "This is a valid research result."

    @pytest.mark.asyncio
    async def test_success_result_with_url_sources_is_cached(self):
        """Keep the legacy admission fallback when rich details are unavailable."""
        mocks = await self._run_worker_with(
            {
                "result": "This is a valid research result.",
                "sources": ["https://a.com"],
            },
            job_id="test-job-url-sources-cache",
        )

        assert mocks["research_memory"].store.call_args.kwargs["sources"] == [
            "https://a.com"
        ]

    @pytest.mark.asyncio
    async def test_default_valkey_url(self):
        """Verify JobStore is constructed with the default VALKEY_URL."""
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_run_research = AsyncMock(return_value={"result": "ok", "sources": []})
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store) as mock_store_cls,
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
                job_id="test-job-url",
                prompt="test",
                urls=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="key",
                llm_model="model",
                searxng_url="http://searxng:8080",
                scraper_url="http://scraper:8001",
            )

        mock_store_cls.assert_called_once_with("redis://valkey:6379/0")


class TestProcessCrawlAsync:
    """Test _process_crawl_async — single URL crawl job handler."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify success: scrape returns markdown, job completed with pages payload."""
        from agent.worker import _process_crawl_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "processing"}  # not cancelled
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "markdown": "# Crawled page",
                    "metadata": {"og": {"title": "Test Title"}},
                    "title": "Page Title",
                },
            }
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            patch("agent.worker._index_page_async", AsyncMock()),
        ):
            await _process_crawl_async(
                job_id="crawl-1",
                url="https://example.com",
                max_pages=10,
                max_depth=2,
                scraper_url="http://scraper:8001",
            )

        # Verify store.complete_job called with pages payload
        mock_store.complete_job.assert_called_once()
        call_args = mock_store.complete_job.call_args[0]
        assert call_args[0] == "crawl-1"
        payload = call_args[1]
        assert payload["completed"] == 1
        assert payload["total"] == 1
        assert payload["pages"][0]["url"] == "https://example.com"
        assert payload["pages"][0]["markdown"] == "# Crawled page"

        # Webhook called 3 times: crawl.started, crawl.page, crawl.completed
        assert mock_deliver_webhook.call_count == 3
        events = [call[0][1] for call in mock_deliver_webhook.call_args_list]
        assert events[0] == "crawl.started"
        assert events[1] == "crawl.page"
        assert events[2] == "crawl.completed"
        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        """Verify failure: scrape raises, job failed, scraper closed."""
        from agent.worker import _process_crawl_async

        mock_store = MagicMock()
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(side_effect=Exception("Crawl error"))
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_crawl_async(
                job_id="crawl-fail",
                url="https://example.com",
                max_pages=10,
                max_depth=2,
                scraper_url="http://scraper:8001",
            )

        # The CrawlEngine now handles start URL scrape failures internally
        # and returns a CrawlResult with errors rather than raising.
        # The crawl completes with 0 pages and error entries.
        mock_store.complete_job.assert_called_once()
        call_args = mock_store.complete_job.call_args[0][1]
        assert call_args["completed"] == 0
        assert len(call_args["errors"]) > 0
        # Webhook called 2 times: crawl.started, then crawl.completed
        assert mock_deliver_webhook.call_count == 2
        events = [call[0][1] for call in mock_deliver_webhook.call_args_list]
        assert events[0] == "crawl.started"
        assert events[1] == "crawl.completed"
        # scraper.close() called in finally
        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_during_crawl(self):
        """Verify cancellation: engine detects cancelled status, preserves it."""
        from agent.worker import _process_crawl_async

        mock_store = MagicMock()
        # Simulate job being cancelled in Redis (as DELETE would set)
        mock_store.get_job.return_value = {"status": "cancelled"}
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "markdown": "# Crawled page",
                    "metadata": {},
                },
            }
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            patch("agent.worker._index_page_async", AsyncMock()),
        ):
            await _process_crawl_async(
                job_id="crawl-cancel",
                url="https://example.com",
                max_pages=10,
                max_depth=2,
                scraper_url="http://scraper:8001",
            )

        # complete_job should NOT be called — cancel_job already set status
        mock_store.complete_job.assert_not_called()
        # Webhook: crawl.started, crawl.page (for start URL), crawl.completed (cancelled)
        assert mock_deliver_webhook.call_count >= 2
        events = [call[0][1] for call in mock_deliver_webhook.call_args_list]
        assert events[0] == "crawl.started"
        # Last event should be crawl.completed (for cancelled status)
        assert events[-1] == "crawl.completed"
        # The cancelled webhook has success=True and data=[] (VAL-PARITY-007)
        # The cancelled status is stored in Redis and retrievable via GET /v2/crawl/{id}
        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_scraper_closed_in_finally(self):
        """Verify scraper.close() is called even on success."""
        from agent.worker import _process_crawl_async

        mock_store = MagicMock()
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            return_value={
                "success": True,
                "data": {"markdown": "# Ok", "metadata": {}},
            }
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            patch("agent.worker._index_page_async", AsyncMock()),
        ):
            await _process_crawl_async(
                job_id="crawl-close",
                url="https://example.com",
                max_pages=10,
                max_depth=2,
                scraper_url="http://scraper:8001",
            )

        mock_scraper_instance.close.assert_called_once()


class TestProcessBatchScrapeAsync:
    """Test _process_batch_scrape_async — multi-URL batch scrape."""

    @pytest.mark.asyncio
    async def test_success_multiple_urls(self):
        """Verify multiple URLs are scraped and results accumulated."""
        from agent.worker import _process_batch_scrape_async

        mock_store = MagicMock()
        mock_store.get_completed.return_value = 2
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "markdown": "# First page",
                        "metadata": {"og": {"title": "First"}},
                    },
                },
                {
                    "success": True,
                    "data": {
                        "markdown": "# Second page",
                        "metadata": {"meta": {"title": "Second"}},
                    },
                },
            ]
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()
        mock_index_batch = AsyncMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            patch("agent.worker._index_batch_async", mock_index_batch),
        ):
            await _process_batch_scrape_async(
                job_id="batch-1",
                urls=["https://a.com", "https://b.com"],
                scraper_url="http://scraper:8001",
            )

        # Both URLs succeeded — increment_completed called twice
        assert mock_store.increment_completed.call_count == 2

        # complete_job gets the result dict from work_fn
        mock_store.complete_job.assert_called_once()
        payload = mock_store.complete_job.call_args[0][1]
        assert payload["completed"] == 2
        assert payload["total"] == 2
        assert len(payload["pages"]) == 2

        # Verify batch indexing triggered
        mock_index_batch.assert_called_once()
        batch_pages = mock_index_batch.call_args[0][0]
        assert len(batch_pages) == 2
        assert batch_pages[0]["url"] == "https://a.com"
        assert batch_pages[1]["url"] == "https://b.com"

        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """Verify first URL succeeds, second fails — partial results stored."""
        from agent.worker import _process_batch_scrape_async

        mock_store = MagicMock()
        mock_store.get_completed.return_value = 1
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "markdown": "# Only success",
                        "metadata": {},
                    },
                },
                Exception("Second failed"),
            ]
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_batch_scrape_async(
                job_id="batch-partial",
                urls=["https://a.com", "https://b.com"],
                scraper_url="http://scraper:8001",
            )

        # Scrape exception is caught at the URL loop level (worker.py:400-416).
        # Partial results are stored — complete_job is called, not fail_job.
        mock_store.complete_job.assert_called_once()
        payload = mock_store.complete_job.call_args[0][1]
        assert payload["completed"] == 1
        assert payload["total"] == 2
        assert len(payload["pages"]) == 1
        assert len(payload["errors"]) == 1
        assert "Second failed" in payload["errors"][0]["error"]
        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_outright_failure(self):
        """Verify scrape raises immediately, job failed."""
        from agent.worker import _process_batch_scrape_async

        mock_store = MagicMock()
        mock_store.get_completed.return_value = 0
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            side_effect=Exception("Immediate fail")
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_batch_scrape_async(
                job_id="batch-fail",
                urls=["https://a.com"],
                scraper_url="http://scraper:8001",
            )

        # Exception caught at URL loop level — complete_job with 0 pages, 1 error
        mock_store.complete_job.assert_called_once()
        payload = mock_store.complete_job.call_args[0][1]
        assert payload["completed"] == 0
        assert payload["total"] == 1
        assert len(payload["pages"]) == 0
        assert len(payload["errors"]) == 1
        assert "Immediate fail" in payload["errors"][0]["error"]
        mock_scraper_instance.close.assert_called_once()


class TestProcessExtractAsync:
    """Test _process_extract_async — structured extraction job."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify success: run_extract returns result, job completed."""
        from agent.worker import _process_extract_async

        mock_store = MagicMock()
        mock_run_extract = AsyncMock(
            return_value={"result": '{"name": "test"}', "sources": ["https://a.com"]}
        )
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_extract", mock_run_extract),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_extract_async(
                job_id="extract-1",
                urls=["https://a.com"],
                prompt=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="key",
                llm_model="gpt-4o-mini",
                scraper_url="http://scraper:8001",
            )

        mock_store.complete_job.assert_called_once_with(
            "extract-1", {"result": '{"name": "test"}', "sources": ["https://a.com"]}
        )
        mock_deliver_webhook.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        """Verify failure: run_extract raises, job failed."""
        from agent.worker import _process_extract_async

        mock_store = MagicMock()
        mock_run_extract = AsyncMock(side_effect=Exception("Extract error"))
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_extract", mock_run_extract),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_extract_async(
                job_id="extract-fail",
                urls=["https://a.com"],
                prompt=None,
                schema_=None,
                llm_base_url="http://llm:8000",
                llm_api_key="key",
                llm_model="gpt-4o-mini",
                scraper_url="http://scraper:8001",
            )

        mock_store.fail_job.assert_called_once_with("extract-fail", "Extract error")
        assert mock_deliver_webhook.call_args[0][1] == "failed"


class TestProcessLlmstxtAsync:
    """Test _process_llmstxt_async — LLMs.txt generation job."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify success: generate_llmstxt returns result, job completed."""
        from agent.worker import _process_llmstxt_async

        mock_store = MagicMock()
        mock_generate_llmstxt = AsyncMock(
            return_value={
                "llms_txt": "# Generated",
                "url": "https://example.com",
                "pages_discovered": 10,
                "pages_summarized": 5,
            }
        )
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.llmstxt.generate_llmstxt", mock_generate_llmstxt),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_llmstxt_async(
                job_id="llmstxt-1",
                url="https://example.com",
                max_pages=50,
                scraper_url="http://scraper:8001",
            )

        mock_store.complete_job.assert_called_once()
        mock_deliver_webhook.assert_called_once()
        mock_generate_llmstxt.assert_called_once_with(
            "https://example.com", 50, "http://scraper:8001"
        )

    @pytest.mark.asyncio
    async def test_failure(self):
        """Verify failure: generate_llmstxt raises, job failed."""
        from agent.worker import _process_llmstxt_async

        mock_store = MagicMock()
        mock_generate_llmstxt = AsyncMock(side_effect=Exception("LLMs.txt error"))
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.llmstxt.generate_llmstxt", mock_generate_llmstxt),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
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
            await _process_llmstxt_async(
                job_id="llmstxt-fail",
                url="https://example.com",
                max_pages=50,
                scraper_url="http://scraper:8001",
            )

        mock_store.fail_job.assert_called_once_with("llmstxt-fail", "LLMs.txt error")
        assert mock_deliver_webhook.call_args[0][1] == "failed"


class TestIndexPageAsync:
    """Test _index_page_async — fire-and-forget single-page indexing."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify index_page called with correct args."""
        from agent.worker import _index_page_async

        mock_semantic_instance = MagicMock()
        mock_semantic_instance.index_page = AsyncMock(return_value={"status": "ok"})
        mock_semantic_instance.close = AsyncMock()

        with (
            patch(
                "agent.semantic_client.SemanticClient",
                return_value=mock_semantic_instance,
            ),
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
            await _index_page_async(
                url="https://example.com",
                title="Test Page",
                content="# Test content",
            )

        mock_semantic_instance.index_page.assert_called_once_with(
            "https://example.com", "Test Page", "# Test content"
        )
        mock_semantic_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_caught(self, caplog):
        """Verify exception is caught and logged, not propagated."""
        from agent.worker import _index_page_async

        mock_semantic_instance = MagicMock()
        mock_semantic_instance.index_page = AsyncMock(
            side_effect=Exception("Index error")
        )
        mock_semantic_instance.close = AsyncMock()

        import logging

        caplog.set_level(logging.DEBUG)

        with (
            patch(
                "agent.semantic_client.SemanticClient",
                return_value=mock_semantic_instance,
            ),
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
            # Should not raise
            await _index_page_async(
                url="https://example.com",
                title="Test",
                content="Content",
            )

        # Exception was handled (no exception propagated)
        assert "Failed to index" in caplog.text

    @pytest.mark.asyncio
    async def test_not_propagated(self):
        """Verify the function does not propagate exceptions to caller."""
        from agent.worker import _index_page_async

        mock_semantic_instance = MagicMock()
        mock_semantic_instance.index_page = AsyncMock(
            side_effect=RuntimeError("Unexpected")
        )
        mock_semantic_instance.close = AsyncMock()

        with (
            patch(
                "agent.semantic_client.SemanticClient",
                return_value=mock_semantic_instance,
            ),
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
            # Must not raise — fire-and-forget
            await _index_page_async(
                url="https://example.com",
                title="Test",
                content="Content",
            )

        # We got here without exception
        mock_semantic_instance.index_page.assert_called_once()


class TestIndexBatchAsync:
    """Test _index_batch_async — fire-and-forget batch indexing."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """Verify empty pages list returns immediately, no SemanticClient created."""
        from agent.worker import _index_batch_async

        with patch("agent.semantic_client.SemanticClient") as mock_semantic_cls:
            await _index_batch_async([])

        mock_semantic_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_success(self):
        """Verify index_batch called with pages."""
        from agent.worker import _index_batch_async

        mock_semantic_instance = MagicMock()
        mock_semantic_instance.index_batch = AsyncMock(return_value={"status": "ok"})
        mock_semantic_instance.close = AsyncMock()
        pages = [
            {"url": "https://a.com", "title": "A", "content": "Content A"},
            {"url": "https://b.com", "title": "B", "content": "Content B"},
        ]

        with (
            patch(
                "agent.semantic_client.SemanticClient",
                return_value=mock_semantic_instance,
            ),
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
            await _index_batch_async(pages)

        mock_semantic_instance.index_batch.assert_called_once_with(pages)
        mock_semantic_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_caught(self, caplog):
        """Verify exception is caught and logged, not propagated."""
        from agent.worker import _index_batch_async

        mock_semantic_instance = MagicMock()
        mock_semantic_instance.index_batch = AsyncMock(
            side_effect=Exception("Batch error")
        )
        mock_semantic_instance.close = AsyncMock()

        import logging

        caplog.set_level(logging.DEBUG)

        with (
            patch(
                "agent.semantic_client.SemanticClient",
                return_value=mock_semantic_instance,
            ),
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
            await _index_batch_async(
                [{"url": "https://a.com", "title": "A", "content": "C"}]
            )

        assert "Failed to batch-index" in caplog.text


class TestCancellationMetrics:
    """Cancelled jobs must never be recorded as completed or failed."""

    @pytest.mark.asyncio
    async def test_run_job_with_observability_records_cancelled_not_completed(self):
        from agent.worker import JobCancelledError, _run_job_with_observability

        mock_store = MagicMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        async def work_fn():
            raise JobCancelledError("cancelled by DELETE")

        with (
            patch("agent.worker.record_job_cancelled") as mock_cancel,
            patch("agent.worker.record_job_start") as mock_start,
            patch("agent.worker.record_job_end") as mock_end,
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
            patch("agent.worker.METRICS", mock_metrics),
        ):
            await _run_job_with_observability(
                job_id="job-1",
                job_type="batch_scrape",
                store=mock_store,
                webhook_config=None,
                work_fn=work_fn,
            )

        mock_cancel.assert_called_once_with("batch_scrape")
        mock_store.complete_job.assert_not_called()
        mock_store.fail_job.assert_not_called()
        mock_deliver_webhook.assert_not_called()
        mock_start.assert_called_once_with("batch_scrape")
        mock_end.assert_called_once_with("batch_scrape")
        completed_calls = [
            c
            for c in mock_metrics.counter.call_args_list
            if c.args and c.args[0] == "jobs_completed_total"
        ]
        failed_calls = [
            c
            for c in mock_metrics.counter.call_args_list
            if c.args and c.args[0] == "jobs_failed_total"
        ]
        assert completed_calls == []
        assert failed_calls == []

    @pytest.mark.asyncio
    async def test_cancelled_crawl_does_not_record_completed_metrics(self):
        from agent.worker import _process_crawl_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "cancelled"}
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            return_value={"success": True, "data": {"markdown": "# x", "metadata": {}}}
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
            patch("agent.worker.METRICS", mock_metrics),
            patch("agent.worker.record_job_cancelled") as mock_cancel,
            patch("agent.worker.record_job_start"),
            patch("agent.worker.record_job_end"),
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
            patch("agent.worker._index_page_async", AsyncMock()),
        ):
            await _process_crawl_async(
                job_id="crawl-cancel-metrics",
                url="https://example.com",
                max_pages=10,
                max_depth=2,
                scraper_url="http://scraper:8001",
            )

        mock_cancel.assert_called_once_with("crawl")
        completed_calls = [
            c
            for c in mock_metrics.counter.call_args_list
            if c.args and c.args[0] == "jobs_completed_total"
        ]
        assert completed_calls == []
        generic_duration_calls = [
            c
            for c in mock_metrics.histogram.call_args_list
            if c.args and c.args[0] == "job_duration_seconds"
        ]
        assert generic_duration_calls == []

    @pytest.mark.asyncio
    async def test_cancelled_batch_scrape_does_not_complete(self):
        from agent.worker import _process_batch_scrape_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "cancelled"}
        mock_store.get_completed.return_value = 0
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(
            return_value={"success": True, "data": {"markdown": "# x", "metadata": {}}}
        )
        mock_scraper_instance.close = AsyncMock()
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
            patch("agent.worker.METRICS", mock_metrics),
            patch("agent.worker.record_job_cancelled") as mock_cancel,
            patch("agent.worker.record_job_start"),
            patch("agent.worker.record_job_end"),
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
            patch("agent.worker._index_batch_async", AsyncMock()),
        ):
            await _process_batch_scrape_async(
                job_id="batch-cancel",
                urls=["https://a.com", "https://b.com"],
                scraper_url="http://scraper:8001",
            )

        mock_cancel.assert_called_once_with("batch_scrape")
        mock_store.complete_job.assert_not_called()
        mock_store.fail_job.assert_not_called()
        mock_scraper_instance.scrape.assert_not_called()
        mock_scraper_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_plan_execute_does_not_complete(self):
        from agent.worker import _process_plan_execution_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "cancelled"}
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        fake_llm = MagicMock()
        fake_llm.close = AsyncMock()
        fake_searxng = MagicMock()
        fake_searxng.close = AsyncMock()
        fake_scraper = MagicMock()
        fake_scraper.close = AsyncMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
            patch("agent.worker.METRICS", mock_metrics),
            patch("agent.worker.record_job_cancelled") as mock_cancel,
            patch("agent.worker.record_job_start"),
            patch("agent.worker.record_job_end"),
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
            patch("agent.llm.LLMClient", return_value=fake_llm),
            patch("agent.scraper_client.ScraperClient", return_value=fake_scraper),
            patch("agent.searxng_client.SearXNGClient", return_value=fake_searxng),
        ):
            await _process_plan_execution_async(
                job_id="plan-cancel",
                prompt="test",
                plan={"phases": [{"action": "search", "description": "query"}]},
                modifications=None,
                llm_base_url="http://llm",
                llm_api_key="k",
                llm_model="m",
                searxng_url="http://searxng",
                scraper_url="http://scraper",
            )

        mock_cancel.assert_called_once_with("plan_execute")
        mock_store.complete_job.assert_not_called()
        mock_store.fail_job.assert_not_called()
        fake_llm.close.assert_called_once()
        fake_searxng.close.assert_called_once()
        fake_scraper.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_agent_does_not_complete(self):
        from agent.worker import _process_agent_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "cancelled"}
        mock_run_research = AsyncMock(return_value={"result": "x", "sources": []})
        mock_research_memory = MagicMock()
        mock_research_memory.query = AsyncMock(return_value={"hit": False})
        mock_deliver_webhook = AsyncMock()
        mock_metrics = MagicMock()
        mock_metrics.counter.return_value.inc = MagicMock()
        mock_metrics.histogram.return_value.observe = MagicMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.run_research", mock_run_research),
            patch("agent.worker.deliver_webhook", mock_deliver_webhook),
            patch("agent.worker.METRICS", mock_metrics),
            patch("agent.worker.record_job_cancelled") as mock_cancel,
            patch("agent.worker.record_job_start"),
            patch("agent.worker.record_job_end"),
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
                job_id="agent-cancel",
                prompt="test",
                urls=None,
                schema_=None,
                llm_base_url="http://llm",
                llm_api_key="k",
                llm_model="m",
                searxng_url="http://searxng",
                scraper_url="http://scraper",
                research_memory=mock_research_memory,
            )

        mock_cancel.assert_called_once_with("agent")
        mock_run_research.assert_not_called()
        mock_store.complete_job.assert_not_called()
        mock_store.fail_job.assert_not_called()
        mock_deliver_webhook.assert_not_called()
