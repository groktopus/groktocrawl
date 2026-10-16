"""Validate the experimental CI execution boundary and required-check outcomes."""

import ipaddress
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from scripts.classify_ci_changes import requires_full_runtime, requires_twin_contracts
from tests.integration.host_bridge_sentinel import validate_capture_address_plan

ROOT = Path(__file__).parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/runtime.yml").read_text())


def test_runtime_workflow_has_no_publishing_or_privileged_execution():
    triggers = WORKFLOW.get("on", WORKFLOW.get(True))
    assert set(triggers) == {"workflow_call"}
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert "secrets." not in repr(WORKFLOW)
    assert set(WORKFLOW["jobs"]) == {
        "changes",
        "twin-contracts",
        "integration-tests",
        "research-storage",
        "protected-browser-profile",
        "protected-flare-profile",
        "protected-capture-composition",
        "runtime-gate",
    }
    for job in WORKFLOW["jobs"].values():
        assert job["runs-on"] == "ubuntu-latest"
        assert 0 < job["timeout-minutes"] <= 60
        assert job.get("permissions", WORKFLOW["permissions"]) == {"contents": "read"}
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
            assert "docker/login-action" not in step.get("uses", "")
            assert "docker push" not in step.get("run", "")


def test_stack_is_built_locally_with_fixture_search_and_owned_volumes():
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    override = yaml.safe_load((ROOT / "docker-compose.ci.yml").read_text())
    for name, service in base["services"].items():
        if "build" in service:
            assert override["services"][name]["image"].startswith("groktocrawl-x-ci/")
    search = override["services"]["slopsearx"]
    assert search["build"] == base["services"]["slopsearx-fixture"]["build"]
    assert search["healthcheck"] == base["services"]["slopsearx-fixture"]["healthcheck"]
    assert override["volumes"]["hf-cache"]["external"] is False
    job = WORKFLOW["jobs"]["integration-tests"]
    assert job["env"]["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.ci.yml"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert (
        "--profile indexing --profile fixture --profile durable-research build"
        in commands
    )
    assert "up -d --no-build --pull never" in commands
    assert "scripts/ci_public_image_prefetch.py" in commands
    assert "--group runtime" in commands
    assert "--profile indexing" in commands
    assert "--profile fixture" in commands
    assert "--profile durable-research" in commands
    assert "DURABLE_RESEARCH_POSTGRES_DSN=" in commands
    assert "PGPASSFILE=/tmp/postgres.pgpass" in commands
    assert "SEARXNG_URL=http://slopsearx-fixture:8080" in commands
    assert "SEARCH_BASE_URL=http://slopsearx-fixture:8080" in commands
    assert "LLM_BASE_URL=http://llm-svc:8011/v1?run_id=" in commands
    assert "docker rm -f" not in commands
    assert "down --volumes --remove-orphans" in commands
    for filename in (
        "compose.experimental-candidate.yml",
        "compose.protected-flare-ci.yml",
        "compose.protected-capture-ci.yml",
        "compose.protected-capture-lab.yml",
    ):
        assert f'docker cp {filename} "$svc":/app/{filename}' in commands


def test_standard_integration_provisions_durable_database():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())
    jobs = workflow["jobs"].values()
    job = next(job for job in jobs if job.get("name") == "Integration Tests")
    steps = {step.get("name"): step for step in job["steps"]}
    assert "--profile durable-research up -d" in steps["Start the Docker stack"]["run"]
    assert "--profile durable-research down" in steps["Tear down stack"]["run"]
    assert "/app/tests/integration/" in steps["Run integration tests"]["run"]
    assert job["env"]["COMPOSE_PROJECT_NAME"].endswith(
        "${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert "token_urlsafe(32)" in steps["Set up .env"]["run"]
    assert "umask 077" in steps["Set up .env"]["run"]
    assert (
        "DURABLE_RESEARCH_POSTGRES_PASSWORD_FILE=$research_password_file"
        in steps["Set up .env"]["run"]
    )
    assert "--volumes" in steps["Tear down stack"]["run"]
    assert (
        "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME"
        in steps["Force-remove all stale project containers"]["run"]
    )
    assert "rm -f" in steps["Tear down stack"]["run"]


def test_protected_browser_profile_is_required_by_runtime_gate():
    profile = WORKFLOW["jobs"]["protected-browser-profile"]
    gate = WORKFLOW["jobs"]["runtime-gate"]
    assert profile["needs"] == "changes"
    assert "needs.changes.outputs.requires_full_runtime == 'true'" in profile["if"]
    assert "protected-browser-profile" in gate["needs"]
    assert "BROWSER_PROFILE_RESULT" in gate["steps"][0]["env"]
    assert 'test "$BROWSER_PROFILE_RESULT" = success' in gate["steps"][0]["run"]
    steps = profile["steps"]
    controller_probe = next(
        step
        for step in steps
        if step.get("name")
        == "Verify renderer network boundary from the controller API"
    )
    renderer_probe = next(
        step
        for step in steps
        if step.get("name")
        == "Verify renderer firewall and privilege drop in its namespace"
    )
    assert (
        "docker compose exec -T --user 10002:20000 candidate-browser-controller"
        in controller_probe["run"]
    )
    assert "browser_capture_boundary_probe.py" in controller_probe["run"]
    assert "docker compose exec -T candidate-browser-renderer" in renderer_probe["run"]
    assert "browser_renderer_isolation_probe.py" in renderer_probe["run"]


def test_protected_flare_profile_is_required_by_runtime_gate():
    profile = WORKFLOW["jobs"]["protected-flare-profile"]
    gate = WORKFLOW["jobs"]["runtime-gate"]
    assert profile["needs"] == "changes"
    assert "needs.changes.outputs.requires_full_runtime == 'true'" in profile["if"]
    assert "protected-flare-profile" in gate["needs"]
    assert "PROTECTED_FLARE_RESULT" in gate["steps"][0]["env"]
    assert 'test "$PROTECTED_FLARE_RESULT" = success' in gate["steps"][0]["run"]
    assert "protected-capture-composition" in gate["needs"]
    assert "PROTECTED_CAPTURE_RESULT" in gate["steps"][0]["env"]
    assert 'test "$PROTECTED_CAPTURE_RESULT" = success' in gate["steps"][0]["run"]
    steps = profile["steps"]
    probe = next(
        step
        for step in steps
        if step.get("name", "").startswith("Exercise upstream Flare")
    )
    assert "tests/integration/protected_flare_api_probe.py" in probe["run"]
    assert (
        "docker compose --profile protected-flare exec -T --user 10001:20000 candidate-scraper"
        in probe["run"]
    )
    assert any("candidate-flare-test-origin" in step.get("run", "") for step in steps)
    init = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())[
        "services"
    ]
    for service_name in (
        "candidate-scraper-socket-init",
        "candidate-flare-control-socket-init",
    ):
        socket_init = init[service_name]
        assert socket_init["user"] == "0:0"
        assert socket_init["cap_drop"] == ["ALL"]
        assert set(socket_init["cap_add"]) == {"CHOWN", "FOWNER", "FSETID"}
        assert "stat -c '%u:%g:%a'" in " ".join(socket_init["command"])
    diagnostic = next(
        step
        for step in steps
        if step.get("name") == "Collect bounded protected Flare startup diagnostics"
    )
    assert "candidate-scraper-socket-init" in diagnostic["run"]
    assert "candidate-flare-control-socket-init" in diagnostic["run"]


