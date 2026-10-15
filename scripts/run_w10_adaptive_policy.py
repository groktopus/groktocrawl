#!/usr/bin/env python3
"""Execute resumable W10 adaptive-policy retrieval trials."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import random
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.bounded_adaptive_policy import (
    CandidateAssessment,
    EvidenceGap,
    QueryProposal,
    WorkState,
    admit_candidate,
    gate_proposal,
    replay_policy_trace,
    stop_reason,
)

POLICIES = ("fixed", "unconstrained", "gap", "gated", "full")
CASE_LIMIT_SECONDS = 180
PURPOSES = (
    "missing_support",
    "contradiction",
    "freshness",
    "primary_source",
    "publisher_independence",
    "entity_identity",
)

CheckpointWriter = Callable[[dict[str, Any], list[dict[str, Any]]], None]


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"the {CASE_LIMIT_SECONDS}-second case limit was reached")
    return max(0.1, remaining)


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, parsed.query, ""))


def publisher_id(value: str) -> str:
    host = (urlsplit(value).hostname or "").casefold()
    return host.removeprefix("www.")


def trial_evidence_checkpoint(
    *,
    case: dict[str, Any],
    policy: str,
    repetition: int,
    stage: str,
    attempts: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    initial_gap_assessment: list[dict[str, Any]] | None = None,
    interim_assessment: dict[str, Any] | None = None,
    received_assessment: dict[str, Any] | None = None,
    received_assessment_receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split resumable trial evidence into tracked metadata and private excerpts."""
    public_candidates = [
        {key: value for key, value in item.items() if key != "reviewed_excerpt"}
        for item in candidates.values()
    ]
    private_candidates = [
        {
            "candidate_id": digest(key)[:16],
            "canonical_url": key,
            "url": item["url"],
            "accessed_at": item.get("accessed_at", item["observed_at"]),
            "acquisition_status": item["acquisition_status"],
            "selected_on_attempt": item.get("selected_on_attempt"),
            "reviewed_bytes_sha256": item.get("reviewed_bytes_sha256"),
            "reviewed_excerpt": item.get("reviewed_excerpt", ""),
            "exclusion_reason": item.get("acquisition_error"),
        }
        for key, item in candidates.items()
    ]
    return (
        {
            "schema_version": "enterprise-evaluation/w10-policy-checkpoint/1",
            "case_id": case["case_id"],
            "challenge_type": case.get("challenge_type", case.get("category")),
            "policy": policy,
            "repetition": repetition,
            "stage": stage,
            "attempts": attempts,
            "proposals": proposals,
            "initial_gap_assessment": initial_gap_assessment,
            "interim_assessment": interim_assessment,
            "received_assessment": received_assessment,
            "received_assessment_receipt": received_assessment_receipt,
            "candidates": public_candidates,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
        private_candidates,
    )


def build_work_order(
    cases: list[dict[str, Any]],
    policies: list[str],
    repetitions: int,
    seed: int,
) -> list[tuple[dict[str, Any], str, int]]:
    """Rotate policy order inside each case while shuffling case order by repetition."""
    work: list[tuple[dict[str, Any], str, int]] = []
    bases: dict[str, list[str]] = {}
    for case in cases:
        order = list(policies)
        case_seed = int(digest(f"{seed}:{case['case_id']}:policies")[:16], 16)
        random.Random(case_seed).shuffle(order)
        bases[case["case_id"]] = order
    for repetition in range(repetitions):
        case_order = list(cases)
        random.Random(seed + repetition).shuffle(case_order)
        for case in case_order:
            base = bases[case["case_id"]]
            offset = repetition % len(base)
            rotated = base[offset:] + base[:offset]
            work.extend((case, policy, repetition) for policy in rotated)
    return work


def model_json(
    client: httpx.Client,
    *,
    model: str,
    name: str,
    schema: dict[str, Any],
    prompt: dict[str, Any],
    max_tokens: int,
    deadline: float,
    reasoning_effort: str | None = None,
    use_completion_token_limit: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    try:
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative research evaluator. Use only the "
                        "provided material. Return JSON matching the schema."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "temperature": 0,
            (
                "max_completion_tokens"
                if use_completion_token_limit
                else "max_tokens"
            ): max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
        response = client.post(
            "/chat/completions",
            json=request,
            timeout=remaining_seconds(deadline),
        )
        response.raise_for_status()
        envelope = response.json()
        content = envelope["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as error:
        raise RuntimeError(f"{name}: {type(error).__name__}: {error}") from error
    return parsed, {
        "latency_ms": round((time.monotonic() - started) * 1000, 3),
        "model": envelope.get("model"),
        "usage": envelope.get("usage") or {},
        "response_sha256": digest(content),
    }


def search(
    client: httpx.Client, query: str, limit: int, *, deadline: float
) -> tuple[list[dict[str, Any]], float]:
    started = time.monotonic()
    response = client.post(
        "/v2/search",
        json={"query": query, "limit": limit, "search_type": "fast"},
        timeout=remaining_seconds(deadline),
    )
    response.raise_for_status()
    payload = response.json()
    results = (payload.get("data") or {}).get("web") or []
    if not payload.get("success") or not isinstance(results, list):
        raise RuntimeError("search returned no valid result list")
    return results[:limit], round((time.monotonic() - started) * 1000, 3)


def scrape(
    client: httpx.Client, result: dict[str, Any], *, deadline: float
) -> dict[str, Any]:
    started = time.monotonic()
    url = str(result.get("url", ""))
    record: dict[str, Any] = {
        "url": url,
        "canonical_url": canonical_url(url),
        "publisher_id": publisher_id(url),
        "title": str(result.get("title", ""))[:500],
        "snippet": str(result.get("description", result.get("content", "")))[:1000],
        "accessed_at": datetime.now(UTC).isoformat(),
    }
    try:
        response = client.post(
            "/v2/scrape",
            json={"url": url, "formats": ["markdown"]},
            timeout=remaining_seconds(deadline),
        )
        response.raise_for_status()
        payload = response.json()
        markdown = str((payload.get("data") or {}).get("markdown", ""))
        if not payload.get("success") or not markdown:
            raise RuntimeError("empty scrape")
        record.update(
            acquisition_status="acquired",
            reviewed_bytes_sha256=digest(markdown),
            reviewed_excerpt=markdown[:4000],
        )
    except Exception as error:
        record.update(
            acquisition_status="unavailable",
            reviewed_bytes_sha256=None,
            reviewed_excerpt="",
            acquisition_error=f"{type(error).__name__}: {error}"[:500],
        )
    record["acquisition_ms"] = round((time.monotonic() - started) * 1000, 3)
    return record


def search_record(result: dict[str, Any], *, attempt: int, rank: int) -> dict[str, Any]:
    url = str(result.get("url", ""))
    return {
        "url": url,
        "canonical_url": canonical_url(url),
        "publisher_id": publisher_id(url),
        "title": str(result.get("title", ""))[:500],
        "snippet": str(result.get("description", result.get("content", "")))[:1000],
        "observed_at": datetime.now(UTC).isoformat(),
        "search_origins": [{"attempt": attempt, "rank": rank}],
        "acquisition_status": "not_selected",
        "reviewed_bytes_sha256": None,
        "reviewed_excerpt": "",
    }


def planning_schema(gap_ids: list[str], *, unconstrained: bool) -> dict[str, Any]:
    allowed_gaps = [*gap_ids, "general"] if unconstrained else gap_ids
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["initial_gaps", "proposals"],
        "properties": {
            "initial_gaps": {
                "type": "array",
                "minItems": len(gap_ids),
                "maxItems": len(gap_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["gap_id", "status", "reason"],
                    "properties": {
                        "gap_id": {"type": "string", "enum": gap_ids},
                        "status": {
                            "type": "string",
                            "enum": ["open", "closed", "ambiguous"],
                        },
                        "reason": {"type": "string"},
                    },
                },
            },
            "proposals": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query", "gap_id", "predicted_evidence", "purpose"],
                    "properties": {
                        "query": {"type": "string"},
                        "gap_id": {"type": "string", "enum": allowed_gaps},
                        "predicted_evidence": {"type": "string"},
                        "purpose": {"type": "string", "enum": list(PURPOSES)},
                    },
                },
            },
        },
    }


