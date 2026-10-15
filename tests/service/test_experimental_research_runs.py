"""Fixture-backed W6 run, replay, artifact, evidence, and deletion adapters."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from agent.routes import experimental_research as experimental
from fastapi import FastAPI


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    experimental._RUNS.clear()
    experimental._IDEMPOTENCY.clear()
    experimental._SESSION_ATTACHMENTS.clear()
    value = FastAPI()
    value.include_router(experimental.router)
    return value


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _wait_for_terminal(
    client: httpx.AsyncClient, run_id: str, headers: dict[str, str] | None = None
) -> dict:
    for _ in range(100):
        response = await client.get(
            f"/experimental/research/v1/runs/{run_id}", headers=headers
        )
        payload = response.json()
        if payload["state"] in {"completed", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0)
    raise AssertionError("fixture run did not reach a terminal state")


@pytest.mark.asyncio
async def test_run_idempotency_status_replay_and_retained_reads(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "run-1"},
            json={"objective": "Assess the fictional pilot"},
        )
        assert created.status_code == 202
        admission = created.json()
        duplicate = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "run-1"},
            json={"objective": "Assess the fictional pilot"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["run_id"] == admission["run_id"]

        conflict = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "run-1"},
            json={"objective": "A different objective"},
        )
        assert conflict.status_code == 409

        status = await _wait_for_terminal(client, admission["run_id"])
        assert status["state"] == "completed"
        result = status["result"]
        assert result["research_id"] == admission["research_id"]

        events = await client.get(admission["events_url"])
        assert events.status_code == 200
        assert "event: done" in events.text
        first_event_id = events.text.split("id: ", 1)[1].split("\n", 1)[0]
        replay = await client.get(
            admission["events_url"], headers={"Last-Event-ID": first_event_id}
        )
        assert replay.status_code == 200
        assert first_event_id not in replay.text

        manifest = await client.get(result["manifest_url"])
        assert manifest.status_code == 200
        artifact = await client.get(result["artifacts"]["summary"])
        assert artifact.status_code == 200
        evidence = await client.get(
            f"/experimental/research/v1/research/{result['research_id']}/evidence/source-1"
        )
        assert evidence.status_code == 200
        assert evidence.json()["content_digest"]

        deleted = await client.delete(
            f"/experimental/research/v1/research/{result['research_id']}"
        )
        assert deleted.status_code == 202
        assert (await client.get(result["manifest_url"])).status_code == 410
        assert (await client.get(result["artifacts"]["summary"])).status_code == 410
        assert (
            await client.get(
                f"/experimental/research/v1/research/{result['research_id']}/evidence/source-1"
            )
        ).status_code == 410


@pytest.mark.asyncio
async def test_cancel_returns_one_cancelled_terminal(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "cancel-1"},
            json={"objective": "Cancel the fictional pilot"},
        )
        run_id = created.json()["run_id"]
        cancelled = await client.post(f"/experimental/research/v1/runs/{run_id}/cancel")
        assert cancelled.status_code == 202
        status = await _wait_for_terminal(client, run_id)
        assert status["state"] == "cancelled"
        events = await client.get(created.json()["events_url"])
        assert events.text.count("event: cancelled") == 1
        assert "event: done" not in events.text


@pytest.mark.asyncio
async def test_session_attachment_is_idempotent_and_revision_guarded(
    app: FastAPI,
) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "attach-1"},
            json={"objective": "Attach the fictional pilot"},
        )
        admission = created.json()
        status = await _wait_for_terminal(client, admission["run_id"])
        assert status["state"] == "completed"

        attached = await client.post(
            "/experimental/research/v1/sessions/session-1/attachments",
            json={"run_id": admission["run_id"], "expected_revision": 0},
        )
        assert attached.status_code == 200
        assert attached.json()["revision"] == 1

        duplicate = await client.post(
            "/experimental/research/v1/sessions/session-1/attachments",
            json={"run_id": admission["run_id"], "expected_revision": 1},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["revision"] == 1

        conflict = await client.post(
            "/experimental/research/v1/sessions/session-1/attachments",
            json={"run_id": admission["run_id"], "expected_revision": 0},
        )
        assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_run_scope_blocks_foreign_reads_and_mutations(app: FastAPI) -> None:
    owner = {"Authorization": "Bearer owner"}
    foreign = {"Authorization": "Bearer foreign"}
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={**owner, "Idempotency-Key": "scope-1"},
            json={"objective": "Check scope isolation"},
        )
        admission = created.json()
        run_id = admission["run_id"]

        assert (
            await client.get(admission["status_url"], headers=foreign)
        ).status_code == 404
        assert (
            await client.get(admission["events_url"], headers=foreign)
        ).status_code == 404
        assert (
            await client.post(f"{admission['status_url']}/cancel", headers=foreign)
        ).status_code == 404

        status = await _wait_for_terminal(client, run_id, headers=owner)
        assert status["state"] == "completed"
        result = status["result"]
        assert (
            await client.get(result["manifest_url"], headers=foreign)
        ).status_code == 404
        assert (
            await client.delete(
                f"/experimental/research/v1/research/{result['research_id']}",
                headers=foreign,
            )
        ).status_code == 404

        attached = await client.post(
            "/experimental/research/v1/sessions/scope-session/attachments",
            headers=owner,
            json={"run_id": run_id, "expected_revision": 0},
        )
        assert attached.status_code == 200
        foreign_attachment = await client.post(
            "/experimental/research/v1/sessions/scope-session/attachments",
            headers=foreign,
            json={"run_id": run_id, "expected_revision": 0},
        )
        assert foreign_attachment.status_code == 404


@pytest.mark.asyncio
async def test_deletion_tombstone_wins_over_late_completion(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = asyncio.Event()
    original_factory = experimental.example_journey

    def delayed_factory(**kwargs):
        journey = original_factory(**kwargs)
        original_run = journey.run

        async def delayed_run():
            await gate.wait()
            return await original_run()

        journey.run = delayed_run
        return journey

    monkeypatch.setattr(experimental, "example_journey", delayed_factory)
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "delete-race-1"},
            json={"objective": "Check deletion race"},
        )
        admission = created.json()
        run_id = admission["run_id"]
        await asyncio.sleep(0)
        record = experimental._RUNS[run_id]
        assert record.state == "running"

        deleted = await client.delete(
            f"/experimental/research/v1/research/{admission['research_id']}"
        )
        assert deleted.status_code == 202
        gate.set()
        await asyncio.wait_for(record.task, timeout=2)
        assert record.state == "cancelled"
        assert record.result is None
        assert record.deleted is True

        assert (await client.get(admission["status_url"])).status_code == 410
        assert (await client.get(admission["events_url"])).status_code == 410
        assert (await client.get(f"/experimental/research/v1/workspace/{run_id}")).status_code == 410
        attached = await client.post(
            "/experimental/research/v1/sessions/deleted-session/attachments",
            json={"run_id": run_id, "expected_revision": 0},
        )
        assert attached.status_code == 410


@pytest.mark.asyncio
async def test_workspace_scope_revision_reload_and_deletion(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "workspace"},
                json={"objective": "Inspect retained fictional evidence"},
            )
        ).json()
        status = await _wait_for_terminal(client, created["run_id"])
        listing = (await client.get("/experimental/research/v1/workspace")).json()
        assert listing["items"][0]["run_id"] == created["run_id"]
        selected_url = listing["items"][0]["url"]
        selected = (await client.get(selected_url)).json()
        assert selected["revision"] and selected["manifest"]
        assert selected["capabilities"]["recovery_mode"] == "process_local"
        assert (
            await client.get(selected_url, params={"expected_revision": "stale"})
        ).status_code == 409
        assert (
            await client.get(selected_url, headers={"Authorization": "Bearer foreign"})
        ).status_code == 404
        assert (
            await client.get(
                "/experimental/research/v1/workspace",
                headers={"Authorization": "Bearer foreign"},
            )
        ).json()["items"] == []
        assert (await client.get(selected_url)).json()["revision"] == selected[
            "revision"
        ]
        await client.delete(
            "/experimental/research/v1/research/" + status["research_id"]
        )
        assert (await client.get(selected_url)).status_code == 410
        assert (await client.get("/experimental/research/v1/workspace")).json()[
            "items"
        ] == []
        assert (
            await client.get("/experimental/research/v1/workspace?limit=101")
        ).status_code == 422


@pytest.mark.asyncio
async def test_named_workspace_actions_pin_revision_and_citations(app: FastAPI):
    async with await _client(app) as client:
        created = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "named-action"},
                json={"objective": "Explicit actions"},
            )
        ).json()
        await _wait_for_terminal(client, created["run_id"])
        url = "/experimental/research/v1/workspace/" + created["run_id"]
        selected = (await client.get(url)).json()
        assert (
            await client.post(url + "/actions", json={"action": "render"})
        ).status_code == 409
        assert (
            await client.post(
                url + "/actions", json={"action": "render", "expected_revision": "old"}
            )
        ).status_code == 409
        base = {"expected_revision": selected["revision"]}
        export = (
            await client.post(url + "/actions", json={**base, "action": "export"})
        ).json()
        assert export["audited"] and export["markdown"]
        cite = selected["evidence"][0]
        evidence = (
            await client.post(
                url + "/actions",
                json={
                    **base,
                    "action": "request_evidence",
                    "snapshot_id": cite["snapshot_id"],
                },
            )
        ).json()
        assert evidence["body"][cite["start"] : cite["end"]]
        assert (
            await client.post(
                url + "/actions",
                json={**base, "action": "request_evidence", "snapshot_id": "foreign"},
            )
        ).status_code == 404
        assert (
            await client.post(url + "/actions", json={**base, "action": "cancel"})
        ).status_code == 409


@pytest.mark.asyncio
async def test_deleting_one_root_does_not_block_other_roots(app: FastAPI):
    async with await _client(app) as client:
        runs = []
        for key in ["first", "second"]:
            created = (
                await client.post(
                    "/experimental/research/v1/runs",
                    headers={"Idempotency-Key": key},
                    json={"objective": key},
                )
            ).json()
            runs.append(await _wait_for_terminal(client, created["run_id"]))
        await client.delete(
            "/experimental/research/v1/research/" + runs[0]["research_id"]
        )
        other = await client.get(
            "/experimental/research/v1/workspace/" + runs[1]["run_id"]
        )
        assert other.status_code == 200
        for url in runs[1]["result"]["artifacts"].values():
            assert (await client.get(url)).status_code == 200
        assert (
            await client.get("/experimental/research/v1/artifacts/unknown")
        ).status_code == 404
