import json

import pytest

import scripts.run_w10_adaptive_policy as w10_runner
from scripts.build_w10_adjudication_packet import select_gap_disagreements
from scripts.run_w10_adaptive_policy import (
    apply_planner_gap_state,
    assessment_schema,
    build_work_order,
    execute_trial,
    resolve_terminal_stop_reason,
    trial_evidence_checkpoint,
)
from scripts.summarize_w10_adaptive_policy import (
    aggregate,
    gate,
    gate_rejection_audit,
    trial_metrics,
)
from scripts.validate_w10_run import digest as file_digest
from scripts.validate_w10_run import validate_run


def row(**overrides):
    value = {
        "status": "completed",
        "closed_weight": 6,
        "total_weight": 10,
        "closed_claims": 2,
        "total_claims": 3,
        "admitted": 5,
        "useful": 4,
        "followup_queries": 0,
        "unnecessary_followup_queries": 0,
        "unsupported_high_importance": 1,
        "within_bounds": True,
        "elapsed_ms": 1_000,
    }
    value.update(overrides)
    return value


def test_gate_requires_effect_precision_work_and_failure_guards():
    fixed = aggregate([row(closed_weight=5, useful=4, admitted=5)])
    full = aggregate(
        [
            row(
                closed_weight=7,
                useful=4,
                admitted=5,
                followup_queries=2,
                unnecessary_followup_queries=0,
            )
        ]
    )
    result = gate(full, fixed)
    assert result["passed"]
    assert result["closure_gain"] == pytest.approx(0.2)


def test_gate_fails_when_precision_is_undefined_or_queries_add_nothing():
    fixed = aggregate([row(closed_weight=5)])
    full = aggregate(
        [
            row(
                closed_weight=7,
                admitted=0,
                useful=0,
                followup_queries=2,
                unnecessary_followup_queries=2,
            )
        ]
    )
    result = gate(full, fixed)
    assert not result["passed"]
    assert not result["checks"]["precision_within_5pp"]
    assert not result["checks"]["unnecessary_queries_at_most_10pct"]


def test_gate_rejection_audit_uses_only_observed_equivalent_queries():
    rejected = {
        "status": "completed",
        "case_id": "case-1",
        "repetition": 0,
        "policy": "gated",
        "attempts": [{"query": "initial"}],
        "proposals": [
            {
                "query": "Primary evidence!",
                "admitted": False,
                "reason": "topic_drift",
            },
            {
                "query": "Unobserved query",
                "admitted": False,
                "reason": "duplicate_intent",
            },
        ],
        "candidates": [],
    }
    executed = {
        "status": "completed",
        "case_id": "case-1",
        "repetition": 0,
        "policy": "gap",
        "attempts": [{"query": "initial"}, {"query": "primary evidence"}],
        "proposals": [],
        "candidates": [
            {
                "acquisition_status": "acquired",
                "search_origins": [{"attempt": 1, "rank": 1}],
                "operational_assessment": {
                    "supports_or_challenges": True,
                    "marginal_value": True,
                },
            }
        ],
    }

    result = gate_rejection_audit([rejected, executed])
    assert result["rejections"] == 2
    assert result["observed_gain_elsewhere"] == 1
    assert result["unknown"] == 1


def test_summary_credits_promoted_source_to_acquiring_followup():
    record = {
        "status": "completed",
        "case_id": "case-1",
        "policy": "full",
        "repetition": 0,
        "attempts": [{"query": "initial"}, {"query": "follow-up"}],
        "proposals": [{"admitted": True, "executed": True}],
        "candidates": [
            {
                "candidate_id": "source-1",
                "acquisition_status": "acquired",
                "admitted": True,
                "selected_on_attempt": 1,
                "search_origins": [
                    {"attempt": 0, "rank": 8},
                    {"attempt": 1, "rank": 1},
                ],
                "operational_assessment": {
                    "supports_or_challenges": True,
                    "marginal_value": True,
                },
            }
        ],
        "gap_results": [{"gap_id": "claim", "status": "closed"}],
        "metrics": {
            "searches": 2,
            "model_calls": 3,
            "admitted_count": 1,
            "closed_weight": 3,
            "total_weight": 3,
            "elapsed_ms": 100,
        },
        "round_decisions": [],
    }
    case = {
        "challenge_type": "unsupported_claim",
        "claims": [{"claim_id": "claim", "importance": 3}],
    }

    result = trial_metrics(record, case)
    assert result["gainful_followup_queries"] == 1
    assert result["unnecessary_followup_queries"] == 0


