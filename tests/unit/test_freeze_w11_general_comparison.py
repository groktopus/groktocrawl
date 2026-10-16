from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.freeze_w11_general_comparison import (
    MAX_CHECKPOINT_BYTES as FROZEN_MAX_CHECKPOINT_BYTES,
)
from scripts.freeze_w11_general_comparison import build_freeze, write_exclusive
from scripts.prepare_w11_arm import (
    GRANTS,
    SLOPSEARX_IMAGE_DIGEST,
    SLOPSEARX_SOURCE_REVISION,
    SLOPSEARX_VERSION,
    arm_environment,
)
from scripts.run_w11_general_retrieval import (
    MAX_CHECKPOINT_BYTES as RUNTIME_MAX_CHECKPOINT_BYTES,
)


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return path


def _digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(tmp_path: Path) -> dict[str, Path | str]:
    cases = {
        "cases": [
            {
                "case_id": f"case-{index:02d}",
                "challenge_type": "freshness" if index < 6 else "authority",
                "as_of": "2026-09-11T00:00:00Z",
            }
            for index in range(12)
        ]
    }
    cases_path = _write(tmp_path / "cases.json", cases)
    selection_path = _write(
        tmp_path / "selection.json",
        {
            "schema_version": "enterprise-evaluation/w10-policy-selection/1",
            "complete": True,
            "outcome": "bounded_recovery",
            "w11_measurement_authorized": True,
            "selected_challenge_types": ["freshness"],
            "known_challenge_types": ["authority", "freshness"],
            "inputs": {"challenge_cases": _digest(cases_path)},
        },
    )
    manifest_path = _write(
        tmp_path / "manifest.json",
        {
            "schema_version": "enterprise-evaluation/w10-policy-run/1",
            "cases_sha256": _digest(cases_path),
            "records": 180,
            "completed": 180,
            "failed": 0,
            "failed_attempts": 0,
            "result_limit": 8,
        },
    )
    entries = [
        {
            "position": position + 1,
            "case_id": f"case-{(position // 2) % 12:02d}",
            "challenge_type": (
                "freshness" if ((position // 2) % 12) < 6 else "authority"
            ),
            "repetition": (position // 24),
            "arm": ["flat_http", "recorded_continuation"][position % 2],
            "control_policy": (
                "full" if ((position // 2) % 12) < 6 else "fixed"
            ),
        }
        for position in range(72)
    ]
    work_order_path = _write(
        tmp_path / "work-order.json",
        {
            "schema_version": "enterprise-evaluation/w11-general-work-order/1",
            "seed": 11052026,
            "repetitions": 3,
            "result_limit": 8,
            "arms": ["flat_http", "recorded_continuation"],
            "policy_by_challenge_type": {"authority": "fixed", "freshness": "full"},
            "entries": entries,
            "inputs": {
                "w10_selection_sha256": _digest(selection_path),
                "cases_sha256": _digest(cases_path),
                "w10_run_manifest_sha256": _digest(manifest_path),
            },
        },
    )
    engines = ["brave", "wikipedia"]
    scope_path = _write(
        tmp_path / "scope.json",
        {
            "schema_version": "enterprise-evaluation/w11-scope-equivalence/1",
            "scope_equal": True,
            "dispatches": 0,
            "selected_engine_count": 2,
            "http_control": {"engines": engines},
            "research_arm": {"initial_plan_engines": engines},
        },
    )
    _, arm = arm_environment(
        "research", token="token", brave_api_key="key", mcp_port=8120, http_port=8121
    )
    arm_path = _write(tmp_path / "arm.json", arm)
    disabled = sorted((set(GRANTS) | {"jobs", "science"}) - {"research"})
    preflight_path = _write(
        tmp_path / "preflight.json",
        {
            "schema_version": "enterprise-evaluation/w11-preflight/1",
            "configuration_sha256": "a" * 64,
            "slopsearx": {
                "version": SLOPSEARX_VERSION,
                "source_revision": SLOPSEARX_SOURCE_REVISION,
                "image_digest": SLOPSEARX_IMAGE_DIGEST,
                "grants": {
                    "enabled": ["research"],
                    "disabled": disabled,
                    "targeted_sensitive_allowed": False,
                },
                "policy_bounds": {"job_max_queries": 20},
            },
            "mcp": {
                "disabled_grant_probes": {
                    name: {"error_code": "tool_disabled"} for name in disabled
                }
            },
        },
    )
    compatibility_path = _write(
        tmp_path / "compatibility.json",
        {
            "schema_version": "enterprise-evaluation/w11-http-compatibility/1",
            "valid": True,
            "issues": [],
            "slopsearx": {
                "version": SLOPSEARX_VERSION,
                "source_revision": SLOPSEARX_SOURCE_REVISION,
                "image_digest": SLOPSEARX_IMAGE_DIGEST,
            },
        },
    )
    text_paths = {}
    for name in ("protocol", "analysis", "runner", "grader", "retrieval", "quality"):
        text_paths[name] = tmp_path / f"{name}.txt"
        text_paths[name].write_text(name)
    return {
        "w10_selection_path": selection_path,
        "w10_manifest_path": manifest_path,
        "cases_path": cases_path,
        "work_order_path": work_order_path,
        "scope_path": scope_path,
        "arm_manifest_path": arm_path,
        "preflight_path": preflight_path,
        "compatibility_path": compatibility_path,
        "source_commit": "b" * 40,
        "groktocrawl_source_commit": "d" * 40,
        "groktocrawl_image_digests": {
            "agent": "sha256:" + "c" * 64,
            "scraper": "sha256:" + "e" * 64,
        },
        "model": "local",
        "protocol_path": text_paths["protocol"],
        "analysis_plan_path": text_paths["analysis"],
        "runner_path": text_paths["runner"],
        "grader_path": text_paths["grader"],
        "retrieval_summary_path": text_paths["retrieval"],
        "quality_summary_path": text_paths["quality"],
    }


def test_build_freeze_binds_design_limits_and_redacted_inputs(tmp_path: Path) -> None:
    assert FROZEN_MAX_CHECKPOINT_BYTES == RUNTIME_MAX_CHECKPOINT_BYTES
    result = build_freeze(**_inputs(tmp_path))

    assert result["design"]["trial_count"] == 72
    assert result["design"]["pair_count"] == 36
    assert result["limits"] == {
        "model_calls": 72,
        "distinct_queries": 108,
        "search_attempts": 1080,
        "engine_attempts": 2160,
        "admitted_results": 576,
        "elapsed_seconds": 25920,
        "stored_bytes": 754974720,
        "interpretation": (
            "Experiment-wide upper bounds. Elapsed time covers retrieval and grading "
            "phases; stored bytes allow 5 MiB for each public and private checkpoint."
        ),
    }
    encoded = json.dumps(result)
    assert "token" not in encoded
    assert "http://" not in encoded
    assert "https://" not in encoded


def test_build_freeze_rejects_tampered_scope(tmp_path: Path) -> None:
    values = _inputs(tmp_path)
    scope_path = values["scope_path"]
    assert isinstance(scope_path, Path)
    scope = json.loads(scope_path.read_text())
    scope["research_arm"]["initial_plan_engines"] = ["wikipedia"]
    _write(scope_path, scope)

    with pytest.raises(ValueError, match="scope-equivalence"):
        build_freeze(**values)


def test_build_freeze_independently_checks_selection_case_binding(tmp_path: Path) -> None:
    values = _inputs(tmp_path)
    selection_path = values["w10_selection_path"]
    work_order_path = values["work_order_path"]
    assert isinstance(selection_path, Path)
    assert isinstance(work_order_path, Path)
    selection = json.loads(selection_path.read_text())
    selection["inputs"]["challenge_cases"] = "f" * 64
    _write(selection_path, selection)
    work_order = json.loads(work_order_path.read_text())
    work_order["inputs"]["w10_selection_sha256"] = _digest(selection_path)
    _write(work_order_path, work_order)

    with pytest.raises(ValueError, match="does not bind"):
        build_freeze(**values)


def test_build_freeze_independently_checks_selected_policy_mapping(tmp_path: Path) -> None:
    values = _inputs(tmp_path)
    work_order_path = values["work_order_path"]
    assert isinstance(work_order_path, Path)
    work_order = json.loads(work_order_path.read_text())
    work_order["policy_by_challenge_type"]["authority"] = "full"
    _write(work_order_path, work_order)

    with pytest.raises(ValueError, match="final W10 selection"):
        build_freeze(**values)


def test_build_freeze_requires_exact_case_arm_coverage(tmp_path: Path) -> None:
    values = _inputs(tmp_path)
    work_order_path = values["work_order_path"]
    assert isinstance(work_order_path, Path)
    work_order = json.loads(work_order_path.read_text())
    work_order["entries"][0]["case_id"] = work_order["entries"][2]["case_id"]
    _write(work_order_path, work_order)

    with pytest.raises(ValueError, match="each frozen case and arm exactly"):
        build_freeze(**values)


def test_build_freeze_rejects_enabled_unrelated_grant(tmp_path: Path) -> None:
    values = _inputs(tmp_path)
    preflight_path = values["preflight_path"]
    assert isinstance(preflight_path, Path)
    preflight = json.loads(preflight_path.read_text())
    preflight["slopsearx"]["grants"]["enabled"].append("staged_search")
    preflight["slopsearx"]["grants"]["disabled"].remove("staged_search")
    preflight["mcp"]["disabled_grant_probes"].pop("staged_search")
    _write(preflight_path, preflight)

    with pytest.raises(ValueError, match="preflight grants"):
        build_freeze(**values)


def test_write_exclusive_preserves_first_freeze(tmp_path: Path) -> None:
    output = tmp_path / "freeze.json"
    write_exclusive(output, {"first": True})

    with pytest.raises(FileExistsError):
        write_exclusive(output, {"first": False})
    assert json.loads(output.read_text()) == {"first": True}
