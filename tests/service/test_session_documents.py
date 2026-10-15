"""Local document admission, privacy, lifecycle and exact citation fixtures."""

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from agent.exceptions import GroktoCrawlError
from agent.routes import documents, session
from agent.session_scope import request_scope
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from parse_svc.app import _parse_anydoc, _parse_pdf, _parse_text

FIXTURES = Path(__file__).parents[1] / "fixtures" / "documents"
ORIGINAL_PARSE = documents._parse_document


class DocumentStore:
    """Deterministic session boundary; real parsers, no providers/network/storage."""

    def __init__(self):
        self.meta = {"id": "session-a", "owner_scope": "anonymous"}
        self.refs = {}
        self.lock = asyncio.Lock()
        self.owner = None
        self.pending = False
        self.deleted_during_commit = False

    async def aget(self, session_id):
        return self.meta if session_id == "session-a" else None

    async def aget_refs(self, _session_id):
        return self.refs.copy()

    async def aget_ref(self, _session_id, ref_id):
        return self.refs.get(ref_id)

    async def aadd_ref(self, _session_id, ref_id, ref):
        if self.deleted_during_commit:
            self.meta = None
            return False
        self.refs[ref_id] = ref
        return True

    async def acquire_lock(self, _session_id, **_kwargs):
        await self.lock.acquire()
        return "fixture-owner"

    def set_lock_owner(self, token):
        self.owner = token
        return None

    def reset_lock_owner(self, _context):
        self.owner = None

    async def arelease_lock(self, _session_id, _token):
        self.lock.release()

    async def ahas_pending_steps(self, _session_id):
        return self.pending

    async def aremove_document(self, _session_id, ref_id):
        if self.refs.get(ref_id, {}).get("source") != "document":
            return False
        del self.refs[ref_id]
        return True


@pytest.fixture
def document_app(monkeypatch):
    store = DocumentStore()
    monkeypatch.setattr(documents, "SessionStore", lambda **_kwargs: store)

    async def parse_fixture(content, filename, _media_type):
        parser = (
            _parse_pdf
            if filename.endswith(".pdf")
            else _parse_anydoc
            if filename.endswith(".docx")
            else _parse_text
        )
        result = await asyncio.to_thread(parser, content, filename)
        return {"success": True, "data": result}

    monkeypatch.setattr(documents, "_parse_document", parse_fixture)
    app = FastAPI()
    app.state.valkey_url = "redis://fixture.invalid/0"
    app.include_router(documents.router)
    app.include_router(session.router)

    @app.exception_handler(GroktoCrawlError)
    async def handle(_request, exc):
        return JSONResponse(
            {"error": exc.detail, "error_code": exc.error_code},
            status_code=exc.status_code,
        )

    return app, store


async def attach(client, filename="budget-table.pdf", content=None):
    content = content if content is not None else (FIXTURES / filename).read_bytes()
    return await client.post(
        "/v2/session/session-a/documents", files={"file": (filename, content)}
    )


@pytest.mark.asyncio
async def test_real_pdf_table_and_docx_heading_admission_exact_passages(document_app):
    app, store = document_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        pdf = await attach(client)
        assert pdf.status_code == 200
        pdf_meta = pdf.json()
        assert pdf_meta["extraction"]["extraction"] == "pypdf"
        assert pdf_meta["extraction"]["pages"] == 1
        assert pdf_meta["anchors"] == []  # pypdf supplies no passage/page mapping.
        text = store.refs[pdf_meta["ref_id"]]["markdown"]
        assert "Research | 12000 | 11000" in text
        assert pdf_meta["content_digest"] == hashlib.sha256(text.encode()).hexdigest()
        assert (
            pdf_meta["file_digest"]
            == hashlib.sha256((FIXTURES / "budget-table.pdf").read_bytes()).hexdigest()
        )
        begin = text.index("Research |")
        quote = await client.get(
            pdf_meta["url"],
            params={"start": begin, "end": begin + len("Research | 12000 | 11000")},
        )
        assert quote.json()["markdown"] == "Research | 12000 | 11000"
        assert (
            quote.json()["quote_digest"]
            == hashlib.sha256(quote.json()["markdown"].encode()).hexdigest()
        )
        docx = await attach(client, "policy-heading.docx")
        assert docx.status_code == 200
        assert docx.json()["extraction"]["extraction"] == "anydoc"
        assert [item["label"] for item in docx.json()["anchors"]] == [
            "# Research policy",
            "## Approvals",
        ]
        assert (
            len(
                (await client.get("/v2/session/session-a/documents")).json()[
                    "documents"
                ]
            )
            == 2
        )


@pytest.mark.asyncio
async def test_duplicate_bytes_are_session_local_idempotent_and_detach_removes_evidence(
    document_app,
):
    app, store = document_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        first = (await attach(client, "report.txt", b"Private source text.")).json()
        second = (
            await attach(client, "other-name.txt", b"Private source text.")
        ).json()
        assert first["ref_id"] == second["ref_id"]
        assert second["duplicate"] is True
        assert second["filename"] == "report.txt"
        assert len(store.refs) == 1
        assert (await client.delete(first["url"])).status_code == 200
        assert (await client.get(first["url"])).status_code == 404
        assert store.refs == {}


@pytest.mark.asyncio
async def test_owned_session_rejects_foreign_owner_before_parsing_or_reading(
    document_app, monkeypatch
):
    app, store = document_app
    store.meta["owner_scope"] = request_scope(
        SimpleNamespace(headers={"Authorization": "Bearer owner-fixture"})
    )

    async def forbidden_parse(*_args):
        raise AssertionError("Foreign documents must not reach the parser")

    monkeypatch.setattr(documents, "_parse_document", forbidden_parse)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Authorization": "Bearer foreign-fixture"},
    ) as client:
        assert (
            await attach(client, "private.txt", b"Private source text.")
        ).status_code == 404
        assert (await client.get("/v2/session/session-a/documents")).status_code == 404
        assert (
            await client.get("/v2/session/session-a/documents/doc_unknown")
        ).status_code == 404
        assert (
            await client.delete("/v2/session/session-a/documents/doc_unknown")
        ).status_code == 404
    assert store.refs == {}


