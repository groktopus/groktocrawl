#!/usr/bin/env python3
"""Acquire and blindly grade W11 retrieval trials with the W10 policy rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.bounded_adaptive_policy import (
    CandidateAssessment,
    admit_candidate,
)

from scripts.run_w10_adaptive_policy import (
    assessment_schema,
    canonical_url,
    model_json,
    publisher_id,
    scrape,
)
from scripts.run_w11_general_retrieval import atomic_json

CASE_LIMIT_SECONDS = 180


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def register_candidates(retrieval: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for attempt, search in enumerate(retrieval):
        for rank, result in enumerate(search.get("results", []), 1):
            url = str(result.get("url", ""))
            canonical = canonical_url(url)
            if not canonical:
                continue
            origin = {"attempt": attempt, "rank": rank}
            if canonical in candidates:
                candidates[canonical]["search_origins"].append(origin)
                continue
            candidates[canonical] = {
                "url": url,
                "canonical_url": canonical,
                "publisher_id": publisher_id(url),
                "title": str(result.get("title", ""))[:500],
                "snippet": str(
                    result.get("description", result.get("content", result.get("snippet", "")))
                )[:1000],
                "search_origins": [origin],
                "acquisition_status": "not_selected",
                "reviewed_bytes_sha256": None,
                "reviewed_excerpt": "",
            }
    return candidates


def acquisition_order(
    candidates: dict[str, dict[str, Any]], *, policy: str
) -> list[tuple[str, int]]:
    if policy not in {"fixed", "full"}:
        raise ValueError("W11 general comparison accepts only fixed or full W10 policy")
    by_attempt: dict[int, list[str]] = {}
    for key, item in candidates.items():
        first = min(origin["attempt"] for origin in item["search_origins"])
        by_attempt.setdefault(first, []).append(key)
    selected: list[tuple[str, int]] = []
    initial_limit = 8 if policy == "fixed" else 4
    selected.extend((key, 0) for key in by_attempt.get(0, [])[:initial_limit])
    if policy == "full":
        for attempt in sorted(value for value in by_attempt if value > 0):
            selected.extend((key, attempt) for key in by_attempt[attempt][:2])
    seen = {key for key, _ in selected}
    for key in candidates:
        if len(selected) >= 8:
            break
        if key not in seen:
            selected.append((key, min(x["attempt"] for x in candidates[key]["search_origins"])))
            seen.add(key)
    return selected[:8]


def grade_trial(
    *,
    case: dict[str, Any],
    entry: dict[str, Any],
    retrieval: list[dict[str, Any]],
    api_client: httpx.Client,
    llm_client: httpx.Client,
    model: str,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    deadline = started + CASE_LIMIT_SECONDS
    candidates = register_candidates(retrieval)
    for key, attempt in acquisition_order(candidates, policy=entry["control_policy"]):
        origins = candidates[key]["search_origins"]
        candidates[key].update(scrape(api_client, candidates[key], deadline=deadline))
        candidates[key]["search_origins"] = origins
        candidates[key]["selected_on_attempt"] = attempt

    candidate_payload = []
    id_to_key: dict[str, str] = {}
    for key, item in candidates.items():
        if item["acquisition_status"] == "not_selected":
            continue
        candidate_id = digest(key)[:16]
        id_to_key[candidate_id] = key
        candidate_payload.append(
            {
                "candidate_id": candidate_id,
                "title": item["title"],
                "excerpt": item["reviewed_excerpt"],
                "available": item["acquisition_status"] == "acquired",
            }
        )
    blind_seed = int(digest(f"{seed}:{case['case_id']}:{entry['repetition']}")[:16], 16)
    random.Random(blind_seed).shuffle(candidate_payload)
    gap_ids = [claim["claim_id"] for claim in case["claims"]]
    assessment_wire, receipt = model_json(
        llm_client,
        model=model,
        name="w11_evidence_assessment",
        schema=assessment_schema(gap_ids, [item["candidate_id"] for item in candidate_payload]),
        prompt={
            "question": case["query"],
            "as_of": case["as_of"],
            "gaps": case["claims"],
            "candidates": candidate_payload,
            "instruction": (
                "Use each supplied candidate ID exactly once as a key in the candidates "
                "object and each gap ID exactly once as a key in the gaps object. Judge "
                "every candidate separately. Close a gap only from acquired content that "
                "meets its closure rule. A snippet alone is insufficient. Score currency, "
                "relevance, authority, accuracy, and purpose from 0 poor to 2 strong. "
                "Marginal value means material new evidence, better authority or currency, "
                "a resolved contradiction, or an independent publisher."
            ),
        },
        max_tokens=8000,
        deadline=deadline,
        reasoning_effort="low",
        use_completion_token_limit=True,
    )
    candidate_grades = assessment_wire.get("candidates")
    gap_grades = assessment_wire.get("gaps")
    if not isinstance(candidate_grades, dict) or set(candidate_grades) != set(id_to_key):
        raise ValueError("assessment must grade every candidate exactly once")
    if not isinstance(gap_grades, dict) or set(gap_grades) != set(gap_ids):
        raise ValueError("assessment must grade every gap exactly once")

    admitted_ids: list[str] = []
    admitted_canonicals: set[str] = set()
    admitted_publishers: set[str] = set()
    for key, source in candidates.items():
        candidate_id = digest(key)[:16]
        source["candidate_id"] = candidate_id
        grade = candidate_grades.get(candidate_id)
        if grade is None:
            source.update(admitted=False, admission_reason="not_selected_for_acquisition")
            continue
        admitted = source["acquisition_status"] == "acquired" and len(admitted_ids) < 8
        reason = "admitted_within_source_limit" if admitted else "source_limit"
        if entry["control_policy"] == "full":
            quality = grade["quality"]
            decision = admit_candidate(
                CandidateAssessment(
                    candidate_id=candidate_id,
                    relevant_gap_ids=tuple(grade["relevant_gap_ids"]),
                    source_quality=tuple(
                        quality[name]
                        for name in ("currency", "relevance", "authority", "accuracy", "purpose")
                    ),
                    canonical_id=key,
                    publisher_id=source["publisher_id"],
                    acquired=source["acquisition_status"] == "acquired",
                    supports_or_challenges=(
                        grade["supports_or_challenges"] and grade["marginal_value"]
                    ),
                    derivative=grade["derivative_of"] is not None,
                    improves_currency=grade["improves_currency"],
                    improves_authority=grade["improves_authority"],
                    resolves_contradiction=grade["resolves_contradiction"],
                ),
                admitted_canonical_ids=frozenset(admitted_canonicals),
                admitted_publishers=frozenset(admitted_publishers),
            )
            admitted, reason = decision.admitted, decision.reason
            if len(admitted_ids) >= 8:
                admitted, reason = False, "source_limit"
        source.update(operational_assessment=grade, admitted=admitted, admission_reason=reason)
        if admitted:
            admitted_ids.append(candidate_id)
            admitted_canonicals.add(key)
            admitted_publishers.add(source["publisher_id"])

    gaps = []
    for gap_id in gap_ids:
        grade = gap_grades[gap_id]
        supporting = [value for value in grade["candidate_ids"] if value in admitted_ids]
        status = grade["status"]
        if not supporting:
            status = "open"
        elif set(supporting) != set(grade["candidate_ids"]):
            status = "ambiguous"
        gaps.append({"gap_id": gap_id, **grade, "candidate_ids": supporting, "status": status})
    total_weight = sum(claim["importance"] for claim in case["claims"])
    closed = {gap["gap_id"] for gap in gaps if gap["status"] == "closed"}
    closed_weight = sum(
        claim["importance"] for claim in case["claims"] if claim["claim_id"] in closed
    )
    public_candidates = [
        {
            key: value
            for key, value in item.items()
            if key not in {"reviewed_excerpt", "acquisition_error"}
        }
        for item in candidates.values()
    ]
    public = {
        "schema_version": "enterprise-evaluation/w11-general-grade/1",
        "status": "completed",
        **{key: entry[key] for key in ("position", "case_id", "challenge_type", "repetition", "arm", "control_policy")},
        "candidates": public_candidates,
        "gap_results": gaps,
        "assessment_receipt": receipt,
        "metrics": {
            "candidate_count": len(candidates),
            "acquired_count": sum(item["acquisition_status"] == "acquired" for item in candidates.values()),
            "admitted_count": len(admitted_ids),
            "closed_weight": closed_weight,
            "total_weight": total_weight,
            "weighted_closure": closed_weight / total_weight,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "model_calls": 1,
        },
    }
    private = {
        "entry": entry,
        "assessment_wire": assessment_wire,
        "assessment_receipt": receipt,
        "acquisitions": [
            {
                "candidate_id": digest(key)[:16],
                "url": item["url"],
                "reviewed_excerpt": item["reviewed_excerpt"],
                "reviewed_bytes_sha256": item["reviewed_bytes_sha256"],
                "acquisition_status": item["acquisition_status"],
                "acquisition_error": item.get("acquisition_error"),
            }
            for key, item in candidates.items()
        ],
    }
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--retrieval-private", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--llm-base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int, default=11052026)
    args = parser.parse_args()
    if args.public_output.resolve() == args.private_output.resolve():
        raise ValueError("public and private output directories must differ")
    cases = {item["case_id"]: item for item in json.loads(args.cases.read_text())["cases"]}
    args.public_output.mkdir(parents=True, exist_ok=True)
    args.private_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.private_output.chmod(0o700)
    api_key = os.environ.get("LLM_API_KEY", "not-needed")
    failures = 0
    with (
        httpx.Client(base_url=args.api_base_url, timeout=180) as api_client,
        httpx.Client(
            base_url=args.llm_base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=180,
        ) as llm_client,
    ):
        for source_path in sorted(args.retrieval_private.glob("*.json")):
            source = json.loads(source_path.read_text())
            entry = source["entry"]
            public_path = args.public_output / source_path.name
            private_path = args.private_output / source_path.name
            if public_path.is_file() and private_path.is_file():
                continue
            if public_path.is_file() != private_path.is_file():
                raise RuntimeError(f"incomplete grading checkpoint: {source_path.name}")
            try:
                public, private = grade_trial(
                    case=cases[entry["case_id"]],
                    entry=entry,
                    retrieval=source["retrieval"],
                    api_client=api_client,
                    llm_client=llm_client,
                    model=args.model,
                    seed=args.seed,
                )
                atomic_json(private_path, private, mode=0o600)
                atomic_json(public_path, public)
            except Exception as error:
                failures += 1
                atomic_json(
                    private_path,
                    {"entry": entry, "error_type": type(error).__name__, "error": str(error)},
                    mode=0o600,
                )
                atomic_json(
                    public_path,
                    {
                        "schema_version": "enterprise-evaluation/w11-general-grade/1",
                        "status": "failed",
                        **entry,
                        "error_type": type(error).__name__,
                    },
                )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
