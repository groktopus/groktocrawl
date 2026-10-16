"""Tests for the private Candidate D packet validator."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate-candidate-d-packet.py"
SPEC = importlib.util.spec_from_file_location("candidate_d_packet", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CATEGORIES = sorted(MODULE.CATEGORIES)


def _write_packet(packet: Path) -> dict[str, str]:
    packet.mkdir(mode=0o700)
    sources = []
    for index in range(8):
        captured = f"source text {index}"
        sources.append(
            {
                "source_id": f"source-{index}",
                "title": f"Source {index}",
                "canonical_url": f"https://example.com/{index}",
                "retrieved_at": "2026-09-10T12:00:00Z",
                "captured_text": captured,
                "content_sha256": hashlib.sha256(captured.encode()).hexdigest(),
            }
        )
    cases = []
    for index in range(30):
        tags = ["high_consequence"]
        if index < 12:
            tags.extend(["contradiction", "insufficient_evidence", "scope_limit"])
        if index < 4:
            tags.append("time_awareness")
        cases.append(
            {
                "case_id": f"case-{index}",
                "split": "held_out_candidate_d",
                "category": CATEGORIES[index % len(CATEGORIES)],
                "question": f"Question {index}?",
                "as_of": "2026-09-10T12:00:00Z",
                "required_subquestions": [
                    {
                        "id": "part-a",
                        "prompt": "Resolve this part.",
                        "resolving_source_ids": [f"source-{index % 8}"],
                    }
                ],
                "risk_tags": tags,
            }
        )
    files = {
        "corpus.json": json.dumps(
            {
                "schema_version": "enterprise-research-packet/1",
                "created_at": "2026-09-10T12:00:00Z",
                "domain": "enterprise agentic-engineering software factories",
                "sources": sources,
                "cases": cases,
            }
        ),
        "access-log.json": json.dumps(
            {
                "schema_version": "enterprise-research-access-log/1",
                "statement": "No candidate implementation or output was accessed.",
                "entries": [{"url": "https://example.com/method"}],
            }
        ),
        "summary.md": "Private packet summary.\n",
    }
    digests = {}
    for name, content in files.items():
        path = packet / name
        path.write_text(content)
        path.chmod(0o600)
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def test_valid_packet_returns_aggregates_only(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    expected = _write_packet(packet)

    result = MODULE.validate(packet, expected)

    assert result == {
        "valid": True,
        "cases": 30,
        "sources": 8,
        "categories": {
            "architecture": 4,
            "data": 4,
            "economics": 4,
            "evaluation": 4,
            "governance": 4,
            "operations": 4,
            "organization": 3,
            "security": 3,
        },
        "consultations": 1,
    }
    assert "question" not in json.dumps(result)


def test_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    expected = _write_packet(packet)
    expected["corpus.json"] = "0" * 64

    with pytest.raises(ValueError, match="digest differs"):
        MODULE.validate(packet, expected)


def test_unknown_source_reference_fails_closed(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    expected = _write_packet(packet)
    corpus_path = packet / "corpus.json"
    corpus = json.loads(corpus_path.read_text())
    corpus["cases"][0]["required_subquestions"][0]["resolving_source_ids"] = [
        "source-missing"
    ]
    corpus_path.write_text(json.dumps(corpus))
    expected["corpus.json"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="source references do not close"):
        MODULE.validate(packet, expected)
