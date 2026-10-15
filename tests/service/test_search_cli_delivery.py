"""Real CLI entrypoint contracts for search metadata and SSE delivery."""

from __future__ import annotations

import json
import sys

import pytest
import requests

from tests.service.test_cli import _cli_ns


class JsonResponse:
    status_code = 200
    headers: dict[str, str] = {}
    url = "http://fixture/v2/search"

    def __init__(self, payload: dict):
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class SseResponse:
    status_code = 200
    headers: dict[str, str] = {}
    url = "http://fixture/v2/search"

    def __init__(self, events: list[dict]):
        self.lines = [f"data: {json.dumps(event)}" for event in events]
        self.lines.append("data: [DONE]")

    def iter_lines(self, decode_unicode=True):
        assert decode_unicode is True
        yield from self.lines

    def close(self):
        pass


def invoke_cli(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(
        sys, "argv", ["groktocrawl", "--server", "http://fixture", *argv]
    )
    _cli_ns["main"]()


def test_json_search_preserves_evidence_and_continuation_metadata(monkeypatch, capsys):
    payload = {
        "success": True,
        "data": {"web": [{"url": "https://example.test/a", "title": "A"}]},
        "evidence_coverage": {},
        "output": {},
        "query_variations": ["term", "related term"],
        "warning": "one engine unavailable",
    }
    monkeypatch.setattr(requests, "request", lambda **_kwargs: JsonResponse(payload))

    invoke_cli(monkeypatch, ["search", "term", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "results": payload["data"]["web"],
        "query": "term",
        "evidence_coverage": {},
        "output": {},
        "query_variations": ["term", "related term"],
        "warning": "one engine unavailable",
    }


@pytest.mark.parametrize("json_mode", [False, True])
def test_stream_error_is_visible_and_exits_nonzero(monkeypatch, capsys, json_mode):
    events = [{"type": "error", "content": "Semantic service is unavailable"}]
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: SseResponse(events))
    argv = ["search", "term", "--stream"]
    if json_mode:
        argv.append("--json")

    with pytest.raises(SystemExit) as exc:
        invoke_cli(monkeypatch, argv)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Semantic service is unavailable" in captured.out + captured.err
    if json_mode:
        assert json.loads(captured.out.splitlines()[0]) == events[0]


def test_human_rich_stream_displays_tokens_and_sources_once(monkeypatch, capsys):
    events = [
        {
            "type": "search_result",
            "result": {
                "url": "https://example.test/a",
                "title": "A",
                "description": "snippet",
            },
        },
        {"type": "token", "content": "Grounded "},
        {"type": "token", "content": "answer."},
        {
            "type": "done",
            "total_results": 1,
            "output": {
                "content": "Grounded answer.",
                "grounding": [{"url": "https://example.test/a", "title": "A"}],
            },
        },
    ]
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: SseResponse(events))

    invoke_cli(monkeypatch, ["search", "term", "--search-type", "rich", "--stream"])

    output = capsys.readouterr().out
    assert output.count("https://example.test/a") == 1
    assert output.count("Grounded answer.") == 1


def test_human_structured_stream_displays_completion_without_duplicate_results(
    monkeypatch, capsys
):
    events = [
        {
            "type": "search_result",
            "result": {
                "url": "https://example.test/a",
                "title": "A",
                "description": "snippet",
            },
        },
        {
            "type": "done",
            "results": [{"url": "https://example.test/a", "title": "A"}],
            "output": {"content": {"answer": "structured"}},
        },
    ]
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: SseResponse(events))

    invoke_cli(
        monkeypatch,
        [
            "search",
            "term",
            "--search-type",
            "rich",
            "--output-schema",
            '{"type":"object"}',
            "--stream",
        ],
    )

    output = capsys.readouterr().out
    assert output.count("https://example.test/a") == 1
    assert '"answer": "structured"' in output
