"""Consumer-refusal tests for the #586 barrier invariant.

The invariant: barrier/challenge content must NEVER reach the LLM. Every
agent-svc seam that ingests scraper results refuses flagged payloads:

  * scraper_client.scrape_with_fallback — browser stage warning guard
  * research.discovery._scrape_single / _scrape_urls (keyword path)
  * research.discovery._scrape_answer_sources (rerank-reuse seam)
  * rich-search enrichment (sync + streaming)
  * crawler.py per-page refusal (error/skip, bounded retries, start-URL fail)
  * batch-scrape worker (worker.py) pages/index payloads
  * session agent scrape/deepen steps
  * agent routes/scrape.py refusal + no auto-index

Payloads are modeled on the F1 Fastly challenge fixture; the harnesses
mirror tests/service/test_scrape_passthrough.py and test_worker.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

AGENT_SVC = Path(__file__).resolve().parents[2] / "agent-svc"
if str(AGENT_SVC) not in sys.path:
    sys.path.insert(0, str(AGENT_SVC))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
F1_PATH = FIXTURES / "fastly-challenge-full.html"


# ── Served-fixture plumbing (pipeline-fidelity harness) ─────────


class _AsyncFileServer:
    """Minimal async HTTP/1.1 server serving one payload on 127.0.0.1."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.server: asyncio.Server | None = None
        self.port: int = 0

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.read(64 * 1024)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: " + str(len(self.payload)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.write(self.payload)
            await writer.drain()
        except Exception:  # client disconnects are expected
            pass
        finally:
            writer.close()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/challenge"


@pytest_asyncio.fixture
async def _barrier_scraper_loopback(monkeypatch):
    """Scraper settings for loopback fixture serving (VAL-BARR-008 note).

    SCRAPER_PRIVATE_URL_ALLOWLIST=127.0.0.1 (exact-hostname match) plus the
    scrape cache pointed at an unbound loopback port so Valkey reads miss and
    writes no-op deterministically. The cached settings objects are patched
    attribute-wise (repo pattern): fetch.py binds ``_settings`` at import
    time, so under xdist workers that imported the module before this fixture
    ran, env vars alone would not reach it.
    """
    from scraper import cache as cache_mod
    from scraper import fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod._settings, "scraper_private_url_allowlist", "127.0.0.1"
    )
    monkeypatch.setattr(cache_mod._settings, "valkey_host", "127.0.0.1")
    monkeypatch.setattr(cache_mod, "_cache_client", None)

    yield


@pytest_asyncio.fixture
async def served_challenge_url(_barrier_scraper_loopback):
    """Serve the F1 Fastly challenge fixture over ephemeral loopback HTTP."""
    server = _AsyncFileServer(F1_PATH.read_bytes())
    await server.start()
    try:
        yield server.url
    finally:
        await server.stop()


class _FakeCacheClient:
    """Dict-backed stand-in for the Valkey cache client (get-only use)."""

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}

    async def get(self, key: str):
        return self._payloads.get(key)


@pytest.fixture()
def seeded_scraper_cache(monkeypatch):
    """Serve seeded payloads behind scraper.cache's REAL ``_check_cache``.

    Same seam pattern as test_cache_source_html_size_migration.py; exposes
    the backing client so tests can assert on stored keys.
    """
    from scraper import cache as cache_mod

    client = _FakeCacheClient()

    async def _fake_get_client():
        return client

    monkeypatch.setattr(cache_mod, "_cache_client", client)
    monkeypatch.setattr(cache_mod, "_get_cache_client", _fake_get_client)

    def _seed(payload: dict):
        import json as _json

        key = cache_mod._scrape_cache_key(payload["url"])
        client._payloads[key] = _json.dumps(payload)
        return payload

    _seed.client = client  # type: ignore[attr-defined]
    return _seed


# ── Payload builders modeled on the F1 challenge fixture ─────────


def flagged_success(markdown: str | None = None) -> dict:
    """A success payload whose markdown is Fastly challenge text."""
    return {
        "success": True,
        "warning": "Block-page content detected (block_detected=fail); "
        "the page may be a challenge or error interstitial.",
        "data": {
            "markdown": markdown or CHALLENGE_MARKDOWN,
            "source": "playwright",
            "quality": {
                "score": 0.38,
                "checks": {
                    "boilerplate": "warn",
                    "completeness": "warn",
                    "block_detected": "fail",
                    "volume": "pass",
                },
                "detail": "block:fail",
            },
        },
    }