def test_capture_composition_runs_real_ingress_scrape_meta_and_network_probes():
    job = WORKFLOW["jobs"]["protected-capture-composition"]
    assert job["needs"] == "changes"
    assert "requires_full_runtime == 'true'" in job["if"]
    steps = job["steps"]
    start = next(
        step
        for step in steps
        if step.get("name") == "Build and start the isolated service composition"
    )
    assert "candidate-capture-peer-sentinel" in start["run"]
    assert "candidate-host-bridge-sentinel" in start["run"]
    positive_control = next(
        step
        for step in steps
        if step.get("name") == "Verify private sentinel response positive control"
    )
    assert "positive-control/" in positive_control["run"]
    assert "Access-Control-Allow-Origin" in positive_control["run"]
    baseline = next(
        step
        for step in steps
        if step.get("name") == "Snapshot append-only sentinel evidence before controls"
    )
    assert "sentinel_ledger.py snapshot" in baseline["run"]
    assert "SENTINEL_LEDGER_BASELINE" in baseline["run"]
    host_positive = next(
        step
        for step in steps
        if step.get("name")
        == "Verify host bridge sentinel positive viability before denial probes"
    )
    assert "candidate-capture-egress" in host_positive["run"]
    assert "host_bridge_sentinel.py" in host_positive["run"]
    assert "HOST_BRIDGE_POSITIVE_NONCE" in host_positive["run"]
    egress = next(
        step
        for step in steps
        if step.get("name")
        == "Verify direct scraper network denial and fixed model/source brokers"
    )
    assert steps.index(host_positive) < steps.index(egress)
    diagnostics = next(
        step
        for step in steps
        if step.get("name") == "Collect bounded composition diagnostics"
    )
    assert "candidate-scraper-socket-init" in diagnostics["run"]
    assert "candidate-flare-control-socket-init" in diagnostics["run"]
    ingress = next(
        step
        for step in steps
        if step.get("name") == "Verify the fixed agent-to-scraper HTTP ingress bridge"
    )
    assert "docker compose exec -T candidate-private-sentinel" in ingress["run"]
    assert "scraper_ingress_boundary_probe.py" in ingress["run"]
    assert (
        "docker compose exec -T --user 10001:20000 candidate-scraper" in egress["run"]
    )
    assert "scraper_egress_boundary_probe.py" in egress["run"]
    ingress_source = (
        ROOT / "tests/integration/scraper_ingress_boundary_probe.py"
    ).read_text()
    assert '"/scrape"' in ingress_source and '"/scrape/meta"' in ingress_source
    assert "force_browser" in ingress_source
    egress_source = (
        ROOT / "tests/integration/scraper_egress_boundary_probe.py"
    ).read_text()
    for capability in ("cache", "robots", "rate", "clearance"):
        assert f'"{capability}"' in egress_source
    assert "RESERVE_SLOT_SCRIPT" in egress_source
    compose = yaml.safe_load((ROOT / "compose.protected-capture-ci.yml").read_text())
    assert (
        compose["services"]["candidate-capture-peer-sentinel"]["networks"][
            "candidate_capture"
        ]["ipv4_address"]
        == "${CAPTURE_PEER_IP:-172.31.254.3}"
    )
    assert (
        compose["services"]["candidate-host-bridge-sentinel"]["network_mode"] == "host"
    )
    assert '"degraded"' not in ingress_source
    sentinel_check = next(
        step
        for step in steps
        if step.get("name")
        == "Verify hostile JavaScript did not reach the private sentinel"
    )
    assert sentinel_check["if"] == "always()"
    assert "sentinel_ledger.py verify" in sentinel_check["run"]
    assert "SENTINEL_LEDGER_BASELINE" in sentinel_check["run"]
    assert "PRIVATE_SENTINEL_POSITIVE_NONCE" in sentinel_check["run"]
    assert "HOST_BRIDGE_POSITIVE_NONCE" in sentinel_check["run"]
    assert "private_denial.status_code != 403" in egress_source
    sentinel_source = (
        ROOT / "tests/integration/private_network_sentinel.py"
    ).read_text()
    assert "POSITIVE_EVENTS" in sentinel_source
    assert 'record("http"' in sentinel_source
    assert 'record("positive-control"' in sentinel_source


