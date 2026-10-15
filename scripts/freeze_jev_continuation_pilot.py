#!/usr/bin/env python3
"""Freeze an exploratory Jev packet from a pinned public Git revision.

This script performs no network search and makes no provider calls. It reads
public Git blobs from the local object database and records exact passages,
source hashes, excerpt byte intervals, and explicitly non-independent
assistant reference assessments.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import jev_continuation_request_builder as builder

REVISION = "7b9bf51de1334d4d7e6bd6256d3085c8c2a8d60d"
OUT = (
    Path(__file__).parents[1]
    / "docs/experiments/typesafe-jev/continuation-pilot-2026-10-03.case-packet.json"
)


def blob(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{REVISION}:{path}"], text=True)


def paragraph(path: str, anchor: str) -> tuple[str, str, int, int]:
    text = blob(path)
    offset = text.index(anchor)
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    if line.lstrip().startswith("- "):
        start = line_start + len(line) - len(line.lstrip())
        end = line_end
        excerpt = text[start:end]
    else:
        start = text.rfind("\n\n", 0, offset) + 2
        end = text.find("\n\n", offset)
        if end < 0:
            end = len(text)
        excerpt = text[start:end].strip()
        # Preserve exact source coordinates after whitespace trimming.
        start = text.index(excerpt, start, end)
    start_byte = len(text[:start].encode("utf-8"))
    end_byte = start_byte + len(excerpt.encode("utf-8"))
    return text, excerpt, start_byte, end_byte


def make_case(
    *,
    case_id: str,
    question: str,
    obligation: str,
    sources: list[tuple[str, str, str]],
    status: str,
    rationale: str,
    proposal_kind: str,
    proposal: str,
    specific_information: str | None,
    subject: str,
    context: str,
    stage3_yes: bool,
    stage4_yes: bool | None,
    stratum: str,
) -> dict:
    source_records = []
    source_texts: dict[str, str] = {}
    extracted: list[tuple[str, str, str, int, int]] = []
    for path, source_id, anchor in sources:
        text, excerpt, start_byte, end_byte = paragraph(path, anchor)
        extracted.append((path, source_id, excerpt, start_byte, end_byte))
        source_texts[source_id] = text
        source_records.append(
            {
                "source_id": source_id,
                "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "url": f"https://github.com/magnus919/groktocrawl-x/blob/{REVISION}/{path}",
                "title": path,
                "supplied_text": excerpt,
                "excerpt_start_byte": start_byte,
                "excerpt_end_byte": end_byte,
                "text_scope": "excerpt",
                "fetch_status": "success",
            }
        )
    state = {
        "research_question": question,
        "obligation": {
            "id": case_id,
            "statement": obligation,
            "scope": f"public project documentation at {REVISION}",
        },
        "first_pass": {
            "acquisition_status": "complete",
            "coverage": {
                "scope": "bounded_excerpts_only",
                "acquired_source_ids": [source[1] for source in extracted],
                "presented_source_ids": [source[1] for source in extracted],
                "omitted_source_ids": [],
                "omitted_text_within_sources": True,
            },
            "sources": source_records,
        },
        "public_search_scope": {
            "allowed_source_types": [
                "public project documentation",
                "public source code",
                "official upstream documentation",
            ],
            "excluded_scopes": [
                "private deployment configuration",
                "authenticated data",
                "non-public operational state",
            ],
        },
    }

    spans = []
    for _path, source_id, excerpt, start_byte, end_byte in extracted:
        raw = source_texts[source_id].encode("utf-8")
        spans.append(
            {
                "source_id": source_id,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "start_byte": start_byte,
                "end_byte": end_byte,
                "quote": excerpt,
                "subject": subject,
                "version": f"origin/main@{REVISION[:12]}",
                "context": context,
            }
        )
    hypothesis = {
        "hypothesis_id": f"{case_id}-h1",
        "kind": proposal_kind,
        "proposition": proposal,
        "subject": subject,
        "version": f"origin/main@{REVISION[:12]}",
        "context": context,
        "source_spans": spans,
    }
    if proposal_kind == "missing_information":
        hypothesis["specific_information"] = specific_information
        hypothesis["absence_scope"] = "supplied_material_only"

    return {
        "case_id": case_id,
        "stratum": stratum,
        "state": state,
        "acquired_source_texts": source_texts,
        "assistant_reference": {
            "evidence_status": status,
            "stage1_yes": status == "satisfied",
            "stage3_yes": stage3_yes,
            "stage4_yes_if_reached": stage4_yes,
            "search_needed": "yes" if stage3_yes and stage4_yes else "no",
            "rationale": rationale,
            "provenance": "assistant best-effort assessment; not independent gold",
        },
        "research_agent_proposal": hypothesis,
        "proposal_provenance": (
            "separate research-agent stage, assistant-authored for this pilot; "
            "not Jev-generated and not independent gold"
        ),
    }


def main() -> None:
    cases = [
        make_case(
            case_id="pilot-01",
            question="What is the documented default maxConcurrency for a crawl, and what range is accepted?",
            obligation="Determine documented crawl concurrency default and range.",
            sources=[
                (
                    "docs/adr/0038-crawl-engine.md",
                    "crawl-adr",
                    "Configurable via `maxConcurrency` (1-50, default 3)",
                )
            ],
            status="satisfied",
            rationale="The exact default and range are stated.",
            proposal_kind="missing_information",
            proposal="The supplied passage does not state the default maxConcurrency.",
            specific_information="the documented default maxConcurrency value",
            subject="crawl maxConcurrency",
            context="configuration contract",
            stage3_yes=False,
            stage4_yes=None,
            stratum="adequate-with-false-missing-proposal",
        ),
        make_case(
            case_id="pilot-02",
            question="What execution order does the scraping guide specify for site adapters and generic fetching?",
            obligation="Determine where site adapters run relative to generic fetching.",
            sources=[
                (
                    "docs/guides/features.md",
                    "scrape-guide",
                    "Adapters run before generic fetching",
                )
            ],
            status="satisfied",
            rationale="The passage explicitly says adapters run before generic fetching.",
            proposal_kind="missing_information",
            proposal="The supplied passage does not state whether adapters run before or after generic fetching.",
            specific_information="whether site adapters run before or after generic fetching",
            subject="adapter execution order",
            context="supported-site scraping pipeline",
            stage3_yes=False,
            stage4_yes=None,
            stratum="adequate-with-false-missing-proposal",
        ),
        make_case(
            case_id="pilot-03",
            question="What exact retry_limit is used for accepted asynchronous jobs that encounter a downstream rate limit?",
            obligation="Determine numeric retry limit for downstream retry_scheduled jobs.",
            sources=[
                (
                    "docs/guides/api.md",
                    "api-rate-limit",
                    "Accepted asynchronous jobs that hit a downstream rate-limit condition",
                )
            ],
            status="missing",
            rationale="The excerpt names retry_limit and exhaustion but no numeric value.",
            proposal_kind="missing_information",
            proposal="The exact numeric retry_limit for accepted downstream rate-limit retries is not stated in the supplied excerpt.",
            specific_information="the numeric retry_limit value for accepted asynchronous downstream-rate-limit retries",
            subject="accepted async rate-limit retry_limit",
            context="public API contract for retry_scheduled jobs",
            stage3_yes=True,
            stage4_yes=True,
            stratum="missing-numeric-contract",
        ),
        make_case(
            case_id="pilot-04",
            question="What response does minAge cache-only mode return on a cache miss?",
            obligation="Determine minAge cache-miss semantics.",
            sources=[
                (
                    "docs/adr/0038-crawl-engine.md",
                    "crawl-cache-adr",
                    "Valkey-backed response cache",
                )
            ],
            status="missing",
            rationale="The excerpt names maxAge/minAge semantics but not the miss response.",
            proposal_kind="missing_information",
            proposal="The exact cache-miss behavior for minAge is not stated in the supplied excerpt.",
            specific_information="the response produced by minAge cache-only mode when the cache misses",
            subject="crawl cache minAge",
            context="cache miss behavior in the public contract",
            stage3_yes=True,
            stage4_yes=True,
            stratum="missing-cache-behavior",
        ),
        make_case(
            case_id="pilot-05",
            question="How many nested sitemap index levels does the documented parser support?",
            obligation="Determine documented nested sitemap index recursion depth.",
            sources=[
                (
                    "docs/adr/0038-crawl-engine.md",
                    "sitemap-parser",
                    "nested sitemap index recursion (up to 3 levels)",
                )
            ],
            status="satisfied",
            rationale="The excerpt states the maximum nesting depth.",
            proposal_kind="missing_information",
            proposal="The supplied passage does not state the maximum nested sitemap index depth.",
            specific_information="the maximum nested sitemap index recursion depth",
            subject="sitemap index recursion depth",
            context="documented sitemap parser behavior",
            stage3_yes=False,
            stage4_yes=None,
            stratum="adequate-with-false-missing-proposal",
        ),
        make_case(
            case_id="pilot-06",
            question="How many total attempts does the CLI make for a rate-limited admission?",
            obligation="Determine the bounded CLI admission retry count.",
            sources=[
                (
                    "docs/guides/api.md",
                    "api-admission-retry",
                    "A 429 admission is a temporary condition",
                )
            ],
            status="satisfied",
            rationale="The excerpt explicitly states the maximum total attempts.",
            proposal_kind="missing_information",
            proposal="The supplied passage does not state the maximum total attempts.",
            specific_information="the maximum number of total CLI attempts after 429 admission",
            subject="CLI rate-limit admission retry count",
            context="documented API and CLI behavior for a 429 admission",
            stage3_yes=False,
            stage4_yes=None,
            stratum="adequate-with-false-missing-proposal",
        ),
        make_case(
            case_id="pilot-07",
            question="Do these two passages agree about the documented maxConcurrency default and range?",
            obligation="Compare two passages describing the same crawl concurrency contract.",
            sources=[
                (
                    "docs/adr/0038-crawl-engine.md",
                    "crawl-concurrency-choice",
                    "Configurable via `maxConcurrency` (1-50, default 3)",
                ),
                (
                    "agent-svc/agent/models.py",
                    "crawl-request-model",
                    "max_concurrency: int = Field(",
                ),
            ],
            status="satisfied",
            rationale="Both cited passages state default 3 and a maximum of 50 concurrent tasks.",
            proposal_kind="contradiction",
            proposal="The two passages make materially incompatible claims about the crawl concurrency default or maximum.",
            specific_information=None,
            subject="crawl maxConcurrency default and range",
            context="documented crawl concurrency configuration contract",
            stage3_yes=False,
            stage4_yes=None,
            stratum="aligned-passage-noncontradiction-control",
        ),
        make_case(
            case_id="pilot-08",
            question="What maxConcurrency value is currently configured in a particular operator deployment?",
            obligation="Determine live effective setting for a specific deployment.",
            sources=[
                (
                    "docs/adr/0038-crawl-engine.md",
                    "crawl-adr",
                    "Configurable via `maxConcurrency` (1-50, default 3)",
                )
            ],
            status="insufficient_to_assess",
            rationale="Public documentation gives a default, not a particular deployment's effective value.",
            proposal_kind="missing_information",
            proposal="The supplied public documentation does not identify a particular deployment's effective maxConcurrency setting.",
            specific_information="the actual effective maxConcurrency setting in a particular deployment",
            subject="deployment-specific crawl concurrency setting",
            context="live configuration of a particular operator deployment",
            stage3_yes=True,
            stage4_yes=False,
            stratum="private-state-unanswerable",
        ),
    ]
    source_blobs: dict[str, str] = {}
    for case in cases:
        blob_by_id = {}
        for source_id, text in case.pop("acquired_source_texts").items():
            source_path = next(
                source["title"]
                for source in case["state"]["first_pass"]["sources"]
                if source["source_id"] == source_id
            )
            source_blobs[source_path] = text
            blob_by_id[source_id] = source_path
        case["source_blob_by_id"] = blob_by_id
    packet = {
        "study_id": "jev-continuation-v2-pilot-2026-10-03",
        "source_revision": REVISION,
        "source_revision_ref": "origin/main at freeze",
        "model": "jev-1.13.0",
        "contract": "continuation-evidence-gap-addressability/2",
        "acquired_public_source_blobs": source_blobs,
        "cases": cases,
    }
    for case in cases:
        state = case["state"]
        hypothesis = case["research_agent_proposal"]
        stage1 = builder.build_sufficiency_request(state)
        stage3 = builder.build_hypothesis_review_request(
            state,
            [hypothesis],
            {
                source_id: source_blobs[path]
                for source_id, path in case["source_blob_by_id"].items()
            },
        )
        if not isinstance(stage1, builder.RequestPlan):
            raise ValueError(f"{case['case_id']} stage 1 invalid: {stage1.reason}")
        if not isinstance(stage3, builder.RequestPlan):
            raise ValueError(f"{case['case_id']} stage 3 invalid: {stage3.reason}")
        stage4_payload = {
            "model": builder.MODEL,
            "state": state,
            "questions": {
                "n0": {
                    "type": "noul",
                    "instructions": {
                        "question": builder._ADDRESSABILITY_QUESTION,
                        "gap_hypothesis": hypothesis,
                    },
                }
            },
        }
        stage4_serialized = json.dumps(
            stage4_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        case["frozen_request_sha256"] = {
            "stage1_sufficiency": hashlib.sha256(stage1.serialized).hexdigest(),
            "stage3_hypothesis_review": hashlib.sha256(stage3.serialized).hexdigest(),
            "stage4_addressability_if_stage3_positive": hashlib.sha256(
                stage4_serialized
            ).hexdigest(),
        }
    OUT.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes; {len(cases)} cases)")


if __name__ == "__main__":
    main()
