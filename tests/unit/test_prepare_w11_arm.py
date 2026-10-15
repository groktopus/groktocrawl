"""Tests for isolated W11 arm preparation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prepare_w11_arm", ROOT / "scripts" / "prepare_w11_arm.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize(
    ("arm", "enabled"),
    [
        ("control", set()),
        ("research", {"research"}),
        ("staged", {"staged_search"}),
        ("provenance", {"retrieval_receipts"}),
        ("evidence", {"retrieval_receipts", "staged_search"}),
        ("composition", {"research", "retrieval_receipts"}),
        ("saved", {"saved_search_events", "saved_searches"}),
        ("dossier", {"dependency_dossier", "research", "security"}),
    ],
)
def test_arm_enables_only_declared_w11_grants(arm, enabled):
    content, manifest = module.arm_environment(
        arm,
        token="secret",
        brave_api_key="provider",
        mcp_port=19007,
        http_port=19081,
    )

    values = dict(line.split("=", 1) for line in content.splitlines())
    assert {
        grant
        for grant, env in module.GRANTS.items()
        if values[env] == "1"
    } == enabled
    assert manifest["grants"]["enabled"] == sorted(enabled)
    assert "secret" not in str(manifest)
    assert "provider" not in str(manifest)
    assert manifest["isolation"]["dedicated_valkey_volume_required"] is True


def test_arm_rejects_missing_secrets_and_unsafe_ports():
    with pytest.raises(ValueError, match="token"):
        module.arm_environment(
            "research",
            token="",
            brave_api_key="provider",
            mcp_port=19007,
            http_port=19081,
        )
    with pytest.raises(ValueError, match="Brave"):
        module.arm_environment(
            "research",
            token="secret",
            brave_api_key="",
            mcp_port=19007,
            http_port=19081,
        )
    with pytest.raises(ValueError, match="ports must differ"):
        module.arm_environment(
            "research",
            token="secret",
            brave_api_key="provider",
            mcp_port=19007,
            http_port=19007,
        )


def test_write_exclusive_uses_private_mode_and_refuses_overwrite(tmp_path):
    path = tmp_path / "arm.env"
    module.write_exclusive(path, "secret\n", mode=0o600)

    assert path.read_text() == "secret\n"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.write_exclusive(path, "replacement\n", mode=0o600)
