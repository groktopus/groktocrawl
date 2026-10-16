import importlib.util
import json
from pathlib import Path

SCRIPT = Path("scripts/validate_w12_mission_run.py")
SPEC = importlib.util.spec_from_file_location("validate_w12_mission_run", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_validator_rejects_an_empty_run(tmp_path):
    issues = validator.validate_run(tmp_path, expected_revision="a" * 40)
    assert len(issues) == 5
    assert all("missing" in issue for issue in issues)


def test_manifest_accepts_terminal_failures_below_guardrail(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "manifest.json").write_text(
        json.dumps(
            {
                "source_revision": "a" * 40,
                "work_order_sha256": json.loads(
                    Path(
                        "docs/experiments/research-mission/w12.1-work-order.json"
                    ).read_text()
                )["trials_sha256"],
            }
        )
    )
    (public / "summary.json").write_text(
        json.dumps({"attempted": 72, "completed": 71, "failed": 1})
    )
    issues = []
    validator.check_manifest(
        tmp_path,
        manifest_name="manifest.json",
        summary_name="summary.json",
        work_name="w12.1-work-order.json",
        expected_revision="a" * 40,
        expected_count=72,
        issues=issues,
    )
    assert issues == []
