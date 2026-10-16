#!/usr/bin/env python3
"""Validate a proposed held-out evaluation packet without claiming independence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

MIN_CASES = 30
MIN_ADVERSE_FRACTION = 0.20
HELD_OUT_SPLIT = "held_out_candidate"


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def normalized_question(value: str) -> str:
    return " ".join(value.split()).casefold()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_packet(packet: Path, known_corpus: Path | None = None) -> dict[str, Any]:
    """Return a reviewable validation report; errors make the packet ineligible."""
    errors: list[str] = []
    corpus_path = packet / "corpus.json"
    access_path = packet / "access-log.json"
    corpus: dict[str, Any] = {}
    access: dict[str, Any] = {}

    try:
        corpus = load_json(corpus_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"corpus.json cannot be read: {exc}")
    try:
        access = load_json(access_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"access-log.json cannot be read: {exc}")

    cases = corpus.get("cases", []) if isinstance(corpus, dict) else []
    sources = corpus.get("sources", []) if isinstance(corpus, dict) else []
    if not isinstance(cases, list):
        errors.append("corpus cases must be a list")
        cases = []
    if not isinstance(sources, list):
        errors.append("corpus sources must be a list")
        sources = []

    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    questions = [
        normalized_question(case.get("question", ""))
        for case in cases
        if isinstance(case, dict)
    ]
    if len(cases) < MIN_CASES:
        errors.append(f"held-out packet needs at least {MIN_CASES} cases")
    if len(case_ids) != len(set(case_ids)):
        errors.append("case IDs must be unique")
    if len(questions) != len(set(questions)):
        errors.append("normalized questions must be unique")

    source_by_id = {
        source.get("source_id"): source
        for source in sources
        if isinstance(source, dict)
    }
    adverse_count = 0
    categories: Counter[str] = Counter()
    families: Counter[str] = Counter()
    for case in cases:
        if not isinstance(case, dict):
            errors.append("each case must be an object")
            continue
        if case.get("split") != HELD_OUT_SPLIT:
            errors.append(f"{case.get('case_id', '<unknown>')} is not {HELD_OUT_SPLIT}")
        if not case.get("question") or not case.get("as_of"):
            errors.append(f"{case.get('case_id', '<unknown>')} needs question and as_of")
        if case.get("negative_or_abstention") is True:
            adverse_count += 1
        categories[str(case.get("category", ""))] += 1
        families[str(case.get("template_family", ""))] += 1
        if "expected_claims_status" in case or case.get("author_exposed") is True:
            errors.append(f"{case.get('case_id', '<unknown>')} exposes expectations")
        subquestions = case.get("required_subquestions")
        if not isinstance(subquestions, list) or not subquestions:
            errors.append(f"{case.get('case_id', '<unknown>')} needs subquestions")
        else:
            sub_ids = [item.get("id") for item in subquestions if isinstance(item, dict)]
            if len(sub_ids) != len(set(sub_ids)) or any(not item for item in sub_ids):
                errors.append(f"{case.get('case_id', '<unknown>')} has invalid subquestion IDs")
            if any("expectation" in item for item in subquestions if isinstance(item, dict)):
                errors.append(f"{case.get('case_id', '<unknown>')} exposes subquestion expectations")
        for source_id in case.get("source_ids", []):
            source = source_by_id.get(source_id)
            if not isinstance(source, dict):
                errors.append(f"{case.get('case_id', '<unknown>')} references missing source {source_id}")

    if adverse_count < math.ceil(len(cases) * MIN_ADVERSE_FRACTION):
        errors.append("packet does not meet the 20% adverse/abstention minimum")
    if len(categories) < 6:
        errors.append("packet needs at least six topic categories")
    if len(families) < 6:
        errors.append("packet needs at least six template families")

    for source in sources:
        if not isinstance(source, dict):
            errors.append("each source must be an object")
            continue
        text = source.get("text")
        if not isinstance(text, str) or not text:
            errors.append(f"source {source.get('source_id', '<unknown>')} has no text")
        elif source.get("sha256") != hashlib.sha256(text.encode()).hexdigest():
            errors.append(f"source {source.get('source_id', '<unknown>')} hash does not match text")
        if not source.get("lineage_id"):
            errors.append(f"source {source.get('source_id', '<unknown>')} needs lineage_id")

    if known_corpus is not None:
        try:
            known = load_json(known_corpus)
            known_cases = known.get("cases", [])
            known_ids = {case.get("case_id") for case in known_cases if isinstance(case, dict)}
            known_questions = {
                normalized_question(case.get("question", ""))
                for case in known_cases
                if isinstance(case, dict)
            }
            if known_ids.intersection(case_ids):
                errors.append("held-out case IDs overlap the exposed corpus")
            if known_questions.intersection(questions):
                errors.append("held-out questions overlap the exposed corpus")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"known corpus cannot be read: {exc}")

    if not isinstance(access, dict):
        errors.append("access-log.json must be an object")
        access = {}
    if access.get("implementation_visible") is not False:
        errors.append("access log must explicitly record implementation_visible=false")
    if not access.get("curator") or not access.get("sealed_at"):
        errors.append("access log needs curator and sealed_at")
    if not isinstance(access.get("entries"), list) or not access["entries"]:
        errors.append("access log needs at least one entry")
    if access.get("covers_all_cases") is not True:
        errors.append("access log must record covers_all_cases=true")

    report = {
        "schema_version": "enterprise-evaluation/heldout-validation/1",
        "status": "candidate_validation_failed" if errors else "candidate_validation_passed",
        "held_out_eligible": False,
        "cases": len(cases),
        "adverse_or_abstention_cases": adverse_count,
        "topic_categories": dict(categories),
        "template_families": dict(families),
        "corpus_sha256": digest_bytes(corpus_path.read_bytes()) if corpus_path.exists() else None,
        "access_log_sha256": digest_bytes(access_path.read_bytes()) if access_path.exists() else None,
        "errors": errors,
        "human_approval_required": "Provide a named reviewer approval after isolation review; this tool cannot prove independence.",
    }
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--packet", type=Path, required=True)
    result.add_argument("--known-corpus", type=Path)
    result.add_argument("--output", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    report = validate_packet(args.packet, args.known_corpus)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
