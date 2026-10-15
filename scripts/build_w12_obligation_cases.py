#!/usr/bin/env python3
"""Build the frozen synthetic replay corpus for W12.4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_case(
    case_id: str,
    stratum: str,
    kind: str,
    *,
    easy: bool = False,
    generic_closes: bool = False,
    unanswerable: bool = False,
) -> dict[str, Any]:
    obligation_id = f"{case_id}-obligation"
    answer_id = f"{case_id}-answer"
    initial_id = f"{case_id}-initial"
    generic_id = f"{case_id}-generic"
    targeted_id = f"{case_id}-targeted"
    required = initial_id if easy else answer_id
    queries = [
        {
            "query_id": f"{case_id}-q0",
            "purpose": "initial",
            "obligation_id": None,
            "candidate_ids": [initial_id],
        },
        {
            "query_id": f"{case_id}-q1",
            "purpose": "generic",
            "obligation_id": None,
            "candidate_ids": [generic_id],
        },
        {
            "query_id": f"{case_id}-q2",
            "purpose": "obligation",
            "obligation_id": obligation_id,
            "candidate_ids": [answer_id],
        },
    ]
    candidates = [
        {
            "candidate_id": initial_id,
            "query_id": f"{case_id}-q0",
            "canonical_id": initial_id,
            "publisher_id": f"{case_id}-initial-publisher",
            "closes_obligation_ids": [obligation_id] if easy else [],
            "acquired": True,
            "derivative": False,
            "material": easy,
        },
        {
            "candidate_id": generic_id,
            "query_id": f"{case_id}-q1",
            "canonical_id": generic_id,
            "publisher_id": f"{case_id}-initial-publisher",
            "closes_obligation_ids": [obligation_id] if generic_closes else [],
            "acquired": True,
            "derivative": not generic_closes,
            "material": generic_closes,
        },
        {
            "candidate_id": answer_id,
            "query_id": f"{case_id}-q2",
            "canonical_id": targeted_id,
            "publisher_id": f"{case_id}-authority",
            "closes_obligation_ids": [] if unanswerable else [obligation_id],
            "acquired": not unanswerable,
            "derivative": False,
            "material": not unanswerable,
        },
    ]
    return {
        "case_id": case_id,
        "stratum": stratum,
        "question": f"Frozen {stratum.replace('_', ' ')} research question {case_id}.",
        "obligations": [
            {
                "obligation_id": obligation_id,
                "kind": kind,
                "importance": 3 if stratum != "easy_stop" else 2,
                "closure_rule": f"Acquire the pinned evidence for {kind}.",
                "required_candidate_ids": [required],
            }
        ],
        "queries": queries,
        "candidates": candidates,
    }


def corpus() -> dict[str, Any]:
    specs = [
        ("easy-1", "easy_stop", "support", True, False, False),
        ("easy-2", "easy_stop", "support", True, False, False),
        ("primary-1", "missing_primary", "primary_source", False, False, False),
        ("primary-2", "missing_primary", "primary_source", False, True, False),
        ("fresh-1", "stale", "freshness", False, False, False),
        ("independent-1", "source_duplication", "independence", False, False, False),
        ("independent-2", "source_duplication", "independence", False, True, False),
        ("conflict-1", "contradiction", "contradiction", False, False, False),
        ("conflict-2", "contradiction", "contradiction", False, False, False),
        ("entity-1", "entity_identity", "entity_identity", False, False, False),
        ("low-rank-1", "low_rank", "support", False, True, False),
        ("unknown-1", "unanswerable", "support", False, False, True),
    ]
    return {
        "schema_version": "obligation-replay-corpus/1",
        "cases": [
            build_case(
                case_id,
                stratum,
                kind,
                easy=easy,
                generic_closes=generic,
                unanswerable=unanswerable,
            )
            for case_id, stratum, kind, easy, generic, unanswerable in specs
        ],
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
