from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_script(name: str):
    path = Path(__file__).parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grader = load_script("run_w8_source_equivalence_grading.py")
aggregator = load_script("aggregate_w8_source_equivalence.py")
freezer = load_script("freeze_w8_source_equivalence.py")


def candidate(url: str = "https://example.com/article") -> dict:
    return {
        "candidate_id": "c1",
        "case_id": "q1",
        "candidate_url": url,
        "query": "Who launched Project Alder?",
        "as_of": "2026-09-11",
        "expected_ambiguity": "Identify the named project.",
        "known_useful_sources": [
            {"url": "https://reference.example/alder", "relevance_judgment": "Official launch details"}
        ],
    }


def acquisition(text: str = "Official launch details for Project Alder.") -> dict:
    return {"status": "acquired", "returned_url": "https://example.com/article", "reviewed_text": text}


def test_canonical_url_only_applies_conservative_normalization() -> None:
    assert grader.canonical_url("HTTPS://Example.COM:443/path/#fragment") == "https://example.com/path"
    assert grader.canonical_url("https://example.com/path?a=1") != grader.canonical_url("https://example.com/path?a=2")


def test_exact_identity_accepts_candidate_or_returned_url() -> None:
    item = candidate("https://reference.example/alder/")
    assert grader.exact_identity(item, acquisition())
    item = candidate()
    record = acquisition()
    record["returned_url"] = "https://reference.example/alder#section"
    assert grader.exact_identity(item, record)


def test_schema_excludes_exact_reference_without_identity() -> None:
    assert "exact_reference" not in grader.response_schema(False)["properties"]["label"]["enum"]
    assert "exact_reference" in grader.response_schema(True)["properties"]["label"]["enum"]


def test_batch_schema_pins_item_count_and_identity() -> None:
    schema = grader.batch_response_schema(5)
    grades = schema["properties"]["grades"]
    assert grades["minItems"] == grades["maxItems"] == 5
    assert "candidate_id" in grades["items"]["required"]
    assert grades["items"]["properties"]["evidence_quote"] == {"type": "null"}


def test_review_item_payload_pins_allowed_labels() -> None:
    payload = grader.review_item_payload(candidate(), acquisition())
    assert "exact_reference" not in payload["allowed_labels"]
    assert payload["candidate_id"] == "c1"


def test_grade_quote_must_be_null() -> None:
    grade = {
        "label": "substantively_equivalent",
        "matched_reference_urls": ["https://reference.example/alder"],
        "evidence_quote": "invented quote",
        "reason": "Relevant.",
        "confidence": "high",
    }
    with pytest.raises(ValueError, match="evidence_quote"):
        grader.validate_grade(grade, candidate=candidate(), acquisition=acquisition(), identity_established=False)


def test_excerpt_selection_is_bounded_and_query_directed() -> None:
    text = "noise " * 4000 + "Project Alder official launch details"
    excerpts = grader.select_excerpts(candidate(), text)
    assert len(excerpts) <= 3
    assert all(len(item) <= 2000 for item in excerpts)
    assert any("Project Alder official" in item for item in excerpts)


def test_aggregate_counts_shared_candidates_in_each_arm() -> None:
    grades = [{
        "candidate_id": "c1", "status": "graded",
        "grade": {"label": "substantively_equivalent"},
    }]
    mapping = [{
        "candidate_id": "c1", "case_id": "q1",
        "sightings": [
            {"arm": "baseline", "position": 2, "search_index": 1},
            {"arm": "adaptive", "position": 1, "search_index": 1},
        ],
    }]
    result = aggregator.aggregate(grades, mapping)
    assert result["arms"]["baseline"]["useful_sightings"] == 1
    assert result["arms"]["adaptive"]["useful_sightings"] == 1
    assert result["arms"]["baseline"]["mean_reciprocal_rank_first_useful"] == 0.5


def test_aggregate_refuses_incomplete_grades() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        aggregator.aggregate(
            [{"candidate_id": "c1", "status": "grading_failed", "grade": None}],
            [{"candidate_id": "c1", "case_id": "q1", "sightings": []}],
        )


def test_aggregate_requires_a_sealed_complete_freeze() -> None:
    freeze = {
        "schema_version": "w8-source-equivalence-grade-freeze/1",
        "record_count": 1,
        "arm_map_read": False,
    }
    aggregator.validate_freeze(freeze, 1)
    freeze["arm_map_read"] = True
    with pytest.raises(ValueError, match="sealed arms"):
        aggregator.validate_freeze(freeze, 1)


def test_freeze_requires_complete_successful_identity_matched_records(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    record = {
        "schema_version": "w8-source-equivalence-grade/1",
        "candidate_id": "c1",
        "status": "graded",
        "grade": {"label": "unrelated"},
    }
    (records / "c1.json").write_text(json.dumps(record))
    freeze = freezer.build_freeze(tmp_path, expected_count=1)
    assert freeze["record_count"] == 1
    assert set(freeze["record_sha256"]) == {"c1.json"}
    assert freeze["arm_map_read"] is False

    with pytest.raises(ValueError, match="incomplete"):
        freezer.build_freeze(tmp_path, expected_count=2)


def test_freeze_rejects_failed_grade(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    record = {
        "schema_version": "w8-source-equivalence-grade/1",
        "candidate_id": "c1",
        "status": "grading_failed",
        "grade": None,
    }
    (records / "c1.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="not successful"):
        freezer.build_freeze(tmp_path, expected_count=1)


def test_freeze_rejects_filename_identity_mismatch(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    record = {
        "schema_version": "w8-source-equivalence-grade/1",
        "candidate_id": "c1",
        "status": "graded",
        "grade": {"label": "unrelated"},
    }
    (records / "wrong.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="filename"):
        freezer.build_freeze(tmp_path, expected_count=1)
