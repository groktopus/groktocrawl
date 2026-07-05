"""Tests for agent-svc/agent/research.py — research loop helpers and pipelines."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestValidateJsonIfSchema:
    def setup_method(self):
        from agent.research import _validate_json_if_schema

        self.func = _validate_json_if_schema

    def test_no_schema_does_nothing(self):
        self.func('{"key": "value"}', None)

    def test_valid_json_passes(self):
        self.func('{"key": "value"}', {"type": "object"})

    def test_strips_code_fences(self):
        self.func('```json\n{"key": "value"}\n```', {"type": "object"})
        self.func('```\n{"key": "value"}\n```', {"type": "object"})

    def test_invalid_json_logs_warning(self, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        self.func("not json", {"type": "object"})
        assert "not valid JSON" in caplog.text


class TestIsVideoPlatformUrl:
    def setup_method(self):
        from agent.research import _is_video_platform_url

        self.func = _is_video_platform_url

    def test_youtube(self):
        assert self.func("https://youtube.com/watch?v=abc123")
        assert self.func("https://youtu.be/abc123")
        assert self.func("https://www.youtube.com/watch?v=abc123")

    def test_tiktok(self):
        assert self.func("https://tiktok.com/@user/video/123")
        assert self.func("https://www.tiktok.com/@user/video/123")
        assert self.func("https://vm.tiktok.com/abcdef/")

    def test_instagram(self):
        assert self.func("https://instagram.com/p/abc123")
        assert self.func("https://www.instagram.com/p/abc123")

    def test_non_video_platform(self):
        assert not self.func("https://example.com/article")
        assert not self.func("https://github.com/user/repo")

    def test_edge_cases(self):
        assert not self.func("")
        assert not self.func("not-a-url")


@pytest.fixture
def mock_scraper():
    """Return a ScraperClient-mimicking object with scrape + scrape_with_fallback."""
    m = MagicMock()
    m.scrape = AsyncMock()

    async def _fb(url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None):
        return await m.scrape(url, force_browser=False, scrape_options=scrape_options)

    m.scrape_with_fallback = _fb
    return m


class TestScrapeUrls:
    @pytest.mark.asyncio
    async def test_scrapes_urls(self, mock_scraper):
        from agent.research import _scrape_urls

        mock_scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Hello", "source": "llms.txt"},
        }

        docs, details = await _scrape_urls(
            ["https://a.com"],
            mock_scraper,
            min_sources=1,
            max_attempts=5,
        )

        assert len(docs) == 1
        assert "a.com" in docs[0]
        assert details[0]["url"] == "https://a.com"

    @pytest.mark.asyncio
    async def test_respects_min_sources(self, mock_scraper):
        from agent.research import _scrape_urls

        mock_scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }

        docs, _details = await _scrape_urls(
            ["https://a.com", "https://b.com", "https://c.com"],
            mock_scraper,
            min_sources=2,
            max_attempts=5,
        )

        assert len(docs) >= 2

    @pytest.mark.asyncio
    async def test_handles_scrape_failures(self, mock_scraper):
        from agent.research import _scrape_urls

        mock_scraper.scrape.side_effect = [
            {"success": False, "error": "Not found"},
            {"success": True, "data": {"markdown": "# Content", "source": "test"}},
        ]

        docs, details = await _scrape_urls(
            ["https://fail.com", "https://ok.com"],
            mock_scraper,
            min_sources=1,
            max_attempts=5,
        )

        assert len(docs) == 1
        assert details[0]["url"] == "https://ok.com"

    @pytest.mark.asyncio
    async def test_handles_exception(self, mock_scraper):
        from agent.research import _scrape_urls

        mock_scraper.scrape.side_effect = RuntimeError("network error")

        docs, _details = await _scrape_urls(
            ["https://err.com"],
            mock_scraper,
            min_sources=1,
            max_attempts=5,
        )
        assert len(docs) == 0


class TestRunResearch:
    @pytest.fixture
    def mocks(self):
        """Patch all three clients used by run_research."""
        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.close = AsyncMock()

        return searxng, scraper, llm

    @pytest.mark.asyncio
    async def test_with_urls(self, mocks):
        from agent.research import run_research

        searxng, scraper, llm = mocks

        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Research content", "source": "test"},
        }
        llm.generate.return_value = "Here is the synthesized answer."

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="What is AI?",
                urls=["https://example.com/ai"],
            )

        assert result["result"] == "Here is the synthesized answer."
        assert len(result["sources"]) == 1
        assert result["sources"][0] == "https://example.com/ai"
        searxng.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_urls_searches(self, mocks):
        from agent.research import run_research

        searxng, scraper, llm = mocks

        searxng.search.return_value = (
            [{"url": "https://result.com", "title": "Result", "description": "desc"}],
            MagicMock(),
        )
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        llm.generate.return_value = "Answer from search."

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(prompt="Tell me about AI")

        assert result["result"] == "Answer from search."
        searxng.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_scraped_content_returns_fallback(self, mocks):
        from agent.research import run_research

        searxng, scraper, llm = mocks

        scraper.scrape.return_value = {
            "success": False,
            "error": "Not found",
        }

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="Anything?",
                urls=["https://example.com/missing"],
            )

        assert "unable to find or scrape" in result["result"].lower()
        assert result["sources"] == []

    @pytest.mark.asyncio
    async def test_passes_schema_to_llm(self, mocks):
        from agent.research import run_research

        searxng, scraper, llm = mocks

        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        llm.generate.return_value = '{"name": "AI"}'
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch("agent.research.loop._generate_research_plan") as mock_plan,
        ):
            mock_plan.return_value = {
                "reasoning": "",
                "research_strategy": "focused",
                "focused_queries": ["Extract name"],
            }
            result = await run_research(
                prompt="Extract name",
                urls=["https://example.com"],
                schema=schema,
            )

        assert result["result"] == '{"name": "AI"}'
        # _generate_research_plan is patched out; llm.generate is called for
        # synthesis + gap detection (multi-pass). At least one call has schema.
        assert llm.generate.call_count >= 2
        schema_calls = [
            c for c in llm.generate.call_args_list if c[1].get("schema") == schema
        ]
        assert len(schema_calls) >= 1

    @pytest.mark.asyncio
    async def test_requested_model_override(self, mocks):
        from agent.research import run_research

        searxng, scraper, llm = mocks

        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "x", "source": "t"},
        }
        llm.generate.return_value = "ok"

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="test",
                urls=["https://x.com"],
                llm_model="default-model",
                requested_model="gpt-4o",
            )

        assert result["result"] == "ok"
        # LLMClient should have been constructed with model="gpt-4o"
        llm_call = llm.generate.call_args[1]
        assert "system_prompt" in llm_call

    @pytest.mark.asyncio
    async def test_sources_uses_filtered_list(self, mocks):
        """Verify that result['sources'] is a list of URL strings from source_details."""
        from agent.research import run_research

        searxng, scraper, llm = mocks

        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        llm.generate.return_value = "Answer."

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_research(
                prompt="test",
                urls=["https://x.com", "https://y.com"],
            )

        assert isinstance(result["sources"], list)
        assert len(result["sources"]) > 0
        assert all(isinstance(s, str) for s in result["sources"])


class TestRunAnswer:
    @pytest.mark.asyncio
    async def test_full_pipeline_returns_answer(self):
        from agent.research import run_answer

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "description": "An example page",
                }
            ],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {
                "markdown": "# Example Page\n\nThis is the content.",
                "source": "test",
            },
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.generate.return_value = "Based on [1] the answer is 42."
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_answer(query="What is the answer?", num_sources=1)

        assert "answer" in result
        assert "Based on" in result["answer"]
        assert len(result["sources"]) == 1
        assert len(result["citations"]) == 1
        assert result["citations"][0]["index"] == 1
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_no_content_fallback(self):
        from agent.research import run_answer

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = ([], MagicMock())
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_answer(query="Anything?")

        assert "unable to find or scrape" in result["answer"].lower()
        assert result["sources"] == []
        assert result["citations"] == []


class TestRunExtract:
    """Test run_extract — structured extraction from given URLs."""

    @pytest.fixture
    def mocks(self):
        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.close = AsyncMock()

        return scraper, llm

    @pytest.mark.asyncio
    async def test_with_urls_only(self, mocks):
        from agent.research import run_extract

        scraper, llm = mocks
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Extracted content", "source": "test"},
        }
        llm.generate.return_value = '{"name": "Extracted Data"}'

        with (
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_extract(
                urls=["https://example.com"],
            )

        assert result["result"] == '{"name": "Extracted Data"}'
        assert len(result["sources"]) == 1
        assert result["sources"][0] == "https://example.com"
        assert len(result["source_details"]) == 1

    @pytest.mark.asyncio
    async def test_with_prompt(self, mocks):
        from agent.research import run_extract

        scraper, llm = mocks
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        llm.generate.return_value = "Extracted info"

        with (
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_extract(
                urls=["https://example.com"],
                prompt="Extract the company name and revenue",
            )

        assert result["result"] == "Extracted info"
        # Verify custom prompt passed through
        llm_call = llm.generate.call_args[1]
        assert "Extract the company name and revenue" in llm_call["user_prompt"]

    @pytest.mark.asyncio
    async def test_with_schema(self, mocks):
        from agent.research import run_extract

        scraper, llm = mocks
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        llm.generate.return_value = '{"name": "test"}'
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        with (
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch("agent.research.loop._validate_json_if_schema") as mock_validate,
        ):
            result = await run_extract(
                urls=["https://example.com"],
                schema=schema,
            )

        assert result["result"] == '{"name": "test"}'
        mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_content_returns_fallback(self, mocks):
        from agent.research import run_extract

        scraper, llm = mocks
        scraper.scrape.return_value = {
            "success": False,
            "error": "Not found",
        }

        with (
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            result = await run_extract(
                urls=["https://example.com/missing"],
            )

        assert "No content could be extracted" in result["result"]
        assert result["sources"] == []


class TestRunResearchStream:
    """Test run_research_stream — streaming version of the research loop."""

    @pytest.mark.asyncio
    async def test_yields_discovery_then_synthesis(self):
        """Verify event sequence: sources_pending → source_scraped → sources → token → done."""
        from agent.research import run_research_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Example", "description": "desc"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Streamed content", "source": "test"},
        }
        scraper.close = AsyncMock()

        # generate_stream must be a real async generator, not an AsyncMock
        async def _stream(*args, **kwargs):
            yield {"type": "token", "content": "Here is "}
            yield {"type": "token", "content": "the answer."}
            yield {"type": "done", "full_content": "Here is the answer."}

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.generate.return_value = (
            '{"research_strategy": "focused", "focused_queries": ["What is AI?"]}'
        )
        llm.generate_stream = _stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_research_stream(prompt="What is AI?"):
                events.append(event)

        # Verify event sequence — Phase 0 planning → Phase 1 discovery → Phase 2 synthesis
        assert len(events) >= 7
        # Phase 0: planning
        assert events[0]["type"] == "status"
        assert events[0]["state"] == "planning"
        assert events[1]["type"] == "research_plan"
        assert "queries" in events[1]
        # Phase 1: research_pass + searching + discovery
        assert events[2]["type"] == "research_pass"
        assert events[2]["pass"] == 1
        assert events[3]["type"] == "status"
        assert events[3]["state"] == "searching"
        assert events[4]["type"] == "sources_pending"
        assert len(events[4]["sources"]) == 1
        # source_scraped events
        scraped_events = [e for e in events if e["type"] == "source_scraped"]
        assert len(scraped_events) >= 1
        # sources event
        sources_events = [e for e in events if e["type"] == "sources"]
        assert len(sources_events) >= 1
        # token events
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) >= 1
        # done event
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_schema_mode_no_tokens(self):
        """Verify schema mode yields done directly without token events."""
        from agent.research import run_research_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Ex", "description": "d"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(return_value='{"key": "value"}')
        llm.close = AsyncMock()

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_research_stream(
                prompt="Extract data", schema=schema
            ):
                events.append(event)

        types = [e["type"] for e in events]
        assert "sources_pending" in types
        assert "sources" in types
        assert "done" in types
        assert "token" not in types
        assert events[-1]["type"] == "done"
        assert events[-1]["result"] == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_no_content_early_done(self):
        """Verify no scraped content yields early done with fallback."""
        from agent.research import run_research_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Ex", "description": "d"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": False,
            "error": "Not found",
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.generate.return_value = (
            '{"research_strategy": "focused", "focused_queries": ["Anything?"]}'
        )
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_research_stream(prompt="Anything?"):
                events.append(event)

        types = [e["type"] for e in events]
        assert "done" in types
        assert "unable to find or scrape" in events[-1]["result"].lower()

    @pytest.mark.asyncio
    async def test_error_from_llm(self):
        """Verify error from llm.generate_stream yields error event."""
        from agent.research import run_research_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Ex", "description": "d"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock()
        llm.generate.return_value = (
            '{"research_strategy": "focused", "focused_queries": ["Test"]}'
        )

        async def _error_stream(*args, **kwargs):
            yield {"type": "error", "content": "LLM rate limit exceeded"}

        llm.generate_stream = _error_stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_research_stream(prompt="Test"):
                events.append(event)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "rate limit" in error_events[0]["content"].lower()


class TestRunAnswerStream:
    """Test run_answer_stream — streaming grounded Q&A pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Verify event sequence: sources_pending → sources → token → done."""
        from agent.research import run_answer_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "description": "A test page",
                }
            ],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Q&A content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()

        async def _stream(*args, **kwargs):
            yield {"type": "token", "content": "Based on [1] "}
            yield {"type": "token", "content": "the answer is 42."}
            yield {"type": "done", "full_content": "Based on [1] the answer is 42."}

        llm.generate_stream = _stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_answer_stream(
                query="What is the answer?", num_sources=1
            ):
                events.append(event)

        types = [e["type"] for e in events]
        assert "sources_pending" in types
        assert "sources" in types
        assert "token" in types
        assert "done" in types

        done_event = next(e for e in events if e["type"] == "done")
        assert "answer" in done_event
        assert done_event["answer"] == "Based on [1] the answer is 42."
        # Citations should be parsed
        assert len(done_event["citations"]) >= 1
        assert "latency_ms" in done_event

    @pytest.mark.asyncio
    async def test_no_content_fallback(self):
        """Verify no search results yields fallback answer."""
        from agent.research import run_answer_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = ([], MagicMock())
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_answer_stream(query="Anything?"):
                events.append(event)

        done_event = next(e for e in events if e["type"] == "done")
        assert "No relevant web pages found" in done_event["answer"]
        assert done_event["citations"] == []

    @pytest.mark.asyncio
    async def test_citation_parsing(self):
        """Verify [N] markers in LLM response produce correct citations."""
        from agent.research import run_answer_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [
                {
                    "url": "https://source-a.com",
                    "title": "Source A",
                    "description": "desc a",
                },
                {
                    "url": "https://source-b.com",
                    "title": "Source B",
                    "description": "desc b",
                },
            ],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()

        async def _stream(*args, **kwargs):
            yield {"type": "token", "content": "Per [1] and [2], the answer is clear."}
            yield {
                "type": "done",
                "full_content": "Per [1] and [2], the answer is clear.",
            }

        llm.generate_stream = _stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
        ):
            events = []
            async for event in run_answer_stream(
                query="Test with citations", num_sources=2
            ):
                events.append(event)

        done_event = next(e for e in events if e["type"] == "done")
        citations = done_event["citations"]
        assert len(citations) == 2
        assert citations[0]["index"] == 1
        assert citations[1]["index"] == 2

    @pytest.mark.asyncio
    async def test_keyword_retrieval_mode(self):
        """Verify _rerank_answer_sources NOT called for keyword mode."""
        from agent.research import run_answer_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Ex", "description": "d"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()

        async def _stream(*args, **kwargs):
            yield {"type": "done", "full_content": "Answer."}

        llm.generate_stream = _stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch("agent.research.rerank._rerank_answer_sources") as mock_rerank,
        ):
            events = []
            async for event in run_answer_stream(
                query="Test", retrieval_mode="keyword"
            ):
                events.append(event)

        mock_rerank.assert_not_called()

    @pytest.mark.asyncio
    async def test_semantic_retrieval_mode(self):
        """Verify _rerank_answer_sources IS called for semantic mode."""
        from agent.research import run_answer_stream

        searxng = MagicMock()
        searxng.search = AsyncMock()
        searxng.search.return_value = (
            [{"url": "https://example.com", "title": "Ex", "description": "d"}],
            MagicMock(),
        )
        searxng.close = AsyncMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()

        async def _stream(*args, **kwargs):
            yield {"type": "done", "full_content": "Answer."}

        llm.generate_stream = _stream
        llm.close = AsyncMock()

        with (
            patch("agent.research.loop.SearXNGClient", return_value=searxng),
            patch("agent.research.loop.ScraperClient", return_value=scraper),
            patch("agent.research.loop.LLMClient", return_value=llm),
            patch("agent.research.rerank._rerank_answer_sources") as mock_rerank,
        ):
            events = []
            async for event in run_answer_stream(
                query="Test", retrieval_mode="semantic"
            ):
                events.append(event)

        mock_rerank.assert_called_once()


class TestRunRichSearch:
    """Test run_rich_search — search result enrichment with LLM."""

    @pytest.mark.asyncio
    async def test_enrichment(self):
        """Verify returns dict with content and grounding."""
        from agent.research import run_rich_search

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "Desc A"},
            {"url": "https://b.com", "title": "B", "description": "Desc B"},
        ]

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Full content from A", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(
            return_value="Result A: full content. Result B: full content."
        )
        llm.close = AsyncMock()

        with (
            patch("agent.research.search.ScraperClient", return_value=scraper),
            patch("agent.research.search.LLMClient", return_value=llm),
        ):
            result = await run_rich_search(
                search_results=search_results,
                query="test query",
                limit=2,
            )

        assert result is not None
        assert "content" in result
        assert "grounding" in result
        assert len(result["grounding"]) == 2
        assert result["grounding"][0]["url"] == "https://a.com"

    @pytest.mark.asyncio
    async def test_with_output_schema(self):
        """Verify output_schema produces parsed JSON content."""
        from agent.research import run_rich_search

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "Desc A"},
        ]

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "Name: TestCorp", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(return_value='{"company": "TestCorp"}')
        llm.close = AsyncMock()

        schema = {"type": "object", "properties": {"company": {"type": "string"}}}

        with (
            patch("agent.research.search.ScraperClient", return_value=scraper),
            patch("agent.research.search.LLMClient", return_value=llm),
        ):
            result = await run_rich_search(
                search_results=search_results,
                query="test",
                limit=1,
                output_schema=schema,
            )

        assert result is not None
        assert result["content"] == {"company": "TestCorp"}
        assert "grounding" in result

    @pytest.mark.asyncio
    async def test_json_block_in_markdown(self):
        """Verify ```json block inside markdown is parsed."""
        from agent.research import run_rich_search

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "Desc"},
        ]

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(
            return_value='Here is the data:\n```json\n{"company": "TestCorp", "revenue": 100}\n```'
        )
        llm.close = AsyncMock()

        schema = {"type": "object"}

        with (
            patch("agent.research.search.ScraperClient", return_value=scraper),
            patch("agent.research.search.LLMClient", return_value=llm),
        ):
            result = await run_rich_search(
                search_results=search_results,
                query="test",
                limit=1,
                output_schema=schema,
            )

        assert result is not None
        assert result["content"]["company"] == "TestCorp"
        assert result["content"]["revenue"] == 100

    @pytest.mark.asyncio
    async def test_no_results_returns_none(self):
        """Verify no enriched results returns None."""
        from agent.research import run_rich_search

        # Empty search results
        scraper = MagicMock()
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.close = AsyncMock()

        with (
            patch("agent.research.search.ScraperClient", return_value=scraper),
            patch("agent.research.search.LLMClient", return_value=llm),
        ):
            result = await run_rich_search(
                search_results=[],
                query="test",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_system_prompt_override(self):
        """Verify custom system_prompt is passed to LLM."""
        from agent.research import run_rich_search

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "Desc"},
        ]

        scraper = MagicMock()
        scraper.scrape = AsyncMock()

        async def _fb(
            url, generic_timeout=20.0, browser_timeout=45.0, scrape_options=None
        ):
            return await scraper.scrape(
                url, force_browser=False, scrape_options=scrape_options
            )

        scraper.scrape_with_fallback = _fb
        scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "Content", "source": "test"},
        }
        scraper.close = AsyncMock()

        llm = MagicMock()
        llm.generate = AsyncMock(return_value="Custom result")
        llm.close = AsyncMock()

        custom_prompt = "Focus only on recent results from 2025."

        with (
            patch("agent.research.search.ScraperClient", return_value=scraper),
            patch("agent.research.search.LLMClient", return_value=llm),
        ):
            result = await run_rich_search(
                search_results=search_results,
                query="test",
                limit=1,
                system_prompt=custom_prompt,
            )

        assert result is not None
        llm_call = llm.generate.call_args[1]
        assert llm_call["system_prompt"] == custom_prompt


class TestRerankAnswerSources:
    """Test _rerank_answer_sources — search result reranking for answer pipeline."""

    @pytest.mark.asyncio
    async def test_keyword_mode_passthrough(self):
        """Verify keyword mode returns search_results unchanged."""
        from agent.research import _rerank_answer_sources

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "desc a"},
            {"url": "https://b.com", "title": "B", "description": "desc b"},
        ]

        result = await _rerank_answer_sources(
            search_results=search_results,
            query="test query",
            retrieval_mode="keyword",
            semantic_url="http://semantic:8003",
            scraper_url="http://scraper:8001",
            limit=5,
        )

        assert result == search_results

    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self):
        """Verify empty search_results returns []."""
        from agent.research import _rerank_answer_sources

        result = await _rerank_answer_sources(
            search_results=[],
            query="test",
            retrieval_mode="semantic",
            semantic_url="http://semantic:8003",
            scraper_url="http://scraper:8001",
            limit=5,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_semantic_mode(self):
        """Verify semantic mode uses embed and returns reranked results."""
        from agent.research import _rerank_answer_sources

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "desc a"},
            {"url": "https://b.com", "title": "B", "description": "desc b"},
        ]

        mock_semantic = MagicMock()
        mock_semantic.embed = AsyncMock(
            return_value=[
                [1.0, 0.0],  # query embedding
                [0.9, 0.0],  # result A embedding -> cos sim = 0.9
                [0.1, 0.0],  # result B embedding -> cos sim = 0.1
            ]
        )
        mock_semantic.close = AsyncMock()

        mock_scraper = MagicMock()
        mock_scraper.scrape = AsyncMock()
        mock_scraper.scrape.return_value = {
            "success": True,
            "data": {"markdown": "# Content", "source": "test"},
        }
        mock_scraper.close = AsyncMock()

        with (
            patch("agent.research.rerank.SemanticClient", return_value=mock_semantic),
            patch("agent.research.rerank.ScraperClient", return_value=mock_scraper),
        ):
            result = await _rerank_answer_sources(
                search_results=search_results,
                query="test",
                retrieval_mode="semantic",
                semantic_url="http://semantic:8003",
                scraper_url="http://scraper:8001",
                limit=5,
            )

        # Result A (higher similarity) should be first
        assert len(result) == 2
        assert result[0]["url"] == "https://a.com"
        mock_semantic.embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_vector_mode(self):
        """Verify vector mode uses search_vector and returns results."""
        from agent.research import _rerank_answer_sources

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "desc a"},
        ]

        mock_semantic = MagicMock()
        mock_semantic.search_vector = AsyncMock(
            return_value=[
                {"url": "https://vector-result.com", "title": "Vector Result"},
                {"url": "https://another.com", "title": "Another"},
            ]
        )
        mock_semantic.close = AsyncMock()

        mock_scraper = MagicMock()
        mock_scraper.close = AsyncMock()

        with (
            patch("agent.research.rerank.SemanticClient", return_value=mock_semantic),
            patch("agent.research.rerank.ScraperClient", return_value=mock_scraper),
        ):
            result = await _rerank_answer_sources(
                search_results=search_results,
                query="test",
                retrieval_mode="vector",
                semantic_url="http://semantic:8003",
                scraper_url="http://scraper:8001",
                limit=5,
            )

        assert len(result) == 2
        assert result[0]["url"] == "https://vector-result.com"
        mock_semantic.search_vector.assert_called_once()

    @pytest.mark.asyncio
    async def test_hybrid_vector_mode(self):
        """Verify hybrid_vector merges keyword + vector results, deduplicated."""
        from agent.research import _rerank_answer_sources

        search_results = [
            {"url": "https://a.com", "title": "A", "description": "desc a"},
            {"url": "https://b.com", "title": "B", "description": "desc b"},
        ]

        mock_semantic = MagicMock()
        mock_semantic.search_vector = AsyncMock(
            return_value=[
                {"url": "https://a.com", "title": "A"},  # duplicate
                {"url": "https://c.com", "title": "C"},  # new
            ]
        )
        mock_semantic.close = AsyncMock()

        mock_scraper = MagicMock()
        mock_scraper.close = AsyncMock()

        with (
            patch("agent.research.rerank.SemanticClient", return_value=mock_semantic),
            patch("agent.research.rerank.ScraperClient", return_value=mock_scraper),
        ):
            result = await _rerank_answer_sources(
                search_results=search_results,
                query="test",
                retrieval_mode="hybrid_vector",
                semantic_url="http://semantic:8003",
                scraper_url="http://scraper:8001",
                limit=5,
            )

        # Should have 3 unique results (a.com, b.com, c.com), deduplicated
        assert len(result) == 3
        urls = [r["url"] for r in result]
        assert urls == ["https://a.com", "https://b.com", "https://c.com"]
