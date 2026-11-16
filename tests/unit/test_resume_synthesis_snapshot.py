"""Offline tests for replaying a durable acquisition JSONL snapshot."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[2] / "scripts" / "resume_synthesis_snapshot.py"
_SPEC = importlib.util.spec_from_file_location("resume_synthesis_snapshot", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_loads_only_complete_unique_acquired_sources(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = [
        {"type": "sources_pending", "query": "q", "url": "https://example.test/a"},
        {
            "type": "source_scraped",
            "query": "q",
            "url": "https://example.test/a",
            "title": "A",
            "markdown": "body",
        },
        {
            "type": "source_scraped",
            "query": "q",
            "url": "https://example.test/a",
            "title": "A",
            "markdown": "body",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    question, sources = _MODULE.load_snapshot_jsonl(path)

    assert question == "q"
    assert sources == [
        {"url": "https://example.test/a", "title": "A", "markdown": "body"}
    ]


def test_loads_canonical_stream_events_with_explicit_question(tmp_path):
    path = tmp_path / "captured-stream.jsonl"
    rows = [
        {
            "type": "source_scraped",
            "url": "https://example.test/a",
            "source": "playwright",
            "chars": 4,
            "markdown": "body",
        },
        {"type": "done", "query": "not part of the canonical done payload"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    question, sources = _MODULE.load_snapshot_jsonl(
        path, question="Research the captured subject"
    )

    assert question == "Research the captured subject"
    assert sources == [
        {"url": "https://example.test/a", "title": "", "markdown": "body"}
    ]


def test_jsonl_query_if_present_must_match_explicit_question(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "source_scraped",
                "query": "another question",
                "url": "https://example.test/a",
                "markdown": "body",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="multiple research questions"):
        _MODULE.load_snapshot_jsonl(path, question="requested question")


def test_jsonl_without_query_requires_explicit_question(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "source_scraped",
                "url": "https://example.test/a",
                "markdown": "body",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="research question is required"):
        _MODULE.load_snapshot_jsonl(path)


def test_cli_passes_explicit_question_for_canonical_jsonl(monkeypatch, tmp_path):
    import sys

    source_file = tmp_path / "captured.jsonl"
    source_file.write_text(
        json.dumps(
            {
                "type": "source_scraped",
                "url": "https://example.test/source",
                "source": "playwright",
                "chars": 13,
                "markdown": "captured body",
            }
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "state.json"
    calls = []

    def request(_base_url, endpoint, _api_key, body):
        calls.append((endpoint, body))
        if endpoint == "/v2/session/create":
            return {"session_id": "session-fixture"}
        if endpoint.endswith("/evidence/import"):
            return {"sources": [{"id": "ref-fixture"}], "source_chars": 13}
        return {"result": {"status": "in_progress", "completed_chunk_count": 0}}

    monkeypatch.setenv("GROKTOCRAWL_API_KEY", "local-test-key")
    monkeypatch.setattr(_MODULE, "_request", request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resume_synthesis_snapshot.py",
            "--base-url",
            "http://example.test",
            "--sources-jsonl",
            str(source_file),
            "--question",
            "Research the captured subject",
            "--state-file",
            str(state_file),
            "--max-steps",
            "1",
        ],
    )

    assert _MODULE.main() == 0

    import_call = next(
        body for endpoint, body in calls if endpoint.endswith("/evidence/import")
    )
    step_call = next(body for endpoint, body in calls if endpoint.endswith("/step"))
    assert import_call["sources"] == [
        {
            "url": "https://example.test/source",
            "title": "",
            "markdown": "captured body",
        }
    ]
    assert step_call["params"]["question"] == "Research the captured subject"


def test_rejects_conflicting_body_for_same_url(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = [
        {
            "type": "source_scraped",
            "query": "q",
            "url": "https://example.test/a",
            "markdown": "body one",
        },
        {
            "type": "source_scraped",
            "query": "q",
            "url": "https://example.test/a",
            "markdown": "body two",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting bodies"):
        _MODULE.load_snapshot_jsonl(path)


def test_resume_state_is_atomically_saved_owner_only(tmp_path):
    path = tmp_path / "resume.json"
    _MODULE._atomic_private_json(path, {"session_id": "local-fixture"})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "session_id": "local-fixture"
    }
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_resume_reuses_saved_budgets_unless_explicitly_overridden():
    state = {"input_budget_chars": 48_000, "output_budget_tokens": 512}

    assert _MODULE.resolve_budgets(None, None, state) == (48_000, 512)
    assert _MODULE.resolve_budgets(None, 2_048, state) == (48_000, 2_048)
    with pytest.raises(ValueError, match="fixed for a synthesis snapshot"):
        _MODULE.resolve_budgets(64_000, 2_048, state)

    with pytest.raises(ValueError, match="input budget"):
        _MODULE.resolve_budgets(7_999, None, state)
    with pytest.raises(ValueError, match="output token budget"):
        _MODULE.resolve_budgets(None, 255, state)


@pytest.mark.parametrize(
    "budget_option,budget_value",
    [("--input-budget-chars", "7999"), ("--output-budget-tokens", "255")],
)
def test_invalid_initial_budget_fails_before_api_writes(
    monkeypatch, tmp_path, budget_option, budget_value
):
    import sys

    source_file = tmp_path / "captured.jsonl"
    source_file.write_text(
        json.dumps(
            {
                "type": "source_scraped",
                "query": "question",
                "url": "https://example.test/source",
                "markdown": "captured body",
            }
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "state.json"
    calls = []
    monkeypatch.setenv("GROKTOCRAWL_API_KEY", "local-test-key")
    monkeypatch.setattr(_MODULE, "_request", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resume_synthesis_snapshot.py",
            "--base-url",
            "http://example.test",
            "--sources-jsonl",
            str(source_file),
            "--state-file",
            str(state_file),
            budget_option,
            budget_value,
        ],
    )

    with pytest.raises(SystemExit):
        _MODULE.main()

    assert calls == []
    assert not state_file.exists()


def test_resume_rejects_input_budget_change_without_mutating_state(
    monkeypatch, tmp_path
):
    import sys

    state_file = tmp_path / "state.json"
    saved = {
        "version": 1,
        "session_id": "session-fixture",
        "question": "question",
        "synthesis_id": "stable-synthesis-id",
        "ref_ids": ["evidence-fixture"],
        "input_budget_chars": 48_000,
        "output_budget_tokens": 512,
        "last_status": "in_progress",
    }
    _MODULE._atomic_private_json(state_file, saved)
    calls = []
    monkeypatch.setenv("GROKTOCRAWL_API_KEY", "local-test-key")
    monkeypatch.setattr(_MODULE, "_request", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resume_synthesis_snapshot.py",
            "--base-url",
            "http://example.test",
            "--state-file",
            str(state_file),
            "--resume",
            "--input-budget-chars",
            "64000",
        ],
    )

    with pytest.raises(SystemExit):
        _MODULE.main()

    assert calls == []
    assert json.loads(state_file.read_text(encoding="utf-8")) == saved


def test_loads_full_acquired_body_from_dossier_index_with_length_check(tmp_path):
    body = "# A source\n\nExact evidence.\n"
    dossier = tmp_path / "acquired-source.md"
    dossier.write_text(
        "---\n"
        "url: https://example.test/source\n"
        "title: content-negotiation\n"
        f"char_count: {len(body)}\n"
        "content_status: available\n"
        "---\n\n" + body,
        encoding="utf-8",
    )
    index = tmp_path / "00-index.md"
    index.write_text(
        "| Type | Content | File | Title | URL |\n"
        "|------|---------|------|-------|-----|\n"
        "| acquired | available | `acquired-source.md` | content-negotiation | https://example.test/source |\n",
        encoding="utf-8",
    )

    sources = _MODULE.load_snapshot_index(index, "source query")

    assert sources == [
        {
            "url": "https://example.test/source",
            "title": "A source",
            "markdown": body,
        }
    ]