def assessment_schema(gap_ids: list[str], candidate_ids: list[str]) -> dict[str, Any]:
    quality = {
        "type": "object",
        "additionalProperties": False,
        "required": ["currency", "relevance", "authority", "accuracy", "purpose"],
        "properties": {
            key: {"type": "integer", "minimum": 0, "maximum": 2}
            for key in ("currency", "relevance", "authority", "accuracy", "purpose")
        },
    }
    candidate_grade = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "relevant_gap_ids",
            "supports_or_challenges",
            "quality",
            "derivative_of",
            "marginal_value",
            "improves_currency",
            "improves_authority",
            "resolves_contradiction",
            "reason",
        ],
        "properties": {
            "relevant_gap_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": gap_ids},
            },
            "supports_or_challenges": {"type": "boolean"},
            "quality": quality,
            "derivative_of": {
                "type": ["string", "null"],
                "enum": [*candidate_ids, None],
            },
            "marginal_value": {"type": "boolean"},
            "improves_currency": {"type": "boolean"},
            "improves_authority": {"type": "boolean"},
            "resolves_contradiction": {"type": "boolean"},
            "reason": {"type": "string", "maxLength": 240},
        },
    }
    gap_grade = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "candidate_ids", "reason"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["open", "closed", "ambiguous"],
            },
            "candidate_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": candidate_ids},
            },
            "reason": {"type": "string", "maxLength": 240},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates", "gaps"],
        "properties": {
            "candidates": {
                "type": "object",
                "additionalProperties": False,
                "required": candidate_ids,
                "properties": dict.fromkeys(candidate_ids, candidate_grade),
            },
            "gaps": {
                "type": "object",
                "additionalProperties": False,
                "required": gap_ids,
                "properties": dict.fromkeys(gap_ids, gap_grade),
            },
        },
    }