def test_fixture_subnet_static_addresses_avoid_gateway_and_collisions():
    base_compose = yaml.safe_load(
        (ROOT / "compose.experimental-candidate.yml").read_text()
    )
    assignments: dict[tuple[str, str], str] = {}
    network_subnets: dict[str, ipaddress.IPv4Network] = {}
    for filename in (
        "compose.experimental-candidate.yml",
        "compose.protected-flare-ci.yml",
        "compose.protected-capture-ci.yml",
        "compose.protected-capture-lab.yml",
    ):
        document = yaml.safe_load((ROOT / filename).read_text())
        for network_name, network in document.get("networks", {}).items():
            network = network or {}
            for config in network.get("ipam", {}).get("config", []):
                if "subnet" in config:
                    subnet_value = config["subnet"]
                    if isinstance(subnet_value, str) and subnet_value.startswith("${"):
                        subnet_value = subnet_value.split(":-", 1)[1].removesuffix("}")
                    subnet = ipaddress.ip_network(subnet_value)
                    previous = network_subnets.setdefault(network_name, subnet)
                    assert previous == subnet
        for service_name, service in document.get("services", {}).items():
            networks = service.get("networks", {})
            if not isinstance(networks, dict):
                continue
            for network_name, config in networks.items():
                if isinstance(config, dict) and "ipv4_address" in config:
                    key = (service_name, network_name)
                    address = config["ipv4_address"]
                    previous = assignments.setdefault(key, address)
                    assert previous == address

    expected_networks = {
        "candidate_egress",
        "candidate_private",
        "candidate_capture",
        "candidate_browser_capture",
        "candidate_flare_capture",
        "candidate_model_gateway",
        "candidate_model_upstream",
        "candidate_flare_fixture",
    }
    assert set(network_subnets) == expected_networks
    assert network_subnets["candidate_egress"] == ipaddress.ip_network(
        "172.31.252.0/24"
    )
    # Keep Docker's dynamic allocator inside ranges that cannot consume fixed
    # gateway, renderer, or peer addresses from the parent /29 networks.
    expected_pools = {
        "candidate_capture": ("172.31.254.4/30", 2),
        "candidate_browser_capture": ("172.31.254.12/30", 2),
        "candidate_flare_capture": ("172.31.254.20/30", 2),
        "candidate_model_gateway": ("172.31.254.36/30", 2),
    }
    for network_name, (expected_pool, capacity) in expected_pools.items():
        config = base_compose["networks"][network_name]["ipam"]["config"][0]
        pool_value = config["ip_range"]
        if isinstance(pool_value, str) and pool_value.startswith("${"):
            pool_value = pool_value.split(":-", 1)[1].removesuffix("}")
        pool = ipaddress.ip_network(pool_value)
        assert pool == ipaddress.ip_network(expected_pool)
        assert pool.subnet_of(network_subnets[network_name])
        assert pool.num_addresses - 2 >= capacity
        for (service_name, assigned_network), address in assignments.items():
            if assigned_network == network_name:
                if isinstance(address, str) and address.startswith("${"):
                    address = address.split(":-", 1)[1].removesuffix("}")
                assert ipaddress.ip_address(address) not in pool, (
                    f"{service_name} static address overlaps {network_name} pool"
                )
        assert base_compose["networks"][network_name]["internal"] is True
    for (left_name, left), (right_name, right) in itertools.combinations(
        network_subnets.items(), 2
    ):
        assert not left.overlaps(right), (
            f"{left_name} {left} overlaps {right_name} {right}"
        )

    for service_name, service in base_compose["services"].items():
        assert service.get("network_mode") or service.get("networks"), (
            f"{service_name} would create an implicit default network"
        )

    fixture_network = "candidate_flare_fixture"
    subnet = network_subnets[fixture_network]
    gateway = next(subnet.hosts())
    used = {
        key: ipaddress.ip_address(address)
        for key, address in assignments.items()
        if key[1] == fixture_network
    }
    assert len(used) >= 2
    assert len(set(used.values())) == len(used), "fixture services share a static IP"
    for service, address in used.items():
        assert address in subnet, f"{service[0]} address is outside fixture subnet"
        assert address != gateway, f"{service[0]} collides with Docker's subnet gateway"
        assert address != subnet.broadcast_address


