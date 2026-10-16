"""Deployment contracts for the constrained-host resource envelope."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_integration_workflow_enables_indexing_profile():
    workflow_path = ROOT / ".github/workflows/docker.yml"
    workflow = workflow_path.read_text()
    # The stack startup still activates the indexing profile (semantic-svc/qdrant),
    # but no longer redundantly rebuilds the full stack on top of build-and-push
    # (#494): images are provisioned separately (pulled on push, service-local
    # build on PR) before an `up -d` with no `--build`.
    assert (
        "docker compose --profile indexing --profile durable-research up -d" in workflow
    )
    assert (
        "docker compose --profile indexing --profile durable-research up --build -d"
        not in workflow
    )
    assert "docker compose exec -T agent-svc env \\" in workflow
    assert "docker compose exec -T \\\\n            -e" not in workflow


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

    semantic_dependencies = compose["services"]["semantic-svc"]["depends_on"]
    assert semantic_dependencies["qdrant"]["condition"] == "service_healthy"
    qdrant_healthcheck = compose["services"]["qdrant"]["healthcheck"]["test"]
    assert qdrant_healthcheck[:3] == ["CMD", "bash", "-c"]
    assert "/readyz" in qdrant_healthcheck[-1]
    assert "$${status}" in qdrant_healthcheck[-1]

    agent_dependencies = compose["services"]["agent-svc"].get("depends_on", {})
    assert "semantic-svc" not in agent_dependencies

    for service_name in ("scraper-svc", "browser-svc", "flare-solverr"):
        service = compose["services"][service_name]
        assert service["mem_limit"]
        assert service["cpus"]
        assert service["pids_limit"] > 0
