#!/usr/bin/env python3
"""Validate the versioned W11 deterministic contract-case manifest."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_FAMILY_COUNTS = {
    "dependency_dossier": 4,
    "entity_projection": 2,
    "research_recovery": 7,
    "retrieval_provenance": 7,
    "saved_search": 5,
    "staged_search": 8,
    "workflow_composition": 7,
}
CASE_ID = re.compile(r"w11-[a-z0-9]+(?:-[a-z0-9]+)+")
TEST_REFERENCE = re.compile(r"tests/test_[a-z0-9_]+\.py(?:::[A-Za-z0-9_]+)*")


def validate_cases(payload: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ["manifest must be a JSON object"]
    if payload.get("schema_version") != "enterprise-evaluation/w11-contract-cases/1":
        issues.append("unexpected schema_version")
    reference = payload.get("slopsearx_reference")
    if not isinstance(reference, dict):
        issues.append("slopsearx_reference must be an object")
    else:
        if reference.get("version") != "0.5.0":
            issues.append("SlopSearX version must be 0.5.0")
        if re.fullmatch(r"[0-9a-f]{40}", str(reference.get("source_revision", ""))) is None:
            issues.append("SlopSearX source_revision must be an exact commit")
        if re.fullmatch(r"[0-9a-f]{40}", str(reference.get("proof_revision", ""))) is None:
            issues.append("SlopSearX proof_revision must be an exact commit")
    boundary = payload.get("scoring_boundary")
    if not isinstance(boundary, str) or "do not contribute" not in boundary:
        issues.append("contract scoring boundary is missing")

    cases = payload.get("cases")
    if not isinstance(cases, list):
        return [*issues, "cases must be a list"]
    ids: list[str] = []
    families: Counter[str] = Counter()
    for index, case in enumerate(cases):
        prefix = f"case {index}"
        if not isinstance(case, dict):
            issues.append(f"{prefix}: must be an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or CASE_ID.fullmatch(case_id) is None:
            issues.append(f"{prefix}: invalid case_id")
        else:
            ids.append(case_id)
            prefix = case_id
        family = case.get("family")
        if not isinstance(family, str) or family not in EXPECTED_FAMILY_COUNTS:
            issues.append(f"{prefix}: invalid family")
        else:
            families[family] += 1
        if not isinstance(case.get("scenario"), str) or not case["scenario"].strip():
            issues.append(f"{prefix}: scenario is missing")
        expected = case.get("expected")
        if (
            not isinstance(expected, list)
            or not expected
            or any(not isinstance(item, str) or not item for item in expected)
            or len(expected) != len(set(expected))
        ):
            issues.append(f"{prefix}: expected outcomes must be unique strings")
        references = case.get("reference_tests")
        if (
            not isinstance(references, list)
            or not references
            or any(
                not isinstance(item, str) or TEST_REFERENCE.fullmatch(item) is None
                for item in references
            )
        ):
            issues.append(f"{prefix}: reference_tests are missing or malformed")
    if len(ids) != len(set(ids)):
        issues.append("case IDs must be unique")
    if dict(families) != EXPECTED_FAMILY_COUNTS:
        issues.append(
            f"family counts {dict(sorted(families.items()))} do not match "
            f"{EXPECTED_FAMILY_COUNTS}"
        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    issues = validate_cases(json.loads(args.manifest.read_text()))
    if issues:
        print(json.dumps({"valid": False, "issues": issues}, indent=2))
        return 1
    print(json.dumps({"valid": True, "issues": []}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