def test_lab_qualification_overlay_keeps_live_model_route_and_bounds_sentinels():
    base = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    lab = yaml.safe_load((ROOT / "compose.protected-capture-lab.yml").read_text())
    services = lab["services"]

    assert all("ports" not in service for service in services.values())
    assert [
        name
        for name, service in services.items()
        if service.get("network_mode") == "host"
    ] == ["candidate-host-bridge-sentinel"]
    assert "candidate-llm" not in services
    assert "candidate-model-egress" not in services
    assert "LLM_GATEWAY_PRIVATE_HOSTS" not in repr(services)
    assert (
        services["candidate-scraper"]["environment"]["CAPTURE_PROBE_MODEL_NAME"]
        == "${LLM_MODEL:-local}"
    )
    assert services["candidate-scraper"]["environment"]["CAPTURE_FIXTURE_ORIGIN"] == (
        "http://${CAPTURE_FIXTURE_HOST:-capture-origin.example.test}"
    )
    assert "extra_hosts" not in services["candidate-scraper"]
    assert services["candidate-capture-egress"]["extra_hosts"] == [
        "${CAPTURE_FIXTURE_HOST:-capture-origin.example.test}:93.184.216.34"
    ]
    assert "LLM_BASE_URL" in base["services"]["candidate-model-egress"]["environment"]
    assert (
        "LLM_API_KEY"
        in base["services"]["candidate-scraper-model-control"]["environment"]
    )
    assert "LLM_BASE_URL" in base["services"]["candidate-agent"]["environment"]

    host_sentinel = services["candidate-host-bridge-sentinel"]
    assert host_sentinel["network_mode"] == "host"
    assert "ports" not in host_sentinel
    subnet_setting = "${CAPTURE_HOST_BRIDGE_SUBNET:-172.31.254.0/29}"
    gateway_setting = "${CAPTURE_HOST_BRIDGE_IP:-172.31.254.1}"
    assert (
        base["networks"]["candidate_capture"]["ipam"]["config"][0]["gateway"]
        == gateway_setting
    )
    assert host_sentinel["environment"]["SENTINEL_BIND_HOST"] == gateway_setting
    assert (
        host_sentinel["environment"]["SENTINEL_EXPECTED_BIND_HOST"] == gateway_setting
    )
    assert host_sentinel["environment"]["SENTINEL_EXPECTED_SUBNET"] == subnet_setting
    assert (
        services["candidate-scraper"]["environment"]["CAPTURE_HOST_BRIDGE_IP"]
        == gateway_setting
    )
    assert (
        services["candidate-capture-egress"]["environment"]["CAPTURE_HOST_BRIDGE_IP"]
        == gateway_setting
    )
    assert host_sentinel["healthcheck"]["test"][-1].find("SENTINEL_BIND_HOST") >= 0
    assert host_sentinel["environment"]["SENTINEL_POSITIVE_EVENTS"] == (
        "/state/host-bridge-positive-control.jsonl"
    )
    assert host_sentinel["mem_limit"] == "64m"
    assert host_sentinel["cpus"] == 0.25
    assert host_sentinel["pids_limit"] == 32

    fixture_network = lab["networks"]["candidate_flare_fixture"]
    assert fixture_network["internal"] is True
    assert fixture_network["ipam"]["config"] == [{"subnet": "93.184.216.32/29"}]
    assert services["candidate-capture-egress"]["networks"]["candidate_flare_fixture"][
        "ipv4_address"
    ] == ("93.184.216.38")
    assert services["candidate-flare-test-origin"]["networks"][
        "candidate_flare_fixture"
    ]["ipv4_address"] == ("93.184.216.34")
    assert services["candidate-flare-test-origin"]["mem_limit"] == "64m"
    assert services["candidate-flare-test-origin"]["cpus"] == 0.25
    assert services["candidate-flare-test-origin"]["pids_limit"] == 32


