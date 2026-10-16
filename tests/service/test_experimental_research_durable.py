"""Bounded durable status recovery through the experimental route."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from agent.experimental.durable_research import DurableResearchLedger
from agent.routes import experimental_research as experimental
from fastapi import FastAPI
from redis import Redis


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    # The dedicated URL is used by the isolated Valkey lanes. The compose
    # integration lane already provides the same durable store as VALKEY_URL.
    url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ.get("VALKEY_URL")
    if url is None:
        raise RuntimeError("DURABLE_RESEARCH_REDIS_URL or VALKEY_URL is required")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_DURABLE", "true")
    namespace = f"groktocrawl:test:service:{uuid4()}"
    redis = Redis.from_url(url, decode_responses=True)
    original_ledger_factory = experimental.DurableResearchLedger
    created_ledgers: list[DurableResearchLedger] = []

    def namespaced_ledger_factory(redis_url: str) -> DurableResearchLedger:
        ledger = original_ledger_factory(redis_url, namespace=namespace)
        created_ledgers.append(ledger)
        return ledger

    experimental._RUNS.clear()
    experimental._IDEMPOTENCY.clear()
    experimental._DURABLE_LEDGERS.clear()
    value = FastAPI()
    value.state.valkey_url = url
    value.state.research_durable_namespace = namespace
    value.include_router(experimental.router)
    monkeypatch.setattr(
        experimental, "DurableResearchLedger", namespaced_ledger_factory
    )
    try:
        yield value
    finally:
        prefix = f"{namespace}:"
        for key in redis.scan_iter(match=f"{prefix}*"):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if key.startswith(prefix):
                redis.delete(key)
        for ledger in created_ledgers:
            ledger.redis.close()
        experimental._DURABLE_LEDGERS.pop(url, None)
        redis.close()


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _wait_for_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    for _ in range(100):
        response = await client.get(f"/experimental/research/v1/runs/{run_id}")
        payload = response.json()
        if payload["state"] in {"completed", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0)
    raise AssertionError("durable fixture run did not reach a terminal state")


def _sse_events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_status_recovers_from_durable_terminal_projection(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "durable-status-1"},
            json={"objective": "Recover durable status"},
        )
        assert created.status_code == 202
        admission = created.json()
        status = await _wait_for_terminal(client, admission["run_id"])
        assert status["state"] == "completed"

        url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ["VALKEY_URL"]
        ledger = DurableResearchLedger(
            url, namespace=app.state.research_durable_namespace
        )
        durable = ledger.get(admission["run_id"])
        assert durable is not None
        assert durable.checkpoint_name == "terminal_projection"
        assert durable.terminal_payload is not None
        original_events = (await client.get(admission["events_url"])).text
        original_manifest = await client.get(status["result"]["manifest_url"])
        original_artifact = await client.get(status["result"]["artifacts"]["summary"])
        assert original_manifest.status_code == 200
        assert original_artifact.status_code == 200

        experimental._RUNS.clear()
        recovered = await client.get(admission["status_url"])
        assert recovered.status_code == 200
        assert recovered.json()["state"] == "completed"
        assert recovered.json()["result"] == status["result"]
        recovered_events = await client.get(admission["events_url"])
        assert recovered_events.status_code == 200
        assert _sse_events(recovered_events.text) == _sse_events(original_events)
        recovered_manifest = await client.get(status["result"]["manifest_url"])
        recovered_artifact = await client.get(status["result"]["artifacts"]["summary"])
        assert recovered_manifest.json() == original_manifest.json()
        assert recovered_artifact.content == original_artifact.content


@pytest.mark.asyncio
async def test_cancel_persists_durable_terminal_state(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockedJourney:
        async def run(self) -> None:
            started.set()
            await release.wait()

    monkeypatch.setattr(
        experimental,
        "example_journey",
        lambda **_: BlockedJourney(),
    )
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "durable-cancel-1"},
            json={"objective": "Cancel durable status"},
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        await asyncio.wait_for(started.wait(), timeout=1)
        cancelled = await client.post(f"/experimental/research/v1/runs/{run_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["state"] == "cancelled"

        durable = DurableResearchLedger(
            os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ["VALKEY_URL"],
            namespace=app.state.research_durable_namespace,
        ).get(run_id)
        assert durable is not None
        assert durable.state == "cancelled"
        release.set()
        await asyncio.sleep(0)
        experimental._RUNS.clear()
        recovered = await client.get(created.json()["status_url"])
        assert recovered.status_code == 200
        assert recovered.json()["state"] == "cancelled"
        events = await client.get(created.json()["events_url"])
        assert "event: cancelled" in events.text


@pytest.mark.asyncio
async def test_admitted_run_recovers_and_cancel_does_not_resurrect(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused = asyncio.Event()

    async def pause(_record: object) -> None:
        await paused.wait()

    monkeypatch.setattr(experimental, "_execute_run", pause)
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "x"},
            json={"objective": "Recover admitted status"},
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        experimental._RUNS.clear()
        recovered = await client.get(created.json()["status_url"])
        assert recovered.status_code == 200
        assert recovered.json()["state"] == "accepted"
        cancelled = await client.post(f"{created.json()['status_url']}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["state"] == "cancelled"
        experimental._RUNS.clear()
        terminal = await client.get(f"/experimental/research/v1/runs/{run_id}")
        assert terminal.status_code == 200
        assert terminal.json()["state"] == "cancelled"
        paused.set()


@pytest.mark.asyncio
async def test_running_run_reclaims_after_lease_loss(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    real_example_journey = experimental.example_journey

    class BlockedJourney:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        async def run(self) -> object:
            started.set()
            await release.wait()
            return await self.inner.run()  # type: ignore[attr-defined]

    monkeypatch.setattr(
        experimental,
        "example_journey",
        lambda **kwargs: BlockedJourney(real_example_journey(**kwargs)),
    )
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "durable-lease-loss-1"},
            json={"objective": "Reclaim running status"},
        )
        run_id = created.json()["run_id"]
        await asyncio.wait_for(started.wait(), timeout=1)
        original = experimental._RUNS[run_id]
        assert original.durable_ledger is not None
        original_task = original.task
        assert original_task is not None
        original.durable_ledger.redis.delete(original.durable_ledger._lease_key(run_id))
        original_task.cancel()
        await asyncio.gather(original_task, return_exceptions=True)
        experimental._RUNS.clear()
        started.clear()
        recovered = await client.get(created.json()["status_url"])
        assert recovered.status_code == 200
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        status = await _wait_for_terminal(client, run_id)
        assert status["state"] == "completed"


@pytest.mark.asyncio
async def test_tampered_durable_artifact_projection_fails_closed(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "x"},
            json={"objective": "Reject tampered artifact"},
        )
        admission = created.json()
        status = await _wait_for_terminal(client, admission["run_id"])
        assert status["state"] == "completed"

        url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ["VALKEY_URL"]
        ledger = DurableResearchLedger(
            url, namespace=app.state.research_durable_namespace
        )
        raw = ledger.redis.get(ledger._run_key(admission["run_id"]))
        assert raw is not None
        record = json.loads(raw)
        artifact_id = status["result"]["artifacts"]["summary"].rsplit("/", 1)[-1]
        record["terminal_payload"]["artifacts"][artifact_id]["body_b64"] = (
            "dGFtcGVyZWQ="
        )
        ledger.redis.set(ledger._run_key(admission["run_id"]), json.dumps(record))

        experimental._RUNS.clear()
        artifact = await client.get(status["result"]["artifacts"]["summary"])
        assert artifact.status_code == 503


@pytest.mark.asyncio
async def test_durable_deletion_tombstone_survives_process_loss(app: FastAPI) -> None:
    async with await _client(app) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "x"},
            json={"objective": "Persist deletion authority"},
        )
        admission = created.json()
        status = await _wait_for_terminal(client, admission["run_id"])
        assert status["state"] == "completed"

        deleted = await client.delete(
            f"/experimental/research/v1/research/{status['research_id']}"
        )
        assert deleted.status_code == 202
        assert deleted.json() == {
            "research_id": status["research_id"],
            "state": "deleted",
        }

        url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ["VALKEY_URL"]
        durable = DurableResearchLedger(
            url, namespace=app.state.research_durable_namespace
        ).get(admission["run_id"])
        assert durable is not None
        assert durable.state == "deleted"
        experimental._RUNS.clear()

        recovered_status = await client.get(admission["status_url"])
        assert recovered_status.status_code == 410
        recovered_artifact = await client.get(status["result"]["artifacts"]["summary"])
        assert recovered_artifact.status_code == 410


@pytest.mark.asyncio
async def test_cached_workspace_cannot_serve_after_external_durable_delete(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        admitted = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "external-delete"},
                json={"objective": "No stale private evidence"},
            )
        ).json()
        for _ in range(100):
            status = (await client.get(admitted["status_url"])).json()
            if status["state"] == "completed":
                break
            await asyncio.sleep(0)
        assert status["state"] == "completed"
        record = experimental._RUNS[admitted["run_id"]]
        assert record.journey is not None
        record.durable_ledger.delete(
            record.run_id,
            terminal_payload={"deleted": True, "research_id": record.research_id},
        )
        assert not record.deleted  # mimic another process's deletion
        assert (
            await client.get("/experimental/research/v1/workspace/" + record.run_id)
        ).status_code == 410
        assert (
            await client.get(
                "/experimental/research/v1/research/"
                + record.research_id
                + "/evidence/source-1"
            )
        ).status_code == 410
        assert (
            await client.get(status["result"]["artifacts"]["summary"])
        ).status_code == 410
        assert (await client.get("/experimental/research/v1/workspace")).json()[
            "items"
        ] == []
