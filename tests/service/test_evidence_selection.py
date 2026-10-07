"""Fixtures proving late evidence stays accessible without unbounded context."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from agent.models import AnswerRequest, SearchRequest
from agent.research.evidence import build_evidence, select_passages


def late_document():
    return (
        "General introduction about gardening.\n" * 500
        + "\n## Capacitor measurements\n| Device | Capacitance | Voltage |\n"
        + "| Amber | 470.25 uF | 12.8 V |\n| Violet | 921.75 uF | 4.9 V |\n"
    )


def test_late_numeric_table_is_verbatim_and_offsets_bind_to_full_source():
    text = late_document()
    selected = select_passages(
        text, "capacitor Amber Violet measurements voltage", 2400
    )
    body = "\n".join(s["text"] for s in selected["spans"])
    assert "470.25 uF" in body and "921.75 uF" in body
    assert "12.8 V" in body and "4.9 V" in body
    assert selected["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert selected["selected_chars"] <= 2400
    assert selected["omitted_chars"] > 0 and not selected["complete"]
    for span in selected["spans"]:
        assert text[span["start"] : span["end"]] == span["text"]
    assert any(s["start"] > 8000 for s in selected["spans"])


def test_many_sources_share_one_budget_without_losing_provenance():
    sources = [
        {
            "id": f"ref_{i}",
            "url": f"https://fixture.test/{i}",
            "markdown": late_document(),
        }
        for i in range(4)
    ]
    result = build_evidence(sources, "capacitor measurements", 4096)
    assert result["coverage"]["selected_chars"] <= 4096
    assert len(result["coverage"]["sources"]) == 4
    assert not result["coverage"]["complete"]
    assert "untrusted" in result["context"]
    assert "470.25" in result["context"]
    for source in sources:
        assert source["markdown"] == late_document()


def test_small_sources_are_complete_empty_sources_do_not_claim_completeness():
    result = build_evidence([{"id": "ref", "markdown": "Amount: 12.75"}], "Amount", 256)
    assert result["coverage"]["complete"]
    assert result["coverage"]["sources"][0]["omitted_chars"] == 0
    assert not build_evidence([], "Amount", 256)["coverage"]["complete"]


def test_selection_is_deterministic_and_foreign_sources_are_not_consulted():
    sources = [{"id": "private-ref", "markdown": late_document()}]
    assert build_evidence(sources, "capacitor", 256) == build_evidence(
        sources, "capacitor", 256
    )
    assert "other-session" not in build_evidence(sources, "capacitor", 256)["context"]


@pytest.mark.parametrize("budget", [0, 255, 128001, True, "400"])
def test_budget_rejection_is_visible(budget):
    with pytest.raises(ValueError):
        build_evidence([], "question", budget)


@pytest.mark.asyncio
async def test_session_query_reads_full_refs_and_rejects_foreign_ref(monkeypatch):
    from agent import session

    manager = session.SessionManager.__new__(session.SessionManager)
    manager.store = MagicMock()
    manager.store.aget_artifact = AsyncMock(return_value="Only a preview")
    manager.store.aget_refs = AsyncMock(
        return_value={
            "ref_1": {"url": "https://fixture.test", "markdown": late_document()}
        }
    )
    manager.store.aappend_step = AsyncMock(return_value=2)
    manager.store.aappend_artifact = AsyncMock()
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="470.25 uF [ref_1]")
    llm.close = AsyncMock()
    monkeypatch.setattr(session, "LLMClient", lambda *_args: llm)
    result = await manager._step_query(
        "one",
        {
            "question": "Amber capacitor measurements",
            "ref_ids": ["ref_1"],
            "evidence_budget_chars": 2400,
        },
        "http://fixture",
        "",
        "fixture",
    )
    assert "470.25" in llm.generate.call_args.kwargs["context"]
    assert result["evidence_coverage"]["sources"][0]["id"] == "ref_1"
    with pytest.raises(ValueError, match="from this session"):
        await manager._step_query(
            "one",
            {"question": "Amber", "ref_ids": ["foreign"]},
            "http://fixture",
            "",
            "fixture",
        )
    assert llm.generate.await_count == 1


@pytest.mark.asyncio
async def test_search_window_reports_omissions_and_requests_explicit_upstream_page():
    from agent.searxng_client import SearXNGClient

    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": f"https://fixture.test/{i}", "title": str(i)}
                    for i in range(8)
                ]
            },
        )

    client = SearXNGClient("http://fixture.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first, health = await client.search("capacitor", limit=3)
        second, next_health = await client.search(
            "capacitor", limit=3, offset=3, page=2
        )
        assert [r["title"] for r in first] == ["0", "1", "2"]
        assert [r["title"] for r in second] == ["3", "4", "5"]
        assert seen[1]["pageno"] == "2"
        assert health.result_coverage["captured_results"] == 8
        assert health.result_coverage["has_more_in_page"]
        assert not next_health.result_coverage["complete"]
        assert next_health.result_coverage["upstream_has_more"] is None
    finally:
        await client.close()


def test_old_requests_keep_defaults_and_new_bounds_are_validated():
    assert AnswerRequest(query="test").evidence_budget_chars == 32000
    request = SearchRequest(query="test")
    assert request.page == 1 and request.offset == 0 and request.limit == 5
    with pytest.raises(ValueError):
        SearchRequest(query="test", page=0)


@pytest.mark.asyncio
async def test_api_search_continuation_is_actionable_and_keeps_coverage(monkeypatch):
    from agent import searxng_client
    from agent.routes.search import search

    async def fake_search(*_args, **kwargs):
        from agent.searxng_client import SearchHealth

        return [{"url": "https://fixture.test/3", "title": "3"}], SearchHealth(
            result_coverage={"has_more_in_page": True, "complete": False}
        )

    client = SimpleNamespace(search=fake_search, close=AsyncMock())
    monkeypatch.setattr(searxng_client, "SearXNGClient", lambda *_args: client)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(searxng_url="http://fixture"))
    )
    result = await search(request, SearchRequest(query="capacitor", limit=1, offset=3))
    assert result.continuation["offset"] == 4
    assert result.continuation["query"] == "capacitor"
    assert not result.coverage["complete"]
    assert result.coverage["next_upstream_page"] == 2


@pytest.mark.asyncio
async def test_answer_sse_preserves_coverage_on_done():
    import json

    from agent.routes.agent import _serialize_answer_stream

    coverage = build_evidence(
        [{"id": "one", "markdown": late_document()}], "capacitor", 512
    )["coverage"]

    async def events():
        yield {
            "type": "done",
            "answer": "470.25",
            "citations": [],
            "latency_ms": 1,
            "evidence_coverage": coverage,
        }

    output = [line async for line in _serialize_answer_stream(events())]
    final = json.loads(output[0].removeprefix("data: "))
    assert final["evidence_coverage"] == coverage
    assert output[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"stream": True},
        {"search_type": "rich"},
        {"sources": ["images"]},
        {"retrieval_mode": "vector"},
    ],
)
async def test_unsupported_continuation_rejected_before_network(fields):
    from agent.routes.search import search
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await search(
            SimpleNamespace(), SearchRequest(query="capacitor", offset=1, **fields)
        )
    assert error.value.status_code == 422
