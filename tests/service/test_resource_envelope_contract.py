"""Deployment contracts for the constrained-host resource envelope."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_integration_workflow_enables_indexing_profile():
    workflow = (ROOT / ".github/workflows/docker.yml").read_text()
    assert "docker compose --profile indexing up --build -d" in workflow


def _services_for_profiles(compose: dict, profiles: set[str]) -> set[str]:
    """Resolve the services Compose activates for the selected profiles."""
    return {
        name
        for name, service in compose["services"].items()
        if not service.get("profiles") or profiles.intersection(service["profiles"])
    }


def test_constrained_host_compose_contract():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    default_services = _services_for_profiles(compose, set())
    indexing_services = _services_for_profiles(compose, {"indexing"})

    assert {"semantic-svc", "qdrant"}.isdisjoint(default_services)
    assert {"semantic-svc", "qdrant"}.issubset(indexing_services)

    agent_dependencies = compose["services"]["agent-svc"].get("depends_on", {})
    assert "semantic-svc" not in agent_dependencies

    for service_name in ("scraper-svc", "browser-svc", "flare-solverr"):
        service = compose["services"][service_name]
        assert service["mem_limit"]
        assert service["cpus"]
        assert service["pids_limit"] > 0
