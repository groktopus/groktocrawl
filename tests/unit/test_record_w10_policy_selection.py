import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.record_w10_policy_selection import build_selection

SHA = "a" * 64
PRIMARY = {
    "schema_version": "enterprise-evaluation/w10-summary/1",
    "complete": True,
    "selected_challenge_types": ["freshness"],
    "decision": "allow_bounded_adaptation_for_selected_types",
}
ADJUDICATION = {
    "schema_version": "enterprise-evaluation/w10-adjudication-analysis/1",
    "primary_summary_sha256": SHA,
    "decision_changed": False,
    "primary_decision": "allow_bounded_adaptation_for_selected_types",
    "adjudicated_sensitivity_decision": (
        "allow_bounded_adaptation_for_selected_types"
    ),
}
ACCOUNTING = {
    "schema_version": "enterprise-evaluation/w10-public-accounting/1",
    "complete": True,
    "inputs": {"summary_sha256": SHA},
}
CASES = {
    "cases": [
        {"challenge_type": "freshness"},
        {"challenge_type": "identity"},
    ]
}
INPUTS = {
    "primary_summary": SHA,
    "adjudication_analysis": "b" * 64,
    "accounting": "c" * 64,
    "challenge_cases": "d" * 64,
}


def build(outcome="bounded_recovery", selected=None, **changes):
    values = {
        "primary": PRIMARY,
        "adjudication": ADJUDICATION,
        "accounting": ACCOUNTING,
        "cases": CASES,
        "outcome": outcome,
        "selected_types": ["freshness"] if selected is None else selected,
        "rationale": "Observed decision",
        "reversal_condition": "A repeated study crosses the frozen boundary",
        "input_digests": INPUTS,
    }
    values.update(changes)
    return build_selection(**values)


def test_bounded_selection_is_bound_and_authorizes_w11():
    result = build()
    assert result["w11_measurement_authorized"] is True
    assert result["selected_challenge_types"] == ["freshness"]
    assert result["adr"] == "ADR-0081"
    assert result["inputs"] == INPUTS


def test_fixed_selection_has_no_adaptive_types():
    result = build("keep_fixed_retrieval", selected=[])
    assert result["w11_measurement_authorized"] is True
    assert result["selected_challenge_types"] == []


def test_decision_changing_sensitivity_requires_followup():
    changed = {
        **ADJUDICATION,
        "decision_changed": True,
        "adjudicated_sensitivity_decision": "retain_fixed_default",
    }
    with pytest.raises(ValueError, match="requires a follow-up"):
        build(adjudication=changed)
    result = build(
        "run_followup_experiment", selected=[], adjudication=changed
    )
    assert result["w11_measurement_authorized"] is False


def test_inconsistent_adjudication_decision_relationship_fails_closed():
    inconsistent = {
        **ADJUDICATION,
        "adjudicated_sensitivity_decision": "retain_fixed_default",
    }
    with pytest.raises(ValueError, match="relationship is inconsistent"):
        build(adjudication=inconsistent)


def test_adaptive_outcomes_cannot_exceed_primary_evidence():
    with pytest.raises(ValueError, match="exceeds the primary"):
        build(selected=["identity"])
    with pytest.raises(ValueError, match="every type"):
        build("full_bounded_policy", selected=["freshness", "identity"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("primary", {**PRIMARY, "complete": False}, "must be complete"),
        ("accounting", {**ACCOUNTING, "complete": False}, "must be complete"),
        (
            "adjudication",
            {**ADJUDICATION, "primary_summary_sha256": "e" * 64},
            "not bound",
        ),
    ],
)
def test_incomplete_or_unbound_inputs_fail_closed(field, value, message):
    with pytest.raises(ValueError, match=message):
        build(**{field: value})


def test_cli_writes_a_digest_bound_selection(tmp_path: Path):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(CASES))
    primary_path = tmp_path / "primary.json"
    primary_path.write_text(json.dumps(PRIMARY))
    primary_sha = hashlib.sha256(primary_path.read_bytes()).hexdigest()
    adjudication_path = tmp_path / "adjudication.json"
    adjudication_path.write_text(
        json.dumps(
            {
                **ADJUDICATION,
                "primary_summary_sha256": primary_sha,
            }
        )
    )
    accounting_path = tmp_path / "accounting.json"
    accounting_path.write_text(
        json.dumps(
            {
                **ACCOUNTING,
                "inputs": {"summary_sha256": primary_sha},
            }
        )
    )
    output_path = tmp_path / "selection.json"
    script = Path(__file__).parents[2] / "scripts" / "record_w10_policy_selection.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--primary-summary",
            str(primary_path),
            "--adjudication-analysis",
            str(adjudication_path),
            "--accounting",
            str(accounting_path),
            "--challenge-cases",
            str(cases_path),
            "--outcome",
            "bounded_recovery",
            "--selected-challenge-type",
            "freshness",
            "--rationale",
            "Freshness passed every frozen gate.",
            "--reversal-condition",
            "A repeated study fails a frozen gate.",
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(output_path.read_text())
    assert result["schema_version"] == "enterprise-evaluation/w10-policy-selection/1"
    assert result["w11_measurement_authorized"] is True
    assert result["inputs"]["primary_summary"] == primary_sha
    assert result["inputs"]["challenge_cases"] == hashlib.sha256(
        cases_path.read_bytes()
    ).hexdigest()
