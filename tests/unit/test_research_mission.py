from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.experimental.research_mission import (
    ResearchMission,
    load_mission_experiment_corpus,
    render_control_brief,
    validate_research_mission,
)


def mission_payload() -> dict:
    return {
        "schema_version": "research-mission/1",
        "mission_id": "case-01",
        "decision": "Choose whether to require signed build provenance.",
        "audience": "Engineering and security leaders",
        "scope": {
            "include": ["enterprise agentic software delivery"],
            "exclude": ["consumer coding assistants"],
        },
        "questions": [
            {
                "question_id": "q1",
                "text": "What decision-relevant risks change?",
                "required": True,
            }
        ],
        "obligations": [
            {
                "obligation_id": "o1",
                "question_id": "q1",
                "description": "Identify measured or documented risk changes.",
                "weight": 5,
                "evidence_role": "primary_authority",
                "closure_rule": "At least one admissible source supports the claim.",
            }
        ],
        "source_policy": {
            "allowed_authority": ["primary", "official", "independent"],
            "minimum_independent_publishers": 1,
            "derivative_sources_count_as_independent": False,
            "inaccessible_sources_can_close_obligations": False,
        },
        "freshness": {
            "mode": "as_of",
            "as_of": "2026-09-19T00:00:00Z",
            "rationale": "The decision uses the current control landscape.",
        },
        "contradiction_policy": "preserve_and_report",
        "acceptable_uncertainty": "Label estimates and unresolved disagreements.",
        "clarifications": [],
        "budget": {
            "max_searches": 4,
            "max_sources": 10,
            "max_model_calls": 6,
            "max_elapsed_seconds": 300,
        },
        "stop_when": ["Every required obligation is closed or marked unresolved."],
        "abstain_when": ["No admissible evidence supports a material answer."],
    }


def test_strict_mission_validates_and_renders_equivalent_control_prose():
    mission = validate_research_mission(mission_payload())
    assert isinstance(mission, ResearchMission)
    assert mission.freshness.as_of == datetime(2026, 9, 19, tzinfo=UTC)
    rendered = render_control_brief(mission)
    assert "Choose whether to require signed build provenance" in rendered
    assert "What decision-relevant risks change?" in rendered
    assert "Identify measured or documented risk changes" in rendered
    assert "2026-09-19T00:00:00+00:00" in rendered
    assert "Derivative sources do not count as independent" in rendered


def test_unknown_fields_and_permission_like_extensions_fail_closed():
    payload = mission_payload()
    payload["tool_permissions"] = ["publish", "write-production"]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_research_mission(payload)


def test_required_question_must_have_an_obligation():
    payload = mission_payload()
    payload["questions"].append(
        {"question_id": "q2", "text": "What remains uncertain?", "required": True}
    )
    with pytest.raises(ValueError, match="required question"):
        validate_research_mission(payload)


def test_obligation_cannot_reference_an_unknown_question():
    payload = mission_payload()
    payload["obligations"][0]["question_id"] = "missing"
    with pytest.raises(ValueError, match="reference a mission question"):
        validate_research_mission(payload)


def test_freshness_boundary_requires_utc_and_matching_mode():
    payload = mission_payload()
    payload["freshness"]["as_of"] = "2026-09-19T00:00:00"
    with pytest.raises(ValueError, match="explicit UTC offset"):
        validate_research_mission(payload)
    payload = mission_payload()
    payload["freshness"] = {
        "mode": "current",
        "as_of": "2026-09-19T00:00:00Z",
        "rationale": "Current evidence required.",
    }
    with pytest.raises(ValueError, match="required only for as_of"):
        validate_research_mission(payload)


def test_scope_and_budget_cannot_make_the_mission_impossible():
    payload = mission_payload()
    payload["scope"]["exclude"] = ["enterprise agentic software delivery"]
    with pytest.raises(ValueError, match="both include and exclude"):
        validate_research_mission(payload)
    payload = mission_payload()
    payload["source_policy"]["minimum_independent_publishers"] = 5
    payload["budget"]["max_sources"] = 4
    with pytest.raises(ValueError, match="exceeds the source budget"):
        validate_research_mission(payload)


def test_mission_is_immutable_after_validation():
    mission = validate_research_mission(mission_payload())
    with pytest.raises(ValueError):
        mission.decision = "Change the decision after admission"


def test_frozen_corpus_has_two_cases_per_stratum_and_closed_sources():
    source_path = Path("docs/experiments/enterprise-evaluation/corpus.json")
    corpus = load_mission_experiment_corpus(
        Path("docs/experiments/research-mission/w12.1-cases.json"),
        source_corpus_path=source_path,
    )
    counts: dict[str, int] = {}
    for item in corpus.cases:
        counts[item.stratum] = counts.get(item.stratum, 0) + 1
    assert set(counts.values()) == {2}
    assert len(counts) == 6
    assert {item.expected_intake for item in corpus.cases} == {"accept", "clarify"}


def test_corpus_loader_rejects_missing_source_reference(tmp_path):
    import json

    original = Path("docs/experiments/research-mission/w12.1-cases.json")
    payload = json.loads(original.read_bytes())
    payload["cases"][0]["source_ids"] = ["missing-source"]
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unavailable sources"):
        load_mission_experiment_corpus(
            changed,
            source_corpus_path=Path(
                "docs/experiments/enterprise-evaluation/corpus.json"
            ),
        )


def test_corpus_loader_rejects_changed_source_corpus(tmp_path):
    source = Path("docs/experiments/enterprise-evaluation/corpus.json")
    changed_source = tmp_path / "source.json"
    changed_source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="digest differs"):
        load_mission_experiment_corpus(
            Path("docs/experiments/research-mission/w12.1-cases.json"),
            source_corpus_path=changed_source,
        )
