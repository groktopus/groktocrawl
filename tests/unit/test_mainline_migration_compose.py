"""Deployment contract checks for the mainline durable/protected Compose topology."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    """Teach PyYAML Compose's value-replacement tag for static inspection."""


def _construct_override(loader: _ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_override)


def _load(path: str) -> dict[str, Any]:
    loader = _ComposeLoader((ROOT / path).read_text(encoding="utf-8"))
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


BASE = _load("docker-compose.yml")
PROTECTED = _load("docker-compose.protected.yml")


def _env(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    values: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        values[key] = value
    return values


def _default(value: str) -> str:
    """Use a Compose interpolation's checked-in default for static checks."""
    if value.startswith("${") and ":-" in value and value.endswith("}"):
        return value.split(":-", 1)[1][:-1]
    return value


def _contract_digest(service: dict[str, Any]) -> str:
    """Hash the parsed complete service block, independent of YAML key order."""
    canonical = json.dumps(service, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_slopsearx_services_are_byte_contract_equivalent_to_mainline_baseline() -> None:
    # Whole-service fingerprints from the known mainline contract at
    # f7340e1d12d96a037461e5de9c969110f0cd8896. This is intentionally a
    # checked-in literal, so CI does not need a remote or a history fetch.
    expected = {
        "slopsearx": "b0133e5d5e646001f0970b4ebc1adb8fa37194c50840555153c23a70516e38c4",
        "slopsearx-mcp": "5cb4dfbda485f0b23d55c12a7f36158191b67aa821bdabc08f8ed56088bc8b22",
    }
    for name, digest in expected.items():
        assert _contract_digest(BASE["services"][name]) == digest
        assert name not in PROTECTED.get("services", {})


def test_durable_research_profile_wires_one_password_secret_and_keeps_default_off() -> (
    None
):
    services = BASE["services"]
    postgres = services["research-postgres"]
    semantic = services["semantic-svc"]
    agent = services["agent-svc"]

    assert postgres["profiles"] == ["durable-research"]
    assert postgres["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/research_postgres_password"
    )
    assert postgres["secrets"] == ["research_postgres_password"]
    assert postgres["volumes"][-1] == (
        "./agent-svc/agent/experimental/migrations:/docker-entrypoint-initdb.d:ro"
    )
    assert "research_postgres_data:/var/lib/postgresql/data" in postgres["volumes"]

    secret = BASE["secrets"]["research_postgres_password"]
    assert secret["file"] == "${DURABLE_RESEARCH_POSTGRES_PASSWORD_FILE:-/dev/null}"
    for service in (semantic, agent):
        assert service["secrets"] == ["research_postgres_password"]
        assert _env(service).get("PGPASSFILE") == "/tmp/postgres.pgpass"
        assert (
            "research-postgres:5432:groktocrawl:groktocrawl:%s" in service["command"][0]
        )

    assert semantic["profiles"] == ["indexing", "durable-research"]
    assert _env(semantic)["PGVECTOR_SHADOW_DSN"].startswith(
        "${PGVECTOR_SHADOW_DSN:-postgresql://groktocrawl@research-postgres:5432/"
    )
    assert _env(agent)["DURABLE_RESEARCH_POSTGRES_DSN"].startswith(
        "${DURABLE_RESEARCH_POSTGRES_DSN:-postgresql://groktocrawl@research-postgres:5432/"
    )
    assert _env(agent)["FEATURE_EXPERIMENTAL_RESEARCH"].endswith("false}")
    assert _env(agent)["FEATURE_EXPERIMENTAL_RESEARCH_DURABLE"].endswith("false}")
    assert agent["depends_on"]["research-postgres"] == {
        "condition": "service_healthy",
        "required": False,
    }


def test_protected_networks_do_not_overlap_and_static_roles_use_expected_segments() -> (
    None
):
    networks = PROTECTED["networks"]
    subnets: dict[str, ipaddress.IPv4Network] = {}
    for name, network in networks.items():
        config = network["ipam"]["config"]
        assert len(config) == 1, f"{name} must have one unambiguous subnet"
        subnets[name] = ipaddress.ip_network(_default(config[0]["subnet"]))

    names = list(subnets)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            assert not subnets[left].overlaps(subnets[right]), (
                f"Docker IPAM subnets overlap: {left} and {right}"
            )

    services = PROTECTED["services"]
    assert services["scraper-svc"]["networks"] == ["capture_source"]
    assert services["scraper-ingress-svc"]["networks"] == ["scraper_private"]
    assert services["agent-svc"]["networks"] == ["default", "scraper_private"]
    assert services["scraper-state-control"]["networks"] == ["control_private"]
    assert services["browser-svc"]["networks"] == ["control_private"]
    assert services["flare-renderer-svc"]["networks"] == {
        "capture_flare": {
            "ipv4_address": "${PROTECTED_CAPTURE_FLARE_RENDERER_IP:-10.240.254.19}"
        }
    }
    assert services["flare-control-svc"]["network_mode"] == "none"
    assert list(services["browser-renderer-svc"]["networks"]) == ["capture_browser"]
    assert list(services["model-egress-svc"]["networks"]) == [
        "model_upstream",
        "model_gateway",
    ]
    assert services["scraper-model-control"]["networks"] == ["model_gateway"]

    for _, service in services.items():
        attached = service.get("networks", {})
        if isinstance(attached, list):
            attached = {name: {} for name in attached}
        for network_name, settings in attached.items():
            address = (
                _default(settings["ipv4_address"])
                if isinstance(settings, dict) and settings.get("ipv4_address")
                else None
            )
            if address:
                assert ipaddress.ip_address(address) in subnets[network_name]

    assert (
        services["scraper-svc"]["environment"]["SCRAPER_CAPTURE_EGRESS_PROXY_URL"]
        == "http://${PROTECTED_CAPTURE_SOURCE_IP:-10.240.254.2}:8080"
    )
    assert (
        services["capture-egress-svc"]["networks"]["capture_source"]["ipv4_address"]
        == "${PROTECTED_CAPTURE_SOURCE_IP:-10.240.254.2}"
    )


def test_protected_roles_have_required_health_dependencies_and_no_worker_model_secret() -> (
    None
):
    services = PROTECTED["services"]
    worker = services["scraper-svc"]
    # The protected entrypoint drops to UID 10001 and then execs its own tini.
    # Leaving the base Compose init enabled would keep an outer root-owned PID 1,
    # which violates the worker's runtime identity check.
    assert worker["init"] is False
    deps = worker["depends_on"]
    for name in (
        "scraper-socket-init",
        "capture-egress-svc",
        "scraper-state-control",
        "scraper-model-control",
        "browser-svc",
    ):
        assert name in deps
    assert deps["capture-egress-svc"]["condition"] == "service_healthy"
    assert deps["scraper-model-control"]["condition"] == "service_healthy"
    assert deps["browser-svc"]["condition"] == "service_healthy"
    assert deps["flare-control-svc"] == {
        "condition": "service_healthy",
        "required": False,
    }

    assert worker["env_file"] == []
    assert not worker.get("secrets")
    assert not any("/run/secrets/" in volume for volume in worker.get("volumes", []))
    worker_env = _env(worker)
    assert not {
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "CAPTCHA_VISION_API_KEY",
    }.intersection(worker_env)
    assert "LLM_API_KEY" in _env(services["scraper-model-control"])
    assert "CAPTCHA_VISION_API_KEY" in _env(services["scraper-model-control"])
    assert (
        services["model-egress-svc"]["networks"]["model_gateway"]["ipv4_address"]
        == "${PROTECTED_MODEL_GATEWAY_IP:-10.240.254.34}"
    )
    assert worker_env["LLM_CONTROL_SOCKET"] == "/run/scraper-llm/control.sock"


def test_protected_capture_publishes_only_ingress_and_preserves_approved_api_ports() -> (
    None
):
    base_services = BASE["services"]
    expected_base_ports = {
        "slopsearx": ["${SLOPSEARX_HOST_PORT:-8081}:8080"],
        "scraper-svc": ["${SCRAPER_HOST_PORT:-8001}:8001"],
        "semantic-svc": ["${SEMANTIC_HOST_PORT:-8003}:8003"],
        "agent-svc": ["${AGENT_PORT:-8080}:8080"],
        "portal-svc": ["${PORTAL_HOST_PORT:-8082}:8081"],
        "mcp-svc": ["${MCP_PORT:-8002}:8002"],
    }
    for name, ports in expected_base_ports.items():
        assert base_services[name]["ports"] == ports

    overlay_services = PROTECTED["services"]
    assert overlay_services["scraper-svc"]["ports"] == []
    assert overlay_services["scraper-ingress-svc"]["ports"] == [
        "${SCRAPER_HOST_PORT:-8001}:8010"
    ]
    for name in (
        "scraper-socket-init",
        "scraper-state-control",
        "capture-egress-svc",
        "browser-renderer-svc",
        "browser-svc",
        "scraper-model-control",
        "model-egress-svc",
        "flare-renderer-svc",
        "flare-control-svc",
    ):
        assert not overlay_services[name].get("ports"), name
