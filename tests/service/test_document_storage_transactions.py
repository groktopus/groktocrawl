"""Actual Valkey Lua, lifecycle and owner fences, on an isolated fixture DB."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.exceptions import NotFoundError
from agent.routes.parse import _consume_upload, _stage_upload
from agent.session_scope import authorize_session, request_scope
from agent.session_store import SessionStore
from redis import Redis

from tests.outcome_governance import governed_skip


@pytest.fixture
def storage():
    url = os.environ.get("DOCUMENT_TEST_REDIS_URL") or os.environ.get(
        "DURABLE_RESEARCH_REDIS_URL"
    )
    if not url:
        governed_skip(
            reason="isolated Redis fixture URL is not configured",
            owner="magnus919",
            issue="419",
            classification="environment",
            review_date="2026-11-07",
            environment="Fast Tests Valkey service",
        )
    store = SessionStore(redis_url=url)
    uploads = Redis.from_url(url, decode_responses=False)
    sessions = []
    upload_ids = []
    yield store, uploads, sessions, upload_ids
    for session_id in sessions:
        store.delete(session_id)
        store.redis.delete(f"session:{session_id}:lock")
    for upload_id in upload_ids:
        uploads.delete(
            *(
                f"parse:upload:{upload_id}{suffix}"
                for suffix in ("", ":data", ":content_type", ":filename", ":owner")
            )
        )
    uploads.close()
    store.redis.close()


def test_staged_upload_owner_and_single_use_are_atomic(storage):
    _store, redis, _sessions, uploads = storage
    upload_id = str(uuid4())
    uploads.append(upload_id)
    prefix = f"parse:upload:{upload_id}"
    redis.set(prefix, b"pending", ex=30)
    redis.set(prefix + ":owner", b"owner", ex=30)
    assert not _stage_upload(
        redis, upload_id, "foreign", b"private", "text/plain", "private.txt"
    )
    assert redis.get(prefix) == b"pending"
    assert _stage_upload(
        redis, upload_id, "owner", b"private", "text/plain", "private.txt"
    )
    assert _consume_upload(redis, upload_id, "foreign") is None
    assert redis.get(prefix + ":data") == b"private"
    assert _consume_upload(redis, upload_id, "owner") == (
        b"private",
        "text/plain",
        "private.txt",
    )
    assert _consume_upload(redis, upload_id, "owner") is None
    assert not redis.exists(prefix + ":owner")


def test_expired_upload_cannot_be_resurrected_by_late_staging(storage):
    _store, redis, _sessions, uploads = storage
    upload_id = str(uuid4())
    uploads.append(upload_id)
    assert not _stage_upload(
        redis, upload_id, "owner", b"private", "text/plain", "private.txt"
    )
    assert not redis.exists(f"parse:upload:{upload_id}:data")
    assert _consume_upload(redis, upload_id, "owner") is None


@pytest.mark.asyncio
async def test_document_detach_requires_current_lock_and_is_source_specific(storage):
    store, _redis, sessions, _uploads = storage
    session_id = await store.acreate(owner_scope="anonymous")
    sessions.append(session_id)
    assert await store.aadd_refs(
        session_id,
        {
            "doc": {"source": "document", "markdown": "Private report"},
            "web": {"source": "web", "markdown": "Public evidence"},
        },
    )
    assert not await store.aremove_document(session_id, "doc")
    token = await store.acquire_lock(session_id, timeout=2)
    context = store.set_lock_owner(token)
    try:
        assert not await store.aremove_document(session_id, "web")
        assert await store.aremove_document(session_id, "doc")
        assert await store.aget_ref(session_id, "doc") is None
        assert await store.aget_ref(session_id, "web") is not None
    finally:
        store.reset_lock_owner(context)
        await store.arelease_lock(session_id, token)


@pytest.mark.asyncio
async def test_new_owner_scope_and_session_deletion_remove_all_document_refs(storage):
    store, _redis, sessions, _uploads = storage
    owner = SimpleNamespace(headers={"Authorization": "Bearer owner-fixture"})
    foreign = SimpleNamespace(headers={"Authorization": "Bearer foreign-fixture"})
    session_id = await store.acreate(owner_scope=request_scope(owner))
    sessions.append(session_id)
    assert await store.aadd_ref(
        session_id, "doc", {"source": "document", "markdown": "Private report"}
    )
    assert (await authorize_session(store, session_id, owner))[
        "owner_scope"
    ] == request_scope(owner)
    with pytest.raises(NotFoundError):
        await authorize_session(store, session_id, foreign)
    assert await store.adelete(session_id)
    assert await store.aget_ref(session_id, "doc") is None
    assert not await store.aadd_ref(
        session_id, "doc", {"source": "document", "markdown": "resurrection forbidden"}
    )


@pytest.mark.asyncio
async def test_document_refs_expire_with_session_and_reads_do_not_extend_ttl(storage):
    store, _redis, sessions, _uploads = storage
    session_id = await store.acreate(ttl=1, owner_scope="anonymous")
    sessions.append(session_id)
    assert await store.aadd_ref(
        session_id, "doc", {"source": "document", "markdown": "Private report"}
    )
    assert await store.aget_ref(session_id, "doc") is not None
    assert store.redis.ttl(f"session:{session_id}:refs") <= 1
    await asyncio.sleep(1.1)
    assert await store.aget(session_id) is None
    assert await store.aget_ref(session_id, "doc") is None


@pytest.mark.asyncio
async def test_actual_upload_reservation_transfer_and_document_admission(
    storage, monkeypatch
):
    import httpx
    from agent.exceptions import GroktoCrawlError
    from agent.routes import documents, parse
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    store, _redis, sessions, uploads = storage
    session_id = await store.acreate(
        owner_scope=request_scope(
            SimpleNamespace(headers={"Authorization": "Bearer owner-fixture"})
        )
    )
    sessions.append(session_id)
    url = (
        os.environ.get("DOCUMENT_TEST_REDIS_URL")
        or os.environ["DURABLE_RESEARCH_REDIS_URL"]
    )
    monkeypatch.setattr(parse, "_get_redis_url", lambda _request: url)
    monkeypatch.setattr(documents, "_get_redis_url", lambda _request: url)
    monkeypatch.setattr(documents, "SessionStore", lambda **_kwargs: store)

    async def extract(content, filename, _media_type):
        return {
            "success": True,
            "data": {
                "markdown": content.decode(),
                "metadata": {"filename": filename, "format": "txt"},
            },
        }

    monkeypatch.setattr(documents, "_parse_document", extract)
    app = FastAPI()
    app.include_router(parse.router)
    app.include_router(documents.router)

    @app.exception_handler(GroktoCrawlError)
    async def handle(_request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"X-API-Key": "owner-fixture"},
    ) as client:
        response = await client.post(
            "/v2/parse/upload-url", headers={"Authorization": "Bearer owner-fixture"}
        )
        assert response.status_code == 200
        upload_id = response.json()["upload_id"]
        uploads.append(upload_id)
        upload_url = f"/v2/parse/upload/{upload_id}"
        assert (
            await client.put(
                upload_url,
                content=b"Private text",
                headers={"Authorization": "Bearer foreign-fixture"},
            )
        ).status_code == 404
        assert (await client.put(upload_url, content=b"")).status_code == 400
        transfer = await client.put(
            upload_url,
            content=b"Private text",
            headers={"Content-Type": "text/plain", "X-Filename": "staged-report.txt"},
        )
        assert transfer.status_code == 200
        admitted = await client.post(
            f"/v2/session/{session_id}/documents", data={"upload_id": upload_id}
        )
        assert admitted.status_code == 200
        assert admitted.json()["filename"] == "staged-report.txt"
        assert admitted.json()["file_digest"]
        assert (
            await client.get(
                f"/v2/session/{session_id}/documents/{admitted.json()['ref_id']}",
                headers={"Authorization": "Bearer owner-fixture"},
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/v2/session/{session_id}/documents", data={"upload_id": upload_id}
            )
        ).status_code == 404
        next_upload = (
            await client.post(
                "/v2/parse/upload-url",
                headers={"Authorization": "Bearer owner-fixture"},
            )
        ).json()["upload_id"]
        uploads.append(next_upload)
        monkeypatch.setattr(parse, "PARSE_MAX_UPLOAD_BYTES", 2)
        assert (
            await client.put(f"/v2/parse/upload/{next_upload}", content=b"123")
        ).status_code == 400
        assert (
            _consume_upload(
                _redis,
                next_upload,
                request_scope(SimpleNamespace(headers={"X-API-Key": "owner-fixture"})),
            )
            is None
        )


@pytest.mark.asyncio
async def test_owned_session_routes_do_not_expose_documents_to_foreign_callers(
    storage, monkeypatch
):
    import httpx
    from agent.exceptions import GroktoCrawlError
    from agent.routes import session
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    store, _redis, sessions, _uploads = storage
    monkeypatch.setattr("agent.session.SessionStore", lambda **_kwargs: store)
    app = FastAPI()
    app.include_router(session.router)

    @app.exception_handler(GroktoCrawlError)
    async def handle(_request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Authorization": "Bearer owner-fixture"},
    ) as client:
        creation = await client.post("/v2/session/create", json={"ttl": 3600})
        assert creation.status_code == 200
        session_id = creation.json()["sessionId"]
        sessions.append(session_id)
        assert await store.aadd_ref(
            session_id,
            "doc",
            {
                "source": "document",
                "markdown": "Private report",
                "url": "retained",
                "char_count": 14,
            },
        )
        foreign = {"Authorization": "Bearer foreign-fixture"}
        assert (
            await client.get(f"/v2/session/{session_id}", headers=foreign)
        ).status_code == 404
        assert (
            await client.post(f"/v2/session/{session_id}/export", headers=foreign)
        ).status_code == 404
        assert (
            await client.post(
                f"/v2/session/{session_id}/resolve",
                json={"ref_ids": ["doc"]},
                headers=foreign,
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/v2/session/{session_id}/step",
                json={
                    "action": "query",
                    "params": {"question": "Reveal private source"},
                },
                headers=foreign,
            )
        ).status_code == 404
        foreign_delete = await client.delete(
            f"/v2/session/{session_id}", headers=foreign
        )
        assert foreign_delete.status_code == 200
        assert foreign_delete.json()["deleted"] is False
        assert await store.aget(session_id) is not None
        assert await store.aget_ref(session_id, "doc") is not None
        assert (await client.get(f"/v2/session/{session_id}")).status_code == 200
        resolved = await client.post(
            f"/v2/session/{session_id}/resolve", json={"ref_ids": ["doc"]}
        )
        assert resolved.status_code == 200
        assert resolved.json()["refs"]["doc"]["markdown"] == "Private report"
        assert (
            await client.post(f"/v2/session/{session_id}/export")
        ).status_code == 200
        owner_delete = await client.delete(f"/v2/session/{session_id}")
        assert owner_delete.status_code == 200
        assert owner_delete.json()["deleted"] is True
        assert await store.aget_ref(session_id, "doc") is None
        repeated_delete = await client.delete(f"/v2/session/{session_id}")
        unknown_delete = await client.delete("/v2/session/unknown-document-session")
        for response in [repeated_delete, unknown_delete]:
            assert response.status_code == 200
            assert response.json()["deleted"] is False
        assert repeated_delete.json() == foreign_delete.json()


@pytest.mark.asyncio
async def test_mixed_local_document_and_web_question_resolves_exact_citation_spans(
    storage, monkeypatch
):
    import hashlib
    from pathlib import Path

    import httpx
    from agent.exceptions import GroktoCrawlError
    from agent.routes import documents, session
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from parse_svc.app import _parse_anydoc, _parse_pdf, _parse_text

    store, _redis, sessions, _uploads = storage
    monkeypatch.setattr("agent.session.SessionStore", lambda **_kwargs: store)
    monkeypatch.setattr(documents, "SessionStore", lambda **_kwargs: store)
    references = []
    generations = []

    async def parse_local(content, filename, _media_type):
        parser = (
            _parse_pdf
            if filename.endswith(".pdf")
            else _parse_text
            if filename.endswith(".txt")
            else _parse_anydoc
        )
        return {
            "success": True,
            "data": await asyncio.to_thread(parser, content, filename),
        }

    monkeypatch.setattr(documents, "_parse_document", parse_local)

    class FixtureLLM:
        def __init__(self, *_args):
            pass

        async def generate(self, *, system_prompt, user_prompt, context):
            assert "untrusted data" in system_prompt
            assert "Research | 12000 | 11000" in context
            assert "fourteen days" in context
            assert "Official public budget is twelve thousand" in context
            assert "Zirconium calibration requires forty-two samples" in context
            assert "do not select this private ref" not in context
            generations.append(context)
            return f"The budget agrees with public evidence [{references[0]}] [ref_web]. Retention is fourteen days [{references[1]}]. Zirconium requires forty-two samples [{references[2]}]."

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", FixtureLLM)
    app = FastAPI()
    app.state.searxng_url = "http://fixture.invalid/search"
    app.state.scraper_url = "http://fixture.invalid/scraper"
    app.state.llm_base_url = "http://fixture.invalid/llm"
    app.state.llm_api_key = ""
    app.state.llm_model = "fixture"
    app.include_router(session.router)
    app.include_router(documents.router)

    @app.exception_handler(GroktoCrawlError)
    async def handle(_request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    fixtures = Path(__file__).parents[1] / "fixtures" / "documents"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        session_id = (await client.post("/v2/session/create", json={})).json()[
            "sessionId"
        ]
        sessions.append(session_id)
        admitted = []
        long_manual = (
            "Routine background information. " * 2000
            + "\nZirconium calibration requires forty-two samples."
        ).encode()
        for filename in ("budget-table.pdf", "policy-heading.docx", "long-manual.txt"):
            response = await client.post(
                f"/v2/session/{session_id}/documents",
                files={
                    "file": (
                        filename,
                        long_manual
                        if filename == "long-manual.txt"
                        else (fixtures / filename).read_bytes(),
                    )
                },
            )
            assert response.status_code == 200
            admitted.append(response.json())
            references.append(response.json()["ref_id"])
        assert await store.aadd_refs(
            session_id,
            {
                "ref_web": {
                    "source": "web",
                    "url": "https://example.test/budget",
                    "title": "Published budget",
                    "markdown": "Official public budget is twelve thousand.",
                },
                "ref_unselected": {
                    "source": "web",
                    "markdown": "do not select this private ref",
                },
            },
        )
        query = await client.post(
            f"/v2/session/{session_id}/step",
            json={
                "action": "query",
                "params": {
                    "question": "Compare research budget and retention with public evidence and zirconium calibration samples",
                    "ref_ids": [*references, "ref_web"],
                    "evidence_budget_chars": 32000,
                },
            },
        )
        assert query.status_code == 200
        result = query.json()["result"]
        assert result["ref_count"] == 4
        assert {item["ref_id"] for item in result["citations"]} == {
            *references,
            "ref_web",
        }
        doc_citations = {
            item["ref_id"]: item
            for item in result["citations"]
            if item["ref_id"] in references
        }
        assert doc_citations[references[1]]["anchors"] == admitted[1]["anchors"]
        assert any(
            span["start"] > 8000 for span in doc_citations[references[2]]["spans"]
        )
        assert (
            len((await store.aget_ref(session_id, references[2]))["markdown"]) > 32000
        )
        for metadata in admitted:
            citation = doc_citations[metadata["ref_id"]]
            assert citation["content_digest"] == metadata["content_digest"]
            assert citation["snapshot_id"] == metadata["snapshot_id"]
            for span in citation["spans"]:
                resolved = await client.get(
                    citation["url"], params={"start": span["start"], "end": span["end"]}
                )
                assert resolved.status_code == 200
                quote = resolved.json()["markdown"]
                assert (
                    hashlib.sha256(quote.encode()).hexdigest() == span["quote_digest"]
                )
        assert (await client.delete(admitted[0]["url"])).status_code == 200
        rejected = await client.post(
            f"/v2/session/{session_id}/step",
            json={
                "action": "query",
                "params": {
                    "question": "Use deleted source",
                    "ref_ids": [references[0]],
                },
            },
        )
        assert rejected.status_code == 404
        assert len(generations) == 1
        assert (await client.get(admitted[0]["url"])).status_code == 404
