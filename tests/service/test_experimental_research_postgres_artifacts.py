"""PostgreSQL-authority route projection and reconciliation contracts."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import runpy
import sys
import types
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from agent.experimental.artifact_authority import (
    ArtifactAuthority,
    ArtifactLifecycleError,
    RetainedArtifactSet,
)
from agent.experimental.durable_research import DurableResearchLedger
from agent.experimental.source_store import StorageConflictError
from agent.routes import experimental_research as experimental
from fastapi import FastAPI
from redis import Redis


class FakeAuthority:
    def __init__(self) -> None:
        self.values: dict[tuple[UUID, UUID], RetainedArtifactSet] = {}
        self.deleted: set[tuple[UUID, UUID]] = set()
        self.identities = {}
        self.knowledge = {}
        self.evidence = {}
        self.commits = 0
        self.deletions = 0

    async def list_retained(self, scope, *, limit=100, offset=0):
        return [
            {
                "research_id": value.research_id,
                "run_id": value.run_id,
                "artifact_set_id": value.artifact_set_id,
                "set_digest": value.set_digest,
                "expires_at": datetime.fromisoformat("2026-11-01T00:00:00+00:00"),
            }
            for value in sorted(
                self.values.values(), key=lambda value: str(value.run_id)
            )
            if value.scope_id == scope
            and (scope, value.research_id) not in self.deleted
        ][offset : offset + limit]

    async def lookup(self, scope, *, run=None, artifact_set=None, artifact_id=None):
        for (owner, research), (run_id, set_id) in self.identities.items():
            if (
                owner == scope
                and (run_id == run or set_id == artifact_set)
                and (owner, research) in self.deleted
            ):
                raise ArtifactLifecycleError(410)
        for value in self.values.values():
            if value.scope_id == scope and (
                value.run_id == run
                or value.artifact_set_id == artifact_set
                or any(item.artifact_id == artifact_id for item in value.artifacts)
            ):
                return await self.read(scope, value.research_id)
        raise ArtifactLifecycleError(404)

    async def retention(self, scope, research):
        if (scope, research) not in self.values and (
            scope,
            research,
        ) not in self.deleted:
            raise ArtifactLifecycleError(404)
        return {
            "deleted": (scope, research) in self.deleted,
            "live": True,
            "expires_at": "2026-11-01T00:00:00+00:00",
        }

    async def read_knowledge(self, scope, research):
        return self.knowledge[(scope, research)]

    async def read_evidence(self, scope, research, snapshot):
        if (scope, research) in self.deleted or snapshot not in self.evidence.get(
            (scope, research), {}
        ):
            raise ArtifactLifecycleError(
                410 if (scope, research) in self.deleted else 404
            )
        return self.evidence[(scope, research)][snapshot]

    async def ensure_evidence_schema(self):
        return None

    async def ensure_scope(self, scope: UUID) -> None:
        return None

    async def commit(
        self,
        scope: UUID,
        research: UUID,
        run: UUID,
        artifact_set: UUID,
        manifest: bytes,
        artifacts: dict[str, tuple[str, bytes]],
        evidence=None,
        knowledge=None,
    ) -> RetainedArtifactSet:
        self.commits += 1
        manifest_digest, retained, set_digest, _ = ArtifactAuthority._validate(
            manifest, artifacts
        )
        candidate = RetainedArtifactSet(
            scope,
            research,
            run,
            artifact_set,
            manifest,
            manifest_digest,
            set_digest,
            retained,
        )
        if (scope, research) in self.deleted:
            raise StorageConflictError("artifact set replay changed")
        prior = self.values.get((scope, research))
        if prior is not None and prior != candidate:
            raise StorageConflictError("artifact set replay changed")
        self.identities[(scope, research)] = (run, artifact_set)
        self.knowledge[(scope, research)] = knowledge
        self.evidence[(scope, research)] = evidence or {}
        self.values[(scope, research)] = candidate
        return candidate

    async def read(self, scope: UUID, research: UUID) -> RetainedArtifactSet:
        if (scope, research) in self.deleted:
            raise ArtifactLifecycleError(410)
        try:
            return self.values[(scope, research)]
        except KeyError as exc:
            raise StorageConflictError("artifact set unavailable") from exc

    async def find_run(self, scope: UUID, run: UUID) -> RetainedArtifactSet | None:
        return next(
            (
                value
                for value in self.values.values()
                if value.scope_id == scope and value.run_id == run
            ),
            None,
        )

    async def delete(self, scope: UUID, research: UUID) -> None:
        self.deletions += 1
        self.deleted.add((scope, research))
        self.knowledge.pop((scope, research), None)
        self.evidence.pop((scope, research), None)
        self.values.pop((scope, research), None)


@pytest.fixture
def authority_app(monkeypatch: pytest.MonkeyPatch):
    url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ.get("VALKEY_URL")
    if url is None:
        raise RuntimeError("DURABLE_RESEARCH_REDIS_URL or VALKEY_URL is required")
    # The older durable-route suite flushes database 15. Give each xdist worker
    # a separate database so fixture cleanup cannot delete another test's run.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "local")
    database = 14 if worker == "local" else 10 + int(worker.removeprefix("gw"))
    url = f"{url.rsplit('/', 1)[0]}/{database}"
    for name in (
        "EXPERIMENTAL_RESEARCH",
        "EXPERIMENTAL_RESEARCH_RUNS",
        "EXPERIMENTAL_RESEARCH_DURABLE",
        "EXPERIMENTAL_RESEARCH_POSTGRES_ARTIFACTS",
    ):
        monkeypatch.setenv(f"FEATURE_{name}", "true")
    Redis.from_url(url, decode_responses=True).flushdb()
    experimental._RUNS.clear()
    experimental._IDEMPOTENCY.clear()
    experimental._DURABLE_LEDGERS.clear()
    experimental._ARTIFACT_AUTHORITIES.clear()
    authority = FakeAuthority()
    experimental._ARTIFACT_AUTHORITIES["fixture-postgres"] = authority  # type: ignore[assignment]
    app = FastAPI()
    app.state.valkey_url = url
    app.state.research_postgres_dsn = "fixture-postgres"
    app.include_router(experimental.router)
    return app, authority, url


async def _wait(client: httpx.AsyncClient, url: str) -> dict:
    for _ in range(200):
        response = await client.get(url)
        if response.json()["state"] in {"completed", "failed", "cancelled"}:
            return response.json()
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish")


@pytest.mark.asyncio
async def test_valkey_has_pointers_and_postgres_recovers_exact_bytes(
    authority_app,
) -> None:
    app, authority, url = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "postgres-authority"},
            json={"objective": "Retain exact artifacts"},
        )
        status = await _wait(client, created.json()["status_url"])
        raw = DurableResearchLedger(url).redis.get(
            DurableResearchLedger(url)._run_key(status["run_id"])
        )
        terminal = json.loads(raw)["terminal_payload"]
        assert "artifact_authority" in terminal
        assert "manifest" not in terminal
        assert "body_b64" not in json.dumps(terminal)
        assert "summary" not in terminal["result"]
        assert all(
            "summary" not in (event.get("result") or {}) for event in terminal["events"]
        )

        original = await client.get(status["result"]["artifacts"]["summary"])
        experimental._RUNS.clear()
        recovered = await client.get(status["result"]["artifacts"]["summary"])
        assert recovered.content == original.content
        assert authority.commits == 1


@pytest.mark.asyncio
async def test_postgres_commit_reconciles_after_valkey_interruption(
    authority_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, authority, url = authority_app
    original = DurableResearchLedger.commit_result
    interrupted = True

    def interrupt_once(self, *args, **kwargs):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise RuntimeError("simulated projection interruption")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DurableResearchLedger, "commit_result", interrupt_once)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "interrupted-projection"},
            json={"objective": "Reconcile committed artifacts"},
        )
        task = experimental._RUNS[created.json()["run_id"]].task
        assert task is not None
        with pytest.raises(RuntimeError, match="projection interruption"):
            await task
        ledger = DurableResearchLedger(url)
        ledger.redis.delete(ledger._lease_key(created.json()["run_id"]))
        experimental._RUNS.clear()
        await client.get(created.json()["status_url"])
        status = await _wait(client, created.json()["status_url"])
        assert status["state"] == "completed"
        assert authority.commits == 1
        assert len(authority.values) == 1


@pytest.mark.asyncio
async def test_deletion_reaches_postgres_before_valkey(authority_app) -> None:
    app, authority, _ = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "delete-authority"},
            json={"objective": "Delete retained artifacts"},
        )
        status = await _wait(client, created.json()["status_url"])
        deleted = await client.delete(
            f"/experimental/research/v1/research/{status['research_id']}"
        )
        assert deleted.status_code == 202
        assert authority.deletions == 1
        experimental._RUNS.clear()
        assert (await client.get(created.json()["status_url"])).status_code == 410


@pytest.mark.asyncio
async def test_recovered_bytes_match_http_sse_cli_and_mcp(
    authority_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One recovered authority set remains identical on every W6 client surface."""
    app, _, _ = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "recovered-client-parity"},
            json={"objective": "Preserve one result across every client"},
        )
        original_status = await _wait(client, created.json()["status_url"])
        summary_url = original_status["result"]["artifacts"]["summary"]
        original_bytes = (await client.get(summary_url)).content

        # Simulate loss of all process-local route state. Subsequent reads must
        # reconstruct the result from Valkey pointers and PostgreSQL bytes.
        experimental._RUNS.clear()
        recovered_status = (await client.get(created.json()["status_url"])).json()
        manifest = (await client.get(recovered_status["result"]["manifest_url"])).json()
        recovered_bytes = (await client.get(summary_url)).content
        events = await client.get(created.json()["events_url"])

        assert recovered_bytes == original_bytes
        assert (
            manifest["artifact_set_id"] == recovered_status["result"]["artifact_set_id"]
        )
        assert "event: done" in events.text
        assert recovered_status["result"]["artifact_set_id"] in events.text

        summary_id = summary_url.rsplit("/", 1)[-1]

        cli_path = Path("/app/w6-fixtures/groktocrawl")
        if not cli_path.is_file():
            cli_path = Path(__file__).resolve().parents[2] / "groktocrawl"
        cli_ns = runpy.run_path(str(cli_path))
        cli_client = cli_ns["Client"](server="http://test")
        cli_response = httpx.Response(200, content=recovered_bytes)
        monkeypatch.setitem(
            sys.modules,
            "requests",
            types.SimpleNamespace(get=lambda *args, **kwargs: cli_response),
        )
        assert (
            cli_client.experimental_research_artifact_bytes(summary_id)
            == original_bytes
        )

        module_path = (
            Path(__file__).resolve().parents[2] / "mcp-svc/groktocrawl_client.py"
        )
        spec = importlib.util.spec_from_file_location(
            "recovered_mcp_client", module_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mcp_client = module.GroktocrawlClient(base_url="http://test")
        mcp_client._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        mcp_artifact = await mcp_client.experimental_research_artifact(summary_id)
        await mcp_client.close()
        assert base64.b64decode(mcp_artifact["content_base64"]) == original_bytes

        foreign = await client.get(
            created.json()["status_url"], headers={"Authorization": "Bearer foreign"}
        )
        assert foreign.status_code == 404
        deleted = await client.delete(
            f"/experimental/research/v1/research/{recovered_status['research_id']}"
        )
        assert deleted.status_code == 202
        experimental._RUNS.clear()
        assert (await client.get(summary_url)).status_code == 410
        assert (await client.get(created.json()["events_url"])).status_code == 410


@pytest.mark.asyncio
async def test_workspace_recovers_cited_evidence_without_process_memory(authority_app):
    app, _, _ = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "workspace-restart"},
                json={"objective": "Retain citations"},
            )
        ).json()
        status = await _wait(client, created["status_url"])
        selected_url = "/experimental/research/v1/workspace/" + status["run_id"]
        before = (await client.get(selected_url)).json()
        assert before["evidence"]
        cite = before["evidence"][0]
        original = (await client.get(cite["url"])).json()
        experimental._RUNS.clear()
        after = (await client.get(selected_url)).json()
        assert after["revision"] == before["revision"]
        assert after["evidence"] == before["evidence"]
        assert after["evidence_recovery"] == "postgres_artifact_authority"
        recovered = (await client.get(cite["url"])).json()
        assert recovered == original
        await client.delete(
            "/experimental/research/v1/research/" + status["research_id"]
        )
        experimental._RUNS.clear()
        assert (await client.get(cite["url"])).status_code == 410


