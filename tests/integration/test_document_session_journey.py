"""Real Parse HTTP and Valkey document journey; only model output is controlled."""

import base64
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
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
def http_servers():
    running = []

    def start(app):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        worker = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        running.append((server, worker, listener))
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Local HTTP server did not start"
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    try:
        yield start
    finally:
        for server, worker, listener in reversed(running):
            server.should_exit = True
            worker.join(timeout=10)
            listener.close()
            assert not worker.is_alive(), "Local HTTP server did not stop"


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_pdf_upload_parse_question_citation_and_delete(
    monkeypatch, http_servers, tmp_path
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
    monkeypatch.setattr(documents, "PARSE_SVC_URL", http_servers(parse_app))
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
            if "Research retention policy requires 47 days." in kwargs["context"]:
                return f"The retention period is 47 days [{selected[0]}]."
            return f"The research budget is 12000 [{selected[0]}]."

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", Model)
    content = (
        Path(__file__).parents[1] / "fixtures/documents/budget-table.pdf"
    ).read_bytes()
    store = SessionStore(redis_url=redis_url)
    api_url = http_servers(app)
    owner_key = "document-" + uuid4().hex
    async with httpx.AsyncClient(
        base_url=api_url,
        headers={"X-API-Key": owner_key},
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
            # Exercise the actual CLI process and MCP tool dispatch against the
            # same HTTP API and retained bytes, not mocked client responses.
            repo = Path(__file__).resolve().parents[2]
            document_path = tmp_path / "budget.pdf"
            document_path.write_bytes(content)
            cli_env = {
                **os.environ,
                "API_KEY": owner_key,
                "GROKTOCRAWL_API_KEY": owner_key,
            }

            def cli(*args):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(repo / "groktocrawl"),
                        "--server",
                        api_url,
                        "--json",
                        "document",
                        *args,
                    ],
                    env=cli_env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)

            assert cli("attach", session_id, str(document_path))["ref_id"] == ref_id
            assert (
                cli("show", session_id, ref_id)["content_digest"]
                == uploaded.json()["content_digest"]
            )
            cli_answer = cli(
                "query", session_id, "What is the research budget?", "--ref", ref_id
            )
            assert cli_answer["result"]["citations"][0]["ref_id"] == ref_id
            monkeypatch.syspath_prepend(str(repo / "mcp-svc"))
            mcp_server = importlib.import_module("mcp_server")
            from groktocrawl_client import GroktocrawlClient

            async with GroktocrawlClient(
                base_url=api_url, api_key=owner_key
            ) as mcp_client:
                monkeypatch.setattr(mcp_server, "_client", mcp_client)

                async def tool(name, arguments):
                    response, _structured = await mcp_server.mcp.call_tool(
                        name, arguments
                    )
                    return json.loads(response[0].text)

                attached = await tool(
                    "document_attach",
                    {
                        "session_id": session_id,
                        "filename": "budget.pdf",
                        "content_base64": base64.b64encode(content).decode(),
                        "media_type": "application/pdf",
                    },
                )
                assert attached["ref_id"] == ref_id
                readback = await tool(
                    "document_read", {"session_id": session_id, "ref_id": ref_id}
                )
                assert readback["content_digest"] == uploaded.json()["content_digest"]
                answer = await tool(
                    "session_query",
                    {
                        "session_id": session_id,
                        "question": "What is the research budget?",
                        "ref_ids": [ref_id],
                    },
                )
                assert answer["result"]["citations"][0]["ref_id"] == ref_id
                # A second document puts the answer beyond the former 8,000
                # character prefix. The selected passage must reach the model.
                late_fact = "Research retention policy requires 47 days."
                long_text = (
                    "Background information about unrelated office supplies.\n" * 200
                ) + late_fact
                assert long_text.index(late_fact) > 8000
                late = await tool(
                    "document_attach",
                    {
                        "session_id": session_id,
                        "filename": "retention.txt",
                        "content_base64": base64.b64encode(long_text.encode()).decode(),
                        "media_type": "text/plain",
                    },
                )
                selected[0] = late["ref_id"]
                late_answer = await tool(
                    "session_query",
                    {
                        "session_id": session_id,
                        "question": "How many days does the research retention policy require?",
                        "ref_ids": [late["ref_id"]],
                    },
                )
                assert late_fact in seen[-1]
                assert late_answer["result"]["citations"][0]["ref_id"] == late["ref_id"]
                late_read = await tool(
                    "document_read",
                    {
                        "session_id": session_id,
                        "ref_id": late["ref_id"],
                        "start": long_text.index(late_fact),
                        "end": len(long_text),
                    },
                )
                assert late_read["markdown"] == late_fact
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
