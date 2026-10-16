"""Real Valkey session attachment/query/read contract with controlled root/model.

This does not certify live research generation; the live HTTP adapter tests do.
"""

import asyncio
import os
import uuid

import httpx
import pytest
from agent.routes import experimental_research as research
from agent.routes.session import router as sessions
from agent.session_store import SessionStore
from fastapi import FastAPI


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_retained_root_attach_query_exact_evidence_and_deletion(monkeypatch):
    url = os.environ.get("SESSION_STORE_TEST_URL") or os.environ.get("VALKEY_URL")
    if not url:
        raise RuntimeError("An isolated Valkey URL is required")
    from urllib.parse import urlsplit

    address = urlsplit(url)
    monkeypatch.setenv("VALKEY_HOST", address.hostname)
    monkeypatch.setenv("VALKEY_PORT", str(address.port or 6379))
    monkeypatch.setenv("VALKEY_DB", address.path.strip("/") or "0")
    from agent.settings import AgentSettings

    settings = AgentSettings(
        VALKEY_HOST=address.hostname,
        VALKEY_PORT=address.port or 6379,
        VALKEY_DB=int(address.path.strip("/") or "0"),
    )
    monkeypatch.setattr("agent.settings.load_settings", lambda: settings)
    for flag in (
        "EXPERIMENTAL_RESEARCH",
        "EXPERIMENTAL_RESEARCH_RUNS",
        "EXPERIMENTAL_RESEARCH_FIXTURE_RUNS",
    ):
        monkeypatch.setenv("FEATURE_" + flag, "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_DURABLE", "false")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_ARTIFACT_AUTHORITY", "false")
    app = FastAPI()
    app.state.redis_url = url
    app.state.valkey_url = url
    app.state.searxng_url = "http://unused"
    app.state.scraper_url = "http://unused"
    app.state.llm_base_url = "http://unused"
    app.state.llm_api_key = "test"
    app.state.llm_model = "test"
    app.include_router(research.router)
    app.include_router(sessions)
    seen = []
    selected_ref = []

    class Model:
        def __init__(self, *args):
            pass

        async def generate(self, **kwargs):
            seen.append(kwargs["context"])
            return f"The retained source states this [{selected_ref[0]}]."

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", Model)
    owner = {"X-API-Key": "attachment-" + uuid.uuid4().hex}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=owner
    ) as client:
        created = await client.post("/v2/session/create", json={"ttl": 120})
        assert created.status_code == 200, created.text
        session_id = created.json()["sessionId"]
        store = SessionStore(redis_url=url)
        run_id = None
        try:
            admitted = await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={"objective": "Inspect retained source evidence"},
            )
            assert admitted.status_code == 202, admitted.text
            run_id = admitted.json()["run_id"]
            for _ in range(100):
                status = (await client.get(admitted.json()["status_url"])).json()
                if status["state"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert status["state"] == "completed", status
            attached = await client.post(
                f"/experimental/research/v1/sessions/{session_id}/attachments",
                json={"run_id": run_id, "expected_revision": 0},
            )
            assert attached.status_code == 200, attached.text
            ref_id = attached.json()["ref_ids"][0]
            selected_ref.append(ref_id)
            stored = await store.aget_ref(session_id, ref_id)
            assert "markdown" not in stored
            queried = await client.post(
                f"/v2/session/{session_id}/step",
                json={
                    "action": "query",
                    "params": {
                        "question": "What does this source say?",
                        "ref_ids": [ref_id],
                    },
                },
            )
            assert queried.status_code == 200, queried.text
            assert seen and ref_id in seen[0]
            assert queried.json()["result"]["citations"][0]["ref_id"] == ref_id
            exact = await client.get(f"/v2/session/{session_id}/evidence/{ref_id}")
            assert exact.status_code == 200, exact.text
            assert exact.json()["content_digest"] == stored["content_digest"]
            deleted = await client.delete(
                f"/experimental/research/v1/research/{attached.json()['research_id']}"
            )
            assert deleted.status_code == 202
            assert (
                await client.get(f"/v2/session/{session_id}/evidence/{ref_id}")
            ).status_code == 410
        finally:
            await store.adelete(session_id)
            store.redis.close()
            if run_id is not None:
                research._RUNS.pop(run_id, None)