@pytest.mark.asyncio
async def test_expired_and_deleted_sessions_never_resurrect_private_refs(document_app):
    app, store = document_app
    store.deleted_during_commit = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (
            await attach(client, "report.txt", b"Private source text.")
        ).status_code == 404
        assert (await client.get("/v2/session/session-a/documents")).status_code == 404
    assert store.refs == {}
    assert not store.lock.locked()


@pytest.mark.asyncio
async def test_expired_or_foreign_staged_upload_is_explicit_and_not_parsed(
    document_app, monkeypatch
):
    app, _store = document_app
    monkeypatch.setattr("redis.Redis.from_url", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(documents, "_consume_upload", lambda *_args: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            "/v2/session/session-a/documents", data={"upload_id": "expired-upload"}
        )
    assert response.status_code == 404
    assert "expired" in response.json()["error"]


@pytest.mark.asyncio
async def test_pending_steps_and_concurrent_admission_obey_one_document_quota(
    document_app, monkeypatch
):
    app, store = document_app
    store.pending = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (await attach(client, "a.txt", b"first document")).status_code == 409
        store.pending = False
        monkeypatch.setattr("agent.session_documents.MAX_SESSION_DOCUMENTS", 1)
        responses = await asyncio.gather(
            attach(client, "a.txt", b"first document"),
            attach(client, "b.txt", b"second document"),
        )
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert len(store.refs) == 1
    assert not store.lock.locked()


@pytest.mark.asyncio
async def test_cancellation_before_parser_completion_stores_no_document(
    document_app, monkeypatch
):
    app, store = document_app
    started = asyncio.Event()

    async def slow_parse(*_args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(documents, "_parse_document", slow_parse)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        task = asyncio.create_task(
            attach(client, "report.txt", b"Private source text.")
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not store.refs
    assert not store.lock.locked()


@pytest.mark.asyncio
async def test_document_invalid_admission_and_integrity_rejections(
    document_app, monkeypatch
):
    app, store = document_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (
            await client.post("/v2/session/session-a/documents", data={})
        ).status_code == 400
        assert (
            await client.post(
                "/v2/session/session-a/documents",
                files={"file": ("a.txt", b"a")},
                data={"upload_id": "both"},
            )
        ).status_code == 400
        assert (await attach(client, "empty.txt", b"")).status_code == 400
        admitted = (await attach(client, "a.txt", b"Private evidence")).json()
        assert (
            await client.get(admitted["url"], params={"start": 3, "end": 1})
        ).status_code == 400
        store.refs[admitted["ref_id"]]["markdown"] = "Changed evidence"
        assert (await client.get(admitted["url"])).status_code == 400
        assert (
            await client.delete("/v2/session/session-a/documents/missing")
        ).status_code == 404
        store.meta.pop("owner_scope")
        assert (await attach(client, "a.txt", b"Private evidence")).status_code == 400
        store.meta["owner_scope"] = "anonymous"
        monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 1)
        assert (await attach(client, "large.txt", b"too large")).status_code == 400


@pytest.mark.asyncio
async def test_staged_document_admission_retains_actual_filename_and_media_type(
    document_app, monkeypatch
):
    app, _store = document_app
    monkeypatch.setattr("redis.Redis.from_url", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        documents,
        "_consume_upload",
        lambda *_args: (b"Private text", "text/plain", "retained-name.txt"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            "/v2/session/session-a/documents", data={"upload_id": "scoped-upload"}
        )
    assert response.status_code == 200
    assert response.json()["filename"] == "retained-name.txt"
    assert response.json()["media_type"] == "text/plain"


@pytest.mark.asyncio
async def test_parse_http_contract_is_local_only_and_errors_are_explicit(
    document_app, monkeypatch
):
    app, _store = document_app
    calls = []

    def parser_response(request):
        calls.append(request)
        assert request.url == "http://parse-svc:8013/parse"
        assert b'name="ocr"\r\n\r\nlocal' in request.content
        assert b'filename="fixture.txt"' in request.content
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "Exact parser text",
                    "metadata": {"extraction": "fixture-local"},
                },
            },
        )

    real_client = httpx.AsyncClient
    client = real_client(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    )
    monkeypatch.setattr(documents, "_parse_document", ORIGINAL_PARSE)
    monkeypatch.setattr(
        documents.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(parser_response)),
    )
    async with client:
        response = await attach(client, "fixture.txt", b"Exact parser text")
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_document_lock_conflict_is_explicit_without_commit(
    document_app, monkeypatch
):
    app, store = document_app

    async def no_lock(*_args, **_kwargs):
        return None

    monkeypatch.setattr(store, "acquire_lock", no_lock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (
            await attach(client, "report.txt", b"Private evidence")
        ).status_code == 409
    assert not store.refs


@pytest.mark.asyncio
async def test_chunked_document_transfer_is_bounded_before_file_spooling(
    document_app, monkeypatch
):
    app, store = document_app
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 10)
    monkeypatch.setattr(documents, "MAX_MULTIPART_OVERHEAD_BYTES", 100)

    async def chunks():
        yield b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n'
        yield b"x" * 100
        yield b"\r\n--fixture--\r\n"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            "/v2/session/session-a/documents",
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=fixture"},
        )
    assert response.status_code == 400
    assert "transfer limit" in response.json()["error"]
    assert not store.refs