def clean_success(markdown: str = "# Real article\n\nSubstantive prose body.") -> dict:
    return {
        "success": True,
        "data": {
            "markdown": markdown,
            "source": "content-negotiation",
            "quality": {
                "score": 0.9,
                "checks": {
                    "boilerplate": "pass",
                    "completeness": "pass",
                    "block_detected": "pass",
                    "volume": "pass",
                },
                "detail": "all checks passed",
            },
        },
    }


CHALLENGE_MARKDOWN = (
    "# JavaScript is disabled in your browser.\n\n"
    "Please enable JavaScript to proceed.\n\n"
    "A required part of this site couldn\u2019t load."
)

CHALLENGE_PHRASES = (
    "javascript is disabled in your browser",
    "please enable javascript to proceed",
)


def _contains_challenge(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in CHALLENGE_PHRASES)


# ── VAL-BARR-009: scrape_with_fallback browser-stage guard ───────


class TestScrapeWithFallbackBrowserGuard:
    @pytest.mark.asyncio
    async def test_warned_browser_payload_is_refused(self):
        from agent.scraper_client import ScraperClient

        client = ScraperClient("http://scraper")

        async def fake_scrape(url, force_browser=False, lightweight_only=False, **kw):
            if force_browser:
                return flagged_success()
            return {"success": False, "error": "generic stage failed"}

        with patch.object(client, "scrape", new=fake_scrape):
            result = await client.scrape_with_fallback("https://x.com")

        assert not result.get("success"), result

    @pytest.mark.asyncio
    async def test_block_fail_browser_payload_is_refused(self):
        from agent.scraper_client import ScraperClient

        client = ScraperClient("http://scraper")
        block_failed = flagged_success()
        block_failed.pop("warning")  # flag via quality only

        async def fake_scrape(url, force_browser=False, lightweight_only=False, **kw):
            if force_browser:
                return block_failed
            return {"success": False, "error": "generic failed"}

        with patch.object(client, "scrape", new=fake_scrape):
            result = await client.scrape_with_fallback("https://x.com")

        assert not result.get("success")

    @pytest.mark.asyncio
    async def test_clean_browser_payload_is_returned(self):
        from agent.scraper_client import ScraperClient

        client = ScraperClient("http://scraper")

        async def fake_scrape(url, force_browser=False, lightweight_only=False, **kw):
            if force_browser:
                return clean_success("# Rendered article\n\nFull body.")
            return {"success": False, "error": "generic failed"}

        with patch.object(client, "scrape", new=fake_scrape):
            result = await client.scrape_with_fallback("https://x.com")

        assert result.get("success") is True
        assert "Rendered article" in result["data"]["markdown"]


# ── VAL-BARR-010: discovery._scrape_single drops flagged scrapes ──


