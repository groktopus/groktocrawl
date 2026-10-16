#!/usr/bin/env python3
"""Validate and freeze every input to the scored W11 Family A comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_w11_arm import (
    GRANTS,
    SLOPSEARX_IMAGE_DIGEST,
    SLOPSEARX_SOURCE_REVISION,
    SLOPSEARX_VERSION,
)
from scripts.w11_retrieval_transports import HTTP_ATTEMPTS

ARMS = ["flat_http", "recorded_continuation"]
REPETITIONS = 3
MAX_QUERIES_PER_TRIAL = 3
MAX_MODEL_CALLS_PER_TRIAL = 1
MAX_SECONDS_PER_PHASE = 180
MAX_CHECKPOINT_BYTES = 5 * 1024 * 1024
EXPECTED_ENABLED_GRANTS = ["research"]
PREFLIGHT_ONLY_GRANTS = {"jobs", "science"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ValueError(f"{path.name} has an unsupported schema")
    return value


def _identity(value: dict[str, Any], *, label: str) -> None:
    expected = {
        "version": SLOPSEARX_VERSION,
        "source_revision": SLOPSEARX_SOURCE_REVISION,
        "image_digest": SLOPSEARX_IMAGE_DIGEST,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError(f"{label} does not identify the frozen SlopSearX release")


def _sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_freeze(
    *,
    w10_selection_path: Path,
    w10_manifest_path: Path,
    cases_path: Path,
    work_order_path: Path,
    scope_path: Path,
    arm_manifest_path: Path,
    preflight_path: Path,
    compatibility_path: Path,
    source_commit: str,
    groktocrawl_source_commit: str,
    groktocrawl_image_digests: dict[str, str],
    model: str,
    protocol_path: Path,
    analysis_plan_path: Path,
    runner_path: Path,
    grader_path: Path,
    retrieval_summary_path: Path,
    quality_summary_path: Path,
) -> dict[str, Any]:
    selection = load(
        w10_selection_path, "enterprise-evaluation/w10-policy-selection/1"
    )
    manifest = json.loads(w10_manifest_path.read_text())
    if manifest.get("schema_version") not in {
        "enterprise-evaluation/w10-policy-run/1",
        "enterprise-evaluation/w11-fixed-control-handoff/1",
    }:
        raise ValueError("W10 manifest has an unsupported schema")
    cases = json.loads(cases_path.read_text())
    work_order = load(work_order_path, "enterprise-evaluation/w11-general-work-order/1")
    scope = load(scope_path, "enterprise-evaluation/w11-scope-equivalence/1")
    arm = load(arm_manifest_path, "enterprise-evaluation/w11-arm/1")
    preflight = load(preflight_path, "enterprise-evaluation/w11-preflight/1")
    compatibility = load(
        compatibility_path, "enterprise-evaluation/w11-http-compatibility/1"
    )

    if selection.get("complete") is not True:
        raise ValueError("W10 policy selection is incomplete")
    if selection.get("w11_measurement_authorized") is not True:
        raise ValueError("W10 did not authorize W11 measurement")
    known_types = {str(case["challenge_type"]) for case in cases.get("cases", [])}
    selected_types = set(selection.get("selected_challenge_types", []))
    if (
        (selection.get("inputs") or {}).get("challenge_cases") != digest(cases_path)
        or set(selection.get("known_challenge_types", [])) != known_types
        or not selected_types <= known_types
    ):
        raise ValueError("W10 selection does not bind the supplied challenge cases")
    if manifest.get("completed") != manifest.get("records") or any(
        manifest.get(field) != 0 for field in ("failed", "failed_attempts")
    ):
        raise ValueError("W10 run manifest is incomplete")
    if manifest.get("cases_sha256") != digest(cases_path):
        raise ValueError("W10 manifest does not bind the supplied cases")
    inputs = work_order.get("inputs", {})
    expected_inputs = {
        "w10_selection_sha256": digest(w10_selection_path),
        "cases_sha256": digest(cases_path),
        "w10_run_manifest_sha256": digest(w10_manifest_path),
    }
    if inputs != expected_inputs:
        raise ValueError("W11 work order does not bind the supplied W10 inputs")
    case_count = len(cases.get("cases", []))
    expected_trials = case_count * REPETITIONS * len(ARMS)
    if (
        case_count != 12
        or work_order.get("arms") != ARMS
        or work_order.get("repetitions") != REPETITIONS
        or len(work_order.get("entries", [])) != expected_trials
        or work_order.get("result_limit") != manifest.get("result_limit")
    ):
        raise ValueError("W11 work order does not match the frozen challenge design")
    expected_policy_by_type = {
        challenge_type: "full" if challenge_type in selected_types else "fixed"
        for challenge_type in sorted(known_types)
    }
    if work_order.get("policy_by_challenge_type") != expected_policy_by_type:
        raise ValueError("W11 work order does not implement the final W10 selection")
    entries = work_order["entries"]
    if [entry.get("position") for entry in entries] != list(range(1, expected_trials + 1)):
        raise ValueError("W11 work-order positions are not complete and ordered")
    case_types = {
        str(case["case_id"]): str(case["challenge_type"]) for case in cases["cases"]
    }
    expected_units = {
        (case_id, challenge_type, repetition, arm)
        for case_id, challenge_type in case_types.items()
        for repetition in range(REPETITIONS)
        for arm in ARMS
    }
    observed_units = [
        (
            str(entry.get("case_id")),
            str(entry.get("challenge_type")),
            entry.get("repetition"),
            entry.get("arm"),
        )
        for entry in entries
    ]
    if len(set(observed_units)) != len(observed_units) or set(observed_units) != expected_units:
        raise ValueError("W11 work order does not cover each frozen case and arm exactly")
    if any(
        entry.get("control_policy")
        != expected_policy_by_type.get(str(entry.get("challenge_type")))
        for entry in entries
    ):
        raise ValueError("W11 work-order entry does not implement its control policy")

    http_engines = scope.get("http_control", {}).get("engines")
    research_engines = scope.get("research_arm", {}).get("initial_plan_engines")
    if (
        scope.get("scope_equal") is not True
        or scope.get("dispatches") != 0
        or not isinstance(http_engines, list)
        or not http_engines
        or http_engines != research_engines
        or scope.get("selected_engine_count") != len(http_engines)
    ):
        raise ValueError("scope-equivalence gate did not pass")

    _identity(arm.get("slopsearx", {}), label="arm manifest")
    if arm.get("arm") != "research":
        raise ValueError("Family A requires the isolated research arm")
    grants = arm.get("grants", {})
    isolation = arm.get("isolation", {})
    if grants.get("enabled") != EXPECTED_ENABLED_GRANTS or not all(isolation.values()):
        raise ValueError("research arm grants or isolation are invalid")
    expected_disabled = sorted(set(GRANTS) - set(EXPECTED_ENABLED_GRANTS))
    if (
        grants.get("disabled") != expected_disabled
        or grants.get("jobs") is not False
        or grants.get("science") is not False
        or grants.get("targeted_sensitive") is not False
    ):
        raise ValueError("research arm does not fail closed")

    _identity(preflight.get("slopsearx", {}), label="preflight")
    observed = preflight.get("slopsearx", {}).get("grants", {})
    expected_preflight_disabled = sorted(
        (set(GRANTS) | PREFLIGHT_ONLY_GRANTS) - {"research"}
    )
    if (
        observed.get("enabled") != EXPECTED_ENABLED_GRANTS
        or observed.get("disabled") != sorted(set(observed.get("disabled", [])))
        or observed.get("disabled") != expected_preflight_disabled
        or observed.get("targeted_sensitive_allowed") is not False
    ):
        raise ValueError("live preflight grants do not match the research arm")
    probes = preflight.get("mcp", {}).get("disabled_grant_probes", {})
    if set(probes) != set(expected_preflight_disabled) or any(
        item.get("error_code") != "tool_disabled" for item in probes.values()
    ):
        raise ValueError("live disabled-grant probes did not all fail closed")
    _identity(compatibility.get("slopsearx", {}), label="HTTP compatibility")
    if compatibility.get("valid") is not True or compatibility.get("issues") != []:
        raise ValueError("HTTP compatibility hard gate did not pass")

    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source commit must be a full lowercase Git SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", groktocrawl_source_commit):
        raise ValueError("GroktoCrawl source commit must be a full lowercase Git SHA")
    if set(groktocrawl_image_digests) != {"agent", "scraper"} or any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        for value in groktocrawl_image_digests.values()
    ):
        raise ValueError("GroktoCrawl agent and scraper images require full sha256 digests")
    if not model.strip():
        raise ValueError("model route must not be empty")

    result_limit = work_order["result_limit"]
    pair_count = case_count * REPETITIONS
    distinct_query_limit = pair_count * MAX_QUERIES_PER_TRIAL
    search_attempt_limit = expected_trials * MAX_QUERIES_PER_TRIAL * HTTP_ATTEMPTS
    engine_attempt_limit = search_attempt_limit * len(http_engines)
    admitted_result_limit = expected_trials * 8
    return {
        "schema_version": "enterprise-evaluation/w11-general-freeze/1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "evaluation_source_commit": source_commit,
        "groktocrawl": {
            "source_commit": groktocrawl_source_commit,
            "image_digests": groktocrawl_image_digests,
        },
        "model_route": model,
        "design": {
            "case_count": case_count,
            "repetitions": REPETITIONS,
            "arms": ARMS,
            "trial_count": expected_trials,
            "pair_count": pair_count,
            "work_order_seed": work_order.get("seed"),
            "result_limit_per_search": result_limit,
            "bootstrap_samples": 10_000,
            "bootstrap_unit": "case",
            "noninferiority_margins": {
                "weighted_claim_closure": -0.02,
                "admitted_source_precision": -0.05,
            },
        },
        "limits": {
            "model_calls": expected_trials * MAX_MODEL_CALLS_PER_TRIAL,
            "distinct_queries": distinct_query_limit,
            "search_attempts": search_attempt_limit,
            "engine_attempts": engine_attempt_limit,
            "admitted_results": admitted_result_limit,
            "elapsed_seconds": expected_trials * MAX_SECONDS_PER_PHASE * 2,
            "stored_bytes": expected_trials * MAX_CHECKPOINT_BYTES * 2,
            "interpretation": (
                "Experiment-wide upper bounds. Elapsed time covers retrieval and grading "
                "phases; stored bytes allow 5 MiB for each public and private checkpoint."
            ),
        },
        "w10_control": {
            "outcome": selection.get("outcome"),
            "selected_challenge_types": selection.get("selected_challenge_types", []),
            "policy_by_challenge_type": work_order.get("policy_by_challenge_type"),
            "w10_selection_sha256": digest(w10_selection_path),
            "w10_run_manifest_sha256": digest(w10_manifest_path),
            "cases_sha256": digest(cases_path),
        },
        "slopsearx": {
            "version": SLOPSEARX_VERSION,
            "source_revision": SLOPSEARX_SOURCE_REVISION,
            "image_digest": SLOPSEARX_IMAGE_DIGEST,
            "enabled_grants": EXPECTED_ENABLED_GRANTS,
            "engine_names": http_engines,
            "engine_scope_sha256": _sha256(http_engines),
            "operator_bounds": preflight["slopsearx"].get("policy_bounds"),
            "configuration_sha256": preflight.get("configuration_sha256"),
        },
        "input_sha256": {
            "work_order": digest(work_order_path),
            "scope_equivalence": digest(scope_path),
            "arm_manifest": digest(arm_manifest_path),
            "preflight": digest(preflight_path),
            "http_compatibility_before": digest(compatibility_path),
            "protocol": digest(protocol_path),
            "analysis_plan": digest(analysis_plan_path),
            "retrieval_runner": digest(runner_path),
            "grader": digest(grader_path),
            "retrieval_summarizer": digest(retrieval_summary_path),
            "quality_summarizer": digest(quality_summary_path),
        },
        "time_handling": {
            "clock": "UTC",
            "run_date": datetime.now(UTC).date().isoformat(),
            "case_as_of_values_sha256": _sha256(
                sorted(str(case.get("as_of")) for case in cases["cases"])
            ),
            "rule": "Use each case's frozen as_of value; do not move it to the execution date.",
        },
        "public_artifact": True,
    }


def write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--w10-selection", type=Path, required=True)
    parser.add_argument("--w10-run-manifest", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--work-order", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--arm-manifest", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--compatibility-before", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--groktocrawl-source-commit", required=True)
    parser.add_argument(
        "--groktocrawl-image-digest",
        action="append",
        required=True,
        metavar="SERVICE=SHA256",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--analysis-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    image_digests = {}
    for item in args.groktocrawl_image_digest:
        service, separator, value = item.partition("=")
        if not separator or service in image_digests:
            raise ValueError("GroktoCrawl image digests must be unique SERVICE=SHA256 pairs")
        image_digests[service] = value
    result = build_freeze(
        w10_selection_path=args.w10_selection,
        w10_manifest_path=args.w10_run_manifest,
        cases_path=args.cases,
        work_order_path=args.work_order,
        scope_path=args.scope,
        arm_manifest_path=args.arm_manifest,
        preflight_path=args.preflight,
        compatibility_path=args.compatibility_before,
        source_commit=args.source_commit,
        groktocrawl_source_commit=args.groktocrawl_source_commit,
        groktocrawl_image_digests=image_digests,
        model=args.model,
        protocol_path=args.protocol,
        analysis_plan_path=args.analysis_plan,
        runner_path=Path(__file__).with_name("run_w11_general_retrieval.py"),
        grader_path=Path(__file__).with_name("grade_w11_general_retrieval.py"),
        retrieval_summary_path=Path(__file__).with_name(
            "summarize_w11_general_retrieval.py"
        ),
        quality_summary_path=Path(__file__).with_name(
            "summarize_w11_general_quality.py"
        ),
    )
    write_exclusive(args.output, result)
    print(f"froze W11 Family A as {digest(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
