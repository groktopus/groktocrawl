import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCRIPT = Path("scripts/run_w12_mission_experiment.py")
SPEC = importlib.util.spec_from_file_location("run_w12_mission_experiment", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def cases():
    payload = json.loads(
        Path("docs/experiments/research-mission/w12.1-cases.json").read_bytes()
    )
    from agent.experimental.research_mission import MissionExperimentCorpus

    return MissionExperimentCorpus.model_validate(payload).cases


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_response_schemas_are_valid_and_closed(arm):
    for case in cases():
        schema = runner.response_schema(case, arm)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        obligations = schema["properties"]["obligation_results"]
        if arm == "control":
            assert obligations == {"type": "null"}
        else:
            expected = {
                item.obligation_id for item in case.reference_mission.obligations
            }
            assert set(obligations["required"]) == expected
            assert obligations["additionalProperties"] is False


def test_env_parser_reads_exact_key_without_expanding_shell(tmp_path):
    marker = tmp_path / "must-not-exist"
    env = tmp_path / ".env"
    env.write_text(
        f"OTHER=no\nLLM_API_KEY='literal-$(touch {marker})'\nLLM_API_KEY_SUFFIX=wrong\n"
    )
    assert runner.read_env_value(env, "LLM_API_KEY").startswith("literal-$(touch")
    assert not marker.exists()


def test_write_json_uses_private_permissions(tmp_path):
    target = tmp_path / "private" / "receipt.json"
    runner.write_json(target, {"ok": True}, private=True)
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text()) == {"ok": True}


def test_model_transport_returns_invalid_json_once_for_durable_capture(monkeypatch):
    calls = 0

    class Response:
        status_code = 200
        text = json.dumps(
            {
                "model": "general",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "not-json"}}
                ],
            }
        )

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "general",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "not-json"}}
                ],
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(runner.httpx, "Client", Client)
    content, receipt, envelope = runner.model_json(
        base_url="http://example.test/v1",
        api_key="secret",
        model="general",
        schema_name="test",
        schema={"type": "object"},
        prompt={"test": True},
        timeout=1,
        max_attempts=3,
    )
    assert content == "not-json"
    assert json.loads(envelope)["model"] == "general"
    assert receipt["attempt"] == 1
    with pytest.raises(json.JSONDecodeError):
        json.loads(content)
    assert calls == 1


def test_trial_preserves_raw_completion_before_schema_failure(tmp_path, monkeypatch):
    case = cases()[0]
    source_payload = json.loads(
        Path("docs/experiments/enterprise-evaluation/corpus.json").read_bytes()
    )
    source_by_id = {
        item["source_id"]: {
            "source_id": item["source_id"],
            "title": item["title"],
            "text": item["text"],
        }
        for item in source_payload["sources"]
    }
    monkeypatch.setattr(
        runner,
        "model_json",
        lambda **_kwargs: (
            "not-json",
            {
                "returned_model": "general",
                "finish_reason": "stop",
                "refusal": False,
                "tool_calls": False,
            },
            '{"raw":"envelope"}',
        ),
    )
    item = runner.WorkItem("trial-1", case.case_id, "control", 1, 1)
    outcome = runner.execute_trial(
        item,
        case=case,
        sources_by_id=source_by_id,
        public_dir=tmp_path / "public",
        private_dir=tmp_path / "private",
        base_url="http://example.test/v1",
        api_key="secret",
        model="general",
        timeout=1,
        max_attempts=1,
    )
    assert outcome["status"] == "failed"
    private = json.loads((tmp_path / "private/trials/trial-1.json").read_text())
    assert private["raw_completion"] == "not-json"
    assert private["raw_envelope"] == '{"raw":"envelope"}'
    public = json.loads((tmp_path / "public/trials/trial-1.json").read_text())
    assert public["status"] == "failed"
    assert public["error_type"] == "JSONDecodeError"