def test_summary_surfaces_interim_final_stop_disagreement():
    record = {
        "status": "completed",
        "case_id": "case-1",
        "policy": "full",
        "repetition": 0,
        "attempts": [{"query": "initial"}, {"query": "follow-up"}],
        "proposals": [{"admitted": True, "executed": True}],
        "candidates": [],
        "gap_results": [{"gap_id": "claim", "status": "open"}],
        "interim_assessment": {"gaps": [{"gap_id": "claim", "status": "closed"}]},
        "metrics": {
            "searches": 2,
            "model_calls": 3,
            "admitted_count": 0,
            "closed_weight": 0,
            "total_weight": 3,
            "elapsed_ms": 100,
        },
        "round_decisions": [{"all_gaps_closed": True}],
    }
    case = {
        "challenge_type": "unsupported_claim",
        "claims": [{"claim_id": "claim", "importance": 3}],
    }

    result = trial_metrics(record, case)
    summary = aggregate([result])
    assert result["premature_stop_disagreement"]
    assert summary["interim_all_closed_trials"] == 1
    assert summary["premature_stop_disagreements"] == 1
    assert summary["premature_stop_disagreement_rate"] == 1.0


def test_adjudication_selects_every_disputed_high_importance_closure():
    observations = [
        {
            "observation_id": "high-open",
            "case_id": "case-1",
            "gap_id": "high",
            "claim": {"importance": 3},
            "model_gap_grade": {"status": "open"},
        },
        {
            "observation_id": "high-closed",
            "case_id": "case-1",
            "gap_id": "high",
            "claim": {"importance": 3},
            "model_gap_grade": {"status": "closed"},
        },
        {
            "observation_id": "low-open",
            "case_id": "case-1",
            "gap_id": "low",
            "claim": {"importance": 1},
            "model_gap_grade": {"status": "open"},
        },
        {
            "observation_id": "low-closed",
            "case_id": "case-1",
            "gap_id": "low",
            "claim": {"importance": 1},
            "model_gap_grade": {"status": "closed"},
        },
    ]

    selected, reasons = select_gap_disagreements(observations)
    assert set(selected) == {"high-open", "high-closed"}
    assert reasons["high-open"] == {"high_importance_closure_disagreement"}


def test_adjudication_selects_interim_final_closure_disagreement():
    observations = [
        {
            "observation_id": "changed",
            "case_id": "case-1",
            "gap_id": "claim",
            "claim": {"importance": 1},
            "interim_gap_grade": {"status": "closed"},
            "model_gap_grade": {"status": "open"},
        }
    ]

    selected, reasons = select_gap_disagreements(observations)
    assert set(selected) == {"changed"}
    assert reasons["changed"] == {"interim_final_closure_disagreement"}


