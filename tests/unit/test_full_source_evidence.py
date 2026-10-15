"""Evidence selection must recover late facts and preserve exact retained bytes."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent.experimental.knowledge import text_digest
from agent.experimental.passage_preparation import (
    EvidenceAdmissionLimitError,
    RetainedSourceText,
    prepare_query_passages,
    prepare_source_passages,
)
from agent.models import CitationStyle
from agent.research.evidence import build_evidence, build_evidence_async, evidence_page
from agent.routes.session import router
from agent.session_scope import request_scope
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request


def source(text, identity="1_1"):
    return {"id": identity, "markdown": text, "url": "https://example.com/spec"}


def assert_exact(text, metadata):
    for span in metadata["spans"]:
        assert text_digest(text[span["start"] : span["end"]]) == span["quote_digest"]
    assert metadata["selected_chars"] + metadata["omitted_chars"] == len(text)


def test_late_numeric_table_unicode_contradiction_and_no_mutation():
    text = "🐙 introductory prose: old price $9.\n" * 4000
    text += "\n## Current pricing contradiction\n| Aurora quantum tier | price |\n| Aurora quantum | $479 |\nOld $9 pricing is discontinued.\n"
    sources = [source(text)]
    original = deepcopy(sources)
    selected = build_evidence(
        sources, "Aurora quantum current price contradiction", 1800
    )
    assert "$479" in selected["context"]
    assert "discontinued" in selected["context"]
    assert sources == original
    metadata = selected["coverage"]["sources"][0]
    assert_exact(text, metadata)
    assert metadata["content_sha256"] == text_digest(text)
    assert not selected["coverage"]["coverage_complete"]
    assert not selected["coverage"]["complete"]


@pytest.mark.parametrize("budget", [256, 32000, 128000])
def test_many_sources_aggregate_budget_and_stable_identity(budget):
    sources = [
        source("irrelevant.\n" * 500 + f"late anchor{index} answer", f"ref{index}")
        for index in range(40)
    ]
    first = build_evidence(sources, "late answer", budget)
    assert first == build_evidence(sources, "late answer", budget)
    assert first["coverage"]["selected_chars"] <= budget
    assert len(first["coverage"]["sources"]) == 40
    for body, metadata in zip(sources, first["coverage"]["sources"], strict=True):
        assert_exact(body["markdown"], metadata)


def test_large_retained_source_classified_recovery_and_page_reconstruction():
    text = "🐙 plain prose\n" * 12000 + "\nlate quantum value 791\n"
    retained = (RetainedSourceText("snapshot-large", text),)
    with pytest.raises(EvidenceAdmissionLimitError) as error:
        prepare_source_passages(retained)
    assert error.value.code == "EVIDENCE_ADMISSION_LIMIT"
    assert error.value.limit == "construction_bytes"
    assert error.value.recovery["action"] == "prepare_query_passages"
    passages, coverage = prepare_query_passages(retained, "quantum value", 1800)
    assert "791" in "".join(p.quote for p in passages)
    assert coverage["coverage_complete"] is False
    reconstructed = ""
    offset = 0
    while True:
        page = evidence_page(source(text), 128000, offset)
        reconstructed += "".join(span["quote"] for span in page["spans"])
        for span in page["spans"]:
            assert span["quote"] == text[span["start"] : span["end"]]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert reconstructed == text


@pytest.mark.parametrize("budget", [True, 255, 128001, "32000", None])
def test_invalid_budgets_rejected(budget):
    with pytest.raises(ValueError):
        build_evidence([source("body")], "body", budget)


@pytest.mark.asyncio
async def test_cancellation_signals_background_scan(monkeypatch):
    from threading import Event

    import agent.research.evidence as module

    started, stopped = Event(), Event()

    def blocked(_sources, _query, _budget, cancelled):
        started.set()
        assert cancelled.wait(3)
        stopped.set()
        raise InterruptedError("cancelled")

    monkeypatch.setattr(module, "build_evidence", blocked)
    task = asyncio.create_task(build_evidence_async([source("body")], "body"))
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(stopped.wait, 3)


@pytest.mark.asyncio
async def test_owned_route_digest_paging_foreign_deletion_and_no_acquisition(
    monkeypatch,
):
    import agent.session as session_module
    from agent.exceptions import GroktoCrawlError

    app = FastAPI()
    app.include_router(router)
    app.state.redis_url = "redis://fixture:6379"
    from fastapi.responses import JSONResponse

    @app.exception_handler(GroktoCrawlError)
    async def error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    scope = request_scope(
        Request({"type": "http", "headers": [(b"x-api-key", b"owner")]})
    )
    store = MagicMock()
    store.aget = AsyncMock(return_value={"id": "session", "owner_scope": scope})
    text = "🐙 retained prose\n" * 1000 + "late quantum 791"
    store.aget_ref = AsyncMock(
        return_value={"markdown": text, "url": "https://example.com"}
    )
    monkeypatch.setattr(
        session_module, "SessionManager", lambda **_kwargs: MagicMock(store=store)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        path = "/v2/session/session/evidence/1_1"
        assert (
            await client.get(path, headers={"X-API-Key": "foreign"})
        ).status_code == 404
        headers = {"Authorization": "Bearer owner"}
        response = await client.get(
            path, headers=headers, params={"query": "quantum", "budget_chars": 256}
        )
        assert response.status_code == 200
        result = response.json()
        assert "791" in "".join(span["quote"] for span in result["spans"])
        assert result["continuation"]["params"]["expected_digest"] == text_digest(text)
        assert (
            await client.get(
                path, headers=headers, params={"expected_digest": "a" * 64}
            )
        ).status_code == 409
        assert (
            await client.get(path, headers=headers, params={"query": "x", "offset": 1})
        ).status_code == 400
        store.aget_ref.side_effect = [{"markdown": text}, None]
        assert (await client.get(path, headers=headers)).status_code == 404
        store.aget.return_value = None
        assert (await client.get(path, headers=headers)).status_code == 404


def test_grounded_answer_context_contains_late_quote_with_source_identity():
    from agent.research.discovery import _build_answer_context
    from agent.research.sources import SourceArtifact

    text = "ordinary prose\n" * 4000 + "late quantum table price 791"
    artifact = SourceArtifact(
        "https://example.com", markdown=text, char_count=len(text)
    )
    assert text in artifact.to_document()
    result = _build_answer_context([], [artifact], "quantum price", 1800)
    assert "791" in result["context"]
    assert "[1] Source: https://example.com" in result["context"]
    assert_exact(text, result["evidence_coverage"]["sources"][0])


@pytest.mark.asyncio
async def test_session_query_reads_full_refs_instead_of_artifact_previews(monkeypatch):
    import agent.session as module

    manager = module.SessionManager.__new__(module.SessionManager)
    text = "old claim 9\n" * 5000 + "late quantum price 791; previous value 9 withdrawn"
    refs = {"1_1": {"markdown": text, "url": "https://example.com"}}
    values = {
        "aget_artifact": "only an old preview",
        "aget_refs": refs,
        "aappend_step": 2,
    }

    async def store_call(async_name, _sync_name, *_args):
        return values.get(async_name)

    manager._store_call = store_call
    llm = MagicMock(
        generate=AsyncMock(return_value="Current price 791 [1_1]"), close=AsyncMock()
    )
    monkeypatch.setattr(module, "LLMClient", lambda *_args: llm)
    result = await manager._step_query(
        "session",
        {
            "question": "quantum price",
            "ref_ids": ["1_1"],
            "evidence_budget_chars": 1800,
        },
        "fixture",
        "",
        "fixture",
    )
    assert "791" in llm.generate.call_args.kwargs["context"]
    assert result["citations"][0]["ref_id"] == "1_1"
    assert result["citations"][0]["content_digest"] == text_digest(text)
    assert refs["1_1"]["markdown"] == text
    refs["empty"] = {"description": "", "markdown": ""}
    with pytest.raises(ValueError, match="no retained text"):
        await manager._step_query(
            "session",
            {"question": "quantum", "ref_ids": ["empty"]},
            "fixture",
            "",
            "fixture",
        )
    assert llm.generate.await_count == 1
    with pytest.raises(ValueError, match="not found"):
        await manager._step_query(
            "session",
            {"question": "quantum", "ref_ids": ["foreign"]},
            "fixture",
            "",
            "fixture",
        )


@pytest.mark.asyncio
async def test_cache_stream_replays_exact_coverage_and_budget_fingerprints_differ():
    import json

    from agent.research.streaming import stream_cached_artifact
    from agent.research_memory import compute_fingerprint

    assert compute_fingerprint(
        prompt="same", evidence_budget_chars=256
    ) != compute_fingerprint(prompt="same", evidence_budget_chars=32000)
    coverage = build_evidence([source("retained quote")], "quote", 256)["coverage"]
    events = [
        json.loads(event.removeprefix("data: "))
        async for event in stream_cached_artifact(
            "answer",
            [],
            "memory",
            "fresh",
            0,
            CitationStyle.inline,
            False,
            evidence_coverage=coverage,
        )
        if event.strip() != "data: [DONE]"
    ]
    assert (
        next(event for event in events if event["type"] == "done")["evidence_coverage"]
        == coverage
    )


def test_selected_construction_keeps_utf8_byte_ceiling():
    with pytest.raises(EvidenceAdmissionLimitError) as error:
        prepare_query_passages(
            (RetainedSourceText("unicode-snapshot", "🐙" * 32000),), "octopus", 32000
        )
    assert error.value.limit == "construction_bytes"
    passages, coverage = prepare_query_passages(
        (RetainedSourceText("unicode-snapshot", "🐙" * 32000),), "octopus", 1800
    )
    assert sum(len(p.quote.encode("utf-8")) for p in passages) <= 120000
    assert coverage["omitted_chars"] > 0


@pytest.mark.parametrize("enabled", [False, True])
def test_scope_normalizes_accepted_auth_header_aliases(monkeypatch, enabled):
    from agent import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", enabled)
    monkeypatch.setattr(auth, "API_KEY", "owner")

    def scope(headers):
        return request_scope(
            Request(
                {
                    "type": "http",
                    "headers": [
                        (k.lower().encode(), v.encode()) for k, v in headers.items()
                    ],
                }
            )
        )

    expected = scope({"X-API-Key": "owner"})
    assert scope({"Authorization": "Bearer owner"}) == expected
    assert scope({"Authorization": "invalid", "X-API-Key": "owner"}) == expected
    if enabled:
        assert (
            scope({"Authorization": "Bearer wrong", "X-API-Key": "owner"}) == expected
        )
    assert "owner" not in expected


@pytest.mark.asyncio
async def test_legacy_delete_idempotency_is_indistinguishable_for_foreign_ids(
    monkeypatch,
):
    import agent.session as module
    from agent.routes.session import delete_session

    store = MagicMock(aget=AsyncMock(return_value=None))
    manager = MagicMock(store=store, delete_session=AsyncMock(return_value=True))
    monkeypatch.setattr(module, "SessionManager", lambda **_kwargs: manager)
    request = Request({"type": "http", "headers": [], "app": MagicMock()})
    absent = await delete_session(request, "id")
    store.aget.return_value = {"id": "id", "owner_scope": "foreign"}
    foreign = await delete_session(request, "id")
    assert absent.model_dump() == foreign.model_dump()
    assert absent.deleted is False
    manager.delete_session.assert_not_awaited()
    store.aget.return_value = {"id": "id", "owner_scope": "anonymous"}
    assert (await delete_session(request, "id")).deleted is True
    manager.delete_session.assert_awaited_once_with("id")
    store.aget.return_value = None
    assert (await delete_session(request, "id")).deleted is False


@pytest.mark.asyncio
async def test_polling_preserves_coverage_when_canonical_producer_emits_it(monkeypatch):
    from agent.research import loop

    coverage = build_evidence([source("exact quote")], "quote", 256)["coverage"]

    async def events(*_args, **_kwargs):
        yield {
            "type": "done",
            "result": "answer",
            "sources": [],
            "source_details": [],
            "evidence_coverage": coverage,
        }

    monkeypatch.setattr(loop, "_run_research_events", events)
    result = await loop.run_research("query", llm_model="fixture")
    assert result["evidence_coverage"] == coverage