def resolve_terminal_stop_reason(
    *,
    policy: str,
    computed_stop: str,
    planner_claimed_complete: bool,
    proposals: list[dict[str, Any]],
) -> str:
    """Turn an exhausted policy path into an explicit terminal reason."""
    if computed_stop != "continue":
        return computed_stop
    if policy == "fixed":
        return "fixed_query_complete"
    if planner_claimed_complete:
        return "planner_claimed_complete"
    if not proposals:
        return "no_followup_proposed"
    if not any(item.get("executed", False) for item in proposals):
        return "no_admitted_proposal"
    return "proposal_exhausted"


def apply_planner_gap_state(
    gaps: tuple[EvidenceGap, ...], assessment: list[dict[str, Any]]
) -> tuple[EvidenceGap, ...]:
    """Validate and apply the planner's initial open/closed gap judgments."""
    expected = {gap.gap_id for gap in gaps}
    observed = [item["gap_id"] for item in assessment]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        raise ValueError("planning must return every gap exactly once")
    statuses = {item["gap_id"]: item["status"] for item in assessment}
    return tuple(
        EvidenceGap(
            gap.gap_id,
            gap.importance,
            gap.closure_rule,
            statuses[gap.gap_id] == "closed",
        )
        for gap in gaps
    )


def execute_trial(
    case: dict[str, Any],
    policy: str,
    repetition: int,
    *,
    api_client: httpx.Client,
    llm_client: httpx.Client,
    model: str,
    result_limit: int,
    seed: int,
    checkpoint_writer: CheckpointWriter | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + CASE_LIMIT_SECONDS
    claims = case["claims"]
    blind_seed = int(digest(f"{seed}:{case['case_id']}:{policy}:{repetition}")[:16], 16)
    gaps = tuple(
        EvidenceGap(item["claim_id"], item["importance"], item["closure_rule"])
        for item in claims
    )
    attempts: list[dict[str, Any]] = []
    initial, latency = search(
        api_client, case["query"], result_limit, deadline=deadline
    )
    attempts.append(
        {
            "query": case["query"],
            "purpose": "initial",
            "latency_ms": latency,
            "result_count": len(initial),
        }
    )
    candidates: dict[str, dict[str, Any]] = {}

    def register(results: list[dict[str, Any]], attempt: int) -> list[str]:
        keys: list[str] = []
        for rank, result in enumerate(results, 1):
            item = search_record(result, attempt=attempt, rank=rank)
            key = item["canonical_url"]
            if not key:
                continue
            if key in candidates:
                candidates[key]["search_origins"].extend(item["search_origins"])
            else:
                candidates[key] = item
            keys.append(key)
        return list(dict.fromkeys(keys))

    def acquire(
        keys: list[str], limit: int, *, selected_on_attempt: int | None
    ) -> None:
        selected = [
            key
            for key in keys
            if candidates[key]["acquisition_status"] == "not_selected"
        ][:limit]
        for key in selected:
            origins = candidates[key]["search_origins"]
            candidates[key].update(
                scrape(api_client, candidates[key], deadline=deadline)
            )
            candidates[key]["search_origins"] = origins
            candidates[key]["selected_on_attempt"] = selected_on_attempt

    initial_keys = register(initial, 0)
    acquire(
        initial_keys,
        8 if policy == "fixed" else 4,
        selected_on_attempt=0,
    )

    planning = None
    planning_receipt = None
    proposal_log: list[dict[str, Any]] = []
    round_decisions: list[dict[str, Any]] = []
    interim_assessment = None
    interim_assessment_receipt = None
    planner_claimed_complete = False

    def checkpoint(
        stage: str,
        *,
        received_assessment: dict[str, Any] | None = None,
        assessment_receipt: dict[str, Any] | None = None,
    ) -> None:
        if checkpoint_writer is None:
            return
        checkpoint_writer(
            *trial_evidence_checkpoint(
                case=case,
                policy=policy,
                repetition=repetition,
                stage=stage,
                attempts=attempts,
                proposals=proposal_log,
                candidates=candidates,
                initial_gap_assessment=(
                    planning.get("initial_gaps") if planning is not None else None
                ),
                interim_assessment=interim_assessment,
                received_assessment=received_assessment,
                received_assessment_receipt=assessment_receipt,
            )
        )

    def assess_current_candidates(
        *, stage: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
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
        random.Random(blind_seed).shuffle(candidate_payload)
        assessment_wire, receipt = model_json(
            llm_client,
            model=model,
            name=f"w10_evidence_assessment_{stage}",
            schema=assessment_schema(
                [gap.gap_id for gap in gaps],
                [item["candidate_id"] for item in candidate_payload],
            ),
            prompt={
                "question": case["query"],
                "as_of": case["as_of"],
                "gaps": claims,
                "candidates": candidate_payload,
                "instruction": (
                    "Use each supplied candidate ID exactly once as a key in the "
                    "candidates object and each gap ID exactly once as a key in the "
                    "gaps object. Judge every candidate separately. Close a gap only "
                    "from acquired "
                    "content that meets its closure rule. A snippet alone is insufficient. "
                    "Score each quality dimension as 0 poor, 1 adequate, or 2 strong. "
                    "Marginal value means the source adds material claim evidence beyond "
                    "the other candidates, improves authority or currency, resolves a "
                    "contradiction, or supplies an independent publisher. Use candidate "
                    "IDs in derivative_of. Keep reasons concrete and brief."
                ),
            },
            max_tokens=4000,
            deadline=deadline,
        )
        checkpoint(
            f"{stage}_assessment_received",
            received_assessment=assessment_wire,
            assessment_receipt=receipt,
        )
        candidate_grades = assessment_wire.get("candidates")
        gap_grades = assessment_wire.get("gaps")
        if not isinstance(candidate_grades, dict) or set(candidate_grades) != set(
            id_to_key
        ):
            raise ValueError("assessment must return every candidate exactly once")
        gap_order = [gap.gap_id for gap in gaps]
        if not isinstance(gap_grades, dict) or set(gap_grades) != set(gap_order):
            raise ValueError("assessment must return every gap exactly once")
        assessment = {
            "candidates": [
                {"candidate_id": candidate_id, **candidate_grades[candidate_id]}
                for candidate_id in (
                    item["candidate_id"] for item in candidate_payload
                )
            ],
            "gaps": [
                {"gap_id": gap_id, **gap_grades[gap_id]} for gap_id in gap_order
            ],
        }
        return assessment, receipt, id_to_key

    checkpoint("initial_acquisition")
    if policy != "fixed":
        blind_initial = [
            {
                "candidate_id": digest(key)[:16],
                "title": item["title"],
                "excerpt": item["reviewed_excerpt"],
                "available": item["acquisition_status"] == "acquired",
            }
            for key, item in candidates.items()
            if item["acquisition_status"] != "not_selected"
        ]
        planning, planning_receipt = model_json(
            llm_client,
            model=model,
            name="w10_query_plan",
            schema=planning_schema(
                [gap.gap_id for gap in gaps], unconstrained=policy == "unconstrained"
            ),
            prompt={
                "question": case["query"],
                "as_of": case["as_of"],
                "gaps": claims,
                "initial_sources": blind_initial,
                "policy": (
                    "Propose broad follow-up searches wherever more information may help."
                    if policy == "unconstrained"
                    else "Tie every proposal to one still-open declared gap."
                ),
                "instruction": "Do not answer the research question.",
            },
            max_tokens=1400,
            deadline=deadline,
        )
        planning_gaps = apply_planner_gap_state(gaps, planning["initial_gaps"])
        all_closed = planning_gaps and all(gap.closed for gap in planning_gaps)
        planner_claimed_complete = bool(all_closed)
        if not all_closed:
            prior: tuple[str, ...] = (case["query"],)
            executed_followups = 0
            stop_remaining = False
            for raw in planning["proposals"]:
                if stop_remaining:
                    proposal_log.append(
                        {
                            **raw,
                            "admitted": False,
                            "executed": False,
                            "reason": "stopped_after_prior_round",
                        }
                    )
                    continue
                admitted, reason = True, "policy_has_no_proposal_gate"
                if policy in {"gated", "full"}:
                    proposal = QueryProposal(**raw)
                    proposal_decision = gate_proposal(
                        proposal,
                        original_query=case["query"],
                        gaps=planning_gaps,
                        prior_queries=prior,
                    )
                    admitted, reason = (
                        proposal_decision.admitted,
                        proposal_decision.reason,
                    )
                proposal_log.append(
                    {
                        **raw,
                        "admitted": admitted,
                        "executed": admitted,
                        "reason": reason,
                    }
                )
                if admitted:
                    executed_followups += 1
                    results, query_ms = search(
                        api_client, raw["query"], result_limit, deadline=deadline
                    )
                    attempts.append(
                        {
                            "query": raw["query"],
                            "purpose": raw["purpose"],
                            "gap_id": raw["gap_id"],
                            "latency_ms": query_ms,
                            "result_count": len(results),
                        }
                    )
                    followup_keys = register(results, len(attempts) - 1)
                    acquire(
                        followup_keys,
                        2,
                        selected_on_attempt=len(attempts) - 1,
                    )
                    checkpoint("followup_acquisition")
                    prior = (*prior, raw["query"])
                    if policy == "full" and executed_followups == 1:
                        (
                            interim_assessment,
                            interim_assessment_receipt,
                            interim_id_to_key,
                        ) = assess_current_candidates(stage="round_1")
                        checkpoint(
                            "round_1_assessment_preserved",
                            received_assessment=interim_assessment,
                            assessment_receipt=interim_assessment_receipt,
                        )
                        interim_by_id = {
                            item["candidate_id"]: item
                            for item in interim_assessment["candidates"]
                        }
                        round_ids = {
                            digest(key)[:16]
                            for key, item in candidates.items()
                            if item["acquisition_status"] == "acquired"
                            and item.get("selected_on_attempt") == 1
                        }
                        evidence_gain = any(
                            (item["supports_or_challenges"] and item["marginal_value"])
                            or item["improves_currency"]
                            or item["improves_authority"]
                            or item["resolves_contradiction"]
                            for candidate_id, item in interim_by_id.items()
                            if candidate_id in round_ids
                        )
                        all_closed_after_round = bool(
                            interim_assessment["gaps"]
                        ) and all(
                            item["status"] == "closed"
                            for item in interim_assessment["gaps"]
                        )
                        stop_after_round = all_closed_after_round or not evidence_gain
                        round_decisions.append(
                            {
                                "round": 1,
                                "executed_query": raw["query"],
                                "acquired_candidate_ids": sorted(
                                    round_ids & set(interim_id_to_key)
                                ),
                                "evidence_gain": evidence_gain,
                                "all_gaps_closed": all_closed_after_round,
                                "decision": "stop" if stop_after_round else "continue",
                                "reason": (
                                    "all_gaps_closed"
                                    if all_closed_after_round
                                    else "no_marginal_gain"
                                    if not evidence_gain
                                    else "marginal_gain"
                                ),
                            }
                        )
                        if stop_after_round:
                            stop_remaining = True

    acquire(
        list(candidates),
        8
        - sum(
            item["acquisition_status"] != "not_selected" for item in candidates.values()
        ),
        selected_on_attempt=None,
    )
    checkpoint("acquisition_complete")

    assessment, assessment_receipt, _ = assess_current_candidates(stage="final")
    assessed = {item["candidate_id"]: item for item in assessment["candidates"]}
    admitted_ids: list[str] = []
    admitted_canonical_ids: set[str] = set()
    admitted_publishers: set[str] = set()
    for canonical, source in candidates.items():
        candidate_id = digest(canonical)[:16]
        source["candidate_id"] = candidate_id
        if source["acquisition_status"] == "not_selected":
            source["admitted"] = False
            source["admission_reason"] = "not_selected_for_acquisition"
            continue
        item = assessed[candidate_id]
        admitted = source["acquisition_status"] == "acquired" and len(admitted_ids) < 8
        admission_reason = (
            "admitted_within_source_limit" if admitted else "source_limit"
        )
        if policy == "full":
            quality = item["quality"]
            candidate_decision = admit_candidate(
                CandidateAssessment(
                    candidate_id=candidate_id,
                    relevant_gap_ids=tuple(item["relevant_gap_ids"]),
                    source_quality=tuple(
                        quality[key]
                        for key in (
                            "currency",
                            "relevance",
                            "authority",
                            "accuracy",
                            "purpose",
                        )
                    ),
                    canonical_id=canonical,
                    publisher_id=source["publisher_id"],
                    acquired=source["acquisition_status"] == "acquired",
                    supports_or_challenges=(
                        item["supports_or_challenges"] and item["marginal_value"]
                    ),
                    derivative=item["derivative_of"] is not None,
                    improves_currency=item["improves_currency"],
                    improves_authority=item["improves_authority"],
                    resolves_contradiction=item["resolves_contradiction"],
                ),
                admitted_canonical_ids=frozenset(admitted_canonical_ids),
                admitted_publishers=frozenset(admitted_publishers),
            )
            admitted = candidate_decision.admitted
            admission_reason = candidate_decision.reason
            if len(admitted_ids) >= 8:
                admitted = False
                admission_reason = "source_limit"
        source["operational_assessment"] = item
        source["admitted"] = admitted
        source["admission_reason"] = admission_reason
        if admitted:
            admitted_ids.append(candidate_id)
            admitted_canonical_ids.add(canonical)
            admitted_publishers.add(source["publisher_id"])
    gap_results = []
    for item in assessment["gaps"]:
        supporting = [value for value in item["candidate_ids"] if value in admitted_ids]
        if not supporting:
            status = "open"
        elif set(supporting) != set(item["candidate_ids"]):
            status = "ambiguous"
        else:
            status = item["status"]
        gap_results.append({**item, "candidate_ids": supporting, "status": status})
    total_weight = sum(item["importance"] for item in claims)
    closed = {item["gap_id"] for item in gap_results if item["status"] == "closed"}
    closed_weight = sum(
        item["importance"] for item in claims if item["claim_id"] in closed
    )
    final_gaps = tuple(
        EvidenceGap(
            gap.gap_id,
            gap.importance,
            gap.closure_rule,
            gap.gap_id in closed,
        )
        for gap in gaps
    )
    model_call_count = (
        int(planning is not None) + 1 + int(interim_assessment is not None)
    )
    if round_decisions and round_decisions[-1]["decision"] == "stop":
        computed_stop = round_decisions[-1]["reason"]
    else:
        computed_stop = stop_reason(
            final_gaps,
            WorkState(
                searches=len(attempts),
                model_calls=model_call_count,
                admitted_sources=len(admitted_ids),
                elapsed_ms=round((time.monotonic() - started) * 1000),
                newly_closed_weight=closed_weight,
                quality_gain=any(
                    item["quality"]["authority"] >= 2 for item in assessed.values()
                ),
                contradiction_gain=any(
                    item["supports_or_challenges"] for item in assessed.values()
                ),
                publisher_gain=False,
            ),
            max_model_calls=3,
        )
    stop = resolve_terminal_stop_reason(
        policy=policy,
        computed_stop=computed_stop,
        planner_claimed_complete=planner_claimed_complete,
        proposals=proposal_log,
    )
    if stop == "continue":
        raise RuntimeError("completed trial retained a nonterminal stop reason")
    public_candidates = [
        {key: value for key, value in item.items() if key != "reviewed_excerpt"}
        for item in candidates.values()
    ]
    policy_events = (
        *(
            {"type": "search", "query": item["query"], "purpose": item["purpose"]}
            for item in attempts
        ),
        *(
            {
                "type": "proposal",
                "gap_id": item["gap_id"],
                "admitted": item["admitted"],
                "reason": item["reason"],
            }
            for item in proposal_log
        ),
        *(
            {
                "type": "round_decision",
                "round": item["round"],
                "decision": item["decision"],
                "reason": item["reason"],
            }
            for item in round_decisions
        ),
        *(
            {
                "type": "candidate",
                "candidate_id": item["candidate_id"],
                "admitted": item["admitted"],
            }
            for item in public_candidates
        ),
        *(
            {"type": "gap", "gap_id": item["gap_id"], "status": item["status"]}
            for item in gap_results
        ),
        {"type": "stop", "reason": stop},
    )
    event_digests = replay_policy_trace(tuple(policy_events))
    return {
        "schema_version": "enterprise-evaluation/w10-policy-trial/1",
        "case_id": case["case_id"],
        "challenge_type": case.get("challenge_type", case.get("category")),
        "policy": policy,
        "repetition": repetition,
        "status": "completed",
        "attempts": attempts,
        "proposals": proposal_log,
        "initial_gap_assessment": (
            planning.get("initial_gaps") if planning is not None else None
        ),
        "interim_assessment": interim_assessment,
        "candidates": public_candidates,
        "gap_results": gap_results,
        "metrics": {
            "searches": len(attempts),
            "model_calls": model_call_count,
            "candidate_count": len(candidates),
            "acquired_count": sum(
                item["acquisition_status"] == "acquired" for item in candidates.values()
            ),
            "admitted_count": len(admitted_ids),
            "closed_weight": closed_weight,
            "total_weight": total_weight,
            "weighted_closure": closed_weight / total_weight,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        },
        "stop_reason": stop,
        "round_decisions": round_decisions,
        "orchestration": {
            "runtime": "langgraph",
            "event_count": len(event_digests),
            "event_digests": list(event_digests),
        },
        "planning_receipt": planning_receipt,
        "assessment_receipt": assessment_receipt,
        "interim_assessment_receipt": interim_assessment_receipt,
        "blind_grade": {
            "candidate_order_seed_sha256": digest(str(blind_seed)),
            "policy_and_rank_labels_exposed": False,
        },
        "private_acquisitions": trial_evidence_checkpoint(
            case=case,
            policy=policy,
            repetition=repetition,
            stage="completed",
            attempts=attempts,
            proposals=proposal_log,
            candidates=candidates,
            initial_gap_assessment=(
                planning.get("initial_gaps") if planning is not None else None
            ),
            interim_assessment=interim_assessment,
        )[1],
    }


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=os.environ.get("GROKTOCRAWL_API_KEY", ""))
    parser.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL", ""))
    parser.add_argument("--llm-api-key", default=os.environ.get("LLM_API_KEY", ""))
    parser.add_argument("--model", default="local")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--result-limit", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument(
        "--policies", nargs="+", choices=POLICIES, default=list(POLICIES)
    )
    args = parser.parse_args()
    if not args.api_key or not args.llm_base_url or not args.llm_api_key:
        parser.error("API and LLM credentials are required")
    source_commit = os.environ.get("W10_SOURCE_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        parser.error("W10_SOURCE_COMMIT must contain the exact 40-character commit")
    try:
        langgraph_version = version("langgraph")
    except PackageNotFoundError:
        parser.error("the isolated experiment environment needs langgraph==0.6.11")
    if langgraph_version != "0.6.11":
        parser.error(
            f"expected langgraph==0.6.11, found langgraph=={langgraph_version}"
        )
    started_at = datetime.now(UTC).isoformat()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    freeze_path = ROOT / "docs/experiments/adaptive-policy/w10-freeze.json"
    search_environment_path = (
        ROOT / "docs/experiments/adaptive-policy/w10-search-environment.json"
    )
    search_environment = json.loads(search_environment_path.read_text())
    if search_environment.get("schema_version") != (
        "enterprise-evaluation/w10-search-environment/1"
    ):
        parser.error("W10 search environment record has an unsupported schema")
    search_environment_sha256 = digest(search_environment_path.read_bytes())
    atomic_json(
        args.output / "run-metadata.json",
        {
            "schema_version": "enterprise-evaluation/w10-run-metadata/1",
            "source_commit": source_commit,
            "started_at": started_at,
            "cases_sha256": digest(args.cases.read_bytes()),
            "freeze_sha256": digest(freeze_path.read_bytes()),
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "search_environment_sha256": search_environment_sha256,
        },
    )
    records = args.output / "records"
    records.mkdir(exist_ok=True, mode=0o700)
    payload = json.loads(args.cases.read_text())
    work = build_work_order(
        payload["cases"], args.policies, args.repetitions, args.seed
    )
    order_payload = {
        "schema_version": "enterprise-evaluation/w10-work-order/1",
        "method": "seeded_case_shuffle_with_per_case_policy_rotation",
        "seed": args.seed,
        "entries": [
            {
                "position": position,
                "case_id": case["case_id"],
                "policy": policy,
                "repetition": repetition,
            }
            for position, (case, policy, repetition) in enumerate(work, 1)
        ],
    }
    atomic_json(args.output / "work-order.json", order_payload)
    work_order_sha256 = digest((args.output / "work-order.json").read_bytes())

    def execute(item: tuple[dict[str, Any], str, int]) -> dict[str, Any]:
        case, policy, repetition = item
        name = f"{case['case_id']}--{policy}--{repetition}.json"
        path = records / name
        private_path = args.output / "private-acquisitions"
        private_record = private_path / name
        if path.exists() and private_record.exists():
            existing = json.loads(path.read_text())
            if existing.get("status") == "completed":
                return existing
        inflight_path = args.output / "inflight" / name
        private_inflight_path = args.output / "private-inflight" / name

        def write_checkpoint(
            public: dict[str, Any], private: list[dict[str, Any]]
        ) -> None:
            inflight_path.parent.mkdir(exist_ok=True, mode=0o700)
            private_inflight_path.parent.mkdir(exist_ok=True, mode=0o700)
            atomic_json(inflight_path, public)
            atomic_json(private_inflight_path, private)

        try:
            with (
                httpx.Client(
                    base_url=args.base_url,
                    headers={"Authorization": f"Bearer {args.api_key}"},
                    timeout=180,
                ) as api,
                httpx.Client(
                    base_url=args.llm_base_url,
                    headers={"Authorization": f"Bearer {args.llm_api_key}"},
                    timeout=180,
                ) as llm,
            ):
                result = execute_trial(
                    case,
                    policy,
                    repetition,
                    api_client=api,
                    llm_client=llm,
                    model=args.model,
                    result_limit=args.result_limit,
                    seed=args.seed,
                    checkpoint_writer=write_checkpoint,
                )
                private = result.pop("private_acquisitions")
                private_path.mkdir(exist_ok=True, mode=0o700)
                atomic_json(private_record, private)
                inflight_path.unlink(missing_ok=True)
                private_inflight_path.unlink(missing_ok=True)
        except Exception as error:
            failure_path = None
            if inflight_path.exists() and private_inflight_path.exists():
                failure_dir = args.output / "failures"
                private_failure_dir = args.output / "private-failures"
                failure_dir.mkdir(exist_ok=True, mode=0o700)
                private_failure_dir.mkdir(exist_ok=True, mode=0o700)
                attempt = len(list(failure_dir.glob(f"{path.stem}--*.json"))) + 1
                failure_name = f"{path.stem}--attempt-{attempt}.json"
                failure_path = failure_dir / failure_name
                os.replace(inflight_path, failure_path)
                os.replace(private_inflight_path, private_failure_dir / failure_name)
            result = {
                "schema_version": "enterprise-evaluation/w10-policy-trial/1",
                "case_id": case["case_id"],
                "policy": policy,
                "repetition": repetition,
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error)[:1000],
                "partial_evidence_file": (
                    str(failure_path.relative_to(args.output)) if failure_path else None
                ),
            }
        atomic_json(path, result)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(execute, work))
    manifest = {
        "schema_version": "enterprise-evaluation/w10-policy-run/1",
        "cases_sha256": digest(args.cases.read_bytes()),
        "records": len(results),
        "completed": sum(item["status"] == "completed" for item in results),
        "failed": sum(item["status"] != "completed" for item in results),
        "failed_attempts": len(list((args.output / "failures").glob("*.json")))
        if (args.output / "failures").exists()
        else 0,
        "policies": args.policies,
        "repetitions": args.repetitions,
        "result_limit": args.result_limit,
        "seed": args.seed,
        "work_order_sha256": work_order_sha256,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "httpx": version("httpx"),
            "langgraph": langgraph_version,
            "model_requested": args.model,
            "api_route": "candidate_loopback",
            "llm_route": "openai_compatible",
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "source_commit": source_commit,
            "freeze_sha256": digest(freeze_path.read_bytes()),
            "search_environment_sha256": search_environment_sha256,
            "search_environment": search_environment,
            "policy_sha256": digest(
                (
                    ROOT / "agent-svc/agent/experimental/bounded_adaptive_policy.py"
                ).read_bytes()
            ),
        },
    }
    atomic_json(args.output / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
