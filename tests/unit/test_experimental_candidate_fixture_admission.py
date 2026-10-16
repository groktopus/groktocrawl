"""The isolated candidate verifier must explicitly opt into fictional runs."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts import verify_experimental_candidate as verifier


def capabilities():
    return {
        "implementation_stage": "postgres_artifact_authority_fixture_adapter",
        "live_research_available": False,
        "operations": {
            "runs": {"execution_mode": "fixture_only", "create_available": True}
        },
    }


def test_candidate_verifier_accepts_only_explicit_fixture_admission():
    verifier._require_fixture_capabilities(capabilities())


@pytest.mark.parametrize(
    "change", ["stage", "creation", "live", "execution", "missing"]
)
def test_candidate_verifier_refuses_incompatible_or_disabled_admission(change):
    value = deepcopy(capabilities())
    if change == "stage":
        value["implementation_stage"] = "postgres_artifact_authority_adapter"
    elif change == "creation":
        value["operations"]["runs"]["create_available"] = False
    elif change == "live":
        value["live_research_available"] = True
    elif change == "execution":
        value["operations"]["runs"]["execution_mode"] = "live"
    else:
        value = {}
    with pytest.raises(
        RuntimeError, match="FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=true"
    ):
        verifier._require_fixture_capabilities(value)


def test_demo_compose_requires_explicit_fixture_opt_in():
    root = Path(__file__).parents[2]
    model = yaml.safe_load((root / "compose.experimental-candidate.yml").read_text())
    assert (
        model["services"]["candidate-agent"]["environment"][
            "FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS"
        ]
        == "${FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS:-false}"
    )
