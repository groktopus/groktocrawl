import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKET_PATH = ROOT / "docs/experiments/typesafe-jev/slsa-replay-2026-10-03.case-packet.json"
PACKET_SHA256 = "01f362a5ed691a970c58169afb33ee05fc45f313eed2022f00683f929179625c"


def test_packet_hash_budget_and_fixed_evaluator_pool():
    raw = PACKET_PATH.read_bytes()
    packet = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == PACKET_SHA256
    assert packet["call_budget"]["hard_max_total_requests"] == 11
    assert packet["call_budget"]["incumbent_control_max_total_requests"] == 4
    assert packet["call_budget"]["incumbent_control_model_alias"] == "free"
    assert len(packet["cases"]) == 4
    assert packet["corpus"]["evaluator_only_replay_ranks"] == list(range(4, 21))
    assert packet["corpus"]["search_snapshot_sha256"] == (
        "8c94ecf1e09a5e005fc7692b29bc0519233d64a7a5d057eb246428b025b14904"
    )


def test_future_replay_content_and_labels_never_enter_first_pass_state():
    packet = json.loads(PACKET_PATH.read_text())
    expected_strata = {
        "met_false_gap_unnecessary_followup",
        "unmet_public_transitivity_rule",
        "unmet_authority_check_label_completeness",
        "unanswerable_private_deployment_value",
    }
    assert {case["stratum"] for case in packet["cases"]} == expected_strata
    evaluator_ids = {f"rank-{rank:02d}" for rank in range(4, 21)}
    for case in packet["cases"]:
        state = case["state"]
        assert not {"evaluation_only", "reference", "labels", "research_agent_proposal"} & state.keys()
        presented = {source["source_id"] for source in state["first_pass"]["sources"]}
        assert not presented & evaluator_ids
        replay = case["evaluation_only"]["replay"]
        assert set(replay["candidate_result_ids"]) == evaluator_ids
        for passage in replay["evaluator_only_passages"]:
            quote = passage["quote"].encode()
            assert passage["end_byte"] - passage["start_byte"] == len(quote)
            assert len(passage["source_sha256"]) == 64


def test_stage_request_hashes_are_frozen_and_failure_references_are_explicit():
    packet = json.loads(PACKET_PATH.read_text())
    for case in packet["cases"]:
        hashes = case["frozen_request_sha256"]
        assert set(hashes) == {
            "stage1_sufficiency",
            "stage3_hypothesis_review",
            "stage4_addressability_if_stage3_positive",
        }
        assert all(len(value) == 64 for value in hashes.values())
        incumbent = case["incumbent_gap_control"]
        assert incumbent["requested_model_alias"] == "free"
        assert incumbent["system_prompt"] == "You are a research gap analyzer."
        assert hashlib.sha256(incumbent["user_prompt"].encode()).hexdigest() == incumbent["prompt_sha256"]
        assert incumbent["context_sha256"]
        assert "evaluator_only" not in incumbent["user_prompt"]
        assert case["evaluation_only"]["reference"]["provenance"].startswith(
            "assistant-authored best effort"
        )
