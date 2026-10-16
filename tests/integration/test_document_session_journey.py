"""Real Parse HTTP and Valkey document journey; only model output is controlled."""

import hashlib
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agent.exceptions import GroktoCrawlError
from agent.routes import documents, session
from agent.session_store import SessionStore
from agent.settings import AgentSettings
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from parse_svc.app import app as parse_app


@pytest.fixture
def parse_http_url():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(parse_app, log_level="error", lifespan="off")
    )
    worker = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Parse HTTP server did not start"
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()
        assert not worker.is_alive(), "Parse HTTP server did not stop"


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_pdf_upload_parse_question_citation_and_delete(
    monkeypatch, parse_http_url
):
    redis_url = os.environ.get("SESSION_STORE_TEST_URL") or os.environ.get("VALKEY_URL")
    if not redis_url:
        raise RuntimeError("An isolated Valkey URL is required")
    address = urlsplit(redis_url)
    settings = AgentSettings(
        VALKEY_HOST=address.hostname,
        VALKEY_PORT=address.port or 6379,
        VALKEY_DB=int(address.path.strip("/") or "0"),
    )
    monkeypatch.setattr("agent.settings.load_settings", lambda: settings)
    monkeypatch.setattr(documents, "PARSE_SVC_URL", parse_http_url)
    app = FastAPI()
    app.state.valkey_url = redis_url
    app.state.searxng_url = "http://unused"
    app.state.scraper_url = "http://unused"
    app.state.llm_base_url = "http://unused"
    app.state.llm_api_key = "test"
    app.state.llm_model = "test"
    app.include_router(documents.router)
    app.include_router(session.router)

    @app.exception_handler(GroktoCrawlError)
    async def handle(_request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    seen = []
    selected = []

    class Model:
        def __init__(self, *args):
            pass

        async def generate(self, **kwargs):
            seen.append(kwargs["context"])
            return f"The research budget is 12000 [{selected[0]}]."

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", Model)
    content = (
        Path(__file__).parents[1] / "fixtures/documents/budget-table.pdf"
    ).read_bytes()
    store = SessionStore(redis_url=redis_url)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "document-" + uuid4().hex},
    ) as client:
        created = await client.post("/v2/session/create", json={"ttl": 120})
        assert created.status_code == 200, created.text
        session_id = created.json()["sessionId"]
        try:
            uploaded = await client.post(
                f"/v2/session/{session_id}/documents",
                files={"file": ("budget.pdf", content, "application/pdf")},
            )
            assert uploaded.status_code == 200, uploaded.text
            ref_id = uploaded.json()["ref_id"]
            selected.append(ref_id)
            assert uploaded.json()["file_digest"] == hashlib.sha256(content).hexdigest()
            assert uploaded.json()["extraction"]["extraction"] == "pypdf"
            queried = await client.post(
                f"/v2/session/{session_id}/step",
                json={
                    "action": "query",
                    "params": {
                        "question": "What is the research budget?",
                        "ref_ids": [ref_id],
                    },
                },
            )
            assert queried.status_code == 200, queried.text
            assert "Research | 12000 | 11000" in seen[0]
            assert queried.json()["result"]["citations"][0]["ref_id"] == ref_id
            exact = await client.get(uploaded.json()["url"])
            assert exact.status_code == 200, exact.text
            body = exact.json()["markdown"]
            assert (
                hashlib.sha256(body.encode()).hexdigest()
                == uploaded.json()["content_digest"]
            )
            begin = body.index("Research |")
            quote = await client.get(
                uploaded.json()["url"],
                params={
                    "start": begin,
                    "end": begin + len("Research | 12000 | 11000"),
                },
            )
            assert quote.json()["markdown"] == "Research | 12000 | 11000"
            foreign = await client.get(
                uploaded.json()["url"], headers={"X-API-Key": "foreign"}
            )
            assert foreign.status_code == 404
            deleted = await client.delete(
                f"/v2/session/{session_id}/documents/{ref_id}"
            )
            assert deleted.status_code == 200, deleted.text
            assert (await client.get(uploaded.json()["url"])).status_code == 404
        finally:
            await store.adelete(session_id)
            store.redis.close()
