"""Combined document admission and explicit follow-up API contract."""

import httpx
import pytest
from agent.routes import followup

from tests.service.test_session_documents import attach
from tests.service.test_session_documents import document_app as document_app


@pytest.mark.asyncio
async def test_document_ref_preview_keeps_identity_and_rejects_detached_ref(
    document_app, monkeypatch
):
    app, store = document_app
    app.include_router(followup.router)
    monkeypatch.setattr(followup, "SessionStore", lambda **_: store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        admitted = await attach(
            client,
            "policy.txt",
            b"Private fixture policy states a 30-day retention period.",
        )
        assert admitted.status_code == 200
        document = admitted.json()
        payload = {
            "wording": "How does it handle retention?",
            "selected": [
                {
                    "kind": "session",
                    "container_id": "session-a",
                    "ref_id": document["ref_id"],
                    "subject": "Fixture policy",
                }
            ],
        }
        preview = await client.post("/v2/followup/preview", json=payload)
        assert preview.status_code == 200
        result = preview.json()
        assert result["proposed_query"] == "How does Fixture policy handle retention?"
        assert result["selected"][0]["identity"]["ref_id"] == document["ref_id"]
        assert result["executed"] is False
        assert "Private fixture policy states" not in preview.text
        assert (await client.delete(document["url"])).status_code == 200
        unavailable = await client.post("/v2/followup/preview", json=payload)
        assert unavailable.status_code == 404
        assert store.refs == {}
