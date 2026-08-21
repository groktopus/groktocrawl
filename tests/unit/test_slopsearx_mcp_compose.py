"""Deterministic contract tests for the direct SlopSearX MCP Compose service."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def _environment(service: dict[str, object]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in service["environment"])


def _command_script(service: dict[str, object]) -> str:
    """The entrypoint wrapper run by the slopsearx-mcp container."""
    assert service["entrypoint"] == ["/bin/sh", "-c"]
    commands = service["command"]
    assert isinstance(commands, list) and len(commands) == 1
    return str(commands[0])


def _resolve(value: str, variables: dict[str, str]) -> str:
    """Resolve the Compose interpolation forms used by this service."""
    match = re.fullmatch(r"\$\{([A-Z0-9_]+)(?::([?-])(.*))?\}", value)
    if not match:
        return value
    key, operator, fallback = match.groups()
    configured = variables.get(key, "")
    if operator == "-":
        return configured or fallback
    if operator == "?":
        if not configured:
            raise ValueError(fallback)
        return configured
    return configured


def test_direct_slopsearx_mcp_is_opt_in_and_uses_shared_wiring():
    service = COMPOSE["services"]["slopsearx-mcp"]
    environment = _environment(service)

    assert service["image"] == (
        "ghcr.io/magnus919/slopsearx@sha256:c7fd83077bf5f189a0125b6377b367740068d012e9caeb48318401a23437711d"
    )
    # Gated behind a profile so a no-config `docker compose up` does not start
    # the companion; it runs only when the `mcp` profile is enabled.
    assert service["profiles"] == ["mcp"]
    assert service["ports"] == ["${SLOPSEARX_MCP_PORT:-8007}:8000"]
    assert _resolve("${SLOPSEARX_MCP_PORT:-8007}", {}) == "8007"
    assert (
        _resolve("${SLOPSEARX_MCP_PORT:-8007}", {"SLOPSEARX_MCP_PORT": "9007"})
        == "9007"
    )
    assert environment["ENGINE_BRAVE_API_KEY"] == "${BRAVE_API_KEY}"
    assert environment["VALKEY_URL"] == "redis://valkey:6379/0"


def test_direct_slopsearx_mcp_token_defaults_empty_so_compose_up_never_aborts():
    environment = _environment(COMPOSE["services"]["slopsearx-mcp"])
    # No `:?` guard: with no SLOPSEARX_MCP_AUTH_TOKEN set, interpolation yields an
    # empty value instead of aborting a plain `docker compose up` at load time.
    assert "${SLOPSEARX_MCP_AUTH_TOKEN:?" not in environment["MCP_AUTH_TOKEN"]
    assert _resolve(environment["MCP_AUTH_TOKEN"], {}) == ""
    assert (
        _resolve(environment["MCP_AUTH_TOKEN"], {"SLOPSEARX_MCP_AUTH_TOKEN": "token"})
        == "token"
    )


def test_direct_slopsearx_mcp_refuses_to_start_without_token():
    service = COMPOSE["services"]["slopsearx-mcp"]
    script = _command_script(service)
    # Secure-by-default is preserved at container startup: the wrapper exits
    # non-zero when MCP_AUTH_TOKEN is empty (never running unauthenticated) and
    # otherwise launches the MCP server.
    assert 'if [ -z "$MCP_AUTH_TOKEN" ]' in script
    assert "exit 1" in script
    assert "exec python -m slopsearx.mcp" in script


def test_direct_slopsearx_mcp_grants_delegate_defaults_upstream():
    environment = _environment(COMPOSE["services"]["slopsearx-mcp"])
    grant_names = (
        "MCP_GRANT_JOBS",
        "MCP_GRANT_SCIENCE",
        "MCP_GRANT_RESEARCH",
        "MCP_GRANT_SECURITY",
        "MCP_TARGETED_SENSITIVE_ALLOWED",
    )

    # Compose expresses no default: an unset grant interpolates to an empty
    # value, which the SlopSearX image parses as "no override", leaving its
    # secure-by-default policy (all grants off) in force.
    assert {_resolve(environment[name], {}) for name in grant_names} == {""}
    enabled: dict[str, str] = dict.fromkeys(grant_names, "1")
    assert {_resolve(environment[name], enabled) for name in grant_names} == {"1"}
    disabled: dict[str, str] = dict.fromkeys(grant_names, "0")
    assert {_resolve(environment[name], disabled) for name in grant_names} == {"0"}


def test_direct_slopsearx_mcp_has_protocol_healthcheck():
    service = COMPOSE["services"]["slopsearx-mcp"]
    probe = service["healthcheck"]["test"][-1]
    compile(probe, "<slopsearx-mcp healthcheck>", "exec")
    assert "http://127.0.0.1:8000/mcp" in probe
    assert "'method':'initialize'" in probe
    assert "'Authorization':'Bearer ' + os.environ['MCP_AUTH_TOKEN']" in probe
    assert "timeout=3" in probe
    assert "response.readline(65536)" in probe
    assert "range(8)" in probe
    assert "line.startswith('data:')" in probe
    assert "get_content_type() == 'text/event-stream'" in probe
    assert "response.read(65536)" in probe
    assert "json.loads(next(" in probe
    assert "serverInfo" in probe
