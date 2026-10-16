#!/usr/bin/env python3
"""Build a secret-free proof summary for the W11 SlopSearX contract cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _results(path: Path) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        classname = str(case.get("classname", ""))
        parts = classname.split(".")
        module = "/".join(parts[:2]) + ".py"
        name = str(case.get("name", "")).split("[", 1)[0]
        class_parts = parts[2:]
        refs = [module, f"{module}::{name}"]
        if class_parts:
            refs.append(f"{module}::{'::'.join(class_parts)}::{name}")
        outcome = "failed" if case.find("failure") is not None or case.find("error") is not None else (
            "skipped" if case.find("skipped") is not None else "passed"
        )
        for ref in refs:
            results.setdefault(ref, []).append(outcome)
    return results


def _counts(path: Path) -> dict[str, int]:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    failed = sum(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    return {"tests": len(cases), "passed": len(cases) - failed - skipped, "failed": failed, "skipped": skipped}


def summarize(manifest: dict[str, Any], local: Path, valkey: Path) -> dict[str, Any]:
    local_results = _results(local)
    valkey_results = _results(valkey)
    cases = []
    unresolved = []
    for case in manifest["cases"]:
        references = []
        for ref in case["reference_tests"]:
            outcomes = local_results.get(ref, [])
            proof = "local"
            if not outcomes or all(outcome == "skipped" for outcome in outcomes):
                outcomes = valkey_results.get(ref, [])
                proof = "isolated_valkey"
            status = "passed" if outcomes and all(outcome == "passed" for outcome in outcomes) else "unproven"
            if status != "passed":
                unresolved.append(ref)
            references.append({"reference": ref, "status": status, "proof": proof})
        cases.append(
            {
                "case_id": case["case_id"],
                "family": case["family"],
                "status": "passed" if all(r["status"] == "passed" for r in references) else "unproven",
                "references": references,
            }
        )
    return {
        "schema_version": "enterprise-evaluation/w11-reference-contract-summary/1",
        "slopsearx_reference": manifest["slopsearx_reference"],
        "evidence": {
            "local_suite": {"sha256": _sha256(local), **_counts(local)},
            "isolated_valkey_suite": {"sha256": _sha256(valkey), **_counts(valkey)},
        },
        "case_count": len(cases),
        "all_cases_passed": not unresolved,
        "unresolved_references": sorted(set(unresolved)),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("local_junit", type=Path)
    parser.add_argument("valkey_junit", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summary = summarize(json.loads(args.manifest.read_text()), args.local_junit, args.valkey_junit)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["all_cases_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
