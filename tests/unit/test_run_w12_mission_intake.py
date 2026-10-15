import importlib.util
import json
from pathlib import Path

SCRIPT = Path("scripts/run_w12_mission_intake.py")
SPEC = importlib.util.spec_from_file_location("run_w12_mission_intake", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def cases():
    from agent.experimental.research_mission import MissionExperimentCorpus

    return MissionExperimentCorpus.model_validate(
        json.loads(
            Path("docs/experiments/research-mission/w12.1-cases.json").read_bytes()
        )
    ).cases


def test_intake_failure_preserves_raw_completion(tmp_path, monkeypatch):
    case = cases()[0]
    requested = {}

    def fake_model_json(**kwargs):
        requested.update(kwargs)
        return (
            "not-json",
            {
                "returned_model": "general",
                "finish_reason": "stop",
                "refusal": False,
                "tool_calls": False,
            },
            '{"raw":"envelope"}',
        )

    monkeypatch.setattr(
        runner.downstream,
        "model_json",
        fake_model_json,
    )
    item = runner.IntakeWorkItem("intake-1", case.case_id, 1, 1)
    outcome = runner.execute_intake(
        item,
        case=case,
        public_dir=tmp_path / "public",
        private_dir=tmp_path / "private",
        base_url="http://example.test/v1",
        api_key="secret",
        model="general",
        timeout=1,
        max_attempts=1,
    )
    assert outcome["status"] == "failed"
    private = json.loads((tmp_path / "private/intake/intake-1.json").read_text())
    assert private["raw_completion"] == "not-json"
    assert private["raw_envelope"] == '{"raw":"envelope"}'
    public = json.loads((tmp_path / "public/intake/intake-1.json").read_text())
    assert public["error_type"] == "JSONDecodeError"
    assert requested["reasoning_effort"] == "minimal"
    assert requested["max_tokens"] == 20000


def test_intake_response_schema_closes_the_top_level_envelope():
    schema = runner.intake_response_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "action",
        "mission",
        "clarifying_question",
        "rationale",
    }
