"""Focused contracts for W8 source-diversity selection."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "run_w8_diversity_selection.py"
SPEC = importlib.util.spec_from_file_location("w8_diversity", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def candidate(identity, rank, *, publisher, stance="supports", quality="high", fact="f1"):
    return {
        "candidate_id": identity, "rank": rank,
        "canonical_document_id": identity.split("-")[0], "publisher_id": publisher,
        "registrable_domain": publisher + ".example", "engines": ["atlas", "beacon"],
        "source_type": "primary" if quality == "high" else "secondary",
        "stance": stance, "quality": quality, "facts": [fact + ": value"],
    }


def case(candidates, *, contradiction=False, false_balance=False):
    return {
        "selection_limit": 3, "candidates": candidates,
        "ground_truth": {
            "required_fact_ids": ["f1", "f2"],
            "material_contradiction_present": contradiction,
            "independent_publisher_count_available": 2,
            "false_balance_risk": false_balance,
        },
    }


def test_independent_policy_collapses_engine_and_canonical_agreement():
    candidates = [
        candidate("doc-copy1", 1, publisher="owner"),
        candidate("doc-copy2", 2, publisher="owner"),
        candidate("other-1", 3, publisher="independent", fact="f2"),
    ]
    selected = MODULE.independent_quality(case(candidates))
    scored = MODULE.score(case(candidates), selected)
    assert scored["canonical_duplicates"] == 0
    assert scored["publisher_count"] == 2
    assert scored["engine_count"] == 2
    assert scored["answer_supported"]


def test_quality_gate_does_not_manufacture_false_balance():
    candidates = [
        candidate("a-1", 1, publisher="one"),
        candidate("b-1", 2, publisher="two", fact="f2"),
        candidate("c-1", 3, publisher="three"),
        candidate("d-1", 4, publisher="dissent", stance="contradicts", quality="low"),
    ]
    item = case(candidates, false_balance=True)
    scored = MODULE.score(item, MODULE.quality_gated_opposition(item))
    assert not scored["false_balance"]


def test_quality_gate_retains_credible_material_conflict():
    candidates = [
        candidate("a-1", 1, publisher="one"),
        candidate("b-1", 2, publisher="two", fact="f2"),
        candidate("c-1", 3, publisher="three"),
        candidate("d-1", 4, publisher="dissent", stance="contradicts", quality="medium"),
    ]
    item = case(candidates, contradiction=True)
    scored = MODULE.score(item, MODULE.quality_gated_opposition(item))
    assert scored["material_contradiction_discovered"]


def test_independent_policy_declines_low_quality_padding():
    candidates = [
        candidate("a-1", 1, publisher="one"),
        candidate("b-1", 2, publisher="two", fact="f2"),
        candidate("c-1", 3, publisher="noise", quality="low"),
    ]
    selected = MODULE.independent_quality(case(candidates))
    assert len(selected) == 2
