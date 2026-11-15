"""Regression tests for bounded, resumable research synthesis state."""

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).parents[1] / "research" / "chunked_synthesis.py"
_SPEC = importlib.util.spec_from_file_location("chunked_synthesis", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
assemble_sections = _MODULE.assemble_sections
build_evidence_manifest = _MODULE.build_evidence_manifest
choose_continuity = _MODULE.choose_continuity
checkpoint_section = _MODULE.checkpoint_section
assign_chunks_to_sections = _MODULE.assign_chunks_to_sections
validate_outline = _MODULE.validate_outline


def test_manifest_covers_every_source_chunk_without_loss():
    sources = [
        {"id": "https://example.test/a", "title": "A", "markdown": "alpha\n" * 40},
        {"id": "https://example.test/b", "title": "B", "markdown": "beta\n" * 23},
    ]

    manifest = build_evidence_manifest(sources, chunk_chars=64)

    chunks = manifest["chunks"]
    assert len(chunks) > len(sources)
    for source in sources:
        source_chunks = [item for item in chunks if item["source_id"] == source["id"]]
        assert (
            "".join(source["markdown"][c["start"] : c["end"]] for c in source_chunks)
            == source["markdown"]
        )
        assert all(c["content_sha256"] for c in source_chunks)


def test_continuity_prefers_full_prior_sections_when_within_input_budget():
    outline = {
        "sections": [{"id": "s1", "title": "Origins"}, {"id": "s2", "title": "Mandate"}]
    }
    completed = [
        {
            "id": "s1",
            "markdown": "Ottoman context [1]",
            "continuity": {"claims": ["Ottoman context"]},
        }
    ]

    result = choose_continuity(outline, completed, max_chars=1000, evidence_chars=100)

    assert result["mode"] == "full_prior_sections"
    assert "Ottoman context [1]" in result["text"]
    assert result["evidence_reserved_chars"] == 100


def test_continuity_uses_structured_digest_without_silent_truncation():
    outline = {
        "sections": [{"id": "s1", "title": "Origins"}, {"id": "s2", "title": "Mandate"}]
    }
    completed = [
        {
            "id": "s1",
            "markdown": "A" * 1000,
            "continuity": {
                "claims": ["claim A"],
                "terminology": ["mandate"],
                "transitions": ["next"],
                "unresolved": ["date"],
            },
        }
    ]

    result = choose_continuity(outline, completed, max_chars=500, evidence_chars=100)

    assert result["mode"] == "structured_digest"
    assert "claim A" in result["text"]
    assert "mandate" in result["text"]
    assert "unresolved" in result["text"].lower()
    assert "A" * 100 not in result["text"]
    assert result["evidence_reserved_chars"] == 100


def test_assembly_requires_exactly_once_all_sections_and_valid_citations():
    outline = {
        "sections": [{"id": "s1", "title": "Origins"}, {"id": "s2", "title": "Mandate"}]
    }
    sections = [
        {"id": "s1", "markdown": "Origins [1]"},
        {"id": "s2", "markdown": "Mandate [2]"},
    ]

    assert (
        assemble_sections(outline, sections, source_count=2)
        == "## Origins\n\nOrigins [1]\n\n## Mandate\n\nMandate [2]"
    )
    with pytest.raises(ValueError, match="exactly once"):
        assemble_sections(outline, [*sections, sections[0]], source_count=2)
    with pytest.raises(ValueError, match="citation"):
        assemble_sections(
            outline,
            [sections[0], {"id": "s2", "markdown": "Mandate [3]"}],
            source_count=2,
        )


def test_resume_preserves_completed_section_and_advances_exactly_once():
    state = {
        "outline": {
            "sections": [
                {"id": "s1", "title": "Origins"},
                {"id": "s2", "title": "Mandate"},
            ]
        },
        "completed_sections": [],
    }
    completed = {
        "id": "s1",
        "markdown": "Origins [1]",
        "continuity": {
            "claims": ["Ottoman"],
            "terminology": [],
            "transitions": [],
            "unresolved": [],
        },
    }

    first = checkpoint_section(state, completed)
    resumed = checkpoint_section(
        first,
        {
            "id": "s2",
            "markdown": "Mandate [2]",
            "continuity": {
                "claims": [],
                "terminology": ["mandate"],
                "transitions": [],
                "unresolved": [],
            },
        },
    )

    assert first["next_section_id"] == "s2"
    assert first["completed_sections"][0] == completed
    assert resumed["status"] == "complete"
    assert resumed["completed_sections"][0] == completed
    with pytest.raises(ValueError, match="already"):
        checkpoint_section(first, completed)


def test_outline_assignment_covers_each_chunk_once_and_is_deterministic():
    manifest = build_evidence_manifest(
        [
            {
                "id": "https://example.test/ottoman",
                "title": "Ottoman Empire",
                "markdown": "a" * 200,
            },
            {
                "id": "https://example.test/mandate",
                "title": "British Mandate",
                "markdown": "b" * 200,
            },
        ],
        chunk_chars=50,
    )
    outline = validate_outline(
        {
            "sections": [
                {"id": "ottoman", "title": "Ottoman context"},
                {"id": "mandate", "title": "British mandate"},
            ]
        }
    )

    first = assign_chunks_to_sections(manifest, outline)
    assert first == assign_chunks_to_sections(manifest, outline)
    assert sum(map(len, first["sections"].values())) == len(manifest["chunks"])
    assert len(
        {chunk for group in first["sections"].values() for chunk in group}
    ) == len(manifest["chunks"])
    order = {chunk["id"]: index for index, chunk in enumerate(manifest["chunks"])}
    assert all(
        [order[chunk_id] for chunk_id in assigned]
        == sorted(order[chunk_id] for chunk_id in assigned)
        for assigned in first["sections"].values()
    )
