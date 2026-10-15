"""Workspace named actions use actual owned document and follow-up authority."""

import httpx
import pytest
from agent import session_store
from agent.routes import experimental_research as experimental
from agent.routes import followup

from tests.service.test_experimental_research_runs import _wait_for_terminal
from tests.service.test_session_documents import attach
from tests.service.test_session_documents import document_app as document_app


@pytest.mark.asyncio
async def test_workspace_named_document_context_and_preview(document_app, monkeypatch):
    app, store = document_app
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_DURABLE", "false")
    experimental._RUNS.clear()
    experimental._IDEMPOTENCY.clear()
    app.include_router(experimental.router)
    app.include_router(followup.router)
    monkeypatch.setattr(session_store, "SessionStore", lambda **_: store)
    monkeypatch.setattr(followup, "SessionStore", lambda **_: store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        admitted = await attach(client, "policy.txt", b"Policy retention is 30 days.")
        document = admitted.json()
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "workspace-document-contract"},
            json={"objective": "Inspect a synthetic policy"},
        )
        run_id = created.json()["run_id"]
        await _wait_for_terminal(client, run_id)
        selected = await client.get(f"/experimental/research/v1/workspace/{run_id}")
        revision = selected.json()["revision"]
        action_url = f"/experimental/research/v1/workspace/{run_id}/actions"

        async def action(name, **options):
            return await client.post(
                action_url,
                json={"action": name, "expected_revision": revision, **options},
            )

        listed = await action("documents", session_id="session-a")
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["ref_id"] == document["ref_id"]
        quote = await action(
            "document_evidence",
            session_id="session-a",
            ref_id=document["ref_id"],
            start=0,
            end=6,
        )
        assert quote.status_code == 200
        assert quote.json()["markdown"] == "Policy"
        preview = await action(
            "followup_preview",
            wording="How does it handle retention?",
            selected=[
                {
                    "kind": "session",
                    "container_id": "session-a",
                    "ref_id": document["ref_id"],
                    "subject": "Fixture policy",
                }
            ],
        )
        assert preview.status_code == 200
        assert preview.json()["executed"] is False
        assert (
            preview.json()["proposed_query"]
            == "How does Fixture policy handle retention?"
        )
        assert (
            await action("documents", session_id="foreign-session")
        ).status_code == 404
        assert (await client.delete(document["url"])).status_code == 200
        assert (
            await action(
                "document_evidence", session_id="session-a", ref_id=document["ref_id"]
            )
        ).status_code == 404
