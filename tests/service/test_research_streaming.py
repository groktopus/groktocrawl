"""Focused tests for research SSE memory admission."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestResearchMemoryAdmission:
    @pytest.mark.asyncio
    async def test_stores_only_valid_sourced_artifacts(self):
        from agent.research.memory import admit_research_memory

        memory = MagicMock()
        memory.store = AsyncMock(return_value="memory-id")
        sources = [{"url": "https://example.com", "title": "Example"}]

        artifact_id = await admit_research_memory(
            memory,
            prompt="Question",
            artifact="Answer [1](https://example.com)",
            source_details=sources,
            model="test-model",
            citation_style="inline",
        )

        assert artifact_id == "memory-id"
        assert memory.store.call_args.kwargs["sources"] == sources
        assert (
            memory.store.call_args.kwargs["artifact"]
            == "Answer [1](https://example.com)"
        )

        for artifact, source_details in (
            ("", sources),
            ("Error: handled", sources),
            ("Answer", []),
        ):
            await admit_research_memory(
                memory,
                prompt="Question",
                artifact=artifact,
                source_details=source_details,
                model="test-model",
                citation_style="inline",
            )
        memory.store.assert_called_once()


class TestLiveResearchStreamingAdmission:
    @staticmethod
    async def _events(*args, **kwargs):
        yield {"type": "status", "state": "planning"}
        yield {
            "type": "done",
            "result": "Answer [1](https://example.com)",
            "sources": ["https://example.com"],
            "source_details": [
                {"url": "https://example.com", "title": "Example", "source": "test"}
            ],
            "latency_ms": 4,
        }

    @pytest.mark.asyncio
    async def test_cache_miss_stores_transformed_result_and_rich_sources(self):
        from agent.models import CitationStyle
        from agent.research.streaming import stream_research_live

        memory = MagicMock()
        memory.store = AsyncMock(return_value="memory-id")
        with patch("agent.research.streaming.run_research_stream", self._events):
            chunks = [
                chunk
                async for chunk in stream_research_live(
                    prompt="Question",
                    urls=None,
                    schema=None,
                    searxng_url="http://searxng",
                    scraper_url="http://scraper",
                    llm_base_url="http://llm",
                    llm_api_key="key",
                    llm_model="test-model",
                    requested_model=None,
                    max_searches_per_request=5,
                    include_images=False,
                    citation_style=CitationStyle.compact,
                    research_memory=memory,
                )
            ]

        assert chunks[-1] == "data: [DONE]\n\n"
        assert (
            memory.store.call_args.kwargs["artifact"]
            == "Answer [1](https://example.com)"
        )
        assert memory.store.call_args.kwargs["sources"] == [
            {"url": "https://example.com", "title": "Example", "source": "test"}
        ]
        done = json.loads(chunks[-2].removeprefix("data: "))
        assert done["type"] == "done"

    @pytest.mark.asyncio
    async def test_memory_store_failure_does_not_truncate_stream(self):
        from agent.models import CitationStyle
        from agent.research.streaming import stream_research_live

        memory = MagicMock()
        memory.store = AsyncMock(side_effect=RuntimeError("memory unavailable"))
        with patch("agent.research.streaming.run_research_stream", self._events):
            chunks = [
                chunk
                async for chunk in stream_research_live(
                    prompt="Question",
                    urls=None,
                    schema=None,
                    searxng_url="http://searxng",
                    scraper_url="http://scraper",
                    llm_base_url="http://llm",
                    llm_api_key="key",
                    llm_model="test-model",
                    requested_model=None,
                    max_searches_per_request=5,
                    include_images=False,
                    citation_style=CitationStyle.compact,
                    research_memory=memory,
                )
            ]

        assert any('"type": "done"' in chunk for chunk in chunks)
        assert chunks[-1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_error_preserves_classification_and_has_no_done_marker(self):
        from agent.models import CitationStyle
        from agent.research.streaming import stream_research_live

        async def error_events(*args, **kwargs):
            yield {"type": "status", "state": "synthesizing"}
            yield {
                "type": "error",
                "content": "LLM provider rate limit exceeded",
                "classification": "retryable",
                "retry_after_seconds": 2.0,
            }

        with patch("agent.research.streaming.run_research_stream", error_events):
            chunks = [
                chunk
                async for chunk in stream_research_live(
                    prompt="Question",
                    urls=None,
                    schema=None,
                    searxng_url="http://searxng",
                    scraper_url="http://scraper",
                    llm_base_url="http://llm",
                    llm_api_key="key",
                    llm_model="test-model",
                    requested_model=None,
                    max_searches_per_request=5,
                    include_images=False,
                    citation_style=CitationStyle.inline,
                )
            ]

        error = json.loads(chunks[-1].removeprefix("data: "))
        assert error == {
            "type": "error",
            "content": "LLM provider rate limit exceeded",
            "classification": "retryable",
            "retry_after_seconds": 2.0,
        }
        assert "data: [DONE]\n\n" not in chunks


@pytest.mark.asyncio
async def test_answer_sse_error_preserves_metadata_and_omits_done():
    from agent.routes.agent import _serialize_answer_stream

    async def events():
        yield {"type": "sources", "sources": []}
        yield {
            "type": "error",
            "content": "LLM provider rate limit exceeded",
            "classification": "retryable",
            "retry_after_seconds": 2.0,
        }

    chunks = [chunk async for chunk in _serialize_answer_stream(events())]
    error = json.loads(chunks[-1].removeprefix("data: "))
    assert error == {
        "type": "error",
        "content": "LLM provider rate limit exceeded",
        "classification": "retryable",
        "retry_after_seconds": 2.0,
    }
    assert "data: [DONE]\n\n" not in chunks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "classification", "retry_after"),
    [
        ("retryable", "retryable", 2.0),
        ("non_retryable", "non_retryable", None),
    ],
)
async def test_schema_research_stream_classifies_provider_failures(
    provider_error, classification, retry_after
):
    from agent.exceptions import ProviderOutputError, RetryableRateLimitError
    from agent.research.loop import _run_research_events

    searxng = MagicMock()
    searxng.close = AsyncMock()
    scraper = MagicMock()
    scraper.close = AsyncMock()
    llm = MagicMock()
    llm.close = AsyncMock()
    if provider_error == "retryable":
        llm.generate = AsyncMock(
            side_effect=RetryableRateLimitError(
                "fixture rate limit", retry_after_seconds=retry_after
            )
        )
    else:
        llm.generate = AsyncMock(side_effect=ProviderOutputError("fixture failure"))

    plan = {
        "focused_queries": ["question"],
        "research_strategy": "focused",
        "reasoning": "fixture",
    }
    discovered = {
        "context": "source context",
        "source_details": [
            {
                "url": "https://example.com",
                "source": "fixture",
                "char_count": 14,
            }
        ],
        "search_results": [{"url": "https://example.com"}],
    }
    with (
        patch("agent.research.loop.SearXNGClient", return_value=searxng),
        patch("agent.research.loop.ScraperClient", return_value=scraper),
        patch("agent.research.loop.LLMClient", return_value=llm),
        patch(
            "agent.research.loop._generate_research_plan",
            new=AsyncMock(return_value=plan),
        ),
        patch(
            "agent.research.loop._run_research_discover_and_scrape",
            new=AsyncMock(return_value=discovered),
        ),
    ):
        events = [
            event
            async for event in _run_research_events(
                "question",
                schema={"type": "object"},
                llm_model="fixture",
                search_type="focused",
                stream_tokens=True,
            )
        ]

    assert events[-1]["type"] == "error"
    assert events[-1]["classification"] == classification
    if retry_after is not None:
        assert events[-1]["retry_after_seconds"] == retry_after
    assert not any(event["type"] == "done" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "classification", "retry_after"),
    [
        ("retryable", "retryable", 3.0),
        ("non_retryable", "non_retryable", None),
    ],
)
async def test_schema_answer_stream_classifies_provider_failures(
    provider_error, classification, retry_after
):
    from agent.exceptions import ProviderOutputError, RetryableRateLimitError
    from agent.research.loop import run_answer_stream

    llm = MagicMock()
    llm.close = AsyncMock()
    if provider_error == "retryable":
        llm.generate = AsyncMock(
            side_effect=RetryableRateLimitError(
                "fixture rate limit", retry_after_seconds=retry_after
            )
        )
    else:
        llm.generate = AsyncMock(side_effect=ProviderOutputError("fixture failure"))

    discovered = {
        "context": "source context",
        "source_map": [{"url": "https://example.com"}],
    }
    searxng = MagicMock()
    searxng.search = AsyncMock(
        return_value=([{"url": "https://example.com"}], {"status": "ok"})
    )
    searxng.close = AsyncMock()
    scraper = MagicMock()
    scraper.close = AsyncMock()
    with (
        patch("agent.research.loop.SearXNGClient", return_value=searxng),
        patch("agent.research.loop.ScraperClient", return_value=scraper),
        patch("agent.research.loop.LLMClient", return_value=llm),
        patch(
            "agent.research.loop._scrape_answer_sources",
            new=AsyncMock(return_value=[]),
        ),
        patch("agent.research.loop._build_answer_context", return_value=discovered),
    ):
        events = [
            event
            async for event in run_answer_stream(
                "question",
                output_schema={"type": "object"},
                llm_model="fixture",
            )
        ]

    assert events[-1]["type"] == "error"
    assert events[-1]["classification"] == classification
    if retry_after is not None:
        assert events[-1]["retry_after_seconds"] == retry_after
    assert not any(event["type"] == "done" for event in events)