@pytest.mark.asyncio
async def test_postgres_workspace_outlives_ledger_and_deletes_durably(authority_app):
    app, authority, _ = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        admitted = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "catalog-lifetime"},
                json={"objective": "Independent artifact retention"},
            )
        ).json()
        status = await _wait(client, admitted["status_url"])
        run_id = status["run_id"]
        record = experimental._RUNS[run_id]
        record.durable_ledger.redis.delete(record.durable_ledger._run_key(run_id))
        experimental._RUNS.clear()
        listing = (await client.get("/experimental/research/v1/workspace")).json()
        assert any(item["run_id"] == run_id for item in listing["items"])
        selected = (
            await client.get("/experimental/research/v1/workspace/" + run_id)
        ).json()
        assert selected["lifetime"]["authority"] == "postgres_artifact_authority"
        cite = selected["evidence"][0]
        assert (await client.get(cite["url"])).status_code == 200
        assert (
            await client.get(status["result"]["artifacts"]["summary"])
        ).status_code == 200
        response = await client.post(
            "/experimental/research/v1/workspace/" + run_id + "/actions",
            json={"action": "delete", "expected_revision": selected["revision"]},
        )
        assert response.status_code == 200
        assert authority.deletions == 1
        experimental._RUNS.clear()
        assert (
            await client.get("/experimental/research/v1/workspace/" + run_id)
        ).status_code == 410
        assert (await client.get(cite["url"])).status_code == 410
        assert (await client.get("/experimental/research/v1/workspace")).json()[
            "items"
        ] == []


