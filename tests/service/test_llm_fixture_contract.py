"""HTTP contract tests for the versioned LLM provider twin (issue #569)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from llm_svc.app import SCHEMA_VERSION, create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_default_scenario_preserves_existing_completion(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fixture-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == (
        "Synthesized answer from provided context."
    )
    diagnostic = client.get("/diagnostics").json()
    assert diagnostic["schema_version"] == SCHEMA_VERSION
    assert diagnostic["entries"][-1]["scenario"] == "default"
    assert "hi" not in str(diagnostic)


def test_streaming_normalizes_chunks_and_terminates_with_done(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/chat/completions?scenario=streaming&scenario_version=v1&chunks=4",
        json={
            "model": "fixture-model",
            "stream": True,
            "messages": [{"role": "user", "content": "stream"}],
        },
    )
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    content = "".join(
        json.loads(line[6:])["choices"][0]["delta"].get("content", "")
        for line in lines[:-1]
    )
    assert content == "Synthesized answer from provided context."


@pytest.mark.parametrize("scenario", ["stream-malformed", "stream-truncated"])
def test_stream_failure_variants_are_transport_visible(
    client: TestClient, scenario: str
) -> None:
    response = client.post(
        f"/v1/chat/completions?scenario={scenario}",
        json={
            "model": "fixture-model",
            "stream": True,
            "messages": [{"role": "user", "content": "stream"}],
        },
    )
    lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert any(line == "data: {not-json" for line in lines) is (
        scenario == "stream-malformed"
    )
    assert (lines[-1] == "data: [DONE]") is (scenario == "stream-malformed")


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        ("rate-limit", 429),
        ("server-error", 503),
        ("malformed-json", 200),
        ("schema-invalid", 200),
        ("truncated", 200),
        ("empty", 200),
        ("refusal", 200),
        ("contradictory", 200),
        ("citation-free", 200),
    ],
)
def test_scenarios_have_stable_diagnostics(
    client: TestClient, scenario: str, status: int
) -> None:
    response = client.post(
        f"/v1/chat/completions?scenario={scenario}&scenario_version={SCHEMA_VERSION}",
        json={
            "model": "scenario-model",
            "messages": [{"role": "user", "content": "secret prompt"}],
        },
    )
    assert response.status_code == status
    assert client.get("/diagnostics").json()["entries"][-1]["scenario"] == scenario
    assert "secret prompt" not in client.get("/diagnostics").text


def test_model_and_request_parameters_are_echoed_without_prompt_retention(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/chat/completions?scenario=echo",
        json={
            "model": "override-model",
            "temperature": 0.7,
            "max_tokens": 17,
            "messages": [{"role": "user", "content": "private"}],
        },
    )
    assert response.json()["model"] == "override-model"
    assert response.json()["choices"][0]["message"]["content"] == (
        '{"model": "override-model", "temperature": 0.7, "max_tokens": 17}'
    )
    assert "private" not in client.get("/diagnostics").text


def test_diagnostics_are_versioned_filterable_bounded_and_resettable(
    client: TestClient,
) -> None:
    client.post(
        "/v1/scenarios/echo/chat/completions?run_id=run-a",
        json={"model": "fixture", "messages": []},
    )
    client.post(
        "/v1/scenarios/echo/chat/completions?run_id=run-b",
        json={"model": "fixture", "messages": []},
    )
    diagnostics = client.get("/diagnostics?run_id=run-a").json()
    assert diagnostics["fixture_version"] != diagnostics["schema_version"]
    assert {entry["run_id"] for entry in diagnostics["entries"]} == {"run-a"}
    assert client.post("/diagnostics/reset?run_id=run-a").json() == {"status": "ok"}
    assert client.get("/diagnostics?run_id=run-a").json()["entries"] == []


@pytest.mark.asyncio
async def test_timeout_scenario_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_svc.app as llm_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    delay_started = asyncio.Event()
    real_sleep = asyncio.sleep

    async def observable_sleep(delay: float) -> None:
        delay_started.set()
        await real_sleep(delay)

    monkeypatch.setattr(
        llm_app,
        "asyncio",
        SimpleNamespace(sleep=observable_sleep, CancelledError=asyncio.CancelledError),
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://fixture"
    ) as client:
        request = asyncio.create_task(
            client.post(
                "/v1/chat/completions?scenario=timeout",
                json={"model": "fixture", "messages": []},
            )
        )
        await asyncio.wait_for(delay_started.wait(), timeout=1.0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        diagnostics = (await client.get("/diagnostics")).json()
        assert diagnostics["entries"][-1]["classification"] == "cancelled"