def test_w10_run_validator_closes_public_private_and_accounting_edges(tmp_path):
    cases_path = tmp_path / "cases.json"
    freeze_path = tmp_path / "freeze.json"
    run_dir = tmp_path / "run"
    (run_dir / "records").mkdir(parents=True)
    (run_dir / "private-acquisitions").mkdir()
    cases_path.write_text(
        '{"cases":[{"case_id":"case-1","claims":[{"claim_id":"claim"}]}]}\n'
    )
    freeze_path.write_text(
        '{"runner_sha256":"runner","search_environment_sha256":"search"}\n'
    )
    (run_dir / "run-metadata.json").write_text(
        json.dumps(
            {
                "source_commit": "a" * 40,
                "cases_sha256": file_digest(cases_path),
                "freeze_sha256": file_digest(freeze_path),
                "runner_sha256": "runner",
                "search_environment_sha256": "search",
            }
        )
    )
    (run_dir / "work-order.json").write_text(
        '{"entries":[{"position":1,"case_id":"case-1",'
        '"policy":"fixed","repetition":0}]}\n'
    )
    name = "case-1--fixed--0.json"
    (run_dir / "records" / name).write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "policy": "fixed",
                "repetition": 0,
                "status": "completed",
                "stop_reason": "fixed_query_complete",
                "metrics": {
                    "searches": 1,
                    "model_calls": 1,
                    "admitted_count": 1,
                    "elapsed_ms": 100,
                },
                "attempts": [{"query": "initial"}],
                "proposals": [],
                "initial_gap_assessment": None,
                "candidates": [
                    {
                        "candidate_id": "source-1",
                        "acquisition_status": "acquired",
                        "selected_on_attempt": 0,
                        "search_origins": [{"attempt": 0, "rank": 1}],
                        "reviewed_bytes_sha256": "content",
                    }
                ],
                "gap_results": [{"gap_id": "claim", "status": "closed"}],
                "orchestration": {
                    "runtime": "langgraph",
                    "event_count": 1,
                    "event_digests": ["event"],
                },
            }
        )
    )
    (run_dir / "private-acquisitions" / name).write_text(
        '[{"candidate_id":"source-1","reviewed_bytes_sha256":"content",'
        '"reviewed_excerpt":"private"}]\n'
    )

    assert validate_run(run_dir, cases_path, freeze_path, expected_records=1) == []
    record = json.loads((run_dir / "records" / name).read_text())
    record["stop_reason"] = "continue"
    record["candidates"][0]["reviewed_excerpt"] = "leak"
    (run_dir / "records" / name).write_text(json.dumps(record))
    issues = validate_run(run_dir, cases_path, freeze_path, expected_records=1)
    assert any("terminal stop reason" in item for item in issues)
    assert any("private excerpt" in item for item in issues)

    record["policy"] = "gated"
    record["stop_reason"] = "search_limit"
    record["candidates"][0].pop("reviewed_excerpt")
    record["initial_gap_assessment"] = [
        {"gap_id": "claim", "status": "closed", "reason": "already covered"}
    ]
    record["attempts"].append({"query": "unnecessary follow-up"})
    record["proposals"] = [
        {
            "query": "unnecessary follow-up",
            "gap_id": "claim",
            "admitted": True,
            "executed": True,
        }
    ]
    record["metrics"]["searches"] = 2
    (run_dir / "records" / name).write_text(json.dumps(record))
    (run_dir / "work-order.json").write_text(
        '{"entries":[{"position":1,"case_id":"case-1",'
        '"policy":"gated","repetition":0}]}\n'
    )
    issues = validate_run(run_dir, cases_path, freeze_path, expected_records=1)
    assert any("planner-closed gap" in item for item in issues)

    record["initial_gap_assessment"][0]["status"] = "open"
    record["round_decisions"] = [{"all_gaps_closed": True}]
    record["interim_assessment"] = None
    (run_dir / "records" / name).write_text(json.dumps(record))
    issues = validate_run(run_dir, cases_path, freeze_path, expected_records=1)
    assert any("lacks preserved interim assessment" in item for item in issues)

    record["status"] = "failed"
    record["partial_evidence_file"] = f"failures/{name}"
    (run_dir / "records" / name).write_text(json.dumps(record))
    (run_dir / "failures").mkdir()
    (run_dir / "private-failures").mkdir()
    checkpoint = {"stage": "final_assessment_received"}
    (run_dir / "failures" / name).write_text(json.dumps(checkpoint))
    (run_dir / "private-failures" / name).write_text(json.dumps(checkpoint))
    issues = validate_run(run_dir, cases_path, freeze_path, expected_records=1)
    assert any("failed assessment is not preserved" in item for item in issues)
    assert any("failed assessment receipt is not preserved" in item for item in issues)

    checkpoint["received_assessment"] = {"candidates": []}
    checkpoint["received_assessment_receipt"] = {"response_sha256": "assessment"}
    (run_dir / "failures" / name).write_text(json.dumps(checkpoint))
    (run_dir / "private-failures" / name).write_text(json.dumps(checkpoint))
    issues = validate_run(run_dir, cases_path, freeze_path, expected_records=1)
    assert not any("failed assessment is not preserved" in item for item in issues)
    assert not any(
        "failed assessment receipt is not preserved" in item for item in issues
    )


