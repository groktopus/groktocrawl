"""Deterministic, Docker-free tests for the grounded-answer eval harness (#570).

Covers the deterministic graders (including the deliberately mis-cited negative
case failing citation support, and abstention qualifiers), harness corpus
validation (content-hash mismatch, fixture version pins, no-retry rule,
negative-ratio enforcement), in-process routing scenario-use assertion via the
fixture ledger/diagnostics, and provenance sanitization (forbidden-key removal
and secret-sentinel redaction). No live providers; no Docker.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.answer_evals import grading, harness, provenance, routing

PRICING = "http://test-site:8000/pricing"
DOCS = "http://test-site:8000/docs"
PINNED_PRICING = "# Fixture Site Pricing\n\n- Free: $0\n- Pro: $10\n- Business: $25"


def _result(
    *,
    answer: str = "",
    sources: list[dict] | None = None,
    citations: list[dict] | None = None,
    protocol: dict | None = None,
) -> dict:
    return {
        "protocol": protocol or {"status": 200, "success": True},
        "answer": answer,
        "sources": sources or [],
        "citations": citations or [],
        "error": None,
    }


def _case(**overrides) -> dict:
    case = {
        "id": "test-case",
        "suite_version": "answer-evals-v1",
        "kind": "positive",
        "target": "answer",
        "query": "test query",
        "num_sources": 5,
        "retrieval_mode": "keyword",
        "citation_style": "inline",
        "search_fixture": {
            "service": "slopsearx-fixture",
            "scenario": "healthy",
            "scenario_version": "v1",
            "fixture_version": "v3",
        },
        "llm_fixture": {
            "service": "llm-svc",
            "scenario": "grounded-answer",
            "scenario_version": "v1",
            "fixture_version": "v2",
        },
        "expected": {
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING, DOCS],
            "citations": [],
            "abstain_expected": False,
            "abstain_qualifier": None,
        },
        "source_content": {PRICING: PINNED_PRICING},
        "timeout_seconds": 60,
        "max_retries": 0,
    }
    case.update(overrides)
    return case


# ── Graders: citation support ──────────────────────────────────────────


def test_citation_support_binds_claim_to_exact_source_and_pinned_content():
    result = _result(
        answer="The Fixture Site Pro plan costs $10 per month. [1]",
        sources=[{"url": PRICING, "title": "", "relevance": ""}],
        citations=[{"index": 1, "url": PRICING}],
    )
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING],
            "citations": [
                {
                    "claim_id": "pricing-pro",
                    "answer_span": "Pro plan costs $10",
                    "evidence_span": "Pro: $10",
                    "source_url": PRICING,
                    "citation_index": 1,
                }
            ],
            "abstain_expected": False,
            "abstain_qualifier": None,
        },
    )
    verdict = grading.check_citation_support(result, case)
    assert verdict["pass"] is True


def test_citation_support_fails_on_deliberately_miscited_claim():
    # The claim cites [2] (docs) while the evidence only exists in source [1].
    result = _result(
        answer="The Fixture Site Pro plan costs $10 per month. [2]",
        sources=[
            {"url": PRICING, "title": "", "relevance": ""},
            {"url": DOCS, "title": "", "relevance": ""},
        ],
        citations=[{"index": 2, "url": DOCS}],
    )
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING, DOCS],
            "citations": [
                {
                    "claim_id": "pricing-pro",
                    "answer_span": "Pro plan costs $10",
                    "evidence_span": "Pro: $10",
                    "source_url": PRICING,
                    "citation_index": 1,
                }
            ],
            "abstain_expected": False,
            "abstain_qualifier": None,
        },
    )
    verdict = grading.check_citation_support(result, case)
    assert verdict["pass"] is False
    assert (
        verdict["detail"]["failures"][0]["criterion"] == "citation_index_to_source_url"
    )


def test_citation_support_fails_when_evidence_span_absent_from_pinned_content():
    result = _result(
        answer="The Fixture Site Pro plan costs $10 per month. [1]",
        sources=[{"url": PRICING, "title": "", "relevance": ""}],
        citations=[{"index": 1, "url": PRICING}],
    )
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING],
            "citations": [
                {
                    "claim_id": "pricing-pro",
                    "answer_span": "Pro plan costs $10",
                    "evidence_span": "Enterprise: $500",
                    "source_url": PRICING,
                    "citation_index": 1,
                }
            ],
            "abstain_expected": False,
            "abstain_qualifier": None,
        },
    )
    verdict = grading.check_citation_support(result, case)
    assert verdict["pass"] is False
    assert (
        verdict["detail"]["failures"][0]["criterion"]
        == "evidence_span_in_pinned_content"
    )


def test_citation_support_fails_when_answer_span_absent():
    result = _result(
        answer="No relevant claim here. [1]",
        sources=[{"url": PRICING, "title": "", "relevance": ""}],
        citations=[{"index": 1, "url": PRICING}],
    )
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING],
            "citations": [
                {
                    "claim_id": "pricing-pro",
                    "answer_span": "Pro plan costs $10",
                    "evidence_span": "Pro: $10",
                    "source_url": PRICING,
                    "citation_index": 1,
                }
            ],
            "abstain_expected": False,
            "abstain_qualifier": None,
        },
    )
    assert grading.check_citation_support(result, case)["pass"] is False


# ── Graders: abstention qualifiers ─────────────────────────────────────


def test_abstention_insufficient_qualifier_recognizes_empty_answer():
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [],
            "citations": [],
            "abstain_expected": True,
            "abstain_qualifier": "insufficient",
        }
    )
    assert grading.check_abstention(_result(answer=""), case)["pass"] is True


def test_abstention_insufficient_qualifier_recognizes_phrase():
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [],
            "citations": [],
            "abstain_expected": True,
            "abstain_qualifier": "insufficient",
        }
    )
    result = _result(answer="I was unable to find or scrape any relevant web pages.")
    assert grading.check_abstention(result, case)["pass"] is True


def test_abstention_contradictory_qualifier_requires_explicit_qualification():
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING, DOCS],
            "citations": [],
            "abstain_expected": True,
            "abstain_qualifier": "contradictory",
        }
    )
    conflicting = _result(
        answer=(
            "The sources conflict: one page says Pro costs $10 while another says $99. "
            "Because the evidence is contradictory, I cannot provide a confident answer."
        )
    )
    assert grading.check_abstention(conflicting, case)["pass"] is True
    insufficient_only = _result(answer="I was unable to find any relevant pages.")
    assert grading.check_abstention(insufficient_only, case)["pass"] is False


def test_abstention_not_expected_rejects_empty_or_abstaining_answer():
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": ["Pro plan costs $10"],
            "prohibited_claims": [],
            "allowable_source_urls": [PRICING],
            "citations": [],
            "abstain_expected": False,
            "abstain_qualifier": None,
        }
    )
    assert grading.check_abstention(_result(answer=""), case)["pass"] is False
    assert (
        grading.check_abstention(_result(answer="No relevant web pages found."), case)[
            "pass"
        ]
        is False
    )
    assert (
        grading.check_abstention(_result(answer="The Pro plan costs $10. [1]"), case)[
            "pass"
        ]
        is True
    )


# ── Graders: protocol / sources / claims / shape / integrity ───────────


def test_protocol_grader_matches_per_case_expected_status():
    case = _case(
        expected={
            "protocol": {"status": 503, "success": False},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [],
            "citations": [],
            "abstain_expected": False,
            "abstain_qualifier": None,
        }
    )
    assert (
        grading.check_protocol(
            _result(protocol={"status": 503, "success": False}), case
        )["pass"]
        is True
    )
    assert (
        grading.check_protocol(
            _result(protocol={"status": 200, "success": True}), case
        )["pass"]
        is False
    )


def test_sources_present_rejects_empty_duplicate_and_disallowed():
    case = _case()
    assert grading.check_sources_present(_result(), case)["pass"] is False
    dup = _result(sources=[{"url": PRICING}, {"url": PRICING}])
    assert grading.check_sources_present(dup, case)["pass"] is False
    disallowed = _result(sources=[{"url": "http://evil.example.com/x"}])
    assert grading.check_sources_present(disallowed, case)["pass"] is False
    ok = _result(sources=[{"url": PRICING}, {"url": DOCS}])
    assert grading.check_sources_present(ok, case)["pass"] is True


def test_citation_shape_inline_and_compact():
    case = _case()
    out_of_range = _result(
        answer="claim [3]",
        sources=[{"url": PRICING}, {"url": DOCS}],
        citations=[{"index": 3, "url": DOCS}],
    )
    assert grading.check_citation_shape(out_of_range, case)["pass"] is False

    compact_case = _case(citation_style="compact")
    compact = _result(
        answer="claim [1](http://test-site:8000/pricing)",
        sources=[{"url": PRICING}],
        citations=[{"index": 1, "url": PRICING}],
    )
    assert grading.check_citation_shape(compact, compact_case)["pass"] is True
    missing_marker = _result(
        answer="claim [1]",
        sources=[{"url": PRICING}],
        citations=[{"index": 1, "url": PRICING}],
    )
    assert grading.check_citation_shape(missing_marker, compact_case)["pass"] is False
    wrong_source = _result(
        answer="claim [1](http://test-site:8000/docs)",
        sources=[{"url": PRICING}],
        citations=[{"index": 1, "url": DOCS}],
    )
    assert grading.check_citation_shape(wrong_source, compact_case)["pass"] is False


def test_required_and_prohibited_claims():
    case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": ["Pro plan costs $10"],
            "prohibited_claims": ["Pro plan costs $99"],
            "allowable_source_urls": [PRICING],
            "citations": [],
            "abstain_expected": False,
            "abstain_qualifier": None,
        }
    )
    good = _result(answer="The Pro plan costs $10 per month.")
    assert grading.check_required_claims(good, case)["pass"] is True
    assert grading.check_prohibited_claims(good, case)["pass"] is True
    bad = _result(answer="The Pro plan costs $99 per month.")
    assert grading.check_required_claims(bad, case)["pass"] is False
    assert grading.check_prohibited_claims(bad, case)["pass"] is False


def test_citation_integrity_rejects_out_of_range_and_unmapped_markers():
    case = _case()
    out_of_range = _result(
        answer="claim [9]",
        sources=[{"url": PRICING}, {"url": DOCS}],
        citations=[{"index": 1, "url": PRICING}],
    )
    assert grading.check_citation_integrity(out_of_range, case)["pass"] is False
    unmapped = _result(
        answer="claim [2]",
        sources=[{"url": PRICING}, {"url": DOCS}],
        citations=[{"index": 1, "url": PRICING}],
    )
    assert grading.check_citation_integrity(unmapped, case)["pass"] is False
    ok = _result(
        answer="claim [1]",
        sources=[{"url": PRICING}],
        citations=[{"index": 1, "url": PRICING}],
    )
    assert grading.check_citation_integrity(ok, case)["pass"] is True


def test_applicable_graders_per_case():
    assert grading.applicable_graders(_case()) == [
        "check_protocol",
        "check_sources_present",
        "check_citation_shape",
        "check_citation_support",
        "check_required_claims",
        "check_prohibited_claims",
        "check_abstention",
        "check_citation_integrity",
    ]
    error_case = _case(
        expected={
            "protocol": {"status": 503, "success": False},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [],
            "citations": [],
            "abstain_expected": False,
            "abstain_qualifier": None,
        }
    )
    assert grading.applicable_graders(error_case) == ["check_protocol"]
    abstain_case = _case(
        expected={
            "protocol": {"status": 200, "success": True},
            "required_claims": [],
            "prohibited_claims": [],
            "allowable_source_urls": [],
            "citations": [],
            "abstain_expected": True,
            "abstain_qualifier": "insufficient",
        }
    )
    assert grading.applicable_graders(abstain_case) == [
        "check_protocol",
        "check_abstention",
        "check_required_claims",
        "check_prohibited_claims",
    ]


# ── Harness corpus validation ──────────────────────────────────────────


def _load_corpus_case(case_id: str) -> dict:
    path = harness.CASES_DIR / f"{case_id}.json"
    case = json.loads(path.read_text())
    case["_path"] = f"cases/{case_id}.json"
    return case


def test_corpus_has_at_least_ten_cases_and_negative_ratio():
    cases = harness.load_cases()
    assert len(cases) >= 10
    summary = harness.negative_ratio_summary(cases)
    assert summary["negative_or_abstention"] / len(cases) >= 0.20
    assert any(c["target"] == "research" for c in cases)
    assert any(c["target"] == "answer" for c in cases)


def test_all_corpus_cases_validate():
    manifest, cases = harness.validate_corpus()
    assert manifest["suite_version"] == "answer-evals-v1"
    assert len(cases) >= 10


def test_pinned_source_content_matches_authoritative_test_site():
    from fastapi.testclient import TestClient

    app_path = ROOT / "test-site/test_site/app.py"
    spec = importlib.util.spec_from_file_location("answer_eval_test_site", app_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__["ENABLE_MARKDOWN"] = True
    client = TestClient(module.app)
    checked: dict[str, str] = {}
    for case in harness.load_cases():
        for url, expected in (case.get("source_content") or {}).items():
            if url in checked:
                assert checked[url] == expected
                continue
            path = url.split("http://test-site:8000", 1)[1]
            response = client.get(path, headers={"Accept": "text/markdown"})
            assert response.status_code == 200
            assert response.text.strip() == expected.strip()
            checked[url] = expected
    assert set(checked) == {
        "http://test-site:8000/pricing",
        "http://test-site:8000/pricing-v2",
    }


def test_content_hash_mismatch_fails_validation():
    case = _load_corpus_case("positive-001-answer-grounded")
    case["query"] = "mutated query"
    errors = harness.validate_case(case)
    assert any("content hash mismatch" in error for error in errors)
    with pytest.raises(harness.CaseValidationError):
        harness.validate_corpus([case])


def test_validation_failure_record_contains_expected_observed_and_its_own_path(
    tmp_path,
):
    error = harness.CaseValidationError(
        "content hash mismatch",
        case_id="positive-001-answer-grounded",
        case_path="cases/positive-001-answer-grounded.json",
        expected={"valid_case": True},
        observed={"validation_errors": ["content hash mismatch"]},
    )
    record = harness.validation_failure_record(error, output_dir=tmp_path)
    artifact = Path(record["artifact_path"])
    persisted = json.loads(artifact.read_text())
    assert persisted["case_id"] == "positive-001-answer-grounded"
    assert persisted["expected"]["required_claim_count"] == 0
    assert persisted["observed"]["validation_error_count"] == 1
    assert persisted["artifact_path"] == str(artifact)
    assert persisted["outcome"] == "fail"


@pytest.mark.asyncio
async def test_run_case_invalid_hash_persists_failure_artifact(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    case["query"] = "mutated query"
    record = await harness.run_case(case, "invalid-hash", output_dir=tmp_path)
    assert record["outcome"] == "fail"
    assert record["case_id"] == "positive-001-answer-grounded"
    assert "content hash mismatch" in record["observed"]["validation_errors"][0]
    persisted = json.loads(Path(record["artifact_path"]).read_text())
    assert persisted["artifact_path"] == record["artifact_path"]


def test_cli_validation_failure_writes_case_and_provenance_artifacts(
    tmp_path, monkeypatch
):
    script_path = ROOT / "scripts/run_answer_evals.py"
    spec = importlib.util.spec_from_file_location("run_answer_evals_test", script_path)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "DEFAULT_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        cli.harness,
        "validate_corpus",
        mock.Mock(
            side_effect=harness.CaseValidationError(
                "content hash mismatch",
                case_id="case-bad",
                case_path="cases/case-bad.json",
                expected={"valid_case": True},
                observed={"validation_errors": ["content hash mismatch"]},
            )
        ),
    )
    assert cli.main(["--selection", "narrow"]) == 1
    case_artifact = tmp_path / "narrow/case-bad.json"
    provenance_artifact = tmp_path / "narrow/provenance.json"
    assert case_artifact.exists()
    assert provenance_artifact.exists()
    assert json.loads(case_artifact.read_text())["artifact_path"] == str(case_artifact)


def test_fixture_version_pin_mismatch_fails_validation():
    case = _load_corpus_case("positive-001-answer-grounded")
    case["llm_fixture"] = {**case["llm_fixture"], "fixture_version": "v99"}
    errors = harness.validate_case(case)
    assert any("llm fixture_version mismatch" in error for error in errors)

    case = _load_corpus_case("positive-001-answer-grounded")
    case["search_fixture"] = {**case["search_fixture"], "fixture_version": "v99"}
    errors = harness.validate_case(case)
    assert any("search fixture_version mismatch" in error for error in errors)


def test_no_retry_rule_enforced():
    case = _load_corpus_case("positive-001-answer-grounded")
    case["max_retries"] = 1
    errors = harness.validate_case(case)
    assert any("max_retries must be 0" in error for error in errors)


def test_negative_ratio_is_enforced_at_load():
    with pytest.raises(harness.CaseValidationError, match="negative/abstention ratio"):
        harness.validate_corpus([_load_corpus_case("positive-001-answer-grounded")])


def test_duplicate_case_ids_fail_corpus_validation():
    case = _load_corpus_case("positive-001-answer-grounded")
    with pytest.raises(harness.CaseValidationError, match="duplicate case ids"):
        harness.validate_corpus([case, dict(case)])


def test_selection_case_ids_resolve():
    manifest, cases = harness.validate_corpus()
    assert harness.select_case_ids("narrow", manifest, cases) == [
        "positive-001-answer-grounded",
        "positive-003-research-grounded",
        "negative-006-answer-empty-search",
    ]
    assert set(harness.select_case_ids("broad", manifest, cases)) == {
        c["id"] for c in cases
    }


# ── Routing: in-process fixture scenario routing ───────────────────────


@pytest.mark.asyncio
async def test_routing_drives_real_pipeline_and_asserts_scenario_usage():
    case = _load_corpus_case("positive-001-answer-grounded")
    runtime = routing.build_runtime(case, run_id="route-test-a")
    observed = await routing.run_pipeline(case, runtime)
    assert observed["protocol"] == {"status": 200, "success": True}
    assert "Pro plan costs $10" in observed["answer"]
    assert observed["citations"] == [{"index": 1, "url": PRICING}]
    usage = await routing.scenario_usage(runtime)
    assert usage["search_observed_scenarios"] == ["healthy"]
    assert usage["llm_observed_scenarios"] == ["grounded-answer"]


@pytest.mark.asyncio
async def test_contradictory_scenario_requires_conflicting_evidence():
    case = _load_corpus_case("positive-001-answer-grounded")
    case["llm_fixture"] = {
        **case["llm_fixture"],
        "scenario": "contradictory-evidence",
    }
    case["expected"] = {
        **case["expected"],
        "abstain_expected": True,
        "abstain_qualifier": "contradictory",
    }
    runtime = routing.build_runtime(case, run_id="causal-contradiction-control")
    observed = await routing.run_pipeline(case, runtime)
    assert observed["answer"] == "The supplied evidence is consistent."
    assert grading.check_abstention(observed, case)["pass"] is False


@pytest.mark.asyncio
async def test_routing_scenario_mismatch_is_surfaces_as_assertion_failure():
    case = _load_corpus_case("positive-001-answer-grounded")
    runtime = routing.build_runtime(case, run_id="route-test-b")
    # Force the search scenario off the case's pin so the ledger leaks a
    # scenario the case did not request.
    runtime.search_scenario = "zero-results"
    await routing.run_pipeline(case, runtime)
    usage = await routing.scenario_usage(runtime)
    assert usage["search_observed_scenarios"] == ["zero-results"]
    assert usage["search_observed_scenarios"] != ["healthy"]


@pytest.mark.asyncio
async def test_run_case_records_scenario_use_and_passes(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    record = await harness.run_case(case, "run-case-x", output_dir=tmp_path)
    assert record["outcome"] == "pass"
    assert record["scenario_use"]["search_observed_scenarios"] == ["healthy"]
    assert record["scenario_use"]["llm_observed_scenarios"] == ["grounded-answer"]
    assert record["request_hash"].startswith("sha256:")
    assert record["artifact_path"].endswith("positive-001-answer-grounded.json")
    assert (tmp_path / "positive-001-answer-grounded.json").exists()


def test_per_case_artifact_minimizes_observed_content(tmp_path):
    result = {
        "case_id": "artifact-privacy",
        "prompt": "secret prompt",
        "sources": [{"url": "http://top-level-secret.invalid"}],
        "expected": {
            "required_claims": ["secret expected claim"],
            "protocol": {
                "status": 200,
                "success": True,
                "detail": "secret expected protocol detail",
            },
            "allowable_source_urls": [
                "https://user:pass@example.invalid/?token=secret"
            ],
        },
        "observed": {
            "protocol": {
                "status": 200,
                "success": True,
                "provider_error": "secret protocol detail",
            },
            "answer": "secret answer from provider",
            "sources": [{"url": "http://secret.invalid", "content": "secret source"}],
            "citations": [{"index": 1, "url": "http://secret.invalid"}],
        },
        "error": "provider failed with secret request body",
        "timeout": False,
        "verdicts": [
            {"grader": "check_protocol", "pass": True, "message": "secret detail"}
        ],
    }
    record = harness._finalize_result(result, output_dir=tmp_path)
    serialized = Path(record["artifact_path"]).read_text()
    persisted = json.loads(serialized)
    assert "secret answer from provider" not in serialized
    assert "secret source" not in serialized
    assert "secret request body" not in serialized
    assert "secret detail" not in serialized
    assert "secret expected claim" not in serialized
    assert "user:pass@example.invalid" not in serialized
    assert "secret prompt" not in serialized
    assert "top-level-secret.invalid" not in serialized
    assert "secret protocol detail" not in serialized
    assert "secret expected protocol detail" not in serialized
    assert persisted["observed"] == {
        "protocol": {"status": 200, "success": True},
        "answer_present": True,
        "source_count": 1,
        "citation_count": 1,
        "error_classification": "evaluation_failure",
        "validation_error_count": 0,
    }


@pytest.mark.asyncio
async def test_run_case_fails_on_scenario_use_assertion(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    with mock.patch(
        "evals.answer_evals.harness.scenario_usage",
        return_value={
            "search_observed_scenarios": ["zero-results"],
            "llm_observed_scenarios": ["grounded-answer"],
            "search_entry_count": 1,
            "llm_entry_count": 1,
        },
    ):
        record = await harness.run_case(case, "run-case-y", output_dir=tmp_path)
    assert record["outcome"] == "fail"
    assert "scenario-use assertion failed" in (record["error"] or "")


@pytest.mark.asyncio
async def test_run_case_grader_exception_is_a_persisted_failure(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    with (
        mock.patch.dict(
            harness.GRADERS,
            {"check_protocol": mock.Mock(side_effect=RuntimeError("secret detail"))},
        ),
        mock.patch(
            "evals.answer_evals.harness.applicable_graders",
            return_value=["check_protocol"],
        ),
    ):
        record = await harness.run_case(case, "grader-failure", output_dir=tmp_path)
    assert record["outcome"] == "fail"
    assert record["error"] == "grader failure: check_protocol (RuntimeError)"
    persisted = json.loads(Path(record["artifact_path"]).read_text())
    assert persisted["artifact_path"] == record["artifact_path"]
    assert persisted["verdicts"][0]["pass"] is False
    assert "secret detail" not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_run_case_transport_exception_is_a_persisted_failure(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    with mock.patch(
        "evals.answer_evals.harness._run_case_pipeline",
        side_effect=httpx.ConnectError("sensitive endpoint detail"),
    ):
        record = await harness.run_case(case, "transport-failure", output_dir=tmp_path)
    assert record["outcome"] == "fail"
    assert record["error"] == "transport failure: ConnectError"
    persisted = json.loads(Path(record["artifact_path"]).read_text())
    assert persisted["artifact_path"] == record["artifact_path"]
    assert "sensitive endpoint detail" not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_run_case_timeout_is_a_persisted_failure(tmp_path):
    case = _load_corpus_case("positive-001-answer-grounded")
    with mock.patch(
        "evals.answer_evals.harness._run_case_pipeline",
        side_effect=TimeoutError,
    ):
        record = await harness.run_case(case, "timeout-failure", output_dir=tmp_path)
    assert record["outcome"] == "fail"
    assert record["timeout"] is True
    assert record["observed"]["protocol"] == {"status": None, "success": False}
    artifact = Path(record["artifact_path"])
    persisted = json.loads(artifact.read_text())
    assert persisted["case_id"] == record["case_id"]
    assert persisted["observed"]["protocol"] == record["observed"]["protocol"]
    assert persisted["observed"]["error_classification"] == "timeout"
    assert persisted["artifact_path"] == str(artifact)


@pytest.mark.asyncio
async def test_run_selection_narrow_matches_pinned_baseline(tmp_path):
    result = await harness.run_selection(
        "narrow", output_dir=tmp_path, run_id="sel-narrow"
    )
    assert result["summary"]["total"] == 3
    assert result["summary"]["failed"] == 0
    assert result["baseline"]["match"] is True
    assert result["baseline"]["baseline_selection"] == "narrow"


@pytest.mark.asyncio
async def test_selection_deadline_writes_complete_case_artifacts(tmp_path):
    manifest, cases = harness.validate_corpus()
    manifest = json.loads(json.dumps(manifest))
    manifest["case_rules"]["overall_selection_deadline_seconds"] = -1
    result = await harness.run_selection(
        "narrow",
        output_dir=tmp_path,
        run_id="deadline-artifacts",
        manifest=manifest,
        cases=cases,
    )
    assert result["summary"]["failed"] == 3
    for record in result["cases"]:
        assert record["timeout"] is True
        assert record["expected"]
        assert record["observed"]["error"] == "selection deadline exceeded"
        artifact = Path(record["artifact_path"])
        persisted = json.loads(artifact.read_text())
        assert persisted["case_id"] == record["case_id"]
        assert persisted["expected"]["protocol"] == record["expected"]["protocol"]
        assert persisted["observed"]["error_classification"] == "selection_deadline"
        assert persisted["artifact_path"] == str(artifact)


@pytest.mark.asyncio
async def test_run_selection_broad_matches_pinned_baseline_including_miscited_fail(
    tmp_path,
):
    result = await harness.run_selection(
        "broad", output_dir=tmp_path, run_id="sel-broad"
    )
    by_id = {r["case_id"]: r for r in result["cases"]}
    assert result["summary"]["failed"] == 1
    assert by_id["negative-005-answer-miscited"]["outcome"] == "fail"
    miscited = by_id["negative-005-answer-miscited"]
    support = next(
        v for v in miscited["verdicts"] if v["grader"] == "check_citation_support"
    )
    assert support["pass"] is False
    assert result["baseline"]["match"] is True


def test_baseline_compare_detects_mismatch():
    baseline = {
        "selection": "narrow",
        "suite_version": "answer-evals-v1",
        "cases": [
            {
                "case_id": "positive-001-answer-grounded",
                "outcome": "pass",
                "graders": {"check_protocol": True},
            }
        ],
    }
    record = {
        "case_id": "positive-001-answer-grounded",
        "outcome": "fail",
        "verdicts": [{"grader": "check_protocol", "pass": False}],
    }
    comparison = harness.compare_to_baseline([record], baseline)
    assert comparison["match"] is False
    assert comparison["diff"][0]["reason"] == "outcome mismatch"


def test_baseline_compare_detects_cases_missing_from_targeted_run():
    baseline = {
        "selection": "broad",
        "suite_version": "answer-evals-v1",
        "cases": [
            {"case_id": "answer-case", "outcome": "pass", "graders": {}},
            {"case_id": "research-case", "outcome": "pass", "graders": {}},
        ],
    }
    comparison = harness.compare_to_baseline(
        [{"case_id": "answer-case", "outcome": "pass", "verdicts": []}],
        baseline,
    )
    assert comparison["match"] is False
    assert {item["case_id"] for item in comparison["diff"]} == {"research-case"}


@pytest.mark.asyncio
async def test_http_smoke_transport_failure_is_controlled_and_sanitized():
    with mock.patch(
        "httpx.AsyncClient.post",
        new=mock.AsyncMock(side_effect=httpx.ConnectError("secret request URL")),
    ):
        result = await harness.http_smoke("http://127.0.0.1:8084")
    assert result["smoke_ok"] is False
    assert result["status"] is None
    assert result["detail"]["classification"] == "ConnectError"
    assert "secret request URL" not in json.dumps(result)


@pytest.mark.asyncio
async def test_baseline_mismatch_writes_complete_failure_artifact(tmp_path):
    with mock.patch(
        "evals.answer_evals.harness.load_baseline",
        return_value={
            "selection": "narrow",
            "suite_version": "answer-evals-v1",
            "cases": [],
        },
    ):
        result = await harness.run_selection(
            "narrow", output_dir=tmp_path, run_id="baseline-mismatch"
        )
    artifact = Path(result["baseline"]["artifact_path"])
    payload = json.loads(artifact.read_text())
    assert payload["case_id"] == "__baseline__"
    assert payload["expected_constraint"]["selection"] == "narrow"
    assert payload["observed_outcome"]["diff"]
    assert payload["artifact_path"] == str(artifact)


@pytest.mark.asyncio
async def test_stale_baseline_suite_version_fails_with_artifact(tmp_path):
    with mock.patch(
        "evals.answer_evals.harness.load_baseline",
        return_value={
            "selection": "narrow",
            "suite_version": "answer-evals-v0",
            "cases": [],
        },
    ):
        result = await harness.run_selection(
            "narrow", output_dir=tmp_path, run_id="stale-baseline"
        )
    assert result["baseline"]["match"] is False
    assert result["baseline"]["diff"][0]["reason"] == "suite version mismatch"
    assert Path(result["baseline"]["artifact_path"]).exists()


def test_candidate_baseline_writes_only_to_candidate_dir(tmp_path, monkeypatch):
    harness.validate_corpus()
    monkeypatch.chdir(tmp_path)
    baseline = harness.build_candidate_baseline("narrow", [])
    assert baseline["selection"] == "narrow"
    # The function itself never touches the filesystem.
    assert not (harness.CANDIDATE_DIR / "narrow.json").exists()
    assert "recorded_at" in baseline  # volatile field, excluded from compare


# ── Provenance sanitization ────────────────────────────────────────────


def test_provenance_sanitize_removes_forbidden_keys():
    data = {
        "case_id": "c1",
        "outcome": "pass",
        "query": "secret query",
        "answer": "secret answer",
        "prompt": "secret prompt",
        "headers": {"Authorization": "Bearer abc"},
        "api_key": "sk-secretkey",
        "source_content": {PRICING: PINNED_PRICING},
        "safe": {"grader": "check_protocol", "pass": True},
    }
    cleaned = provenance.sanitize(data)
    serialized = json.dumps(cleaned)
    assert "case_id" in cleaned and "outcome" in cleaned
    for forbidden in (
        "query",
        "answer",
        "prompt",
        "headers",
        "api_key",
        "source_content",
    ):
        assert forbidden not in cleaned
    assert "secret query" not in serialized
    assert "secret answer" not in serialized
    assert "secret prompt" not in serialized
    assert cleaned["safe"]["grader"] == "check_protocol"


def test_provenance_secret_sentinel_redaction():
    secret = "sk-" + "A" * 24
    value = {
        "run_id": "r1",
        "note": f"credential {secret} leaked",
        "auth": "Authorization: Bearer abc.def.ghi",
        "model": "fixture-model",
    }
    cleaned = provenance.sanitize(value)
    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert secret not in serialized
    assert provenance.REDACTED in serialized


def test_provenance_never_serializes_queries_answers_or_secrets(tmp_path):
    manifest = harness.load_manifest()
    case = _load_corpus_case("positive-001-answer-grounded")
    record = {
        "case_id": case["id"],
        "case_path": case["_path"],
        "request_hash": harness.request_hash(case),
        "fixture": {
            "search_fixture": case["search_fixture"],
            "llm_fixture": case["llm_fixture"],
            "model": "fixture-model",
            "llm_base_url_host": "llm-fixture",
        },
        "graders": [{"id": "check_protocol", "version": "v1"}],
        "verdicts": [{"grader": "check_protocol", "pass": True}],
        "outcome": "pass",
        "artifact_path": str(tmp_path / "artifact.json"),
        "observed": {"answer": "The Pro plan costs $10. [1]", "query": case["query"]},
    }
    prov = provenance.build_provenance(
        run_id="run-1",
        selection="narrow",
        target="answer",
        record_baseline=False,
        case_records=[record],
        manifest=manifest,
    )
    serialized = json.dumps(prov)
    assert "Pro plan costs $10" not in serialized
    assert case["query"] not in serialized
    assert "[1]" not in serialized
    assert "baseline" not in prov
    assert prov["cases"][0]["case_id"] == case["id"]
    assert prov["cases"][0]["outcome"] == "pass"
    provenance.validate_allowlist(prov)


def test_provenance_allowlist_rejects_unknown_keys_inside_lists(tmp_path):
    invalid = {
        "schema_version": "answer-evals-provenance-v1",
        "cases": [{"case_id": "c1", "unexpected_nested_key": True}],
    }
    with pytest.raises(ValueError, match="not allowlisted"):
        provenance.validate_allowlist(invalid)
    with pytest.raises(ValueError, match="not allowlisted"):
        provenance.write_provenance(tmp_path / "should-not-write.json", invalid)


def test_write_provenance_never_touches_pinned_baselines(tmp_path):
    narrow_before = (harness.BASELINES_DIR / "narrow.json").read_text()
    broad_before = (harness.BASELINES_DIR / "broad.json").read_text()
    out = provenance.write_provenance(
        tmp_path / "prov" / "provenance.json", {"run_id": "r"}
    )
    assert out.exists()
    assert (harness.BASELINES_DIR / "narrow.json").read_text() == narrow_before
    assert (harness.BASELINES_DIR / "broad.json").read_text() == broad_before
    assert not (harness.CANDIDATE_DIR / "narrow.json").exists()
    assert not (harness.CANDIDATE_DIR / "broad.json").exists()


# ── Endpoint allowlist ─────────────────────────────────────────────────


def test_endpoint_allowlist_preflight():
    routing.validate_endpoint_allowlist(["http://test-site:8000/pricing"])
    routing.validate_endpoint_allowlist(["http://scraper-svc:8001"])
    routing.validate_endpoint_allowlist(["http://agent-svc-fixture:8080"])
    with pytest.raises(routing.EndpointAllowlistError):
        routing.validate_endpoint_allowlist(["http://evil.example.com/pricing"])
    with pytest.raises(routing.EndpointAllowlistError):
        routing.validate_endpoint_allowlist(["http://slopsearx:8080/search"])
    with pytest.raises(routing.EndpointAllowlistError):
        routing.validate_endpoint_allowlist(["http://agent-svc:8080/v2/answer"])


@pytest.mark.asyncio
async def test_http_smoke_rejects_off_allowlist_host():
    with pytest.raises(routing.EndpointAllowlistError):
        await harness.http_smoke("http://evil.example.com", timeout=1)