def test_host_bridge_target_is_coherent_across_default_and_operator_gateways():
    gateway_setting = "${CAPTURE_HOST_BRIDGE_IP:-172.31.254.1}"
    subnet_setting = "${CAPTURE_HOST_BRIDGE_SUBNET:-172.31.254.0/29}"
    dynamic_range_setting = "${CAPTURE_HOST_BRIDGE_IP_RANGE:-172.31.254.4/30}"
    egress_ip_setting = "${CAPTURE_EGRESS_IP:-172.31.254.2}"
    peer_ip_setting = "${CAPTURE_PEER_IP:-172.31.254.3}"
    base = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    network = base["networks"]["candidate_capture"]["ipam"]["config"][0]
    lab = yaml.safe_load((ROOT / "compose.protected-capture-lab.yml").read_text())[
        "services"
    ]
    ci = yaml.safe_load((ROOT / "compose.protected-capture-ci.yml").read_text())[
        "services"
    ]
    for services in (lab, ci):
        assert (
            services["candidate-host-bridge-sentinel"]["environment"][
                "SENTINEL_BIND_HOST"
            ]
            == gateway_setting
        )
        assert (
            services["candidate-host-bridge-sentinel"]["environment"][
                "SENTINEL_EXPECTED_BIND_HOST"
            ]
            == gateway_setting
        )
        assert (
            services["candidate-host-bridge-sentinel"]["environment"][
                "SENTINEL_EXPECTED_SUBNET"
            ]
            == subnet_setting
        )
        host_environment = services["candidate-host-bridge-sentinel"]["environment"]
        assert (
            host_environment["SENTINEL_RESERVED_ADDRESSES"]
            == f"{egress_ip_setting},{peer_ip_setting}"
        )
        assert host_environment["CAPTURE_HOST_BRIDGE_SUBNET"] == subnet_setting
        assert host_environment["CAPTURE_HOST_BRIDGE_IP_RANGE"] == dynamic_range_setting
        assert host_environment["CAPTURE_EGRESS_IP"] == egress_ip_setting
        assert host_environment["CAPTURE_PEER_IP"] == peer_ip_setting
        assert (
            services["candidate-scraper"]["environment"]["CAPTURE_HOST_BRIDGE_IP"]
            == gateway_setting
        )
        assert (
            services["candidate-scraper"]["environment"]["CAPTURE_HOST_BRIDGE_SUBNET"]
            == subnet_setting
        )
        assert (
            services["candidate-scraper"]["environment"]["CAPTURE_HOST_BRIDGE_IP_RANGE"]
            == dynamic_range_setting
        )
        assert (
            services["candidate-scraper"]["environment"]["CAPTURE_PEER_IP"]
            == peer_ip_setting
        )
        assert (
            services["candidate-scraper"]["environment"]["CAPTURE_EGRESS_IP"]
            == egress_ip_setting
        )
        assert (
            services["candidate-capture-egress"]["environment"][
                "CAPTURE_HOST_BRIDGE_IP"
            ]
            == gateway_setting
        )
        assert (
            services["candidate-capture-egress"]["environment"][
                "CAPTURE_HOST_BRIDGE_SUBNET"
            ]
            == subnet_setting
        )
        assert (
            services["candidate-capture-egress"]["environment"][
                "CAPTURE_HOST_BRIDGE_IP_RANGE"
            ]
            == dynamic_range_setting
        )
        assert (
            services["candidate-capture-egress"]["environment"]["CAPTURE_PEER_IP"]
            == peer_ip_setting
        )
        assert (
            services["candidate-capture-egress"]["environment"]["CAPTURE_EGRESS_IP"]
            == egress_ip_setting
        )
        assert network["gateway"] == gateway_setting
        assert network["subnet"] == subnet_setting
        assert network["ip_range"] == dynamic_range_setting
        assert (
            services["candidate-capture-peer-sentinel"]["networks"][
                "candidate_capture"
            ]["ipv4_address"]
            == peer_ip_setting
        )
        assert (
            services["candidate-capture-peer-sentinel"]["environment"][
                "SENTINEL_BIND_HOST"
            ]
            == peer_ip_setting
        )
        assert (
            services["candidate-capture-peer-sentinel"]["environment"][
                "SENTINEL_EXPECTED_BIND_HOST"
            ]
            == peer_ip_setting
        )
    assert (
        base["services"]["candidate-capture-egress"]["networks"]["candidate_capture"][
            "ipv4_address"
        ]
        == egress_ip_setting
    )
    assert (
        base["services"]["candidate-scraper"]["environment"][
            "SCRAPER_CAPTURE_GATEWAY_IP"
        ]
        == egress_ip_setting
    )

    def interpolate(value: str, variables: dict[str, str]) -> str:
        return re.sub(
            r"\$\{([^}:]+):-([^}]+)\}",
            lambda match: variables.get(match.group(1), match.group(2)),
            value,
        )

    override = {
        "CAPTURE_HOST_BRIDGE_IP": "10.73.19.1",
        "CAPTURE_HOST_BRIDGE_SUBNET": "10.73.19.0/29",
        "CAPTURE_HOST_BRIDGE_IP_RANGE": "10.73.19.4/30",
        "CAPTURE_EGRESS_IP": "10.73.19.2",
        "CAPTURE_PEER_IP": "10.73.19.3",
    }
    rendered_network = {
        key: interpolate(value, override)
        for key, value in network.items()
        if isinstance(value, str)
    }
    assert rendered_network == {
        "subnet": "10.73.19.0/29",
        "ip_range": "10.73.19.4/30",
        "gateway": "10.73.19.1",
    }
    assert (
        validate_capture_address_plan(
            rendered_network["subnet"],
            rendered_network["gateway"],
            rendered_network["ip_range"],
            override["CAPTURE_EGRESS_IP"],
            override["CAPTURE_PEER_IP"],
        )
        == "10.73.19.1"
    )
    for services in (lab, ci):
        host = services["candidate-host-bridge-sentinel"]["environment"]
        assert (
            interpolate(host["SENTINEL_BIND_HOST"], override)
            == rendered_network["gateway"]
        )
        assert (
            interpolate(host["SENTINEL_EXPECTED_SUBNET"], override)
            == rendered_network["subnet"]
        )
        assert (
            interpolate(host["SENTINEL_RESERVED_ADDRESSES"], override)
            == "10.73.19.2,10.73.19.3"
        )