def test_work_order_rotates_each_policy_within_each_case():
    cases = [{"case_id": "a"}, {"case_id": "b"}]
    policies = ["fixed", "gap", "full"]
    first = build_work_order(cases, policies, 3, 20260911)
    assert first == build_work_order(cases, policies, 3, 20260911)
    for case in cases:
        positions = {policy: [] for policy in policies}
        for repetition in range(3):
            ordered = [
                policy
                for item, policy, rep in first
                if item["case_id"] == case["case_id"] and rep == repetition
            ]
            assert sorted(ordered) == sorted(policies)
            for position, policy in enumerate(ordered):
                positions[policy].append(position)
        assert all(len(set(values)) == 3 for values in positions.values())


@pytest.mark.parametrize(
    ("policy", "planner_complete", "proposals", "expected"),
    [
        ("fixed", False, [], "fixed_query_complete"),
        ("full", True, [], "planner_claimed_complete"),
        ("full", False, [], "no_followup_proposed"),
        (
            "gated",
            False,
            [{"executed": False}],
            "no_admitted_proposal",
        ),
        (
            "gap",
            False,
            [{"executed": True}],
            "proposal_exhausted",
        ),
    ],
)
def test_exhausted_policy_paths_have_terminal_reasons(
    policy, planner_complete, proposals, expected
):
    assert (
        resolve_terminal_stop_reason(
            policy=policy,
            computed_stop="continue",
            planner_claimed_complete=planner_complete,
            proposals=proposals,
        )
        == expected
    )


def test_planner_gap_state_is_complete_unique_and_controls_proposal_gate():
    gaps = (
        w10_runner.EvidenceGap("closed", 3, "primary source"),
        w10_runner.EvidenceGap("open", 2, "independent corroboration"),
    )
    assessed = apply_planner_gap_state(
        gaps,
        [
            {"gap_id": "closed", "status": "closed", "reason": "found"},
            {"gap_id": "open", "status": "open", "reason": "missing"},
        ],
    )

    assert [gap.closed for gap in assessed] == [True, False]
    rejected = w10_runner.gate_proposal(
        w10_runner.QueryProposal(
            query="find primary source",
            gap_id="closed",
            predicted_evidence="primary source",
            purpose="missing_support",
        ),
        original_query="initial query",
        gaps=assessed,
        prior_queries=("initial query",),
    )
    assert not rejected.admitted
    assert rejected.reason == "gap_already_closed"

    with pytest.raises(ValueError, match="every gap exactly once"):
        apply_planner_gap_state(
            gaps,
            [
                {"gap_id": "closed", "status": "closed", "reason": "found"},
                {"gap_id": "closed", "status": "open", "reason": "duplicate"},
            ],
        )


def test_trial_checkpoint_preserves_failure_evidence_without_public_excerpts():
    public, private = trial_evidence_checkpoint(
        case={"case_id": "case-1", "challenge_type": "contradiction"},
        policy="full",
        repetition=1,
        stage="assessment_received",
        attempts=[{"query": "example", "result_count": 1}],
        proposals=[],
        received_assessment={"candidates": [{"candidate_id": "omitted"}]},
        received_assessment_receipt={"response_sha256": "def"},
        candidates={
            "https://example.com/evidence": {
                "url": "https://example.com/evidence",
                "title": "Evidence",
                "observed_at": "2026-09-11T00:00:00+00:00",
                "accessed_at": "2026-09-11T00:00:01+00:00",
                "acquisition_status": "acquired",
                "reviewed_bytes_sha256": "abc",
                "reviewed_excerpt": "private source text",
            }
        },
    )

    assert public["stage"] == "assessment_received"
    assert public["attempts"] == [{"query": "example", "result_count": 1}]
    assert public["received_assessment"] == {
        "candidates": [{"candidate_id": "omitted"}]
    }
    assert public["received_assessment_receipt"] == {"response_sha256": "def"}
    assert "reviewed_excerpt" not in public["candidates"][0]
    assert private[0]["reviewed_excerpt"] == "private source text"


