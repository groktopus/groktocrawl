import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "jev_continuation_pilot_runner", ROOT / "scripts/run_jev_continuation_pilot.py"
)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

PACKET = ROOT / "docs/experiments/typesafe-jev/continuation-pilot-2026-10-03.case-packet.json"


def test_runner_sends_only_stage_payloads_and_gates_dependent_stage(tmp_path):
    fake = tmp_path / "fake_provider.py"
    fake.write_text(
        """import json, sys
request = json.loads(sys.stdin.buffer.read())
assert set(request) == {'model', 'state', 'questions'}
state = request['state']
assert 'assistant_reference' not in state and 'research_agent_proposal' not in state
question_id, question = next(iter(request['questions'].items()))
assert question['type'] == 'noul'
instructions = question['instructions']
if isinstance(instructions, dict) and instructions['question'].startswith('Could a bounded search'):
    p = 0.8
elif isinstance(instructions, dict):
    p = 0.9 if state['obligation']['id'] == 'pilot-03' else 0.1
else:
    p = 0.8
print(json.dumps({'model': 'jev-1.13.0', 'answers': {question_id: {'type': 'noul', 'noul': p}}}))
"""
    )
    result = runner.run(PACKET, [sys.executable, str(fake)])
    assert result["requests_attempted"] == result["requests_validated"] == 17
    assert result["requests_unevaluated"] == result["searches_dispatched"] == 0
    assert result["status"] == "completed"
    rows = {case["case_id"]: case for case in result["cases"]}
    assert rows["pilot-03"]["stage4"]["probabilities"]["n0"] == 0.8
    assert rows["pilot-01"]["stage4"]["status"] == "skipped_by_frozen_binary_argmax"


def test_runner_does_not_retry_or_continue_after_failed_stage(tmp_path):
    fake = tmp_path / "failing_provider.py"
    fake.write_text("import sys; sys.exit(9)\n")
    result = runner.run(PACKET, [sys.executable, str(fake)])
    assert result["requests_attempted"] == 8
    assert result["requests_validated"] == 0
    assert result["requests_unevaluated"] == 8
    assert all(case["stage3"]["status"] == "skipped_after_stage1_failure" for case in result["cases"])


def test_runner_rejects_mutated_packet_before_call(tmp_path):
    packet = json.loads(PACKET.read_text())
    packet["cases"].append(packet["cases"][0])
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(packet))
    marker = tmp_path / "called"
    fake = tmp_path / "fake_provider.py"
    fake.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n")
    try:
        runner.run(mutated, [sys.executable, str(fake)])
    except ValueError as error:
        assert "digest" in str(error)
    else:
        raise AssertionError("mutated packet must be rejected")
    assert not marker.exists()


def test_cli_strips_separator_secures_output_and_handles_all_failures(tmp_path):
    fake = tmp_path / "failing_provider.py"
    fake.write_text("import sys; sys.exit(9)\n")
    output = tmp_path / "private-results.json"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_jev_continuation_pilot.py"),
            "--packet",
            str(PACKET),
            "--output",
            str(output),
            "--",
            sys.executable,
            str(fake),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["requests_unevaluated"] == 8
    assert output.stat().st_mode & 0o777 == 0o600
    assert "returned_model" not in json.loads(output.read_text())
    assert json.loads(output.read_text())["status"] == "completed"


def test_cli_reserves_output_before_any_provider_calls(tmp_path):
    fake = tmp_path / "fake_provider.py"
    marker = tmp_path / "called"
    fake.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n")
    output = tmp_path / "existing.json"
    output.write_text("keep this file")
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_jev_continuation_pilot.py"),
            "--packet",
            str(PACKET),
            "--output",
            str(output),
            "--",
            sys.executable,
            str(fake),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert not marker.exists()
    assert output.read_text() == "keep this file"


def test_cli_leaves_failed_run_distinct_from_completed_receipt(tmp_path):
    packet = json.loads(PACKET.read_text())
    packet["extra"] = "mutation"
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(packet))
    fake = tmp_path / "fake_provider.py"
    fake.write_text("import sys; sys.exit(0)\n")
    output = tmp_path / "failed.json"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_jev_continuation_pilot.py"),
            "--packet",
            str(mutated),
            "--output",
            str(output),
            "--",
            sys.executable,
            str(fake),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert json.loads(output.read_text()) == {
        "error_type": "ValueError",
        "status": "failed_incomplete",
    }


def test_provider_metadata_is_allowlisted_in_receipts(tmp_path):
    fake = tmp_path / "provider_with_extra_metadata.py"
    fake.write_text(
        """import json, sys
r = json.loads(sys.stdin.buffer.read())
qid = next(iter(r['questions']))
print(json.dumps({'model':'jev-1.13.0','answers':{qid:{'type':'noul','noul':0.2}},
                  '_elapsed_ms':3.5,'usage':{'input_tokens':4,'output_tokens':2,'trace':'PRIVATE'},
                  'private_blob':'NEVER_COPY'}))
"""
    )
    result = runner.run(PACKET, [sys.executable, str(fake)])
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded and "NEVER_COPY" not in encoded
    receipt = result["cases"][0]["stage1"]
    assert receipt["elapsed_ms"] == 3.5
    assert receipt["usage"] == {"input_tokens": 4, "output_tokens": 2}