def test_ci_public_capture_dns_is_available_only_to_the_gateway():
    base = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    ci = yaml.safe_load((ROOT / "compose.protected-flare-ci.yml").read_text())
    services = ci["services"]
    scraper = base["services"]["candidate-scraper"]
    gateway = services["candidate-capture-egress"]
    origin = services["candidate-flare-test-origin"]

    assert set(scraper["networks"]) == {"candidate_capture"}
    assert "extra_hosts" not in scraper
    assert "extra_hosts" not in gateway
    assert "candidate_flare_fixture" not in scraper["networks"]
    assert gateway["networks"]["candidate_flare_fixture"]["ipv4_address"] == (
        "93.184.216.38"
    )
    assert origin["networks"]["candidate_flare_fixture"]["aliases"] == [
        "flare-origin.test"
    ]
    probe = (ROOT / "tests/integration/scraper_egress_boundary_probe.py").read_text()
    assert "socket.getaddrinfo(fixture_host, None)" in probe
    assert "capture_gateway_hostname_is_public(fixture_host)" in probe
    assert "response = await client.get(FIXTURE_URL)" in probe


def test_rendered_lab_fixture_hostname_is_available_only_to_capture_gateway():
    fixture_host = "capture-origin.example.test"
    fixture_alias = f"{fixture_host}:93.184.216.34"

    if shutil.which("docker") is None:
        base = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
        lab = yaml.safe_load((ROOT / "compose.protected-capture-lab.yml").read_text())
        services = {**base["services"], **lab["services"]}
        scraper = {
            **base["services"]["candidate-scraper"],
            **lab["services"]["candidate-scraper"],
        }
        gateway = services["candidate-capture-egress"]
        assert "extra_hosts" not in scraper
        assert gateway["extra_hosts"] == [
            "${CAPTURE_FIXTURE_HOST:-capture-origin.example.test}:93.184.216.34"
        ]
        assert scraper["environment"]["CAPTURE_FIXTURE_ORIGIN"] == (
            "http://${CAPTURE_FIXTURE_HOST:-capture-origin.example.test}"
        )
        return

    with tempfile.TemporaryDirectory() as temporary_directory:
        password_file = Path(temporary_directory) / "postgres-password"
        password_file.write_text("render-only-placeholder")
        compose_environment = os.environ.copy()
        compose_environment.update(
            {
                "CANDIDATE_API_KEY": "render-only-placeholder",
                "CANDIDATE_POSTGRES_PASSWORD_FILE": str(password_file),
                "LLM_API_KEY": "render-only-placeholder",
                "LLM_BASE_URL": "http://127.0.0.1:1/v1",
            }
        )
        rendered = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.experimental-candidate.yml",
                "-f",
                "compose.protected-capture-lab.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=compose_environment,
        )
    services = json.loads(rendered.stdout)["services"]
    scraper_hosts = services["candidate-scraper"].get("extra_hosts", {})
    gateway_hosts = services["candidate-capture-egress"].get("extra_hosts", {})

    assert fixture_host not in scraper_hosts
    if isinstance(gateway_hosts, dict):
        assert gateway_hosts[fixture_host] == "93.184.216.34"
    else:
        assert fixture_alias in gateway_hosts or (
            f"{fixture_host}=93.184.216.34" in gateway_hosts
        )
    assert services["candidate-scraper"]["environment"]["CAPTURE_FIXTURE_ORIGIN"] == (
        f"http://{fixture_host}"
    )