def test_invalid_final_assessment_is_checkpointed_before_cardinality_rejection(
    monkeypatch,
):
    def fake_search(client, query, limit, *, deadline):
        return (
            [
                {"url": "https://one.example/item", "title": "one"},
                {"url": "https://two.example/item", "title": "two"},
            ],
            1.0,
        )

    def fake_scrape(client, result, *, deadline):
        return {
            "acquisition_status": "acquired",
            "accessed_at": "2026-09-11T00:00:00+00:00",
            "reviewed_bytes_sha256": "digest",
            "reviewed_excerpt": result["title"],
            "acquisition_ms": 1.0,
        }

    def fake_model_json(client, *, name, prompt, **kwargs):
        candidate_id = prompt["candidates"][0]["candidate_id"]
        return (
            {
                "candidates": {
                    candidate_id: {
                        "relevant_gap_ids": ["claim"],
                        "supports_or_challenges": True,
                        "quality": {
                            "currency": 1,
                            "relevance": 1,
                            "authority": 1,
                            "accuracy": 1,
                            "purpose": 1,
                        },
                        "derivative_of": None,
                        "marginal_value": True,
                        "improves_currency": False,
                        "improves_authority": False,
                        "resolves_contradiction": False,
                        "reason": "fixture deliberately omitted the other candidate",
                    }
                },
                "gaps": {
                    "claim": {
                        "status": "closed",
                        "candidate_ids": [candidate_id],
                        "reason": "fixture",
                    }
                },
            },
            {"response_sha256": "invalid-response"},
        )

    checkpoints = []

    def save_checkpoint(public, private):
        checkpoints.append((public, private))

    monkeypatch.setattr(w10_runner, "search", fake_search)
    monkeypatch.setattr(w10_runner, "scrape", fake_scrape)
    monkeypatch.setattr(w10_runner, "model_json", fake_model_json)
    with pytest.raises(ValueError, match="every candidate exactly once"):
        execute_trial(
            {
                "case_id": "case-1",
                "challenge_type": "unsupported_claim",
                "query": "initial evidence",
                "as_of": "2026-09-11",
                "claims": [
                    {
                        "claim_id": "claim",
                        "importance": 3,
                        "closure_rule": "benchmark evidence",
                    }
                ],
            },
            "fixed",
            0,
            api_client=object(),
            llm_client=object(),
            model="local",
            result_limit=8,
            seed=20260911,
            checkpoint_writer=save_checkpoint,
        )

    public, private = checkpoints[-1]
    assert public["stage"] == "final_assessment_received"
    assert len(public["received_assessment"]["candidates"]) == 1
    assert public["received_assessment_receipt"] == {
        "response_sha256": "invalid-response"
    }
    assert len(public["candidates"]) == 2
    assert all("reviewed_excerpt" not in item for item in public["candidates"])
    assert {item["reviewed_excerpt"] for item in private} == {"one", "two"}


def test_assessment_schema_keys_candidate_and_gap_identity():
    schema = assessment_schema(["claim-a", "claim-b"], ["source-a", "source-b"])

    candidates = schema["properties"]["candidates"]
    assert candidates["type"] == "object"
    assert candidates["additionalProperties"] is False
    assert candidates["required"] == ["source-a", "source-b"]
    assert set(candidates["properties"]) == {"source-a", "source-b"}
    assert candidates["properties"]["source-a"]["properties"]["derivative_of"][
        "enum"
    ] == ["source-a", "source-b", None]

    gaps = schema["properties"]["gaps"]
    assert gaps["type"] == "object"
    assert gaps["additionalProperties"] is False
    assert gaps["required"] == ["claim-a", "claim-b"]
    assert set(gaps["properties"]) == {"claim-a", "claim-b"}


