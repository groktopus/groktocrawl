import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_slsa_incumbent_control", ROOT / "scripts/run_slsa_incumbent_control.py"
)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
PACKET = ROOT / "docs/experiments/typesafe-jev/slsa-replay-2026-10-03.case-packet.json"


def test_incumbent_runner_sends_only_frozen_public_prompts_sequentially(tmp_path):
    marker = tmp_path / "calls.jsonl"
    proxy = tmp_path / "proxy.py"
    proxy.write_text(
        """import json, sys
r = json.loads(sys.stdin.buffer.read())
assert r['model'] == 'free'
assert [m['role'] for m in r['messages']] == ['system', 'user']
assert 'rank-07' not in json.dumps(r) and 'evaluator_only' not in json.dumps(r)
with open(sys.argv[1], 'a') as f: f.write(json.dumps(r) + '\\n')
print(json.dumps({'model':'free','_configured_model_alias':'free','_elapsed_ms':4.0,
 'choices':[{'message':{'content':'[]'}}]}))
"""
    )
    result = runner.run(PACKET, [sys.executable, str(proxy), str(marker)])
    assert result["calls_attempted"] == result["calls_evaluated"] == 4
    assert result["searches_dispatched"] == 0
    assert len(marker.read_text().splitlines()) == 4
    assert all(row["status"] == "parsed_incumbent_array" for row in result["outcomes"])
    assert all(row["recommendation_only"] is True for row in result["outcomes"])
    assert all(row["topics"] == [] for row in result["outcomes"])


def test_parse_failures_are_distinct_from_empty_recommendations():
    assert runner._parse_content("[]") == ("parsed_incumbent_array", [], 0)
    assert runner._parse_content("not json") == ("invalid_json", None, None)
    assert runner._parse_content("{\"topic\":\"x\"}") == ("invalid_shape", None, None)
    assert runner._parse_content("[1, \"valid topic\"]") == (
        "parsed_incumbent_array", ["valid topic"], 1
    )


def test_non_object_provider_json_and_inflight_calls_are_journaled(tmp_path):
    proxy = tmp_path / "array_response.py"
    proxy.write_text("print('[]')\n")
    progress = []
    result = runner.run(PACKET, [sys.executable, str(proxy)], checkpoint=progress.append)
    assert result["calls_attempted"] == 4
    assert all(row["status"] == "invalid_response_shape" for row in result["outcomes"])
    assert progress[0]["outcomes"][0]["status"] == "inflight"
    assert progress[1]["outcomes"][0]["status"] == "invalid_response_shape"


def test_cli_existing_output_fails_before_any_call(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve")
    marker = tmp_path / "called"
    proxy = tmp_path / "proxy.py"
    proxy.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n")
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_slsa_incumbent_control.py"),
         "--packet", str(PACKET), "--output", str(output), "--", sys.executable, str(proxy)],
        capture_output=True, text=True, check=False,
    )
    assert process.returncode != 0
    assert output.read_text() == "preserve"
    assert not marker.exists()
