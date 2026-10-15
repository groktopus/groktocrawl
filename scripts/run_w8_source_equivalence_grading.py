#!/usr/bin/env python3
"""Blindly grade source equivalence using frozen acquisitions and a local model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

LABELS = (
    "exact_reference",
    "substantively_equivalent",
    "related_insufficient",
    "unavailable_indeterminate",
    "unrelated",
)
FORBIDDEN_BLIND_KEYS = {"arm", "rank", "position", "sightings"}
TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
SCHEMA_VERSION = "w8-source-equivalence-grades/1"
EXCERPT_WIDTH = 2_000
EXCERPT_OVERLAP = 200
EXCERPT_LIMIT = 3


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def acquisition_path(root: Path, url: str) -> Path:
    identity = hashlib.sha256(url.encode()).hexdigest()
    return root / "records" / f"{identity}.json"


def validate_acquisition(record: dict[str, Any], url: str) -> None:
    if record.get("schema_version") != "w8-source-acquisition/1":
        raise ValueError("unsupported acquisition record")
    if record.get("url") != url:
        raise ValueError("acquisition URL mismatch")
    text, digest = record.get("reviewed_text"), record.get("reviewed_bytes_sha256")
    if record.get("status") == "acquired":
        if not isinstance(text, str) or not text:
            raise ValueError("acquired record has no reviewed text")
        if hashlib.sha256(text.encode()).hexdigest() != digest:
            raise ValueError("acquired text digest mismatch")
    elif text is not None or digest is not None:
        raise ValueError("unavailable record contains reviewed text")


def exact_identity(candidate: dict[str, Any], acquisition: dict[str, Any]) -> bool:
    observed = {candidate["candidate_url"]}
    if isinstance(acquisition.get("returned_url"), str):
        observed.add(acquisition["returned_url"])
    identities = {canonical_url(value) for value in observed}
    references = {
        canonical_url(item["url"]) for item in candidate["known_useful_sources"]
    }
    return bool(identities & references)


def select_excerpts(candidate: dict[str, Any], text: str) -> list[str]:
    terms = set(TOKEN.findall(candidate["query"].lower()))
    for reference in candidate["known_useful_sources"]:
        terms.update(TOKEN.findall(reference["relevance_judgment"].lower()))
    width, overlap = EXCERPT_WIDTH, EXCERPT_OVERLAP
    chunks = [text[start : start + width] for start in range(0, len(text), width - overlap)]
    ranked = sorted(
        enumerate(chunks),
        key=lambda pair: (
            sum(pair[1].lower().count(term) for term in terms),
            -pair[0],
        ),
        reverse=True,
    )
    selected = sorted(ranked[:EXCERPT_LIMIT])
    return [chunk for _, chunk in selected]


def response_schema(identity_established: bool) -> dict[str, Any]:
    labels = list(LABELS)
    if not identity_established:
        labels.remove("exact_reference")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "label",
            "matched_reference_urls",
            "evidence_quote",
            "reason",
            "confidence",
        ],
        "properties": {
            "label": {"type": "string", "enum": labels},
            "matched_reference_urls": {
                "type": "array",
                "items": {"type": "string"},
            },
            "evidence_quote": {"type": "null"},
            "reason": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
    }


def validate_grade(
    grade: dict[str, Any],
    *,
    candidate: dict[str, Any],
    acquisition: dict[str, Any],
    identity_established: bool,
) -> None:
    if set(grade) != {
        "label",
        "matched_reference_urls",
        "evidence_quote",
        "reason",
        "confidence",
    }:
        raise ValueError("grade fields differ from the contract")
    allowed = set(LABELS) if identity_established else set(LABELS) - {"exact_reference"}
    if grade["label"] not in allowed:
        raise ValueError("grade violates deterministic identity")
    references = {item["url"] for item in candidate["known_useful_sources"]}
    if not isinstance(grade["matched_reference_urls"], list) or not set(
        grade["matched_reference_urls"]
    ) <= references:
        raise ValueError("grade cites an unknown reference")
    if grade["evidence_quote"] is not None:
        raise ValueError("grade evidence_quote must be null")
    if grade["confidence"] not in {"high", "medium", "low"}:
        raise ValueError("invalid grade confidence")
    if not isinstance(grade["reason"], str) or not grade["reason"]:
        raise ValueError("grade reason is empty")


def blind_payload(
    candidate: dict[str, Any], acquisition: dict[str, Any], identity_established: bool
) -> dict[str, Any]:
    text = acquisition.get("reviewed_text")
    return {
        "query": candidate["query"],
        "as_of": candidate["as_of"],
        "expected_ambiguity": candidate["expected_ambiguity"],
        "known_useful_references": candidate["known_useful_sources"],
        "candidate_url": candidate["candidate_url"],
        "returned_url": acquisition.get("returned_url"),
        "exact_identity_established": identity_established,
        "discovery_title": candidate.get("discovery_title"),
        "discovery_snippet": candidate.get("discovery_snippet"),
        "acquisition_status": acquisition["status"],
        "reviewed_bytes_sha256": acquisition.get("reviewed_bytes_sha256"),
        "reviewed_excerpts": select_excerpts(candidate, text) if isinstance(text, str) else [],
    }


def review_item_payload(
    candidate: dict[str, Any], acquisition: dict[str, Any]
) -> dict[str, Any]:
    identity = exact_identity(candidate, acquisition)
    return {
        "candidate_id": candidate["candidate_id"],
        "allowed_labels": list(
            response_schema(identity)["properties"]["label"]["enum"]
        ),
        **blind_payload(candidate, acquisition, identity),
    }


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def batch_response_schema(count: int) -> dict[str, Any]:
    grade = response_schema(True)
    grade["required"] = ["candidate_id", *grade["required"]]
    grade["properties"] = {
        "candidate_id": {"type": "string"},
        **grade["properties"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["grades"],
        "properties": {
            "grades": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": grade,
            }
        },
    }


async def model_grade_batch(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    rubric: str,
    candidates: list[dict[str, Any]],
    acquisitions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payloads = [
        review_item_payload(candidate, acquisitions[candidate["candidate_id"]])
        for candidate in candidates
    ]
    batch_id = hashlib.sha256(
        json.dumps(payloads, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    attempts = []
    correction: str | None = None
    for number in range(1, 4):
        started = time.perf_counter()
        try:
            response = await client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + api_key},
                json={
                    "model": "local",
                    "messages": [
                        {
                            "role": "system",
                            "content": rubric
                            + "\nBatch mode: grade every supplied item independently. Return one "
                            "grades array in candidate_id order. Set every evidence_quote to null. "
                            "For each item, label must be one of that item's allowed_labels; never "
                            "emit exact_reference when exact_identity_established is false.",
                        },
                        {
                            "role": "user",
                            "content": json.dumps({"items": payloads}, ensure_ascii=False),
                        },
                    ]
                    + ([{"role": "user", "content": correction}] if correction else []),
                    "temperature": 0,
                    "max_tokens": max(400, 240 * len(candidates)),
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "source_equivalence_grades",
                            "strict": True,
                            "schema": batch_response_schema(len(candidates)),
                        },
                    },
                },
            )
            response.raise_for_status()
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            grades = decoded["grades"]
            by_id = {item.pop("candidate_id"): item for item in grades}
            expected_ids = {item["candidate_id"] for item in candidates}
            if set(by_id) != expected_ids or len(grades) != len(by_id):
                raise ValueError("batch response candidate identities differ")
            for candidate in candidates:
                acquisition = acquisitions[candidate["candidate_id"]]
                validate_grade(
                    by_id[candidate["candidate_id"]],
                    candidate=candidate,
                    acquisition=acquisition,
                    identity_established=exact_identity(candidate, acquisition),
                )
            attempts.append(
                {
                    "attempt": number,
                    "batch_id": batch_id,
                    "status": "received",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "resolved_model": envelope.get("model"),
                    "usage": envelope.get("usage"),
                    "response_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
            return {
                candidate["candidate_id"]: {
                    "status": "graded",
                    "reviewer": "configured-litellm/local",
                    "exact_identity_established": exact_identity(
                        candidate, acquisitions[candidate["candidate_id"]]
                    ),
                    "grade": by_id[candidate["candidate_id"]],
                    "attempts": attempts,
                }
                for candidate in candidates
            }
        except Exception as error:
            correction = "Your previous response failed validation: " + str(error)[:300] + ". "
            if number == 1:
                correction += "Return corrected JSON with evidence_quote set to null."
            else:
                correction += "Return corrected JSON with evidence_quote set to null."
            attempts.append(
                {
                    "attempt": number,
                    "batch_id": batch_id,
                    "status": "failed",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    return {
        candidate["candidate_id"]: {
            "status": "grading_failed",
            "reviewer": "configured-litellm/local",
            "exact_identity_established": exact_identity(
                candidate, acquisitions[candidate["candidate_id"]]
            ),
            "grade": None,
            "attempts": attempts,
        }
        for candidate in candidates
    }


async def run(args: argparse.Namespace) -> int:
    if file_digest(args.pool) != args.pool_sha256:
        raise ValueError("review-pool digest mismatch")
    if file_digest(args.rubric) != args.rubric_sha256:
        raise ValueError("rubric digest mismatch")
    pool = json.loads(args.pool.read_text())
    if pool.get("schema_version") != "w8-source-equivalence-pool/1":
        raise ValueError("unsupported review pool")
    candidates = pool["candidates"]
    if len({item["candidate_id"] for item in candidates}) != len(candidates):
        raise ValueError("duplicate candidate identity")
    if any(FORBIDDEN_BLIND_KEYS & set(item) for item in candidates):
        raise ValueError("review pool discloses arm or rank")
    acquisitions = {}
    for item in candidates:
        path = acquisition_path(args.acquisitions, item["candidate_url"])
        if not path.exists():
            raise ValueError("acquisition set is incomplete")
        record = json.loads(path.read_text())
        validate_acquisition(record, item["candidate_url"])
        acquisitions[item["candidate_id"]] = record
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    records = args.output / "records"
    records.mkdir(mode=0o700, exist_ok=True)
    rubric = args.rubric.read_text()
    pending = [
        item for item in candidates
        if not (records / f"{item['candidate_id']}.json").exists()
    ]
    limits = httpx.Limits(max_connections=args.concurrency)
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=300, limits=limits, follow_redirects=False) as client:
        probe = await client.get(
            args.base_url.rstrip("/") + "/models",
            headers={"Authorization": "Bearer " + args.api_key},
        )
        probe.raise_for_status()
        if not any(item.get("id") == "local" for item in probe.json().get("data", [])):
            raise ValueError("gateway does not advertise local")

        async def grade_group(group: list[dict[str, Any]]) -> None:
            results: dict[str, dict[str, Any]] = {}
            model_candidates = [
                candidate
                for candidate in group
                if acquisitions[candidate["candidate_id"]]["status"] == "acquired"
            ]
            if model_candidates:
                async with semaphore:
                    results.update(
                        await model_grade_batch(
                            client,
                            base_url=args.base_url,
                            api_key=args.api_key,
                            rubric=rubric,
                            candidates=model_candidates,
                            acquisitions=acquisitions,
                        )
                    )
            for candidate in group:
                acquisition = acquisitions[candidate["candidate_id"]]
                if acquisition["status"] != "acquired":
                    results[candidate["candidate_id"]] = {
                    "status": "graded",
                    "reviewer": "deterministic-acquisition-gate/1",
                    "exact_identity_established": exact_identity(candidate, acquisition),
                    "grade": {
                        "label": "unavailable_indeterminate",
                        "matched_reference_urls": [],
                        "evidence_quote": None,
                        "reason": "No usable acquired text was available for review.",
                        "confidence": "high",
                    },
                    "attempts": [],
                }
                record = {
                    "schema_version": "w8-source-equivalence-grade/1",
                    "graded_at": timestamp(),
                    "candidate_id": candidate["candidate_id"],
                    "case_id": candidate["case_id"],
                    "candidate_url": candidate["candidate_url"],
                    "review_payload_sha256": hashlib.sha256(
                        json.dumps(
                            review_item_payload(candidate, acquisition),
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                    "acquisition_sha256": file_digest(
                        acquisition_path(args.acquisitions, candidate["candidate_url"])
                    ),
                    **results[candidate["candidate_id"]],
                }
                atomic_json(records / f"{candidate['candidate_id']}.json", record)

        groups = [pending[start : start + args.batch_size] for start in range(0, len(pending), args.batch_size)]
        for start in range(0, len(groups), args.concurrency):
            await asyncio.gather(*(grade_group(group) for group in groups[start : start + args.concurrency]))
            done = list(records.glob("*.json"))
            status = Counter(json.loads(path.read_text())["status"] for path in done)
            atomic_json(
                args.output / "progress.json",
                {
                    "schema_version": "w8-source-equivalence-grade-progress/1",
                    "completed": len(done),
                    "remaining": len(candidates) - len(done),
                    "statuses": dict(status),
                },
            )
    final = [json.loads(path.read_text()) for path in records.glob("*.json")]
    grades = [item["grade"] for item in final if item["status"] == "graded"]
    receipts = {
        (attempt["batch_id"], attempt["attempt"]): attempt
        for item in final
        for attempt in item["attempts"]
        if attempt["status"] == "received"
    }
    latencies = [attempt["latency_ms"] for attempt in receipts.values()]
    usage = [attempt.get("usage") or {} for attempt in receipts.values()]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pool_sha256": args.pool_sha256,
        "rubric_sha256": args.rubric_sha256,
        "candidate_count": len(candidates),
        "graded": len(grades),
        "failed": sum(item["status"] != "graded" for item in final),
        "labels_blinded": dict(Counter(item["label"] for item in grades)),
        "model_calls": len(latencies),
        "prompt_tokens": sum(item.get("prompt_tokens", 0) for item in usage),
        "completion_tokens": sum(item.get("completion_tokens", 0) for item in usage),
        "p50_model_latency_ms": statistics.median(latencies) if latencies else None,
        "p95_model_latency_ms": sorted(latencies)[int((len(latencies) - 1) * 0.95)] if latencies else None,
        "arm_map_read": False,
    }
    atomic_json(args.output / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--pool-sha256", required=True)
    parser.add_argument("--acquisitions", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--rubric-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", ""))
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 33))
    parser.add_argument("--batch-size", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    if not args.base_url:
        parser.error("LLM_BASE_URL or --base-url is required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
