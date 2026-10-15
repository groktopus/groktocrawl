"""Fail-closed validation for the experimental comparison preflight manifest."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_COMPARISON_FIELDS = (
    "development_corpus",
    "held_out_corpus",
    "required_question_denominators",
    "semantic_rubric",
    "primary_reviewer",
    "adjudicating_reviewer",
    "hardware_and_limits",
    "paired_order_and_seeds",
    "quality_regression_bounds",
    "latency_regression_bounds",
    "resource_regression_bounds",
    "per_operation_and_run_budgets",
    "sample_uncertainty_plan",
)


def _get(mapping: Any, *keys: str) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def validate_manifest(manifest: dict[str, Any], root: Path) -> list[str]:
    """Return sanitized validation errors; an empty result authorizes inspection only."""
    errors: list[str] = []
    if manifest.get("schema_version") not in {
        "research-preflight-draft/1",
        "research-preflight/1",
    }:
        errors.append("schema_version is unsupported")
    if manifest.get("scope") != "magnus919/groktocrawl-x only":
        errors.append("scope must be fork-local")
    if manifest.get("decision_owner") != "Magnus Hedemark":
        errors.append("decision_owner is unresolved")
    if manifest.get("external_provider_budget_usd") != 0:
        errors.append("external_provider_budget_usd must remain zero")
    if manifest.get("hard_invariant_allowed_violations") != 0:
        errors.append("hard_invariant_allowed_violations must be zero")

    status = manifest.get("status")
    authorized = manifest.get("comparison_authorized")
    if status == "blocked_pending_review_and_baseline" and authorized:
        errors.append("blocked status cannot authorize comparison")
    if authorized is not True:
        errors.append("comparison_authorized is false")

    for arm in ("A", "B", "C"):
        record = manifest.get("arms", {}).get(arm)
        if not isinstance(record, dict):
            errors.append(f"arms.{arm} is missing")
            continue
        for field in ("status", "commit", "policy"):
            if record.get(field) in (None, ""):
                errors.append(f"arms.{arm}.{field} is unresolved")
        if arm == "C" and record.get("runtime_version") in (None, ""):
            errors.append("arms.C.runtime_version is unresolved")

    preflight = manifest.get("comparison_preflight")
    if not isinstance(preflight, dict):
        errors.append("comparison_preflight is missing")
    else:
        for field in REQUIRED_COMPARISON_FIELDS:
            if preflight.get(field) in (None, "", [], {}):
                errors.append(f"comparison_preflight.{field} is unresolved")

    pins = manifest.get("file_pins")
    if not isinstance(pins, list) or not pins:
        errors.append("file_pins must contain at least one pin")
    else:
        for index, pin in enumerate(pins):
            path = pin.get("path") if isinstance(pin, dict) else None
            digest = pin.get("sha256") if isinstance(pin, dict) else None
            if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
                errors.append(f"file_pins[{index}].path is unsafe")
                continue
            if not isinstance(digest, str) or HEX64.fullmatch(digest) is None:
                errors.append(f"file_pins[{index}].sha256 is invalid")
                continue
            candidate = root / path
            if not candidate.is_file():
                errors.append(f"file_pins[{index}] target is missing")
            elif hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
                errors.append(f"file_pins[{index}] digest does not match target")

    minima = manifest.get("proposed_protocol_minima")
    if not isinstance(minima, dict) or any(
        not isinstance(minima.get(field), int) or minima[field] <= 0
        for field in ("runtime_repetitions_per_workload", "quality_held_out_questions")
    ):
        errors.append("proposed protocol minima are unresolved")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest", nargs="?", type=Path, default=Path("docs/experiments/research-preflight.json")
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "errors": [f"manifest unreadable: {error.__class__.__name__}"]}))
        return 2
    errors = validate_manifest(manifest, args.root)
    print(json.dumps({"valid": not errors, "status": manifest.get("status"), "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
