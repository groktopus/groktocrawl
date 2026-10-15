#!/usr/bin/env python3
"""Validate the private Candidate D packet without printing held-out content."""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CATEGORIES = {
    "architecture",
    "security",
    "governance",
    "operations",
    "evaluation",
    "data",
    "organization",
    "economics",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not UTC_PATTERN.fullmatch(value):
        return False
    datetime.fromisoformat(value)
    return True


def exact_keys(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} has invalid fields")
    return value


def validate(packet: Path, expected: dict[str, str]) -> dict[str, Any]:
    if packet.stat().st_mode & 0o777 != 0o700:
        raise ValueError("packet directory must have mode 700")
    files = {name: packet / name for name in expected}
    for name, path in files.items():
        if not path.is_file() or path.stat().st_mode & 0o777 != 0o600:
            raise ValueError(f"private {name} is missing or not mode 600")
        if digest(path) != expected[name]:
            raise ValueError(f"private {name} digest differs")

    corpus = exact_keys(
        json.loads(files["corpus.json"].read_text()),
        {"schema_version", "created_at", "domain", "sources", "cases"},
        "corpus",
    )
    if corpus["schema_version"] != "enterprise-research-packet/1" or not timestamp(
        corpus["created_at"]
    ):
        raise ValueError("corpus identity or timestamp is invalid")
    if corpus["domain"] != "enterprise agentic-engineering software factories":
        raise ValueError("corpus domain differs")

    sources = corpus["sources"]
    if not isinstance(sources, list) or not 8 <= len(sources) <= 30:
        raise ValueError("source count is outside the accepted range")
    source_ids: set[str] = set()
    for item in sources:
        source = exact_keys(
            item,
            {
                "source_id",
                "title",
                "canonical_url",
                "retrieved_at",
                "captured_text",
                "content_sha256",
            },
            "source",
        )
        if (
            not isinstance(source["source_id"], str)
            or not source["source_id"]
            or source["source_id"] in source_ids
        ):
            raise ValueError("source identities must be nonempty and unique")
        source_ids.add(source["source_id"])
        parsed = urlsplit(source["canonical_url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source URL is not an eligible public HTTPS identity")
        if not timestamp(source["retrieved_at"]):
            raise ValueError("source retrieval timestamp is invalid")
        text = source["captured_text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("captured source text is empty")
        if source["content_sha256"] != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError("captured source digest differs")

    cases = corpus["cases"]
    if not isinstance(cases, list) or len(cases) != 30:
        raise ValueError("packet must contain exactly 30 cases")
    case_ids: set[str] = set()
    categories: Counter[str] = Counter()
    risks: Counter[str] = Counter()
    for item in cases:
        case = exact_keys(
            item,
            {
                "case_id",
                "split",
                "category",
                "question",
                "as_of",
                "required_subquestions",
                "risk_tags",
            },
            "case",
        )
        if (
            not isinstance(case["case_id"], str)
            or not case["case_id"]
            or case["case_id"] in case_ids
        ):
            raise ValueError("case identities must be nonempty and unique")
        case_ids.add(case["case_id"])
        if case["split"] != "held_out_candidate_d" or not timestamp(case["as_of"]):
            raise ValueError("case split or as-of timestamp differs")
        if case["category"] not in CATEGORIES:
            raise ValueError("case category differs")
        categories[case["category"]] += 1
        if not isinstance(case["question"], str) or not case["question"].strip():
            raise ValueError("case question is empty")
        subquestions = case["required_subquestions"]
        if not isinstance(subquestions, list) or not subquestions:
            raise ValueError("case requires subquestions")
        subquestion_ids: set[str] = set()
        for item in subquestions:
            subquestion = exact_keys(
                item, {"id", "prompt", "resolving_source_ids"}, "subquestion"
            )
            if (
                not isinstance(subquestion["id"], str)
                or not subquestion["id"]
                or subquestion["id"] in subquestion_ids
            ):
                raise ValueError("subquestion identities must be locally unique")
            subquestion_ids.add(subquestion["id"])
            if not isinstance(subquestion["prompt"], str) or not subquestion[
                "prompt"
            ].strip():
                raise ValueError("subquestion prompt is empty")
            resolving = subquestion["resolving_source_ids"]
            if (
                not isinstance(resolving, list)
                or not resolving
                or len(set(resolving)) != len(resolving)
                or not set(resolving) <= source_ids
            ):
                raise ValueError("subquestion source references do not close")
        tags = case["risk_tags"]
        if not isinstance(tags, list) or not tags or len(set(tags)) != len(tags):
            raise ValueError("case risk tags must be nonempty and unique")
        risks.update(tags)

    if set(categories) != CATEGORIES or any(count not in {3, 4} for count in categories.values()):
        raise ValueError("category balance differs")
    if risks["contradiction"] < 4 or risks["insufficient_evidence"] < 4:
        raise ValueError("adversarial evidence coverage is too small")
    if risks["high_consequence"] < 4 or risks["scope_limit"] < 4:
        raise ValueError("risk and scope coverage is too small")
    if risks["time_awareness"] + risks["time_sensitive"] < 4:
        raise ValueError("time-awareness coverage is too small")

    access = exact_keys(
        json.loads(files["access-log.json"].read_text()),
        {"schema_version", "statement", "entries"},
        "access log",
    )
    if access["schema_version"] != "enterprise-research-access-log/1":
        raise ValueError("access-log version differs")
    statement = access["statement"]
    required_statement = "No candidate implementation or output was accessed."
    if not isinstance(statement, str) or required_statement not in statement:
        raise ValueError("access log lacks the isolation declaration")
    entries = access["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("access log has no consultations")
    serialized_entries = json.dumps(entries).lower()
    if "groktocrawl-x" in serialized_entries or "candidate d" in serialized_entries:
        raise ValueError("access log reports candidate-workspace access")

    return {
        "valid": True,
        "cases": len(cases),
        "sources": len(sources),
        "categories": dict(sorted(categories.items())),
        "consultations": len(entries),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--corpus-sha256", required=True)
    parser.add_argument("--access-log-sha256", required=True)
    parser.add_argument("--summary-sha256", required=True)
    arguments = parser.parse_args()
    try:
        result = validate(
            arguments.packet,
            {
                "corpus.json": arguments.corpus_sha256,
                "access-log.json": arguments.access_log_sha256,
                "summary.md": arguments.summary_sha256,
            },
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, json.dumps({"valid": False, "error": str(error)}) + "\n")
    print(json.dumps(result, sort_keys=True))