@pytest.mark.parametrize(
    ("first_round_gain", "expected_attempts", "expected_decision"),
    [(False, 2, "stop"), (True, 3, "continue")],
)
def test_full_policy_stops_between_followups_from_observed_gain(
    monkeypatch, first_round_gain, expected_attempts, expected_decision
):
    results = {
        "initial evidence": [
            {
                "url": f"https://initial-{index}.example/item",
                "title": f"initial-{index}",
            }
            for index in range(5)
        ],
        "primary benchmark evidence": [
            {"url": "https://initial-4.example/item", "title": "initial-4"},
            {"url": "https://follow-1.example/item", "title": "follow-1"},
        ],
        "defect study evidence": [
            {
                "url": f"https://follow-2-{index}.example/item",
                "title": f"follow-2-{index}",
            }
            for index in range(2)
        ],
    }

    def fake_search(client, query, limit, *, deadline):
        return results[query], 1.0

    def fake_scrape(client, result, *, deadline):
        return {
            "acquisition_status": "acquired",
            "accessed_at": "2026-09-11T00:00:00+00:00",
            "reviewed_bytes_sha256": "digest",
            "reviewed_excerpt": result["title"],
            "acquisition_ms": 1.0,
        }

    def fake_model_json(client, *, name, prompt, **kwargs):
        if name == "w10_query_plan":
            return (
                {
                    "initial_gaps": [
                        {"gap_id": "claim", "status": "open", "reason": "missing"}
                    ],
                    "proposals": [
                        {
                            "query": "primary benchmark evidence",
                            "gap_id": "claim",
                            "predicted_evidence": "controlled benchmark",
                            "purpose": "missing_support",
                        },
                        {
                            "query": "defect study evidence",
                            "gap_id": "claim",
                            "predicted_evidence": "measured defects",
                            "purpose": "missing_support",
                        },
                    ],
                },
                {"name": name},
            )
        candidates = {}
        for item in prompt["candidates"]:
            gained = first_round_gain and item["title"] == "initial-4"
            candidates[item["candidate_id"]] = {
                    "relevant_gap_ids": ["claim"],
                    "supports_or_challenges": gained,
                    "quality": {
                        "currency": 1,
                        "relevance": 1,
                        "authority": 1,
                        "accuracy": 1,
                        "purpose": 1,
                    },
                    "derivative_of": None,
                    "marginal_value": gained,
                    "improves_currency": False,
                    "improves_authority": False,
                    "resolves_contradiction": False,
                    "reason": "fixture",
                }
        return (
            {
                "candidates": candidates,
                "gaps": {
                    "claim": {
                        "status": "open",
                        "candidate_ids": [],
                        "reason": "fixture",
                    }
                },
            },
            {"name": name},
        )

    monkeypatch.setattr(w10_runner, "search", fake_search)
    monkeypatch.setattr(w10_runner, "scrape", fake_scrape)
    monkeypatch.setattr(w10_runner, "model_json", fake_model_json)
    monkeypatch.setattr(w10_runner, "replay_policy_trace", lambda events: ("trace",))
    result = execute_trial(
        {
            "case_id": "case-1",
            "challenge_type": "unsupported_claim",
            "query": "initial evidence",
            "as_of": "2026-09-11",
            "claims": [
                {
                    "claim_id": "claim",
                    "importance": 3,
                    "closure_rule": "benchmark evidence",
                }
            ],
        },
        "full",
        0,
        api_client=object(),
        llm_client=object(),
        model="local",
        result_limit=8,
        seed=20260911,
    )

    assert len(result["attempts"]) == expected_attempts
    assert result["metrics"]["model_calls"] == 3
    assert result["round_decisions"][0]["decision"] == expected_decision
    assert len(result["proposals"]) == 2
    assert result["proposals"][1]["executed"] is first_round_gain
    acquired_overlap = next(
        item for item in result["candidates"] if item["title"] == "initial-4"
    )
    assert acquired_overlap["selected_on_attempt"] == 1
