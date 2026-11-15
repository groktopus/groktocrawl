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
