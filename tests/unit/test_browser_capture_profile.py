"""Deployment contract tests for the opt-in isolated browser profile."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_default_compose_browser_remains_compatible():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    browser = compose["services"]["browser-svc"]
    assert "command" not in browser
    assert "BROWSER_PROTECTED_RENDERER" not in browser.get("environment", {})


def test_candidate_renderer_has_only_internal_gateway_network_and_uds_api():
    compose = yaml.safe_load((ROOT / "compose.experimental-candidate.yml").read_text())
    services = compose["services"]
    renderer = services["candidate-browser-renderer"]
    controller = services["candidate-browser-controller"]
    gateway = services["candidate-capture-egress"]

    assert renderer["networks"] == {
        "candidate_browser_capture": {"ipv4_address": "172.31.254.11"}
    }
    assert compose["networks"]["candidate_browser_capture"]["internal"] is True
    assert renderer["environment"]["BROWSER_PROTECTED_RENDERER"] == "1"
    assert renderer["environment"]["BROWSER_CAPTURE_PROXY_URL"] == (
        "http://172.31.254.10:8080"
    )
    assert renderer["cap_drop"] == ["ALL"]
    assert renderer["group_add"] == ["20000"]
    healthcheck = " ".join(renderer["healthcheck"]["test"])
    assert "'/run/browser/renderer.sock'" in healthcheck
    assert "GET /health" in healthcheck and "200 OK" in healthcheck
    assert "20000" in controller["group_add"]
    assert set(renderer["cap_add"]) == {
        "CHOWN",
        "NET_ADMIN",
        "SETUID",
        "SETGID",
        "SETPCAP",
    }
    assert "ports" not in renderer
    assert "candidate_capture" not in controller["networks"]
    assert "candidate_egress" not in controller["networks"]
    assert "candidate_private" in controller["networks"]
    assert controller["command"][-1] == "exec python -m browser_svc.controller_entrypoint"
    assert "candidate-scraper-socket-init" in controller["depends_on"]
    socket_init = services["candidate-scraper-socket-init"]
    assert "candidate_browser_controller_socket:/run/browser-control" in socket_init["volumes"]
    assert "setup_socket_dir /run/browser-control 0:20000 2710" in socket_init["command"][-1]
    assert gateway["environment"]["CAPTURE_PROXY_BIND_HOST"] == "0.0.0.0"
    gateway_healthcheck = " ".join(gateway["healthcheck"]["test"])
    assert "http://127.0.0.1:8081/healthz" in gateway_healthcheck