def test_compose_image_digests_are_full_sha256_values():
    compose_files = [*ROOT.glob("compose*.yml"), *ROOT.glob("docker-compose*.yml")]
    assert compose_files
    for path in compose_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if not re.match(r"\s*image:\s*", line):
                continue
            image = line.split("image:", 1)[1].strip().strip("\"'")
            if "@sha256:" in image:
                digest = image.rsplit("@sha256:", 1)[1]
                assert re.fullmatch(r"[0-9a-f]{64}", digest), (
                    f"{path.name}:{line_number} has malformed SHA-256 image pin"
                )


def test_protected_browser_failure_logs_are_bounded_and_precede_teardown():
    steps = WORKFLOW["jobs"]["protected-browser-profile"]["steps"]
    diagnostics = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Collect bounded protected-profile startup diagnostics"
    )
    teardown = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Tear down protected browser profile"
    )
    step = steps[diagnostics]
    assert step["if"] == "failure()"
    assert diagnostics < teardown
    assert "docker compose ps -a" in step["run"]
    assert "docker compose logs --no-color --tail=100" in step["run"]
    assert "docker inspect --format" in step["run"]
    assert ".State.Health" in step["run"]
    assert "env" not in step["run"]
    assert ".Config.Env" not in step["run"]


@pytest.mark.parametrize(
    ("classification", "runtime_required", "twin_required", "runtime", "twin"),
    list(
        itertools.product(
            ["success", "failure", "cancelled", "skipped"],
            ["true", "false", ""],
            ["true", "false", ""],
            ["success", "failure", "cancelled", "skipped"],
            ["success", "failure", "cancelled", "skipped"],
        )
    ),
)
def test_runtime_gate_fails_closed(
    classification, runtime_required, twin_required, runtime, twin
):
    gate = WORKFLOW["jobs"]["runtime-gate"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "changes",
        "twin-contracts",
        "integration-tests",
        "research-storage",
        "protected-browser-profile",
        "protected-flare-profile",
        "protected-capture-composition",
    }
    step = gate["steps"][0]
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": classification,
            "RUNTIME_REQUIRED": runtime_required,
            "TWIN_REQUIRED": twin_required,
            "RUNTIME_RESULT": runtime,
            "BROWSER_PROFILE_RESULT": "success",
            "PROTECTED_FLARE_RESULT": "success",
            "PROTECTED_CAPTURE_RESULT": "success",
            "TWIN_RESULT": twin,
            "STORAGE_RESULT": "success",
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    expected = (
        classification == "success"
        and runtime_required in {"true", "false"}
        and twin_required in {"true", "false"}
        and (runtime_required == "false" or runtime == "success")
        and (twin_required == "false" or twin == "success")
    )
    assert (result.returncode == 0) is expected


