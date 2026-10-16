import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "capture_w11_preflight", SCRIPTS / "capture_w11_preflight.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StubClient:
    def __init__(self, *, missing_tool: str | None = None, research: bool = True):
        self.names = set(MODULE.REQUIRED_TOOLS)
        if missing_tool:
            self.names.remove(missing_tool)
        self.research = research
        self.calls: list[str] = []

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": name} for name in sorted(self.names)]

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        if name == "slopsearx_get_service_status":
            specialist = {
                grant: grant == "research" and self.research
                for grant in MODULE.SPECIALIST_GRANTS
            }
            return {
                "version": "0.5.0",
                "contract_version": "1.0",
                "active_engines": 7,
                "grants": {
                    "specialist": specialist,
                    "targeted_sensitive_allowed": False,
                },
                "policy_bounds": {"job_max_queries": 3},
                "research_execution": {
                    "mode": "durable_leased",
                    "worker_id": "ephemeral-worker",
                },
                "snapshots_available": True,
                "job_store_available": True,
                "valkey": {"connected": True, "fail_closed": True},
                "workflow_health": {"research": {"available": True}},
            }
        return {
            "error": {
                "code": "tool_disabled",
                "grant": next(
                    (
                        f"MCP_GRANT_{grant.upper()}"
                        for grant, (tool, _arguments) in MODULE.DISABLED_PROBES.items()
                        if tool == name
                    ),
                    None,
                ),
            }
        }


def expected_grants() -> tuple[set[str], set[str]]:
    enabled = {"research"}
    return enabled, MODULE.SPECIALIST_GRANTS - enabled


def test_capture_records_redacted_reproducible_configuration() -> None:
    client = StubClient()
    enabled, disabled = expected_grants()
    record = MODULE.capture_preflight(
        client,
        expected_version="0.5.0",
        expected_enabled=enabled,
        expected_disabled=disabled,
        target_label="isolated-w11-a1",
        source_revision="a" * 40,
        image_digest="sha256:" + "b" * 64,
    )

    assert record["schema_version"] == "enterprise-evaluation/w11-preflight/1"
    assert record["slopsearx"]["grants"] == {
        "enabled": ["research"],
        "disabled": sorted(disabled),
        "targeted_sensitive_allowed": False,
    }
    assert set(record["mcp"]["disabled_grant_probes"]) == disabled
    assert record["configuration_sha256"]
    assert record["slopsearx"]["research_execution"] == {
        "mode": "durable_leased"
    }
    assert "endpoint" not in record
    assert "token" not in repr(record).casefold()
    assert len(client.calls) == 1 + len(disabled)


def test_configuration_digest_excludes_capture_time() -> None:
    enabled, disabled = expected_grants()
    arguments = {
        "expected_version": "0.5.0",
        "expected_enabled": enabled,
        "expected_disabled": disabled,
        "target_label": "isolated-w11-a1",
        "source_revision": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
    }
    first = MODULE.capture_preflight(StubClient(), **arguments)
    second = MODULE.capture_preflight(StubClient(), **arguments)
    assert first["configuration_sha256"] == second["configuration_sha256"]


def test_preflight_fails_when_required_tool_is_missing() -> None:
    enabled, disabled = expected_grants()
    with pytest.raises(MODULE.PreflightError, match="required W11 tools are missing"):
        MODULE.capture_preflight(
            StubClient(missing_tool="slopsearx_extend_research"),
            expected_version="0.5.0",
            expected_enabled=enabled,
            expected_disabled=disabled,
            target_label="isolated-w11-a1",
            source_revision="a" * 40,
            image_digest="sha256:" + "b" * 64,
        )


def test_preflight_fails_when_grants_differ_from_declared_policy() -> None:
    enabled, disabled = expected_grants()
    with pytest.raises(MODULE.PreflightError, match="do not match"):
        MODULE.capture_preflight(
            StubClient(research=False),
            expected_version="0.5.0",
            expected_enabled=enabled,
            expected_disabled=disabled,
            target_label="isolated-w11-a1",
            source_revision="a" * 40,
            image_digest="sha256:" + "b" * 64,
        )


def test_preflight_requires_a_declared_state_for_every_specialist_grant() -> None:
    with pytest.raises(MODULE.PreflightError, match="every specialist grant"):
        MODULE.capture_preflight(
            StubClient(),
            expected_version="0.5.0",
            expected_enabled={"research"},
            expected_disabled=set(),
            target_label="isolated-w11-a1",
            source_revision="a" * 40,
            image_digest="sha256:" + "b" * 64,
        )
