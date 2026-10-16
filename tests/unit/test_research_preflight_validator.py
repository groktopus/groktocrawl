"""Fail-closed comparison preflight validator tests."""

import hashlib
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/validate-research-preflight.py"
SPEC = importlib.util.spec_from_file_location("preflight_validator", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def draft() -> dict:
    return json.loads(
        (
            Path(__file__).parents[2] / "docs/experiments/research-preflight.json"
        ).read_text()
    )


def test_synthetic_current_pin_fixture_is_valid(tmp_path):
    target = tmp_path / "current-input.json"
    payload = b'{"fixture":"synthetic current pin"}\n'
    target.write_bytes(payload)
    manifest = draft()
    manifest["file_pins"] = [
        {
            "path": "current-input.json",
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    ]
    errors = MODULE.validate_manifest(manifest, tmp_path)
    assert errors == []


def test_historical_frozen_preflight_rejects_changed_uv_lock_pin():
    errors = MODULE.validate_manifest(draft(), Path(__file__).parents[2])
    assert errors == ["file_pins[2] digest does not match target"]


def test_missing_file_pin_is_rejected_without_reading_arbitrary_paths():
    manifest = draft()
    manifest["file_pins"] = [{"path": "missing.json", "sha256": "0" * 64}]
    errors = MODULE.validate_manifest(manifest, Path(__file__).parents[2])
    assert "file_pins[0] target is missing" in errors


def test_unsafe_file_pin_is_rejected():
    manifest = draft()
    manifest["file_pins"] = [{"path": "../secret", "sha256": "0" * 64}]
    errors = MODULE.validate_manifest(manifest, Path(__file__).parents[2])
    assert "file_pins[0].path is unsafe" in errors
