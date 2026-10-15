#!/usr/bin/env python3
"""Build the frozen W12.5 specialist-value corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def evidence(
    case_id: str, obligation: str, role: str, stance: str = "supports"
) -> dict[str, Any]:
    return {
        "evidence_id": f"{case_id}-{role}-{obligation}",
        "source_id": f"source-{case_id}-{role}-{obligation}",
        "canonical_source_id": f"canonical-{case_id}-{role}-{obligation}",
        "publisher_id": f"publisher-{case_id}-{role}",
        "obligation_id": obligation,
        "stance": stance,
        "span": f"Pinned {stance} evidence for {obligation} in {case_id}.",
        "derivative": False,
    }


def handoff(
    case_id: str, role: str, obligation_ids: list[str], items: list[dict[str, Any]]
) -> dict[str, Any]:
    sources = [item["source_id"] for item in items]
    return {
        "schema_version": "specialist-evidence-handoff/1",
        "assignment": {
            "specialist_id": f"{case_id}-{role}",
            "question": f"Investigate {', '.join(obligation_ids)} for {case_id}.",
            "obligation_ids": obligation_ids,
            "max_sources": 4,
        },
        "attempted_source_ids": sources,
        "acquired_source_ids": sources,
        "rejected_sources": {},
        "evidence": items,
        "supported_claims": [
            f"Supported {item['obligation_id']}"
            for item in items
            if item["stance"] == "supports"
        ],
        "challenged_claims": [
            f"Challenged {item['obligation_id']}"
            for item in items
            if item["stance"] == "challenges"
        ],
        "unresolved_obligation_ids": [],
        "source_independence_note": "Canonical and publisher identities are explicit.",
        "freshness_note": "Evidence is pinned to the case boundary.",
        "limitations": ["Synthetic evidence fixture."],
        "completion_state": "completed",
        "calls_used": 1,
    }


def build_case(
    case_id: str, stratum: str, separable: bool, contradiction: bool = False
) -> dict[str, Any]:
    obligations = [f"{case_id}-scope"]
    if separable:
        obligations.extend((f"{case_id}-authority", f"{case_id}-risk"))
    control_items = [evidence(case_id, obligations[0], "generalist")]
    if not separable:
        control_items.extend(
            evidence(case_id, item, "generalist") for item in obligations[1:]
        )
    control = {
        "admitted_evidence": control_items,
        "covered_obligation_ids": [item["obligation_id"] for item in control_items],
        "conflicting_obligation_ids": [],
        "rejected_evidence": {},
        "handoff_failures": [],
        "total_calls": 2,
    }
    handoffs: list[dict[str, Any]] = []
    if separable:
        discovery_items = [
            evidence(case_id, obligations[0], "discovery"),
            evidence(case_id, obligations[1], "authority"),
        ]
        risk_items = [evidence(case_id, obligations[2], "risk")]
        if contradiction:
            risk_items.append(
                evidence(case_id, obligations[0], "counter", "challenges")
            )
        handoffs = [
            handoff(case_id, "discovery", obligations[:2], discovery_items),
            handoff(case_id, "risk", [obligations[0], obligations[2]], risk_items),
        ]
    return {
        "case_id": case_id,
        "stratum": stratum,
        "separable": separable,
        "question": f"Produce trustworthy research for the pinned {stratum} case {case_id}.",
        "obligation_ids": obligations,
        "control_artifact": control,
        "specialist_handoffs": handoffs,
        "call_budget": 3,
        "seeded_contradiction_obligation_id": obligations[0] if contradiction else None,
    }


def corpus() -> dict[str, Any]:
    specs = [
        ("simple-1", "simple", False, False),
        ("simple-2", "simple", False, False),
        ("nonseparable-1", "nonseparable", False, False),
        ("compound-1", "compound", True, False),
        ("compound-2", "compound", True, False),
        ("conflict-1", "contradiction", True, True),
        ("conflict-2", "contradiction", True, True),
        ("temporal-1", "temporal", True, False),
        ("temporal-2", "temporal", True, False),
        ("identity-1", "source_identity", True, False),
        ("roles-1", "distinct_roles", True, False),
        ("roles-2", "distinct_roles", True, False),
    ]
    return {
        "schema_version": "specialist-value-corpus/1",
        "cases": [build_case(*spec) for spec in specs],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(corpus(), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
