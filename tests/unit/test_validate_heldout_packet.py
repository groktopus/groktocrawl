import hashlib
import json
from pathlib import Path

from scripts.validate_heldout_packet import validate_packet


def write_packet(tmp_path: Path, *, implementation_visible: bool = False) -> Path:
    packet = tmp_path / "packet"
    packet.mkdir()
    sources = []
    for index in range(6):
        text = f"Source text {index}."
        sources.append(
            {
                "source_id": f"source-{index}",
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "lineage_id": f"lineage-{index}",
            }
        )
    cases = []
    for index in range(30):
        cases.append(
            {
                "case_id": f"heldout-{index}",
                "split": "held_out_candidate",
                "category": f"topic-{index % 6}",
                "template_family": f"family-{index % 6}",
                "question": f"Question {index}?",
                "as_of": "2026-09-08T00:00:00Z",
                "source_ids": [f"source-{index % 6}"],
                "required_subquestions": [{"id": f"q-{index}"}],
                "negative_or_abstention": index < 6,
            }
        )
    (packet / "corpus.json").write_text(json.dumps({"cases": cases, "sources": sources}))
    (packet / "access-log.json").write_text(
        json.dumps(
            {
                "implementation_visible": implementation_visible,
                "curator": "reviewer@example.test",
                "sealed_at": "2026-09-08T00:00:00Z",
                "covers_all_cases": True,
                "entries": [{"actor": "reviewer@example.test", "action": "sealed"}],
            }
        )
    )
    return packet


def test_validate_heldout_packet_requires_explicit_approval_and_isolation(tmp_path):
    report = validate_packet(write_packet(tmp_path))

    assert report["status"] == "candidate_validation_passed"
    assert report["held_out_eligible"] is False
    assert report["cases"] == 30
    assert report["adverse_or_abstention_cases"] == 6
    assert report["errors"] == []


def test_validate_heldout_packet_rejects_exposure_and_known_overlap(tmp_path):
    packet = write_packet(tmp_path, implementation_visible=True)
    known = tmp_path / "known.json"
    known.write_text(json.dumps({"cases": [{"case_id": "heldout-0", "question": "Question 0?"}]}))

    report = validate_packet(packet, known)

    assert report["status"] == "candidate_validation_failed"
    assert report["held_out_eligible"] is False
    assert any("overlap" in error for error in report["errors"])
    assert any("implementation_visible" in error for error in report["errors"])
