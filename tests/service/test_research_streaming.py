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
            "result": "Answer [1]",
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
