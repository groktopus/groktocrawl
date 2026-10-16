"""Execute the real Runtime Gate shell to prove storage fails closed."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "storage_result", ["success", "failure", "cancelled", "skipped", ""]
)
def test_required_storage_result_controls_runtime_gate(storage_result):
    workflow = yaml.safe_load(
        (Path(__file__).parents[2] / ".github/workflows/runtime.yml").read_text()
    )
    gate = workflow["jobs"]["runtime-gate"]
    assert "research-storage" in gate["needs"]
    assert "protected-flare-profile" in gate["needs"]
    assert "protected-capture-composition" in gate["needs"]
    result = subprocess.run(
        ["sh", "-c", gate["steps"][0]["run"]],
        env={
            **os.environ,
            "CLASSIFICATION": "success",
            "RUNTIME_REQUIRED": "true",
            "TWIN_REQUIRED": "true",
            "RUNTIME_RESULT": "success",
            "BROWSER_PROFILE_RESULT": "success",
            "PROTECTED_FLARE_RESULT": "success",
            "PROTECTED_CAPTURE_RESULT": "success",
            "TWIN_RESULT": "success",
            "STORAGE_RESULT": storage_result,
        },
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert (result.returncode == 0) == (storage_result == "success")
