#!/usr/bin/env python3
"""Validate blinded W10 adjudication and emit a secret-free public record."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

SOURCE_SUPPORT = {"supports", "challenges", "mixed", "irrelevant", "ambiguous"}
CLAIM_STATUS = {"closed", "partial", "open", "ambiguous"}
CONTRADICTION = {"adequate", "inadequate", "not_applicable", "ambiguous"}


def digest(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _require_score(item: dict[str, Any], field: str) -> int:
    value = item.get(field)
    if type(value) is not int or not 0 <= value <= 2:  # bool is not a score
        raise ValueError(f"{field} must be an integer from 0 to 2")
    return value


def _require_choice(item: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = item.get(field)
    if value not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return str(value)


def _require_bool(item: dict[str, Any], field: str) -> bool:
    value = item.get(field)
    if type(value) is not bool:
        raise ValueError(f"{field} must be boolean")
    return value


def _index_items(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        observation_id = item.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError(f"{label} contains a missing observation ID")
        if observation_id in indexed:
            raise ValueError(f"{label} contains a duplicate observation ID")
        indexed[observation_id] = item
    return indexed


def validate_and_build(
    packet: dict[str, Any],
    manifest: dict[str, Any],
    responses: dict[str, Any],
    *,
    packet_bytes: bytes,
) -> dict[str, Any]:
    if (
        packet.get("schema_version")
        != "enterprise-evaluation/w10-adjudication-private/1"
    ):
        raise ValueError("unsupported private packet schema")
    if packet.get("blind_to_policy_and_repetition") is not True:
        raise ValueError("private packet is not blinded")
    if (
        manifest.get("schema_version")
        != "enterprise-evaluation/w10-adjudication-manifest/1"
    ):
        raise ValueError("unsupported adjudication manifest schema")
    if manifest.get("private_packet_sha256") != digest(packet_bytes):
        raise ValueError("private packet digest does not match manifest")
    if (
        responses.get("schema_version")
        != "enterprise-evaluation/w10-adjudication-responses/1"
    ):
        raise ValueError("unsupported response schema")
    if responses.get("reviewer_kind") != "agent":
        raise ValueError("reviewer_kind must disclose agent review")
    reviewer_model = responses.get("reviewer_model")
    if not isinstance(reviewer_model, str) or not reviewer_model.strip():
        raise ValueError("reviewer_model must disclose the model route")
    reviewer_transport = responses.get("reviewer_transport")
    if not isinstance(reviewer_transport, str) or not reviewer_transport.strip():
        raise ValueError("reviewer_transport must disclose the request path")
    if responses.get("blind_to_policy_and_repetition") is not True:
        raise ValueError("responses must attest blinded review")

    packet_items = packet.get("items")
    manifest_items = manifest.get("items")
    response_items = responses.get("items")
    if not all(
        isinstance(value, list)
        for value in (packet_items, manifest_items, response_items)
    ):
        raise ValueError("packet, manifest, and responses must contain item lists")
    packet_items = cast(list[dict[str, Any]], packet_items)
    manifest_items = cast(list[dict[str, Any]], manifest_items)
    response_items = cast(list[dict[str, Any]], response_items)

    packet_by_id = _index_items(packet_items, "private packet")
    manifest_by_id = _index_items(manifest_items, "manifest")
    response_by_id = _index_items(response_items, "responses")
    expected = set(packet_by_id)
    if set(manifest_by_id) != expected:
        raise ValueError("manifest observation set does not match private packet")
    if set(response_by_id) != expected:
        missing = sorted(expected - set(response_by_id))
        extra = sorted(set(response_by_id) - expected)
        raise ValueError(
            f"response observation set mismatch; missing={missing}, extra={extra}"
        )

    public_items: list[dict[str, Any]] = []
    source_count = 0
    claim_count = 0
    for observation_id in sorted(expected):
        private_item = packet_by_id[observation_id]
        public_item = manifest_by_id[observation_id]
        response = response_by_id[observation_id]
        expected_hash = digest(
            json.dumps(private_item, ensure_ascii=False, sort_keys=True)
        )
        if public_item.get("private_item_sha256") != expected_hash:
            raise ValueError(f"private item digest mismatch for {observation_id}")
        item_type = private_item.get("item_type")
        if (
            public_item.get("item_type") != item_type
            or response.get("item_type") != item_type
        ):
            raise ValueError(f"item type mismatch for {observation_id}")
        rationale = response.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"rationale must be non-empty for {observation_id}")

        verdict: dict[str, Any]
        if item_type == "source_grade":
            source_count += 1
            verdict = {
                "currency": _require_score(response, "currency"),
                "relevance": _require_score(response, "relevance"),
                "authority": _require_score(response, "authority"),
                "accuracy": _require_score(response, "accuracy"),
                "purpose": _require_score(response, "purpose"),
                "passage_support": _require_choice(
                    response, "passage_support", SOURCE_SUPPORT
                ),
                "useful": _require_bool(response, "useful"),
                "derivative_or_copied": _require_bool(response, "derivative_or_copied"),
            }
        elif item_type == "claim_closure":
            claim_count += 1
            verdict = {
                "claim_status": _require_choice(response, "claim_status", CLAIM_STATUS),
                "contradiction_handling": _require_choice(
                    response, "contradiction_handling", CONTRADICTION
                ),
            }
        else:
            raise ValueError(f"unsupported item type for {observation_id}: {item_type}")
        public_items.append(
            {
                "observation_id": observation_id,
                "case_id": public_item.get("case_id"),
                "item_type": item_type,
                "selection_reasons": public_item.get("selection_reasons"),
                "verdict": verdict,
                "rationale_sha256": digest(rationale.strip()),
            }
        )

    return {
        "schema_version": "enterprise-evaluation/w10-adjudication-public/1",
        "reviewer_kind": "agent",
        "reviewer_model": reviewer_model,
        "reviewer_transport": reviewer_transport,
        "blind_to_policy_and_repetition": True,
        "private_packet_sha256": digest(packet_bytes),
        "manifest_sha256": digest(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        ),
        "responses_sha256": digest(
            json.dumps(responses, ensure_ascii=False, sort_keys=True)
        ),
        "counts": {
            "selected_observations": len(public_items),
            "source_grades": source_count,
            "claim_closures": claim_count,
        },
        "items": public_items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-packet", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    packet_bytes = args.private_packet.read_bytes()
    result = validate_and_build(
        json.loads(packet_bytes),
        json.loads(args.manifest.read_text()),
        json.loads(args.responses.read_text()),
        packet_bytes=packet_bytes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
