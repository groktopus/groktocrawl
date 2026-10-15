import json
import subprocess
import sys
import time

import pytest

from scripts.grade_w10_adjudication_with_hermes import (
    grade_packet,
    parse_json_response,
    prompt_for,
    run_command,
)


def packet():
    return {
        "schema_version": "enterprise-evaluation/w10-adjudication-private/1",
        "blind_to_policy_and_repetition": True,
        "items": [
            {
                "observation_id": "source-1",
                "item_type": "source_grade",
                "reviewed_excerpt": "Evidence",
            },
            {
                "observation_id": "claim-1",
                "item_type": "claim_closure",
                "claim": {"text": "Claim"},
                "admitted_sources": [],
            },
        ],
    }


def test_parse_accepts_plain_or_fenced_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_timeout_kills_descendants_that_inherit_output_pipes():
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "time.sleep(30)"
        ),
    ]
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_command(command, timeout=0.1)

    assert time.monotonic() - started < 2


def test_grades_each_item_once_and_resumes_from_private_checkpoints(tmp_path):
    calls = []

    def invoke(path):
        calls.append(path.read_text())
        if '"item_type": "source_grade"' in calls[-1]:
            return json.dumps(
                {
                    "currency": 2,
                    "relevance": 2,
                    "authority": 1,
                    "accuracy": 2,
                    "purpose": 2,
                    "passage_support": "supports",
                    "useful": True,
                    "derivative_or_copied": False,
                    "rationale": "The passage directly supports the claim.",
                }
            )
        return json.dumps(
            {
                "claim_status": "open",
                "contradiction_handling": "not_applicable",
                "rationale": "No admitted source supports the claim.",
            }
        )

    first = grade_packet(packet(), tmp_path, invoke)
    second = grade_packet(packet(), tmp_path, invoke)

    assert len(calls) == 2
    assert first == second
    assert first["reviewer_kind"] == "agent"
    assert not list(tmp_path.glob("*.prompt.txt"))
    assert all(
        path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.glob("*.json")
    )


def test_invalid_hermes_score_is_not_checkpointed(tmp_path):
    value = packet()
    value["items"] = value["items"][:1]

    with pytest.raises(ValueError, match="scores"):
        grade_packet(
            value,
            tmp_path,
            lambda _: json.dumps(
                {
                    "currency": 7,
                    "relevance": 2,
                    "authority": 2,
                    "accuracy": 2,
                    "purpose": 2,
                    "passage_support": "supports",
                    "useful": True,
                    "derivative_or_copied": False,
                    "rationale": "Bad score",
                }
            ),
            max_attempts=1,
        )
    assert not (tmp_path / "source-1.json").exists()


def test_rejects_schema_drift(tmp_path):
    with pytest.raises(ValueError, match="fields"):
        grade_packet(
            packet(),
            tmp_path,
            lambda _: '{"rationale": "missing fields"}',
            max_attempts=1,
        )


def test_retries_and_privately_preserves_invalid_responses(tmp_path):
    value = packet()
    value["items"] = value["items"][:1]
    calls = 0

    def invoke(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return '{"rationale": "missing fields"}'
        return json.dumps(
            {
                "currency": 2,
                "relevance": 2,
                "authority": 1,
                "accuracy": 2,
                "purpose": 2,
                "passage_support": "supports",
                "useful": True,
                "derivative_or_copied": False,
                "rationale": "The passage directly supports the claim.",
            }
        )

    result = grade_packet(value, tmp_path, invoke, max_attempts=2)

    assert calls == 2
    assert len(result["items"]) == 1
    failures = tmp_path / "failures"
    assert (failures / "source-1--attempt-1.error.txt").exists()
    assert (failures / "source-1--attempt-1.response.txt").read_text() == (
        '{"rationale": "missing fields"}'
    )
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in failures.iterdir())


def test_repeated_resume_failures_keep_prior_diagnostics(tmp_path):
    value = packet()
    value["items"] = value["items"][:1]

    for _ in range(2):
        with pytest.raises(ValueError, match="fields"):
            grade_packet(
                value,
                tmp_path,
                lambda _: '{"rationale": "missing fields"}',
                max_attempts=1,
            )

    failures = list((tmp_path / "failures").glob("*.error.txt"))
    assert len(failures) == 2
    assert {path.name for path in failures} == {
        "source-1--attempt-1.error.txt",
        "source-1--attempt-1--retry-2.error.txt",
    }


def test_exhausted_item_can_be_deferred_without_blocking_later_items(tmp_path):
    calls = []

    def invoke(path):
        prompt = path.read_text()
        calls.append(prompt)
        if '"observation_id": "source-1"' in prompt:
            return '{"rationale": "missing fields"}'
        return json.dumps(
            {
                "claim_status": "open",
                "contradiction_handling": "not_applicable",
                "rationale": "No admitted source supports the claim.",
            }
        )

    result = grade_packet(
        packet(),
        tmp_path,
        invoke,
        max_attempts=1,
        continue_on_exhaustion=True,
    )

    assert len(calls) == 2
    assert [item["observation_id"] for item in result["items"]] == ["claim-1"]
    assert result["unresolved_observation_ids"] == ["source-1"]
    assert (tmp_path / "claim-1.json").exists()


def test_resume_reuses_identical_prompt_left_by_killed_process(tmp_path):
    value = packet()
    value["items"] = value["items"][:1]
    stale = tmp_path / "source-1.prompt.txt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(prompt_for(value["items"][0]))

    result = grade_packet(
        value,
        tmp_path,
        lambda _: json.dumps(
            {
                "currency": 2,
                "relevance": 2,
                "authority": 1,
                "accuracy": 2,
                "purpose": 2,
                "passage_support": "supports",
                "useful": True,
                "derivative_or_copied": False,
                "rationale": "The passage directly supports the claim.",
            }
        ),
    )

    assert len(result["items"]) == 1
    assert not stale.exists()