class TestDiscoveryScrapeSingleRefusal:
    @staticmethod
    def _scraper(payload: dict) -> MagicMock:
        scraper = MagicMock()
        scraper.scrape_with_fallback = AsyncMock(return_value=payload)
        return scraper

    @pytest.mark.asyncio
    async def test_warned_success_returns_none(self, caplog):
        import logging

        from agent.research.discovery import _scrape_single

        # The refusal is logged by agent.barrier_guard (shared helper), so
        # raise the level globally rather than on one module logger.
        with caplog.at_level(logging.INFO):
            artifact = await _scrape_single(
                "https://x.com", self._scraper(flagged_success()), asyncio.Semaphore(1)
            )

        assert artifact is None
        assert any("refused" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_block_fail_success_returns_none(self):
        from agent.research.discovery import _scrape_single

        payload = flagged_success()
        payload.pop("warning")
        artifact = await _scrape_single(
            "https://x.com", self._scraper(payload), asyncio.Semaphore(1)
        )

        assert artifact is None

    @pytest.mark.asyncio
    async def test_clean_success_yields_artifact(self):
        from agent.research.discovery import _scrape_single

        artifact = await _scrape_single(
            "https://x.com", self._scraper(clean_success()), asyncio.Semaphore(1)
        )

        assert artifact is not None
        assert "Real article" in (artifact.markdown or "")

    @pytest.mark.asyncio
    async def test_all_barrier_batch_yields_no_artifacts(self):
        from agent.research.discovery import _scrape_urls

        class Scraper:
            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                return flagged_success()

        artifacts = await _scrape_urls(
            ["https://a.com", "https://b.com", "https://c.com"],
            Scraper(),  # type: ignore[arg-type]
        )

        assert artifacts == []


# ── VAL-BARR-013: keyword-path pipeline composite invariant ──────


class TestKeywordPipelineAllBarrier:
    @pytest.mark.asyncio
    async def test_all_barrier_discovery_yields_empty_context(self):
        from agent.research.discovery import _run_research_discover_and_scrape

        searxng = MagicMock()
        searxng.search = AsyncMock(
            return_value=(
                [{"url": "https://a.com", "title": "A", "description": "d"}],
                MagicMock(),
            )
        )
        searxng.close = AsyncMock()

        class Scraper:
            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                return flagged_success()

        result = await _run_research_discover_and_scrape(
            prompt="q",
            urls=None,
            searxng=searxng,
            scraper=Scraper(),  # type: ignore[arg-type]
        )

        assert result["documents"] == []
        assert result["context"] == ""
        assert not _contains_challenge(result["context"])

    @pytest.mark.asyncio
    async def test_mixed_set_keeps_only_clean_artifacts(self):
        from agent.research.discovery import _run_research_discover_and_scrape

        searxng = MagicMock()
        searxng.search = AsyncMock(
            return_value=(
                [
                    {"url": "https://clean.com", "title": "C", "description": "d"},
                    {"url": "https://barrier.com", "title": "B", "description": "d"},
                ],
                MagicMock(),
            )
        )
        searxng.close = AsyncMock()

        class Scraper:
            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                if "barrier" in url:
                    return flagged_success()
                return clean_success("# Clean page\n\nDistinctive clean prose.")

        result = await _run_research_discover_and_scrape(
            prompt="q",
            urls=None,
            searxng=searxng,
            scraper=Scraper(),  # type: ignore[arg-type]
        )

        assert result["documents"], "clean artifact should survive"
        assert len(result["documents"]) == 1
        assert not _contains_challenge(result["context"])
        assert "Clean page" in result["context"]


# ── VAL-BARR-011: rich-search enrichment refusal (sync+stream) ───


class TestRichSearchRefusal:
    @pytest.mark.asyncio
    async def test_flagged_result_enriches_from_description(self):
        import agent.research.search as search_mod

        calls: list[str] = []

        class FakeLLM:
            def __init__(self, *_a, **_k):
                pass

            async def generate(self, **kwargs):
                calls.append(kwargs.get("user_prompt", ""))
                return "synthesis"

            async def close(self):
                pass

        class FakeScraper:
            def __init__(self, *_a):
                pass

            async def scrape(self, url):
                if "barrier" in url:
                    return flagged_success()
                return clean_success("# Clean source\n\nUseful clean content.")

            async def close(self):
                pass

        with (
            patch.object(search_mod, "ScraperClient", FakeScraper),
            patch.object(search_mod, "LLMClient", FakeLLM),
        ):
            result = await search_mod.run_rich_search(
                search_results=[
                    {
                        "url": "https://clean.test/a",
                        "title": "C",
                        "description": "clean desc",
                    },
                    {
                        "url": "https://barrier.test/b",
                        "title": "B",
                        "description": "harmless description fallback",
                    },
                ],
                query="q",
                llm_model="m",
            )

        context = "\n".join(calls)
        assert result is not None
        assert not _contains_challenge(context), (
            "challenge text must never enter the LLM grounding context"
        )
        assert "harmless description fallback" in context
        assert "Useful clean content" in context

    @pytest.mark.asyncio
    async def test_streaming_flagged_result_emits_no_challenge_event(self):
        import agent.research.search as search_mod

        captured_prompts: list[str] = []

        class FakeLLM:
            def __init__(self, *_a, **_k):
                pass

            async def generate_stream(self, **kwargs):
                captured_prompts.append(kwargs.get("user_prompt", ""))

                async def _events():
                    yield {"type": "token", "content": "ok"}
                    yield {"type": "done", "full_content": "ok"}

                async for ev in _events():
                    yield ev

            async def close(self):
                pass

        class FakeScraper:
            def __init__(self, *_a):
                pass

            async def scrape(self, url):
                if "barrier" in url:
                    return flagged_success()
                return clean_success()

            async def close(self):
                pass

        class FakeSearXNG:
            def __init__(self, *_a, **_k):
                pass

            async def search(self, *a, **k):
                return (
                    [
                        {
                            "url": "https://clean.test/a",
                            "title": "C",
                            "description": "cd",
                        },
                        {
                            "url": "https://barrier.test/b",
                            "title": "B",
                            "description": "streaming desc fallback",
                        },
                    ],
                    MagicMock(),
                )

            async def close(self):
                pass

        with (
            patch.object(search_mod, "SearXNGClient", FakeSearXNG),
            patch.object(search_mod, "ScraperClient", FakeScraper),
            patch.object(search_mod, "LLMClient", FakeLLM),
        ):
            events = []
            async for event in search_mod.run_search_stream(
                query="q",
                limit=5,
                search_type="rich",
                searxng_url="http://searxng",
                scraper_url="http://scraper",
                llm_base_url="http://llm",
                llm_api_key="k",
                llm_model="m",
            ):
                events.append(event)

        scrape_events = [e for e in events if e.get("type") == "scrape_result"]
        for event in scrape_events:
            md = (event.get("contents") or {}).get("markdown", "")
            assert not _contains_challenge(md), event

        context = "\n".join(captured_prompts)
        assert not _contains_challenge(context)
        assert "streaming desc fallback" in context


# ── VAL-BARR-018: answer rerank-reuse seam refusal ───────────────


class TestAnswerRerankReuseRefusal:
    @pytest.mark.asyncio
    async def test_flagged_rerank_artifact_is_dropped_from_answer_context(self):
        from agent.research.discovery import (
            _build_answer_context,
            _scrape_answer_sources,
        )
        from agent.research.sources import SourceArtifact

        flagged = SourceArtifact(
            url="https://barrier.test/x",
            markdown=CHALLENGE_MARKDOWN,
            char_count=len(CHALLENGE_MARKDOWN),
        )
        clean = SourceArtifact(
            url="https://clean.test/y",
            markdown="# Clean answer source\n\nGood content.",
            char_count=40,
        )
        scraper = MagicMock()  # no fresh scrapes needed — both URLs reused

        artifacts = await _scrape_answer_sources(
            ["https://barrier.test/x", "https://clean.test/y"],
            [flagged, clean],
            scraper,
            num_sources=2,
        )

        urls = {a.url for a in artifacts}
        assert "https://barrier.test/x" not in urls
        assert "https://clean.test/y" in urls

        built = _build_answer_context(
            [
                {"url": u, "title": "T", "description": ""}
                for u in ("https://barrier.test/x", "https://clean.test/y")
            ],
            artifacts,
        )
        assert not _contains_challenge(built["context"])

    @pytest.mark.asyncio
    async def test_clean_rerank_artifact_passes_through(self):
        from agent.research.discovery import _scrape_answer_sources
        from agent.research.sources import SourceArtifact

        clean = SourceArtifact(
            url="https://clean.test/y",
            markdown="# Clean\n\nBody.",
            char_count=12,
        )
        artifacts = await _scrape_answer_sources(
            ["https://clean.test/y"], [clean], MagicMock(), num_sources=1
        )

        assert len(artifacts) == 1
        assert artifacts[0].markdown == "# Clean\n\nBody."

    @pytest.mark.asyncio
    async def test_low_yield_article_rerank_artifact_passes_through(self):
        """A #587-style thin-but-legitimate article is NOT refused at reuse.

        Review finding: the rerank re-gate must not depend on scraper-svc's
        quality machinery or refuse legitimate low-yield content — only the
        challenge markers themselves flag here.
        """
        from agent.research.discovery import _scrape_answer_sources
        from agent.research.sources import SourceArtifact

        low_yield = SourceArtifact(
            url="https://thin.test/z",
            markdown="JavaScript powers this interactive card grid.",
            char_count=50,
        )
        artifacts = await _scrape_answer_sources(
            ["https://thin.test/z"], [low_yield], MagicMock(), num_sources=1
        )

        assert len(artifacts) == 1


# ── barrier_guard unit checks (review-finding regressions) ───────


class TestBarrierGuardSemantics:
    def test_markdown_is_challenge_positive_and_negative(self):
        from agent.barrier_guard import markdown_is_challenge

        assert markdown_is_challenge(CHALLENGE_MARKDOWN)
        assert markdown_is_challenge('script src="/_fs-ch-/challenge.js"')
        # Legit JavaScript mentions and generic block phrases stay clean.
        assert not markdown_is_challenge("JavaScript powers interactive maps.")
        assert not markdown_is_challenge("Under maintenance. Please wait.")

    def test_markdown_is_challenge_empty(self):
        from agent.barrier_guard import markdown_is_challenge

        assert not markdown_is_challenge(None)
        assert not markdown_is_challenge("")


# ── VAL-BARR-012: crawler records barrier pages as errors/skips ──


class TestCrawlerBarrierRefusal:
    @staticmethod
    def _engine(payload_for_url):
        from agent.crawler import CrawlEngine, CrawlOptions

        scraper = MagicMock()

        async def _scrape(url, **kwargs):
            return payload_for_url(url)

        scraper.scrape = AsyncMock(side_effect=_scrape)
        engine = CrawlEngine(
            scraper,
            options=CrawlOptions(max_pages=10, max_depth=0, sitemap_mode="skip"),
        )
        return engine

    @pytest.mark.asyncio
    async def test_barrier_child_page_recorded_as_error_not_page(self):
        engine = self._engine(lambda url: flagged_success())
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        assert all(_not_challenge_page(p) for p in result.pages)
        assert any(e.get("error_code") == "BARRIER_DETECTED" for e in result.errors)

    @pytest.mark.asyncio
    async def test_barrier_start_url_fails_honestly(self):
        """A barrier-flagged start URL aborts the crawl with an error entry."""
        from agent.crawler import CrawlEngine, CrawlOptions

        scraper = MagicMock()

        async def _scrape(url, **kwargs):
            return flagged_success()

        scraper.scrape = AsyncMock(side_effect=_scrape)
        engine = CrawlEngine(
            scraper,
            options=CrawlOptions(max_pages=10, max_depth=0, sitemap_mode="skip"),
        )
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        # The run() wrapper converts the raised StartUrlScrapeError into an
        # honest aborted result: zero pages + a barrier error entry.
        assert result.completed == 0
        assert result.pages == []
        assert any(e.get("error_code") == "BARRIER_DETECTED" for e in result.errors), (
            result.errors
        )

    @pytest.mark.asyncio
    async def test_barrier_child_retry_bounded_at_two_then_skipped(self):
        """Bounded retry driven through the real engine run loop (end-to-end).

        A flagged CHILD page is attempted exactly 3 times inside one run()
        (initial + 2 bounded retries with backoff), then skipped: one
        BARRIER_DETECTED error entry, never a page. The start URL scrapes
        clean so the crawl actually reaches the child.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        attempts: list[str] = []

        class Scraper:
            async def scrape(self, url, **kwargs):
                attempts.append(url)
                if url.rstrip("/").endswith("/child"):
                    return flagged_success()
                return clean_success("# Clean start\n\nStart page body.")

        engine = CrawlEngine(
            Scraper(),  # type: ignore[arg-type]
            options=CrawlOptions(max_pages=5, max_depth=1, sitemap_mode="skip"),
        )
        # Seed the flagged child directly (link discovery is stubbed off).
        engine._queue.append(("https://example.com/child", 1, False))
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        # The clean start page succeeded, proving the crawl progressed.
        assert result.completed == 1, result.pages
        child_attempts = [u for u in attempts if u.endswith("/child")]
        assert len(child_attempts) == 3, (
            f"expected initial + exactly 2 retries, got {len(child_attempts)}"
        )
        # Refusal recorded once (final state) with the barrier naming.
        child_errors = [e for e in result.errors if e.get("url", "").endswith("/child")]
        assert len(child_errors) == 1
        assert child_errors[0].get("error_code") == "BARRIER_DETECTED"
        assert result.pages and all(
            "/child" not in p.get("metadata", {}).get("sourceURL", "")
            for p in result.pages
        )

    @pytest.mark.asyncio
    async def test_clean_scrape_still_recorded_as_successful_page(self):
        engine = self._engine(lambda url: clean_success())
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        assert result.completed == 1
        assert not any(e.get("error_code") == "BARRIER_DETECTED" for e in result.errors)


def _not_challenge_page(page: dict) -> bool:
    return not _contains_challenge(page.get("markdown", ""))


# ── VAL-BARR-019: batch-scrape worker + session seams ────────────


class TestBatchWorkerRefusal:
    @pytest.mark.asyncio
    async def test_flagged_payload_becomes_error_not_page_or_index(self):
        from agent.worker import _process_batch_scrape_async

        mock_store = MagicMock()
        mock_store.get_job.return_value = {"status": "processing"}
        mock_scraper_instance = MagicMock()
        mock_scraper_instance.scrape = AsyncMock(return_value=flagged_success())
        mock_scraper_instance.close = AsyncMock()
        mock_index_batch = AsyncMock()

        with (
            patch("agent.worker.JobStore", return_value=mock_store),
            patch("agent.worker.ScraperClient", return_value=mock_scraper_instance),
            patch("agent.worker.deliver_webhook", AsyncMock()),
            patch("agent.worker.METRICS", MagicMock()),
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
                job_id="batch-barrier",
                urls=["https://x.com"],
                scraper_url="http://scraper:8001",
            )

        complete_args = mock_store.complete_job.call_args[0][1]
        assert complete_args["pages"] == []
        assert len(complete_args["errors"]) == 1
        err = complete_args["errors"][0]
        assert err["error_code"] == "BARRIER_DETECTED"
        assert "barrier" in err["error"].lower()
        # No index payload built at all for the flagged page — the batch
        # indexer is never invoked when there are zero clean pages.
        mock_index_batch.assert_not_called()


class TestSessionSeamRefusal:
    @pytest.mark.asyncio
    async def test_session_scrape_step_refuses_flagged_content(self):
        from agent.session import SessionManager

        manager = SessionManager.__new__(SessionManager)
        manager.store = MagicMock()

        class Scraper:
            def __init__(self, *_a, **_k):
                pass

            base_url = "http://scraper"

            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                return flagged_success()

            async def close(self):
                pass

        with patch("agent.session.ScraperClient", Scraper):
            outcome = await SessionManager._step_scrape(
                manager,
                "sess-1",
                {"urls": ["https://x.com"]},
                "http://scraper",
            )

        assert outcome["succeeded"] == 0
        assert outcome["failed"] == 1

    @pytest.mark.asyncio
    async def test_session_deepen_step_refuses_flagged_content(self):
        """The real deepen step refuses every flagged deep-dive source.

        Drives ``_step_deepen`` end-to-end (search -> scrape_with_fallback
        seam -> synthesis) with every new-source scrape barrier-flagged:
        zero new refs/urls stored, the LLM is never invoked, and neither the
        returned findings nor the appended artifact carries challenge text.
        """
        import agent.session as session_mod

        class Scraper:
            def __init__(self, *_a, **_k):
                pass

            base_url = "http://scraper"

            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                return flagged_success()

            async def close(self):
                pass

        manager = session_mod.SessionManager.__new__(session_mod.SessionManager)
        manager.store = MagicMock()
        manager.store.get_ref.return_value = {
            "url": "https://clean.test/source",
            "title": "Source",
            "markdown": "# Clean source\n\nReal content.",
        }
        manager.store.get_refs.return_value = {
            "ref_1_1": manager.store.get_ref.return_value
        }
        manager.store.append_step.return_value = 2

        class FakeSearXNG:
            def __init__(self, *_a, **_k):
                pass

            async def search(self, *a, **k):
                return (
                    [
                        {
                            "url": "https://barrier.test/deep",
                            "title": "D",
                            "description": "d",
                        }
                    ],
                    MagicMock(),
                )

            async def close(self):
                pass

        class FailLLM:
            def __init__(self, *_a, **_k):
                raise AssertionError("LLM must not be invoked in this scenario")

        with (
            patch.object(session_mod, "SearXNGClient", FakeSearXNG),
            patch.object(session_mod, "ScraperClient", Scraper),
            patch.object(session_mod, "LLMClient", FailLLM),
        ):
            outcome = await manager._step_deepen(
                "sess-1",
                {"ref_id": "ref_1_1", "sub_topic": "go deeper", "max_sources": 1},
                "http://searxng",
                "http://scraper",
                "http://llm",
                "k",
                "m",
            )

        # Zero new sources survived the seam...
        assert outcome["new_sources"] == []
        assert manager.store.add_ref.call_count == 0
        # ...and no challenge prose entered findings or the stored artifact.
        assert not _contains_challenge(outcome["new_findings"])
        artifact_section = manager.store.append_artifact.call_args[0][1]
        assert not _contains_challenge(artifact_section)


# ── VAL-BARR-015: agent /v2/scrape surface refusal + no auto-index ──


class TestAgentScrapeSurfaceRefusal:
    def _build_app(self, payload: dict):
        from agent.models import ScrapeData, ScrapeResponse  # noqa: F401
        from agent.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.state.rate_limiter = MagicMock()
        app.state.job_store = MagicMock()
        app.state.max_searches_per_request = 5
        tracker = MagicMock()
        created_tasks: list = []
        tracker.create_background_task = MagicMock(side_effect=created_tasks.append)
        app.state.task_tracker = tracker
        scraper_client = MagicMock()
        scraper_client.scrape = AsyncMock(return_value=payload)
        scraper_client.close = AsyncMock()
        app.state.scraper_client = scraper_client
        app.include_router(router)
        return app, tracker

    def test_block_flagged_payload_is_typed_error_and_never_indexed(self):
        from agent.exceptions import ScrapeError
        from fastapi.testclient import TestClient

        app, tracker = self._build_app(flagged_success())

        @app.exception_handler(ScrapeError)
        async def _handler(_request, exc):  # pragma: no cover - simple mapping
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "error": exc.detail,
                    "error_code": exc.error_code,
                },
            )

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/v2/scrape",
            json={"url": "https://challenge.test/page", "formats": ["markdown"]},
        )

        assert resp.status_code == 502
        body = resp.json()
        assert body["success"] is False
        assert "barrier" in body["error"].lower()
        # No auto-index task was created for the flagged page.
        tracker.create_background_task.assert_not_called()


# ── VAL-BARR-008: scraper /scrape of the fixture never silent success ──

# The pipeline tests below import ``scraper.fetch`` (via scraper.fetch's
# module-level curl_cffi import). curl_cffi is a Fast-Tests-lane dependency —
# the agent-svc container that hosts the integration lane does not have it
# (repo precedent: test_lightweight_only.py lives under tests/unit for the
# same reason). Governed per tests/conftest.py skipif rules.
_curl_cffi_available = True
try:
    import curl_cffi  # noqa: F401
except ModuleNotFoundError:
    _curl_cffi_available = False

_requires_curl_cffi = pytest.mark.skipif(
    not _curl_cffi_available,
    reason="scraper.fetch imports curl_cffi, unavailable in the agent container",
    owner="repository-maintainer",
    issue="#586",
    classification="retained",
    environment="agent-svc integration container lacks scraper-svc heavy deps",
)


class TestScraperPipelineFixtureOutcome:
    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_default_flow_served_fixture_is_flagged_never_bare_success(
        self, _barrier_scraper_loopback, served_challenge_url, monkeypatch
    ):
        """Real HTTP Tier 2 gates escalate F1 and refuse exhausted recovery.

        Browser boundaries are local stubs; HTTP acquisition, extraction,
        quality gates and recovery coordination remain the production paths.
        """
        import scraper.fetch as fetch

        browser_calls = []

        async def blocked_browser(url):
            browser_calls.append(url)
            return {
                "error": "Barrier detected: fastly",
                "barrier": {
                    "detected": True,
                    "type": "fastly",
                    "provider": "fastly",
                    "confidence": 0.95,
                },
                "markdown": "",
                "source": "barrier-detection",
                "url": url,
            }

        async def unavailable(*_args):
            return None

        monkeypatch.setattr(fetch, "fetch_via_playwright", blocked_browser)
        monkeypatch.setattr(fetch, "fetch_via_flaresolverr", unavailable)
        monkeypatch.setattr(fetch, "_fetch_via_browser_svc", unavailable)
        result = await fetch.smart_scrape(served_challenge_url)

        assert browser_calls == [served_challenge_url]
        assert result["error_code"] == "BARRIER_DETECTED"
        assert result["markdown"] == ""
        assert result["recovery"]["outcome"] == "unresolved"
        assert result["recovery"]["exhausted"] is True

    def test_tier3_post_extraction_gate_refuses_f1_markdown(self):
        """The REAL Tier 3 gate function refuses F1's markdown.

        Drives the actual ``_playwright_fetch_unbounded`` refusal decision by
        importing its exact gate inputs through the production seam
        (``_classify_barrier`` + ``_check_block_page`` + BLOCK_PAGE_PATTERNS)
        over the fixture HTML and asserting the same refuse-expression the
        tier uses — no local copies of the pattern lists or thresholds.
        """
        import re

        from scraper.extract import BLOCK_PAGE_PATTERNS, _check_block_page
        from scraper.fetch_quality import _classify_barrier, html_to_markdown

        fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "html"
        f1_html = (fixtures / "fastly-challenge-full.html").read_text(encoding="utf-8")

        title = "JavaScript is disabled in your browser."
        markdown = html_to_markdown(f1_html)
        barrier = _classify_barrier(
            title, "https://x.test/challenge", markdown, f1_html
        )

        block_score, block_status = _check_block_page(markdown)
        matched_patterns = [
            p.pattern for p in BLOCK_PAGE_PATTERNS if p.search(markdown.lower())
        ]
        challenge_corroborated = (
            barrier.detected or barrier.provider is not None
        ) or any(
            marker in markdown.lower()
            for marker in (
                "javascript is disabled",
                "enable javascript",
                "javascript is required",
                "couldn't load",
                "couldn\u2019t load",
                "/_fs-ch-",
                "verify you are",
            )
        )
        # The exact refuse expression from fetch_tiers._playwright_fetch_unbounded
        # (captcha_resolved is statically False in this scenario):
        barrier_hit = (
            barrier.detected
            and barrier.barrier_type != "captcha"
            and barrier.confidence > 0.7
        )
        block_fail_refusal = (
            block_status == "fail"
            and len(matched_patterns) >= 2
            and challenge_corroborated
        )
        assert barrier_hit or block_fail_refusal, (
            f"F1 must trip the real Tier 3 gate "
            f"(barrier={barrier!r}, status={block_status}, score={block_score}, "
            f"matched={matched_patterns})"
        )
        assert len(matched_patterns) >= 2
        assert re.search(r"javascript is disabled", markdown.lower())


# ── VAL-BARR-016: cache interplay — poisoned entries re-gated ────


class TestCacheInterplayRegating:
    @pytest.mark.asyncio
    async def test_poisoned_crawl_cache_entry_refused_through_engine_run(self):
        """Poisoned crawl-cache hit refused end-to-end via CrawlEngine.run().

        A success:true-but-flagged payload placed in the crawl cache
        short-circuits the fresh scrape on the hit, fails the same per-page
        ``is_barrier_flagged`` gate as a fresh scrape, and is never recorded
        as a crawled page.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        class _FakeCrawlCache:
            """check_cache() returns (use_cached, cached_data_dict, err)."""

            def __init__(self):
                self.store: dict[str, dict] = {}

            def check_cache(self, url, max_age_ms=None, min_age_ms=None):
                entry = self.store.get(url)
                return (entry is not None), entry, None

            def set(self, url, result, ttl_ms=None):
                self.store[url] = result

        cache = _FakeCrawlCache()
        cache.set("https://example.com/", flagged_success())

        calls: list[str] = []

        class Scraper:
            async def scrape(self, url, **kwargs):
                calls.append(url)
                return clean_success("# Should never be reached")

        engine = CrawlEngine(
            Scraper(),  # type: ignore[arg-type]
            options=CrawlOptions(
                max_pages=5,
                max_depth=0,
                sitemap_mode="skip",
                scrape_options={"max_age": 3600000},
            ),
            crawl_cache=cache,  # type: ignore[arg-type]
        )
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        assert calls == [], "poisoned cache hit must short-circuit the scrape"
        assert result.pages == [], (
            f"poisoned cached payload must never be recorded as a page: {result.pages}"
        )
        child_errors = [
            e for e in result.errors if e.get("url") == "https://example.com/"
        ]
        assert len(child_errors) == 1
        assert child_errors[0].get("error_code") == "BARRIER_DETECTED"
        assert result.completed == 0

    def test_clean_cached_entry_passes(self):
        from agent.barrier_guard import is_barrier_flagged

        assert is_barrier_flagged(clean_success()) is False

    @pytest.mark.asyncio
    async def test_scraper_cache_hit_regated_by_assess_quality_warning(
        self, seeded_scraper_cache
    ):
        """fetch.py's cache-hit branch re-gates through the real cache read.

        A poisoned entry (bare challenge-markdown success) seeded behind the
        genuine ``_check_cache`` seam comes back re-assessed by
        ``_add_quality`` carrying the block-page warning — including when the
        thin output would ALSO trip the volume gate (block-page precedence).
        """
        from scraper.cache import _check_cache, _scrape_cache_key
        from scraper.fetch_quality import html_to_markdown

        fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "html"
        f1_html = (fixtures / "fastly-challenge-full.html").read_text(encoding="utf-8")
        poisoned_url = "https://poisoned.test/regate"
        seeded_scraper_cache(
            {
                "markdown": html_to_markdown(f1_html),
                "url": poisoned_url,
                "source": "playwright",
                "source_html_size": len(f1_html),
            }
        )

        cached = await _check_cache(poisoned_url)
        assert cached is not None, (
            f"seeded entry {_scrape_cache_key(poisoned_url)} must be served"
        )

        from scraper.fetch_quality import _add_quality

        scored = _add_quality(cached)

        assert scored["quality"]["checks"]["block_detected"] in ("warn", "fail")
        warning = scored.get("warning") or ""
        assert warning.startswith("Block-page content detected"), (
            f"poisoned hit must carry the block-page warning: {warning!r}"
        )


# ── Shared helper contract ───────────────────────────────────────


class TestBarrierGuardHelper:
    def test_warned_payload_is_flagged(self):
        from agent.barrier_guard import is_barrier_flagged

        payload = clean_success()
        payload["warning"] = "degraded"
        assert is_barrier_flagged(payload) is True

    def test_block_fail_payload_is_flagged_without_warning_key(self):
        from agent.barrier_guard import is_barrier_flagged

        payload = flagged_success()
        payload.pop("warning")
        assert is_barrier_flagged(payload) is True

    def test_missing_keys_are_tolerated(self):
        from agent.barrier_guard import is_barrier_flagged

        assert is_barrier_flagged({"success": True}) is False
        assert is_barrier_flagged({}) is False
        assert is_barrier_flagged(None) is False  # type: ignore[arg-type]

    def test_block_narrow_predicate_ignores_non_block_warnings(self):
        from agent.barrier_guard import is_block_flagged

        low_yield = clean_success()
        low_yield["warning"] = "Low yield: truncated body."
        assert is_block_flagged(low_yield) is False