@pytest.mark.parametrize("profile_result", ["failure", "cancelled", "skipped"])
def test_required_browser_profile_failure_or_skip_fails_runtime_gate(profile_result):
    gate = WORKFLOW["jobs"]["runtime-gate"]
    step = gate["steps"][0]
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": "success",
            "RUNTIME_REQUIRED": "true",
            "TWIN_REQUIRED": "false",
            "RUNTIME_RESULT": "success",
            "BROWSER_PROFILE_RESULT": profile_result,
            "TWIN_RESULT": "skipped",
            "STORAGE_RESULT": "success",
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("profile_result", ["failure", "cancelled", "skipped"])
def test_required_flare_profile_failure_or_skip_fails_runtime_gate(profile_result):
    gate = WORKFLOW["jobs"]["runtime-gate"]
    result = subprocess.run(
        ["bash", "-c", gate["steps"][0]["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": "success",
            "RUNTIME_REQUIRED": "true",
            "TWIN_REQUIRED": "false",
            "RUNTIME_RESULT": "success",
            "BROWSER_PROFILE_RESULT": "success",
            "PROTECTED_FLARE_RESULT": profile_result,
            "PROTECTED_CAPTURE_RESULT": "success",
            "TWIN_RESULT": "skipped",
            "STORAGE_RESULT": "success",
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0


def test_documentation_only_change_allows_skipped_browser_profile():
    gate = WORKFLOW["jobs"]["runtime-gate"]
    step = gate["steps"][0]
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": "success",
            "RUNTIME_REQUIRED": "false",
            "TWIN_REQUIRED": "false",
            "RUNTIME_RESULT": "skipped",
            "BROWSER_PROFILE_RESULT": "skipped",
            "PROTECTED_FLARE_RESULT": "skipped",
            "PROTECTED_CAPTURE_RESULT": "skipped",
            "TWIN_RESULT": "skipped",
            "STORAGE_RESULT": "skipped",
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("profile_result", ["failure", "cancelled", "skipped"])
def test_required_capture_profile_failure_or_skip_fails_runtime_gate(profile_result):
    gate = WORKFLOW["jobs"]["runtime-gate"]
    result = subprocess.run(
        ["bash", "-c", gate["steps"][0]["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": "success",
            "RUNTIME_REQUIRED": "true",
            "TWIN_REQUIRED": "false",
            "RUNTIME_RESULT": "success",
            "BROWSER_PROFILE_RESULT": "success",
            "PROTECTED_FLARE_RESULT": "success",
            "PROTECTED_CAPTURE_RESULT": profile_result,
            "TWIN_RESULT": "skipped",
            "STORAGE_RESULT": "success",
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "path", [".github/workflows/runtime.yml", "docker-compose.ci.yml"]
)
def test_runtime_configuration_changes_require_both_test_lanes(path):
    assert requires_full_runtime([path])
    assert requires_twin_contracts([path])


def test_targeted_session_contracts_use_reachable_compose_fixture():
    steps = WORKFLOW["jobs"]["integration-tests"]["steps"]
    step = next(
        item
        for item in steps
        if item.get("name") == "Run targeted source-backed agent contracts"
    )
    assert "TEST_SITE_BASE_URL=http://test-site:8000" in step["run"]
    assert "test_session_search_zero_results_val_ses_085" in step["run"]
    assert "test_cross_concurrent_session_isolation_val_cross_018" in step["run"]
