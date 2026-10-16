import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).parents[2]
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "jev_pilot_builder", ROOT / "scripts/jev_continuation_request_builder.py"
)
builder = importlib.util.module_from_spec(BUILDER_SPEC)
sys.modules[BUILDER_SPEC.name] = builder
BUILDER_SPEC.loader.exec_module(builder)

PACKET_PATH = (
    ROOT
    / "docs/experiments/typesafe-jev/continuation-pilot-2026-10-03.case-packet.json"
)
PACKET = json.loads(PACKET_PATH.read_text())
RESULTS_PATH = (
    ROOT
    / "docs/experiments/typesafe-jev/continuation-pilot-2026-10-03.results.json"
)


def test_frozen_public_pilot_packet_hash_and_scope():
    assert hashlib.sha256(PACKET_PATH.read_bytes()).hexdigest() == (
        "6bdff861da0dfb8f20b7d11983034520d7a37bda6e8d0ad7037da40d314d023c"
    )


def test_sanitized_results_bind_to_frozen_requests_and_gating():
    results = json.loads(RESULTS_PATH.read_text())
    assert results["case_packet_sha256"] == hashlib.sha256(
        PACKET_PATH.read_bytes()
    ).hexdigest()
    assert results["requests_attempted"] == results["requests_validated"] == 19
    assert results["requests_unevaluated"] == results["searches_dispatched"] == 0
    assert len(results["cases"]) == len(PACKET["cases"]) == 8
    for result, case in zip(results["cases"], PACKET["cases"], strict=True):
        assert result["case_id"] == case["case_id"]
        for stage, frozen_key in (
            ("stage1", "stage1_sufficiency"),
            ("stage3", "stage3_hypothesis_review"),
            ("stage4", "stage4_addressability_if_stage3_positive"),
        ):
            if stage in result["request_sha256"]:
                assert result["request_sha256"][stage] == case[
                    "frozen_request_sha256"
                ][frozen_key]
        if "stage4_yes_probability" in result:
            assert result["stage3_yes_probability"] > 0.5
        else:
            assert result["stage3_yes_probability"] <= 0.5
    assert PACKET["source_revision"] == "7b9bf51de1334d4d7e6bd6256d3085c8c2a8d60d"
    assert len(PACKET["cases"]) == 8
    assert all(
        "assistant_reference" not in case["state"]
        and "research_agent_proposal" not in case["state"]
        for case in PACKET["cases"]
    )


@pytest.mark.parametrize("case", PACKET["cases"], ids=lambda c: c["case_id"])
def test_each_frozen_request_digest_matches_builder_and_excludes_labels(case):
    state = case["state"]
    hypothesis = case["research_agent_proposal"]
    stage1 = builder.build_sufficiency_request(state)
    stage3 = builder.build_hypothesis_review_request(
        state,
        [hypothesis],
        {
            source_id: PACKET["acquired_public_source_blobs"][path]
            for source_id, path in case["source_blob_by_id"].items()
        },
    )
    assert isinstance(stage1, builder.RequestPlan)
    assert isinstance(stage3, builder.RequestPlan)
    digests = case["frozen_request_sha256"]
    assert (
        hashlib.sha256(stage1.serialized).hexdigest() == digests["stage1_sufficiency"]
    )
    assert (
        hashlib.sha256(stage3.serialized).hexdigest()
        == digests["stage3_hypothesis_review"]
    )
    assert "assistant_reference" not in stage1.payload["state"]
    assert "assistant_reference" not in stage3.payload["state"]

    stage3_response = builder.ValidatedNoulResponse(
        builder.MODEL,
        MappingProxyType({"q0000": 0.9}),
        hashlib.sha256(stage3.serialized).hexdigest(),
    )
    stage4 = builder.build_addressability_request(
        state,
        hypothesis,
        {
            source_id: PACKET["acquired_public_source_blobs"][path]
            for source_id, path in case["source_blob_by_id"].items()
        },
        stage3_plan=stage3,
        stage3_response=stage3_response,
        stage3_question_id="q0000",
        policy_qualified_hypothesis_id=hypothesis["hypothesis_id"],
    )
    assert isinstance(stage4, builder.RequestPlan)
    assert (
        hashlib.sha256(stage4.serialized).hexdigest()
        == digests["stage4_addressability_if_stage3_positive"]
    )
    assert "assistant_reference" not in stage4.payload["state"]