@pytest.mark.asyncio
async def test_pending_postgres_root_delete_fences_late_fixture_result(
    authority_app, monkeypatch
):
    app, authority, _ = authority_app
    release = asyncio.Event()
    original = experimental.example_journey

    class DelayedJourney:
        def __init__(self, **options):
            self.journey = original(**options)

        async def run(self):
            await release.wait()
            return await self.journey.run()

    monkeypatch.setattr(experimental, "example_journey", DelayedJourney)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        admitted = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "pending-delete"},
                json={"objective": "Do not retain late bytes"},
            )
        ).json()
        await asyncio.sleep(0)
        record = experimental._RUNS[admitted["run_id"]]
        assert record.state in {"accepted", "running"}
        response = await client.delete(
            "/experimental/research/v1/research/" + record.research_id
        )
        assert response.status_code == 202
        release.set()
        await record.task
        assert record.state == "cancelled"
        assert authority.commits == 0
        assert authority.evidence == {}
        assert (
            await client.get("/experimental/research/v1/workspace/" + record.run_id)
        ).status_code == 410


@pytest.mark.asyncio
async def test_failed_root_without_committed_authority_expires(
    authority_app, monkeypatch
):
    app, authority, _ = authority_app

    class FailedJourney:
        def __init__(self, **_):
            pass

        async def run(self):
            raise RuntimeError("Synthetic fixture failure before publication")

    monkeypatch.setattr(experimental, "example_journey", FailedJourney)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        admitted = (
            await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "failed-expired-root"},
                json={"objective": "No artifact commit"},
            )
        ).json()
        status = await _wait(client, admitted["status_url"])
        assert status["state"] == "failed"
        record = experimental._RUNS[admitted["run_id"]]
        assert authority.commits == 0
        record.durable_ledger.redis.delete(
            record.durable_ledger._run_key(record.run_id)
        )
        response = await client.get(
            "/experimental/research/v1/workspace/" + record.run_id
        )
        assert response.status_code == 410
        assert (await client.get("/experimental/research/v1/workspace")).json()[
            "items"
        ] == []


@pytest.mark.asyncio
async def test_postgres_catalog_paging_and_foreign_scope(authority_app):
    app, _, _ = authority_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        admitted_ids = set()
        for i in range(3):
            admitted = (
                await client.post(
                    "/experimental/research/v1/runs",
                    headers={"Idempotency-Key": f"catalog-page-{i}"},
                    json={"objective": f"Catalog page fixture {i}"},
                )
            ).json()
            await _wait(client, admitted["status_url"])
            admitted_ids.add(admitted["run_id"])
        observed = set()
        offset = 0
        while True:
            page = (
                await client.get(
                    "/experimental/research/v1/workspace",
                    params={"limit": 1, "offset": offset},
                )
            ).json()
            assert len(page["items"]) == 1
            observed.add(page["items"][0]["run_id"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert observed == admitted_ids
        foreign = await client.get(
            "/experimental/research/v1/workspace",
            headers={"Authorization": "Bearer foreign-fixture"},
        )
        assert foreign.json()["items"] == []
