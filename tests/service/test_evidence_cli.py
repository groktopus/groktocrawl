"""Evidence client compatibility, selection and completion coverage contracts."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.service.test_cli import _cli_ns

COVERAGE = {"selected_chars": 256, "omitted_chars": 400, "coverage_complete": False}


@pytest.mark.parametrize("method", ["create_agent", "create_agent_stream", "answer"])
def test_explicit_budget_and_default_payload_compatibility(method):
    client = _cli_ns["Client"](server="http://test", dry_run=True)
    if method == "create_agent_stream":
        captured = []
        client._post_stream_with_retry = lambda _p, data, *_a, **_kw: captured.append(
            data
        )
        getattr(client, method)("question")
        assert "evidence_budget_chars" not in captured[-1]
        getattr(client, method)("question", evidence_budget_chars=4096)
        assert captured[-1]["evidence_budget_chars"] == 4096
    else:
        default = getattr(client, method)("question")
        explicit = getattr(client, method)("question", evidence_budget_chars=4096)
        assert "evidence_budget_chars" not in default["json"]
        assert explicit["json"]["evidence_budget_chars"] == 4096


def test_evidence_dry_run_includes_exact_parameters():
    client = _cli_ns["Client"](server="http://test", dry_run=True)
    result = client.select_session_evidence(
        "session-a", "ref-a", offset=256, budget_chars=256, expected_digest="a" * 64
    )
    assert result["method"] == "GET"
    assert result["url"].endswith("/v2/session/session-a/evidence/ref-a")
    assert result["params"] == {
        "query": "",
        "offset": 256,
        "budget_chars": 256,
        "expected_digest": "a" * 64,
    }


@pytest.mark.parametrize(
    "flags",
    [
        ["--budget-chars", "255"],
        ["--budget-chars", "128001"],
        ["--offset", "-1"],
        ["--expected-digest", "bad"],
    ],
)
def test_evidence_parser_rejects_invalid_contract(flags):
    with pytest.raises(SystemExit):
        _cli_ns["make_parser"]().parse_args(["evidence", "s", "r", *flags])


def test_query_offset_conflict_never_calls_api():
    args = _cli_ns["make_parser"]().parse_args(
        ["evidence", "s", "r", "--query", "topic", "--offset", "1"]
    )
    client = MagicMock()
    with pytest.raises(SystemExit):
        _cli_ns["cmd_evidence"](client, args)
    client.select_session_evidence.assert_not_called()


def test_evidence_cli_retains_spans_hash_and_continuation(monkeypatch):
    response = {
        "success": True,
        "spans": [{"start": 0, "end": 3, "quote": "abc"}],
        "content_digest": "a" * 64,
        "coverage_complete": False,
        "continuation": {"params": {"offset": 3}},
    }
    args = _cli_ns["make_parser"]().parse_args(["evidence", "s", "r"])
    client = MagicMock(dry_run=False)
    client.select_session_evidence.return_value = response
    outputs = []
    monkeypatch.setitem(_cli_ns, "emit", lambda _text, payload: outputs.append(payload))
    _cli_ns["cmd_evidence"](client, args)
    assert outputs == [response]


@pytest.mark.parametrize("command", ["agent", "answer"])
@pytest.mark.parametrize("sync", [False, True])
def test_completion_json_preserves_coverage_and_optional_budget(
    command, sync, monkeypatch
):
    args = _cli_ns["make_parser"]().parse_args(
        [
            command,
            "question",
            "--evidence-budget-chars",
            "4096",
            *(["--sync"] if sync else []),
        ]
    )
    result = {
        "result": "answer",
        "answer": "answer",
        "sources": [],
        "citations": [],
        "evidence_coverage": COVERAGE,
    }
    stream = SimpleNamespace(
        iter_lines=lambda **_kw: iter(
            ["data: " + json.dumps({"type": "done", **result}), "data: [DONE]"]
        )
    )
    client = MagicMock(dry_run=False)
    client.create_agent.return_value = {"id": "job-a"}
    client.agent_status.return_value = {"status": "completed", "data": result}
    client.create_agent_stream.return_value = {"_stream": stream}
    client.answer.return_value = result if sync else {"_stream": stream}
    outputs = []
    monkeypatch.setitem(_cli_ns, "JSON_OUTPUT", True)
    monkeypatch.setitem(_cli_ns, "emit", lambda _text, payload: outputs.append(payload))
    monkeypatch.setattr(_cli_ns["time"], "sleep", lambda *_a: None)
    _cli_ns["cmd_" + command](client, args)
    output = outputs[-1]["result"] if command == "agent" and sync else outputs[-1]
    assert output["evidence_coverage"] == COVERAGE
    method = (
        client.answer
        if command == "answer"
        else client.create_agent
        if sync
        else client.create_agent_stream
    )
    assert method.call_args.kwargs["evidence_budget_chars"] == 4096
