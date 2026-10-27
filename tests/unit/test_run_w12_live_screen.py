"""Admission must fail before reading parent questions; no live operations."""

import json
from types import SimpleNamespace

import pytest

from scripts import run_w12_live_screen as cli


def descriptors():
    return {
        "agent": {
            "model": "free",
            "jev_enabled": False,
            "thinking": False,
            "token_parameter": "max_tokens",
        },
        "search": {
            "enabled": True,
            "api_key_configured": True,
            "suggestions": False,
            "jev_or_rerank_env": False,
            "max_results": 20,
        },
        "source": {
            "politeness_enabled": False,
            "distributed_politeness": False,
            "compatibility_proxy_configured": False,
            "environment_proxy_configured": False,
        },
        "gateway": {
            "drop_params": False,
            "free_models": ["openai/ornith/ornith-1.5-122b:free"],
            "retry_policy_configured": False,
            "callbacks_observability_only": True,
            "pre_call_rules_configured": False,
            "declared_costs": [{"input_cost_per_token": 0, "output_cost_per_token": 0}],
        },
    }


def admission():
    hashes = cli.artifact_hashes()
    digest = cli.bundle_hash(hashes)
    return {
        "schema_version": "w12.4-first-screen-admission/2",
        "owner_authorized_live_dispatch": True,
        "independent_review_no_open_p1_p2": True,
        "evaluator_isolation_confirmed": True,
        "runtime_files": hashes,
        "bundle_sha256": digest,
        "reviewed_bundle_sha256": digest,
        "backend_descriptor_sha256": "fixture",
        "blocked_source_sha256": ["a", "b", "c", "d"],
        "model_provider_attempt_upper_per_call": 1,
        "search_provider_attempt_upper_per_call": 1,
        "source_get_upper_per_call": 2,
    }


def test_gateway_cap_dropping_is_a_hard_admission_failure():
    values = descriptors()
    cli.validate_backends(values)
    values["gateway"]["drop_params"] = True
    with pytest.raises(ValueError, match="may remove max_tokens"):
        cli.validate_backends(values)


@pytest.mark.asyncio
async def test_bad_backend_never_opens_question_packet(monkeypatch, tmp_path):
    values = descriptors()
    values["gateway"]["drop_params"] = True

    class DescribeOnly:
        def __init__(self, *args):
            pass

        async def call(self, kind, body, role=None):
            assert kind == "describe"
            return values[role]

    class UnreadableQuestions:
        def read_text(self):
            pytest.fail("parent questions must stay unread")

    parent = tmp_path / "admission.json"
    parent.write_text(json.dumps(admission()))
    monkeypatch.setattr(cli, "SSHBackend", DescribeOnly)
    args = SimpleNamespace(
        describe_only=False,
        execute=True,
        admission=parent,
        questions=UnreadableQuestions(),
        output=tmp_path / "out",
    )
    with pytest.raises(ValueError, match="may remove max_tokens"):
        await cli.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize(
    "key",
    [
        "owner_authorized_live_dispatch",
        "independent_review_no_open_p1_p2",
        "evaluator_isolation_confirmed",
    ],
)
def test_missing_parent_gate_cannot_be_replaced_by_local_claim(key):
    value = admission()
    value[key] = False
    with pytest.raises(ValueError, match="parent admission"):
        cli.validate_admission(value, cli.artifact_hashes())


def test_runtime_or_review_hash_drift_refuses_execution():
    value = admission()
    value["reviewed_bundle_sha256"] = "wrong"
    with pytest.raises(ValueError, match="reviewed candidate"):
        cli.validate_admission(value, cli.artifact_hashes())
    value = admission()
    value["runtime_files"] = {}
    with pytest.raises(ValueError, match="runtime freeze"):
        cli.validate_admission(value, cli.artifact_hashes())
