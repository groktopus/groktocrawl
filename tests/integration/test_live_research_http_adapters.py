"""HTTP acceptance tests for durable objective-driven research.

The search, capture, and OpenAI-compatible model endpoints here are local digital
twins. Valkey and PostgreSQL must be the isolated acceptance services configured
by the caller; no paid or public provider is contacted.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from agent.experimental.durable_research import DurableResearchLedger
from agent.routes import experimental_research as experimental
from agent.scraper_client import ScraperClient
from agent.searxng_client import SearXNGClient
from fastapi import FastAPI
from redis import Redis

OBJECTIVES = {
    "What does the Aurora battery pilot report?": (
        "https://research.test/aurora",
        "AURORA DIGITAL TWIN: The pilot measured 18 percent lower charging loss.",
    ),
    "What does the Birch heat-pump trial report?": (
        "https://research.test/birch",
        "BIRCH DIGITAL TWIN: The trial measured 11 percent lower winter electricity use.",
    ),
}


class LocalProviderTwin:
    """Three local HTTP endpoints with request logs and deterministic responses."""

    def __init__(self) -> None:
        self.search_requests: list[dict] = []
        self.capture_requests: list[dict] = []
        self.model_requests: list[dict] = []
        self.callback_requests: list[tuple[bytes, dict]] = []
        self.session_contexts: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _reply(self, status: int, value: dict) -> None:
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] != "/search":
                    self._reply(404, {"error": "unknown path"})
                    return
                from urllib.parse import parse_qs, urlparse

                query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                with owner._lock:
                    owner.search_requests.append({"query": query, "path": self.path})
                selected = next(
                    (
                        item
                        for objective, item in OBJECTIVES.items()
                        if query
                        in (
                            objective,
                            objective + " independent replication",
                            objective + " limitations",
                        )
                    ),
                    None,
                )
                rows = (
                    [
                        {
                            "url": selected[0]
                            + (
                                "/replication"
                                if query.endswith(" independent replication")
                                else "/limits"
                                if query.endswith(" limitations")
                                else ""
                            ),
                            "title": "Local source",
                            "content": "",
                        }
                    ]
                    if selected
                    else []
                )
                self._reply(
                    200,
                    {
                        "query": query,
                        "number_of_results": len(rows),
                        "results": rows,
                        "engines": [{"name": "local-twin", "results": len(rows)}],
                    },
                )

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if self.path == "/callback":
                    with owner._lock:
                        owner.callback_requests.append((raw, dict(self.headers)))
                    self._reply(200, {"received": True})
                    return
                try:
                    payload = json.loads(raw)
                except Exception:
                    self._reply(400, {"error": "invalid JSON"})
                    return
                if self.path == "/scrape":
                    url = payload.get("url", "")
                    with owner._lock:
                        owner.capture_requests.append(payload)
                    body = next(
                        (
                            text
                            for source_url, text in OBJECTIVES.values()
                            if url == source_url
                            or url
                            in (source_url + "/replication", source_url + "/limits")
                        ),
                        None,
                    )
                    if body is None:
                        self._reply(404, {"success": False, "error": "unknown source"})
                        return
                    self._reply(
                        200,
                        {
                            "success": True,
                            "data": {
                                "url": url,
                                "markdown": body,
                                "source": "local-digital-twin",
                            },
                        },
                    )
                    return
                if self.path == "/chat/completions":
                    with owner._lock:
                        owner.model_requests.append(payload)
                    user = payload["messages"][1]["content"]
                    session_query = user.startswith(
                        "Here is the information I gathered:"
                    )
                    request = {} if session_query else json.loads(user)
                    if session_query:
                        with owner._lock:
                            owner.session_contexts.append(user)
                        reference = re.search(r"research_[0-9a-f]{24}", user)
                        assert reference is not None
                        text = next(
                            text for _url, text in OBJECTIVES.values() if text in user
                        )
                        answer = text + " [" + reference.group() + "]"
                    elif request.get("planning"):
                        objective = request["objective"]
                        answer = {
                            "reasoning": "Controlled multiquery acceptance plan",
                            "research_strategy": "deep",
                            "focused_queries": [
                                objective,
                                objective + " independent replication",
                                objective + " limitations",
                            ],
                        }
                    elif "sources" in request and "objective" in request:
                        source = request["sources"][0]
                        line = source["lines"][0]["text"].rstrip("\n")
                        answer = {
                            "schema_version": "research-construction/4",
                            "evidence": [
                                {"source_index": 1, "start_line": 1, "end_line": 1}
                            ],
                            "claims": [
                                {
                                    "text": f"The captured source reports: {line}",
                                    "qualifiers": [
                                        "One source captured by the local acceptance twin"
                                    ],
                                    "temporal_scope": "historical",
                                    "supported_by": [1],
                                    "contradicted_by": [],
                                }
                            ],
                            "answer_claim_index": 1,
                            "answer_status": "answered",
                            "conflicts": [],
                        }
                    elif "checks" in request:
                        answer = {
                            "schema_version": "model-review-batch/1",
                            "decisions": [
                                {
                                    "check_index": item["check_index"],
                                    "outcome": "supported"
                                    if item["check_type"] == "assessment"
                                    else "pass",
                                    "reason": "Local deterministic acceptance response; not an independent quality judgment.",
                                }
                                for item in request["checks"]
                            ],
                        }
                    else:
                        assessment = request.get("check_type") == "assessment"
                        answer = {
                            "schema_version": "model-review-decision/1",
                            "input_digest": request["input_digest"],
                            "outcome": "supported" if assessment else "pass",
                            "reason": "Local deterministic acceptance response; not an independent quality judgment.",
                        }
                    self._reply(
                        200,
                        {
                            "model": "acceptance-local-model",
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {
                                        "content": answer
                                        if isinstance(answer, str)
                                        else json.dumps(answer)
                                    },
                                }
                            ],
                        },
                    )
                    return
                self._reply(404, {"error": "unknown path"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class LocalAdmission:
    """Keep the test focused on the configured HTTP capture adapter."""

    @staticmethod
    def weight_for(_resource_class: str) -> int:
        return 1

    @staticmethod
    async def acquire(_resource_class: str, *, weight: int) -> None:
        assert weight == 1

    @staticmethod
    def release(_resource_class: str, *, weight: int) -> None:
        assert weight == 1


@pytest.fixture
def provider_twin():
    twin = LocalProviderTwin()
    try:
        yield twin
    finally:
        twin.close()


@pytest_asyncio.fixture
async def durable_apps(
    monkeypatch: pytest.MonkeyPatch, provider_twin: LocalProviderTwin
):
    redis_url = os.environ.get("DURABLE_RESEARCH_REDIS_URL") or os.environ.get(
        "VALKEY_URL"
    )
    postgres_dsn = os.environ.get("DURABLE_RESEARCH_POSTGRES_DSN")
    if not redis_url or not postgres_dsn:
        pytest.fail(
            "acceptance requires isolated DURABLE_RESEARCH_REDIS_URL/VALKEY_URL and DURABLE_RESEARCH_POSTGRES_DSN"
        )
    from urllib.parse import urlsplit

    from agent.settings import load_settings

    address = urlsplit(redis_url)
    monkeypatch.setenv("VALKEY_HOST", address.hostname)
    monkeypatch.setenv("VALKEY_PORT", str(address.port or 6379))
    monkeypatch.setenv("VALKEY_DB", address.path.strip("/") or "0")
    load_settings.cache_clear()

    for flag in (
        "EXPERIMENTAL_RESEARCH",
        "EXPERIMENTAL_RESEARCH_RUNS",
        "EXPERIMENTAL_RESEARCH_DURABLE",
        "EXPERIMENTAL_RESEARCH_POSTGRES_ARTIFACTS",
    ):
        monkeypatch.setenv(f"FEATURE_{flag}", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS", "false")

    namespace = f"groktocrawl:test:live-http:{uuid4()}"
    redis = Redis.from_url(redis_url, decode_responses=True)
    original_factory = experimental.DurableResearchLedger
    created_ledgers: list[DurableResearchLedger] = []

    def namespaced_ledger(url: str) -> DurableResearchLedger:
        ledger = original_factory(url, namespace=namespace)
        created_ledgers.append(ledger)
        return ledger

    monkeypatch.setattr(experimental, "DurableResearchLedger", namespaced_ledger)
    for cache in (
        experimental._RUNS,
        experimental._IDEMPOTENCY,
        experimental._DURABLE_LEDGERS,
        experimental._ARTIFACT_AUTHORITIES,
    ):
        cache.clear()

    def make_app() -> FastAPI:
        app = FastAPI()
        app.state.valkey_url = redis_url
        app.state.searxng_url = provider_twin.base_url
        app.state.scraper_url = provider_twin.base_url
        app.state.research_postgres_dsn = postgres_dsn
        app.state.searxng_client = SearXNGClient(provider_twin.base_url, max_searches=5)
        app.state.scraper_client = ScraperClient(
            provider_twin.base_url, admission=LocalAdmission()
        )
        app.state.llm_base_url = provider_twin.base_url
        app.state.llm_api_key = "local-acceptance-only"
        app.state.llm_model = "acceptance-local-model"
        app.include_router(experimental.router)
        from agent.routes.followup import router as followup_router
        from agent.routes.session import router as session_router

        app.include_router(followup_router)
        app.include_router(session_router)
        return app

    apps = [make_app()]
    cleanup_research_ids: list[str] = []
    cleanup_clients = []
    try:
        yield (
            apps,
            namespace,
            redis,
            created_ledgers,
            make_app,
            cleanup_research_ids,
            cleanup_clients,
        )
    finally:
        if cleanup_research_ids and apps:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=apps[-1]),
                base_url="http://acceptance",
                headers={"Authorization": "Bearer live-http-acceptance-owner"},
            ) as cleanup_client:
                for research_id in cleanup_research_ids:
                    await cleanup_client.delete(
                        f"/experimental/research/v1/research/{research_id}"
                    )
        for app in apps:
            for name in ("searxng_client", "scraper_client"):
                client = getattr(app.state, name, None)
                if client is not None:
                    await client.close()
        for client in cleanup_clients:
            await client.close()
        for ledger in created_ledgers:
            ledger.redis.close()
        for cache in (
            experimental._RUNS,
            experimental._IDEMPOTENCY,
            experimental._DURABLE_LEDGERS,
            experimental._ARTIFACT_AUTHORITIES,
        ):
            cache.clear()
        prefix = f"{namespace}:"
        for key in redis.scan_iter(match=f"{prefix}*"):
            redis.delete(key)
        redis.close()
        load_settings.cache_clear()


async def _terminal(client: httpx.AsyncClient, url: str) -> dict:
    for _ in range(600):
        response = await client.get(url)
        assert response.status_code == 200, response.text
        status = response.json()
        if status["state"] in {"completed", "failed", "cancelled"}:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError("live research run did not reach a terminal state")


@pytest.mark.asyncio
async def test_two_objectives_use_http_adapters_and_recover_actual_bytes_through_mcp(
    durable_apps, provider_twin: LocalProviderTwin, monkeypatch: pytest.MonkeyPatch
):
    apps, _, _, _, make_app, cleanup_research_ids, cleanup_clients = durable_apps
    app = apps[0]
    owner = {"Authorization": "Bearer live-http-acceptance-owner"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://acceptance",
        headers=owner,
    ) as client:
        first_objective = next(iter(OBJECTIVES))
        first = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "aurora-objective"},
            json={"objective": first_objective},
        )
        assert first.status_code == 202, first.text
        first_admission = first.json()
        cleanup_research_ids.append(first_admission["research_id"])
        duplicate = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "aurora-objective"},
            json={"objective": first_objective},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["run_id"] == first_admission["run_id"]
        first_status = await _terminal(client, first_admission["status_url"])
        assert first_status["state"] == "completed", first_status

        mcp_dir = str(Path(__file__).resolve().parents[2] / "mcp-svc")
        if mcp_dir not in sys.path:
            sys.path.insert(0, mcp_dir)
        mcp_server = importlib.import_module("mcp_server")
        from groktocrawl_client import GroktocrawlClient

        mcp_client = GroktocrawlClient(base_url="http://acceptance")
        cleanup_clients.append(mcp_client)
        mcp_client._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://acceptance",
            headers=owner,
        )
        prior_client = mcp_server._client
        monkeypatch.setattr(mcp_server, "_client", mcp_client)
        second_objective = tuple(OBJECTIVES)[1]
        mcp_response, _structured = await mcp_server.mcp.call_tool(
            "research_create",
            {"objective": second_objective, "idempotency_key": "birch-objective"},
        )
        second_admission = json.loads(mcp_response[0].text)
        cleanup_research_ids.append(second_admission["research_id"])
        second_status = await _terminal(client, second_admission["status_url"])
        assert second_status["state"] == "completed", second_status
        assert second_status["result"]["research_id"] == second_admission["research_id"]
        mcp_status_response, _structured = await mcp_server.mcp.call_tool(
            "research_status", {"run_id": second_admission["run_id"]}
        )
        assert json.loads(mcp_status_response[0].text)["state"] == "completed"

        assert [item["query"] for item in provider_twin.search_requests] == [
            first_objective,
            first_objective + " independent replication",
            first_objective + " limitations",
            second_objective,
            second_objective + " independent replication",
            second_objective + " limitations",
        ]
        assert len(provider_twin.capture_requests) == 6
        assert all(
            item.get("lightweight_only") is True
            for item in provider_twin.capture_requests
        )
        assert len(provider_twin.model_requests) == 6
        assert all(
            item["model"] == "acceptance-local-model"
            for item in provider_twin.model_requests
        )
        model_inputs = [
            json.loads(item["messages"][1]["content"])
            for item in provider_twin.model_requests
        ]
        for objective, (_url, source_body) in OBJECTIVES.items():
            construction = next(
                item
                for item in model_inputs
                if item.get("objective") == objective and "sources" in item
            )
            assert source_body in "".join(
                line["text"]
                for source in construction["sources"]
                for line in source["lines"]
            )

        # Drop all route process state, create a distinct FastAPI app, and recover
        # source bytes/artifacts through the durable ledger + PostgreSQL authority.
        for current_app in apps:
            for name in ("searxng_client", "scraper_client"):
                await getattr(current_app.state, name).close()
        experimental._RUNS.clear()
        experimental._IDEMPOTENCY.clear()
        experimental._DURABLE_LEDGERS.clear()
        experimental._ARTIFACT_AUTHORITIES.clear()
        recovered_app = make_app()
        apps.append(recovered_app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=recovered_app),
            base_url="http://acceptance",
            headers=owner,
        ) as recovered:
            observations = []
            for admission, objective, expected_url, expected_body, objective_marker in (
                (
                    first_admission,
                    first_objective,
                    *OBJECTIVES[first_objective],
                    "AURORA",
                ),
                (
                    second_admission,
                    second_objective,
                    *OBJECTIVES[second_objective],
                    "BIRCH",
                ),
            ):
                workspace = await recovered.get(
                    f"/experimental/research/v1/workspace/{admission['run_id']}"
                )
                assert workspace.status_code == 200, workspace.text
                document = workspace.json()
                assert document["state"] == "completed"
                assert document["objective"] == objective
                evidence_link = next(
                    item
                    for item in document["evidence"]
                    if item.get("source", {}).get("canonical_url") == expected_url
                )
                evidence = await recovered.get(evidence_link["url"])
                assert evidence.status_code == 200, evidence.text
                assert evidence.json()["body"].encode() == expected_body.encode()
                assert (
                    evidence.json()["content_digest"]
                    == hashlib.sha256(expected_body.encode()).hexdigest()
                )
                summary_url = document["actions"]["render_summary"]["url"]
                summary = await recovered.get(summary_url)
                assert summary.status_code == 200
                assert objective_marker in summary.text
                assert expected_body in summary.text
                observations.append((evidence.json()["body"], summary.text))

            assert observations[0][0] != observations[1][0]
            assert observations[0][1] != observations[1][1]
        monkeypatch.setattr(mcp_server, "_client", prior_client)


@pytest.mark.asyncio
async def test_live_search_failure_is_not_reported_as_success(
    durable_apps, provider_twin
):
    apps, _, _, _, _, cleanup_research_ids, _cleanup_clients = durable_apps
    app = apps[0]
    objective = "What does the unavailable local source establish?"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance"
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "empty-search"},
            json={"objective": objective},
        )
        assert created.status_code == 202, created.text
        cleanup_research_ids.append(created.json()["research_id"])
        status = await _terminal(client, created.json()["status_url"])
        assert status["state"] == "failed"
        assert status["error"]["code"] in {"research_run_failed", "outcome_unknown"}
        assert "result" not in status or status["result"] is None
        assert any(item["query"] == objective for item in provider_twin.search_requests)
        assert not provider_twin.capture_requests
        assert len(provider_twin.model_requests) == 1
        assert (
            json.loads(provider_twin.model_requests[0]["messages"][1]["content"])[
                "planning"
            ]
            is True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("successful", [True, False])
async def test_real_http_terminal_callback_has_stable_identity_and_status(
    durable_apps, provider_twin, monkeypatch, successful
):
    from agent import webhook
    from agent.experimental.research_notifications import deliver_notifications

    apps, _, _, ledgers, _, cleanup_research_ids, _ = durable_apps
    destination = provider_twin.base_url + "/callback"

    def local_test_destination(url):
        # This test-only allowlist admits exactly the controlled loopback twin.
        # The production destination policy is separately tested unchanged.
        assert url == destination

    monkeypatch.setattr(
        experimental, "validate_outbound_webhook_url", local_test_destination
    )
    monkeypatch.setattr(
        webhook, "validate_outbound_webhook_url", local_test_destination
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps[0]),
        base_url="http://acceptance",
        headers={"Authorization": "Bearer live-http-acceptance-owner"},
    ) as client:
        created = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "callback-" + str(successful)},
            json={
                "objective": next(iter(OBJECTIVES))
                if successful
                else "Unavailable callback test evidence",
                "webhook": destination,
            },
        )
        assert created.status_code == 202, created.text
        admission = created.json()
        cleanup_research_ids.append(admission["research_id"])
        status = await _terminal(client, admission["status_url"])
        state = "completed" if successful else "failed"
        assert status["state"] == state, status
        assert status["webhook"] == {"state": "pending", "attempts": 0}
        await deliver_notifications(ledgers[0])
        assert len(provider_twin.callback_requests) == 1
        body = json.loads(provider_twin.callback_requests[0][0])
        assert body["type"] == "research." + state
        assert body["webhookId"] == body["id"] == admission["run_id"]
        assert body["data"]["research_id"] == admission["research_id"]
        assert body["success"] is successful
        status = (await client.get(admission["status_url"])).json()
        assert status["webhook"] == {"state": "delivered", "attempts": 1}
        assert destination not in json.dumps(status)


@pytest.mark.asyncio
async def test_as_of_never_turns_a_new_capture_into_historical_evidence(durable_apps):
    apps, _, _, _, _, cleanup_research_ids, _ = durable_apps
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps[0]),
        base_url="http://acceptance",
        headers={"Authorization": "Bearer live-http-acceptance-owner"},
    ) as client:
        response = await client.post(
            "/experimental/research/v1/runs",
            headers={"Idempotency-Key": "past-evaluation"},
            json={"objective": next(iter(OBJECTIVES)), "as_of": "2026-01-01T00:00:00Z"},
        )
        assert response.status_code == 202, response.text
        cleanup_research_ids.append(response.json()["research_id"])
        status = await _terminal(client, response.json()["status_url"])
        assert status["state"] == "failed", status
        assert status["error"]["code"] == "historical_validity_unestablished"
        assert status["result"] is None


@pytest.mark.asyncio
async def test_live_retained_source_drives_independent_followup_and_owned_session(
    durable_apps, provider_twin
):
    from agent.session_store import SessionStore

    apps, _, _, ledgers, make_app, cleanup_roots, _ = durable_apps
    owner = {"Authorization": "Bearer live-http-acceptance-owner"}
    session_id = None
    store = SessionStore(
        redis_url=os.environ.get("DURABLE_RESEARCH_REDIS_URL")
        or os.environ["VALKEY_URL"]
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[0]),
            base_url="http://acceptance",
            headers=owner,
        ) as client:
            created = await client.post(
                "/experimental/research/v1/runs",
                headers={"Idempotency-Key": "followup-original"},
                json={"objective": next(iter(OBJECTIVES))},
            )
            assert created.status_code == 202, created.text
            original = created.json()
            cleanup_roots.append(original["research_id"])
            assert (await _terminal(client, original["status_url"]))[
                "state"
            ] == "completed"
            workspace = (
                await client.get(
                    f"/experimental/research/v1/workspace/{original['run_id']}"
                )
            ).json()
            original_url, original_body = next(iter(OBJECTIVES.values()))
            source = next(
                item
                for item in workspace["evidence"]
                if item.get("source", {}).get("canonical_url") == original_url
            )
            retained = (await client.get(source["url"])).json()
            assert retained["body"] == original_body
            selection = {
                "kind": "root",
                "container_id": original["research_id"],
                "ref_id": source["snapshot_id"],
                "subject": "Aurora battery pilot",
            }
            followup_body = {"wording": tuple(OBJECTIVES)[1], "selected": [selection]}
            followup = await client.post(
                "/v2/followup/execute",
                headers={"Idempotency-Key": "followup-root-selection"},
                json=followup_body,
            )
            assert followup.status_code == 202, followup.text
            admission = followup.json()
            cleanup_roots.append(admission["research_id"])
            assert admission["run_id"] != original["run_id"]
            assert admission["research_id"] != original["research_id"]
            assert (await _terminal(client, admission["status_url"]))[
                "state"
            ] == "completed"
            before = (
                len(provider_twin.search_requests),
                len(provider_twin.capture_requests),
                len(provider_twin.model_requests),
            )
            duplicate = await client.post(
                "/v2/followup/execute",
                headers={"Idempotency-Key": "followup-root-selection"},
                json=followup_body,
            )
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()["run_id"] == admission["run_id"]
            assert before == (
                len(provider_twin.search_requests),
                len(provider_twin.capture_requests),
                len(provider_twin.model_requests),
            )
            constructed = next(
                json.loads(item["messages"][1]["content"])
                for item in provider_twin.model_requests
                if json.loads(item["messages"][1]["content"]).get("objective")
                == tuple(OBJECTIVES)[1]
                and "sources" in json.loads(item["messages"][1]["content"])
            )
            text = "".join(
                line["text"]
                for item in constructed["sources"]
                for line in item["lines"]
            )
            assert original_body in text
            assert OBJECTIVES[tuple(OBJECTIVES)[1]][1] in text
            durable = ledgers[0].get(admission["run_id"])
            assert (
                durable.payload["selected_sources"][0]["origin_id"]
                == f"root:{original['research_id']}:{source['snapshot_id']}"
            )
            assert (
                durable.payload["selected_sources"][0]["lineage_id"]
                == retained["content_digest"]
            )
            assert (
                durable.payload["selected_sources"][0]["retrieved_at"]
                == retained["retrieved_at"]
            )

            session = await client.post("/v2/session/create", json={"ttl": 120})
            assert session.status_code == 200, session.text
            session_id = session.json()["sessionId"]
            attached = await client.post(
                f"/experimental/research/v1/sessions/{session_id}/attachments",
                json={"run_id": original["run_id"], "expected_revision": 0},
            )
            assert attached.status_code == 200, attached.text
            stored_refs = {
                ref: await store.aget_ref(session_id, ref)
                for ref in attached.json()["ref_ids"]
            }
            ref_id = next(
                ref
                for ref, material in stored_refs.items()
                if material["url"] == original_url
            )
            saved = await store.aget_ref(session_id, ref_id)
            assert "markdown" not in saved
            assert saved["content_digest"] == retained["content_digest"]
            step = await client.post(
                f"/v2/session/{session_id}/step",
                json={
                    "action": "query",
                    "params": {
                        "question": "What does the Aurora battery pilot report?",
                        "ref_ids": [ref_id],
                    },
                },
            )
            assert step.status_code == 200, step.text
            assert original_body in provider_twin.session_contexts[-1]
            citation = step.json()["result"]["citations"][0]
            assert citation["ref_id"] == ref_id
            assert citation["content_digest"] == retained["content_digest"]
            session_selection = {
                "kind": "session",
                "container_id": session_id,
                "ref_id": ref_id,
                "subject": "Aurora battery pilot",
            }
            linked = await client.post(
                "/v2/followup/execute",
                headers={"Idempotency-Key": "followup-linked-session"},
                json={"wording": tuple(OBJECTIVES)[1], "selected": [session_selection]},
            )
            assert linked.status_code == 202, linked.text
            cleanup_roots.append(linked.json()["research_id"])
            linked_status = await _terminal(client, linked.json()["status_url"])
            assert linked_status["state"] == "completed", linked_status

        experimental._RUNS.clear()
        experimental._IDEMPOTENCY.clear()
        experimental._DURABLE_LEDGERS.clear()
        experimental._ARTIFACT_AUTHORITIES.clear()
        recovered_app = make_app()
        apps.append(recovered_app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=recovered_app),
            base_url="http://acceptance",
            headers=owner,
        ) as client:
            followup_workspace = (
                await client.get(
                    f"/experimental/research/v1/workspace/{admission['run_id']}"
                )
            ).json()
            inherited = []
            for item in followup_workspace["evidence"]:
                evidence = (await client.get(item["url"])).json()
                if (
                    evidence["origin_id"]
                    == f"root:{original['research_id']}:{source['snapshot_id']}"
                ):
                    inherited.append(evidence)
            assert len(inherited) == 1
            assert inherited[0]["body"] == original_body
            assert inherited[0]["lineage_id"] == retained["content_digest"]
            assert inherited[0]["url"] == original_url
            assert inherited[0]["retrieved_at"] == retained["retrieved_at"]
            exact = await client.get(f"/v2/session/{session_id}/evidence/{ref_id}")
            assert exact.status_code == 200, exact.text
            assert (
                "".join(span["quote"] for span in exact.json()["spans"])
                == original_body
            )
            deleted = await client.delete(
                f"/experimental/research/v1/research/{original['research_id']}"
            )
            assert deleted.status_code == 202, deleted.text
            before = (
                len(provider_twin.search_requests),
                len(provider_twin.capture_requests),
                len(provider_twin.model_requests),
            )
            refused = await client.post(
                "/v2/followup/execute",
                headers={"Idempotency-Key": "followup-after-root-deletion"},
                json={"wording": tuple(OBJECTIVES)[1], "selected": [session_selection]},
            )
            assert refused.status_code == 410, refused.text
            assert before == (
                len(provider_twin.search_requests),
                len(provider_twin.capture_requests),
                len(provider_twin.model_requests),
            )
    finally:
        if session_id is not None:
            await store.adelete(session_id)
        store.redis.close()
