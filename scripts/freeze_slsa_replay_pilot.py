#!/usr/bin/env python3
"""Freeze a four-case, evaluator-only replay packet from acquired public SLSA pages.

The prior 20-result query and page acquisitions are inputs. This script makes
no searches and no Jev calls. Later provider requests use only each case's
first-pass state and exact missing-information proposal; replay evidence and
reference labels remain evaluator-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import jev_continuation_request_builder as builder

OUTPUT = Path(__file__).parents[1] / "docs/experiments/typesafe-jev/slsa-replay-2026-10-03.case-packet.json"
FIRSTPASS_RANKS = {1, 2, 3}
CASE_DEFINITIONS = [
    {
        "case_id": "slsa-replay-01",
        "stratum": "met_false_gap_unnecessary_followup",
        "question": "In the supplied SLSA provenance v0.1 schema, which field identifies the builder?",
        "obligation": "Identify the builder identity field in the supplied v0.1 schema excerpt.",
        "source_rank": 1,
        "anchor": '"builder": {\n      "id": "<URI>"\n    }',
        "evidence_status": "met",
        "search_needed": "no",
        "rationale": "The supplied schema excerpt names builder.id.",
        "proposal": "The supplied excerpt does not name a field for builder identity.",
        "specific_information": "the builder identity field name in the v0.1 provenance schema",
        "subject": "SLSA provenance builder identity field",
        "version": "SLSA provenance v0.1",
        "context": "builder field in the supplied schema excerpt",
        "stage3_yes": False,
        "stage4_yes": None,
        "replay_contribution": "No follow-up is needed to close this already met field-identification obligation.",
        "contributing_result_ids": [],
        "replay_spans": [],
    },
    {
        "case_id": "slsa-replay-02",
        "stratum": "unmet_public_transitivity_rule",
        "question": "Does SLSA Build assurance for an artifact require its transitive dependencies to meet the same Build level?",
        "obligation": "Determine whether SLSA Build assurance is required transitively across dependency artifacts.",
        "source_rank": 2,
        "anchor": "the Build Track defines three distinct levels of increasing security.",
        "evidence_status": "unmet",
        "search_needed": "yes",
        "rationale": "The first-pass overview describes SLSA and its Build Track but does not state a transitivity rule.",
        "proposal": "The supplied first-pass excerpt does not state whether SLSA Build level requirements apply transitively to dependencies.",
        "specific_information": "whether SLSA Build-level requirements extend to an artifact's transitive dependencies",
        "subject": "SLSA Build assurance transitivity",
        "version": "SLSA framework overview in the acquired search snapshot",
        "context": "whether artifact-level Build assurance imposes levels on dependency artifacts",
        "stage3_yes": True,
        "stage4_yes": True,
        "replay_contribution": "The hidden result pool includes official FAQ guidance and a separate public explanation directly addressing independent artifact ratings.",
        "contributing_result_ids": ["rank-07", "rank-12"],
        "replay_spans": [
            (7, "SLSA Build levels only cover the trustworthiness of a single build", "transitive dependencies."),
            (12, "SLSA advocates for evaluating each software artefact independently without endorsing transitive trust.", None),
        ],
    },
    {
        "case_id": "slsa-replay-03",
        "stratum": "unmet_authority_check_label_completeness",
        "question": "Which Build-level labels does the official SLSA v1.0 levels page enumerate?",
        "obligation": "Verify the official SLSA v1.0 Build-track level enumeration against the retrieved secondary claim.",
        "source_rank": 3,
        "anchor": "SLSA v1.0 defines four build levels, each building on the previous:",
        "evidence_status": "unmet",
        "search_needed": "yes",
        "rationale": "The first pass contains a secondary assertion but no official v1.0 levels page, so its version-specific enumeration is not authoritatively established.",
        "proposal": "The supplied first-pass excerpt lacks the official SLSA v1.0 Build-level enumeration needed to verify the secondary claim.",
        "specific_information": "the official SLSA v1.0 Build-track level labels",
        "subject": "SLSA v1.0 Build-track level enumeration",
        "version": "SLSA v1.0",
        "context": "official specification enumeration, checked against a secondary page",
        "stage3_yes": True,
        "stage4_yes": True,
        "replay_contribution": "The hidden pool contains the official v1.0 levels page, whose table supplies the authoritative labels Build L0 through Build L3. The secondary excerpt's count of four levels is compatible with that list; this is an authority/completeness check, not a contradiction.",
        "contributing_result_ids": ["rank-11"],
        "replay_spans": [
            (11, "Build L0", None),
            (11, "Build L1", None),
            (11, "Build L2", None),
            (11, "Build L3", None),
        ],
    },
    {
        "case_id": "slsa-replay-04",
        "stratum": "unanswerable_private_deployment_value",
        "question": "What exact signing-key rotation interval is configured in a particular organization's private SLSA deployment?",
        "obligation": "Determine an organization-specific private signing-key rotation interval.",
        "source_rank": 1,
        "anchor": '"builder": {\n      "id": "<URI>"\n    }',
        "evidence_status": "unanswerable",
        "search_needed": "uncertain",
        "rationale": "The requested setting is private deployment state not identified in the public first-pass excerpt or bounded public corpus.",
        "proposal": "The supplied public first-pass excerpt does not contain the private deployment's configured signing-key rotation interval.",
        "specific_information": "the exact key rotation interval configured for one particular private SLSA deployment",
        "subject": "private deployment signing-key rotation interval",
        "version": "the unspecified organization's private deployment",
        "context": "deployment-specific operational configuration excluded from public sources",
        "stage3_yes": True,
        "stage4_yes": False,
        "replay_contribution": "The bounded public result set contains general key-management advice but no value for a particular private deployment.",
        "contributing_result_ids": [],
        "replay_spans": [
            (13, "Store credentials in a secure vault, rotate them regularly, and enforce MFA and RBAC for systems that publish or sign artifacts.", None),
            (16, "Protect signing keys with hardware security modules, implement strong access controls, rotate keys regularly, and maintain detailed audit logs of signing operations.", None),
        ],
    },
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def exact_excerpt(text: str, anchor: str) -> tuple[str, int, int]:
    start = text.index(anchor)
    end = start + len(anchor)
    return text[start:end], len(text[:start].encode()), len(text[:end].encode())


def exact_span(text: str, start_anchor: str, end_anchor: str | None) -> tuple[str, int, int]:
    start = text.index(start_anchor)
    if end_anchor is None:
        end = start + len(start_anchor)
    else:
        end = text.index(end_anchor, start) + len(end_anchor)
    return text[start:end], len(text[:start].encode()), len(text[:end].encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    index_bytes = args.index.read_bytes()
    snapshot_bytes = args.snapshot.read_bytes()
    index = json.loads(index_bytes)
    snapshot = json.loads(snapshot_bytes)
    result_by_url = {row["url"]: row for row in snapshot["response"]["results"]}
    docs: dict[str, str] = {}
    page_metadata: dict[int, dict[str, Any]] = {}
    for row in index["receipts"]:
        rank = row["rank"]
        source_id = f"rank-{rank:02d}"
        markdown_name = row["receipt"].replace(".json", ".md")
        doc = (args.corpus / markdown_name).read_text()
        if sha(doc) != row["markdown_sha256"]:
            raise ValueError(f"acquired markdown digest mismatch for {source_id}")
        search_result = result_by_url[row["url"]]
        docs[source_id] = doc
        page_metadata[rank] = {
            "result_id": source_id,
            "rank": rank,
            "markdown_file": markdown_name,
            "url": row["url"],
            "title": search_result["title"],
            "receipt_sha256": row["receipt_sha256"],
            "markdown_sha256": row["markdown_sha256"],
            "status": row["status"],
        }

    cases: list[dict[str, Any]] = []
    for definition in CASE_DEFINITIONS:
        rank = definition["source_rank"]
        source_id = f"rank-{rank:02d}"
        doc = docs[source_id]
        excerpt, start, end = exact_excerpt(doc, definition["anchor"])
        metadata = page_metadata[rank]
        source = {
            "source_id": source_id,
            "source_sha256": sha(doc),
            "url": metadata["url"],
            "title": metadata["title"],
            "supplied_text": excerpt,
            "excerpt_start_byte": start,
            "excerpt_end_byte": end,
            "text_scope": "excerpt",
            "fetch_status": "success",
        }
        state = {
            "research_question": definition["question"],
            "obligation": {
                "id": definition["case_id"],
                "statement": definition["obligation"],
                "scope": "bounded excerpt from the first three results of the frozen SLSA query",
            },
            "first_pass": {
                "acquisition_status": "complete",
                "coverage": {
                    "scope": "bounded_excerpts_only",
                    "acquired_source_ids": [source_id],
                    "presented_source_ids": [source_id],
                    "omitted_source_ids": [],
                    "omitted_text_within_sources": True,
                },
                "sources": [source],
            },
            "public_search_scope": {
                "allowed_source_types": [
                    "public SLSA specification and FAQ pages",
                    "public technical explanations in the frozen result set",
                ],
                "excluded_scopes": [
                    "private deployment configuration",
                    "authenticated data",
                    "newly acquired search results",
                ],
            },
        }
        span = {
            "source_id": source_id,
            "source_sha256": sha(doc),
            "start_byte": start,
            "end_byte": end,
            "quote": excerpt,
            "subject": definition["subject"],
            "version": definition["version"],
            "context": definition["context"],
        }
        hypothesis = {
            "hypothesis_id": f"{definition['case_id']}-gap-1",
            "kind": "missing_information",
            "proposition": definition["proposal"],
            "specific_information": definition["specific_information"],
            "absence_scope": "supplied_material_only",
            "subject": definition["subject"],
            "version": definition["version"],
            "context": definition["context"],
            "source_spans": [span],
        }
        # Frozen incumbent comparator for `_detect_gaps`: same exact source
        # excerpts as the Jev input, projected into SourceArtifact.to_document
        # format. This intentionally does not claim loop/checkpoint equivalence.
        domain = urlsplit(source["url"]).hostname or ""
        combined_context = f"Source: {source['url']} (domain: {domain})\n\n{excerpt}"
        query_context = f'Original research query: "{definition["question"]}"\n\n'
        incumbent_user_prompt = (
            f"{query_context}Analyze the following research context and identify specific topics, "
            "angles, or aspects of the original query that are NOT adequately covered "
            "by the gathered sources. Focus on what's missing or thin, not what's present. "
            "Return a JSON array of topic strings (max 5) that would make good follow-up search queries. "
            "Return [] if you're satisfied with coverage.\n\n"
            f"Context:\n{combined_context[:12000]}"
        )
        source_map = {source_id: doc}
        stage1 = builder.build_sufficiency_request(state)
        stage3 = builder.build_hypothesis_review_request(state, [hypothesis], source_map)
        if isinstance(stage1, builder.Abstention) or isinstance(stage3, builder.Abstention):
            raise ValueError(f"request builder abstained for {definition['case_id']}: {stage1} {stage3}")
        stage3_yes = builder.ValidatedNoulResponse(
            builder.MODEL,
            MappingProxyType({"q0000": 0.9}),
            hashlib.sha256(stage3.serialized).hexdigest(),
        )
        stage4 = builder.build_addressability_request(
            state,
            hypothesis,
            source_map,
            stage3_plan=stage3,
            stage3_response=stage3_yes,
            stage3_question_id="q0000",
            policy_qualified_hypothesis_id=hypothesis["hypothesis_id"],
        )
        if isinstance(stage4, builder.Abstention):
            raise ValueError(f"stage 4 builder abstained for {definition['case_id']}: {stage4}")

        evaluation = {
            "reference": {
                "obligation_status": definition["evidence_status"],
                "search_needed": definition["search_needed"],
                "rationale": definition["rationale"],
                "provenance": "assistant-authored best effort; pending parent review; not independent gold",
            },
            "stage1_yes_reference": definition["evidence_status"] == "met",
            "stage3_yes_reference": definition["stage3_yes"],
            "stage4_yes_reference_if_reached": definition["stage4_yes"],
            "replay": {
                "candidate_result_ids": [page_metadata[r]["result_id"] for r in range(4, 21)],
                "expected_distinct_page_contribution": definition["contributing_result_ids"],
                "expected_contribution_summary": definition["replay_contribution"],
                "evaluator_only_passages": [
                    {
                        "result_id": f"rank-{followup_rank:02d}",
                        "url": page_metadata[followup_rank]["url"],
                        "title": page_metadata[followup_rank]["title"],
                        "source_sha256": sha(docs[f"rank-{followup_rank:02d}"]),
                        "start_byte": followup_start,
                        "end_byte": followup_end,
                        "quote": followup_quote,
                    }
                    for followup_rank, start_anchor, end_anchor in definition["replay_spans"]
                    for followup_quote, followup_start, followup_end in [
                        exact_span(
                            docs[f"rank-{followup_rank:02d}"],
                            start_anchor,
                            end_anchor,
                        )
                    ]
                ],
            },
        }
        cases.append(
            {
                "case_id": definition["case_id"],
                "stratum": definition["stratum"],
                "state": state,
                "research_agent_proposal": hypothesis,
                "incumbent_gap_control": {
                    "requested_model_alias": "free",
                    "system_prompt": "You are a research gap analyzer.",
                    "user_prompt": incumbent_user_prompt,
                    "prompt_sha256": sha(incumbent_user_prompt),
                    "context_sha256": sha(combined_context),
                    "context_scope": "same exact first-pass excerpt; SourceArtifact.to_document projection",
                },
                "evaluation_only": evaluation,
                "frozen_request_sha256": {
                    "stage1_sufficiency": hashlib.sha256(stage1.serialized).hexdigest(),
                    "stage3_hypothesis_review": hashlib.sha256(stage3.serialized).hexdigest(),
                    "stage4_addressability_if_stage3_positive": hashlib.sha256(stage4.serialized).hexdigest(),
                },
            }
        )

    packet = {
        "schema_version": "jev-slsa-replay-pilot/2",
        "study_id": "jev-slsa-deterministic-replay-2026-10-03",
        "contract": builder.CONTRACT,
        "model": builder.MODEL,
        "call_budget": {
            "hard_max_total_requests": 11,
            "cases": 4,
            "no_retries": True,
            "request_order": "case order; stage 1, stage 3, then stage 4 only when stage 3 binary argmax is yes",
            "searches_dispatched": 0,
            "incumbent_control_max_total_requests": 4,
            "incumbent_control_order": "one sequential request per frozen case after separate approval",
            "incumbent_control_model_alias": "free",
        },
        "corpus": {
            "query": snapshot["response"]["query"],
            "search_snapshot_sha256": sha(snapshot_bytes.decode()),
            "acquisition_index_sha256": sha(index_bytes.decode()),
            "acquisition_index_snapshot_sha256": index["snapshot_sha256"],
            "acquired_pages": len(index["receipts"]),
            "first_pass_result_ranks": sorted(FIRSTPASS_RANKS),
            "evaluator_only_replay_ranks": list(range(4, 21)),
            "source_pages": list(page_metadata.values()),
            "replay_scope": "fixed already-acquired public result set; this is offline replay, not a fresh search or live retrieval evaluation",
        },
        "label_note": "Assistant-authored labels are best-effort and not independent gold. Parent review is requested before any calls.",
        "cases": cases,
    }
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}; cases={len(cases)} calls<=11; source snapshot={packet['corpus']['search_snapshot_sha256']}")


if __name__ == "__main__":
    main()
