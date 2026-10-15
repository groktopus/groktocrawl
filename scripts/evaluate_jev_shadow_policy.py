#!/usr/bin/env python3
"""Offline, post-hoc policy analysis for the frozen Jev shadow receipts.

This module makes no provider calls and dispatches no searches or notices. Its
threshold sweep is descriptive only; it must not be used to tune a threshold
on the exposed cases.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

THRESHOLDS = (0.5, 0.8, 0.9, 0.95, 0.99)
ADDRESSABLE_STATUSES = {"contradiction", "unmet"}


def continuation_control(
    *,
    call_status: str,
    probability: Any,
    threshold: float,
    addressable_gap: bool,
) -> str:
    """Return a shadow action; never dispatch work or veto caller intent."""
    if (
        call_status != "completed"
        or isinstance(probability, bool)
        or not isinstance(probability, int | float)
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
    ):
        return "fallback_to_existing_caller_flow"
    if not addressable_gap:
        return "leave_unresolved_without_repeated_search"
    if probability >= threshold:
        return "candidate_bounded_search_for_caller"
    # Low probability or a received `uncertain` choice is not a no-search veto.
    return "defer_to_existing_caller_flow"


def monitoring_control(
    *, confirmed_change: bool, fetch_ok: bool, call_status: str
) -> str:
    """Preserve deterministic delivery; Jev may only add a shadow annotation."""
    if not fetch_ok:
        return "report_fetch_unavailable_without_semantic_judgment"
    if not confirmed_change:
        return "no_confirmed_change_to_assess"
    if call_status != "completed":
        return "deliver_deterministic_change_without_jev_annotation"
    return "deliver_deterministic_change_with_shadow_annotation"


def analyze(packet: dict[str, Any], receipts: dict[str, Any]) -> dict[str, Any]:
    cases = {case["id"]: case for case in packet["cases"]}
    records = {record["id"]: record for record in receipts["records"]}
    if set(cases) != set(records):
        raise ValueError("receipt IDs do not exactly match the frozen packet")
    validation = [
        case
        for case in packet["cases"]
        if case["study"] == "continuation" and case["split"] == "validation"
    ]
    calibration = [
        case
        for case in packet["cases"]
        if case["study"] == "continuation" and case["split"] == "calibration"
    ]

    def evidence_row(case: dict[str, Any]) -> dict[str, Any]:
        record = records[case["id"]]
        answer = record.get("answer") or {}
        reference = case["evaluation"]["reference"]
        probs = answer.get("probabilities") or {}
        return {
            "id": case["id"],
            "stratum": case["stratum"],
            "status": record.get("status"),
            "search_probability": probs.get("search"),
            "choice_argmax": answer.get("choice"),
            "confidence_separate_field": answer.get("confidence"),
            "reference_search_need": reference.get("search_needed"),
            "obligation_status": reference.get("status"),
        }

    rows = [evidence_row(case) for case in validation]
    sweep = []
    for threshold in THRESHOLDS:
        triggered = [
            row
            for row in rows
            if row["status"] == "completed"
            and isinstance(row["search_probability"], int | float)
            and row["search_probability"] >= threshold
        ]
        gated = [
            row for row in triggered if row["obligation_status"] in ADDRESSABLE_STATUSES
        ]
        sweep.append(
            {
                "threshold_inclusive": threshold,
                "probability_field": "probabilities.search",
                "threshold_triggered_ids": [row["id"] for row in triggered],
                "reference_yes_triggered_ids": [
                    row["id"]
                    for row in triggered
                    if row["reference_search_need"] == "yes"
                ],
                "reference_yes_missed_ids": [
                    row["id"]
                    for row in rows
                    if row["reference_search_need"] == "yes" and row not in triggered
                ],
                "reference_no_triggered_ids": [
                    row["id"]
                    for row in triggered
                    if row["reference_search_need"] == "no"
                ],
                "reference_uncertain_triggered_ids": [
                    row["id"]
                    for row in triggered
                    if row["reference_search_need"] == "uncertain"
                ],
                "addressability_gated_candidate_ids": [row["id"] for row in gated],
                "addressability_gated_reference_yes_missed_ids": [
                    row["id"]
                    for row in rows
                    if row["reference_search_need"] == "yes" and row not in gated
                ],
            }
        )
    return {
        "classification": "illustrative_post_hoc_exposed_case_sweep_not_threshold_selection",
        "provider_calls": 0,
        "searches_dispatched": 0,
        "monitoring_actions_dispatched": 0,
        "threshold_source": "answer.probabilities.search; never the separate confidence field",
        "validation_rows": rows,
        "calibration_rows_separate": [evidence_row(case) for case in calibration],
        "threshold_sweep": sweep,
        "policy_controls": {
            "uncertain_or_below_threshold_with_addressable_gap": "defer_to_existing_caller_flow; do not force-stop or veto",
            "failure_or_invalid_probability": "fallback_to_existing_caller_flow; never convert failure into no_search",
            "unanswerable_without_addressable_public_path": "leave unresolved without repeated search; not an endless-search instruction",
            "confirmed_change": "deterministic delivery remains; Jev may only add a shadow annotation",
            "fetch_failure": "report unavailable; do not infer immateriality",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--receipts", required=True, type=Path)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    receipts = json.loads(args.receipts.read_text(encoding="utf-8"))
    output = analyze(packet, receipts)
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
