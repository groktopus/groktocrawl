"""The experimental capability adapter advertises only implemented behavior."""

from agent.routes.experimental_research import (
    capability_document,
    router,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capabilities_are_hidden_by_default(monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_EXPERIMENTAL_RESEARCH", raising=False)
    response = _client().get("/experimental/research/v1/capabilities")
    assert response.status_code == 404


def test_capabilities_report_contract_only_stage(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    response = _client().get("/experimental/research/v1/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload == capability_document()
    assert payload["implementation_stage"] == "contract_and_golden_traces"
    assert payload["recovery_mode"] == "not_advertised"
    assert payload["operations"]["runs"]["available"] is False


def test_capabilities_advertise_fixture_run_adapter_only_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    payload = _client().get("/experimental/research/v1/capabilities").json()
    assert payload["implementation_stage"] == "fixture_run_adapter"
    assert payload["recovery_mode"] == "process_local"
    assert payload["operations"]["runs"]["available"] is True
    assert payload["operations"]["sessions"]["available"] is True
    assert payload["operations"]["sessions"]["mode"] == "attachment_only"


def test_capabilities_advertise_valkey_fenced_recovery_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_DURABLE", "true")
    payload = _client().get("/experimental/research/v1/capabilities").json()
    assert payload["implementation_stage"] == "durable_fixture_run_adapter"
    assert payload["recovery_mode"] == "valkey_fenced"


def test_capabilities_advertise_postgres_artifact_authority(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_RUNS", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_DURABLE", "true")
    monkeypatch.setenv("FEATURE_EXPERIMENTAL_RESEARCH_POSTGRES_ARTIFACTS", "true")
    payload = _client().get("/experimental/research/v1/capabilities").json()
    assert payload["implementation_stage"] == "postgres_artifact_authority_adapter"
    assert payload["recovery_mode"] == "postgres_artifacts_valkey_fenced"
