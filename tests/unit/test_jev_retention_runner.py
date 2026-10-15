"""Offline contract tests for the frozen issue #371 Jev replay runner."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _runner():
    path = Path(__file__).resolve().parents[2] / "scripts/run_jev_retention_371.py"
    spec = importlib.util.spec_from_file_location("run_jev_retention_371", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _freeze(tmp_path: Path, text: str):
    runner = _runner()
    protocol = (
        Path(__file__).resolve().parents[2]
        / "docs/experiments/jev-retention-post-scrape-2026-10-03-protocol.md"
    )
    url = "https://example.org/source"
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / f"01-{url_hash}.md").write_text(text, encoding="utf-8")
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    freeze = {
        "schema_version": "jev-retention-label-freeze/1",
        "protocol_commit": "d46f852",
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(
            Path(runner.__file__).read_bytes()
        ).hexdigest(),
        "research_question": "Does this passage help answer the question?",
        "labels_frozen_before_jev": True,
        "sources": [
            {
                "id": "url-01",
                "search_rank": 1,
                "url": url,
                "page_sha256": content_hash,
                "page_chars": len(text),
                "labels": [
                    {
                        "span": {"start_char": 0, "end_char": len(text)},
                        "label": "yes",
                    }
                ],
            }
        ],
    }
    freeze_path = tmp_path / "labels.json"
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    return runner, freeze_path, pages, freeze


def test_plan_covers_late_page_text_and_matches_product_chunker(tmp_path):
    runner, freeze_path, pages, _ = _freeze(
        tmp_path, "a" * 10_000 + "required late evidence" + "z" * 100
    )
    plan = runner.plan(freeze_path, pages, max_requests=50)
    from agent.research.jev_filter import _passage_chunks

    markdown = next(pages.iterdir()).read_text()
    assert plan["planned_requests"] == 1
    assert plan["in_flight"] == 1
    assert plan["sources"] == 1
    assert [text for _, _, text in runner.passage_chunks(markdown)] == list(
        _passage_chunks(markdown)
    )
    assert any("required late evidence" in text for _, _, text in runner.passage_chunks(markdown))


def test_plan_rejects_packet_that_exceeds_frozen_request_budget(tmp_path):
    runner, freeze_path, pages, _ = _freeze(tmp_path, "x" * 100_100)
    with pytest.raises(ValueError, match="above limit"):
        runner.plan(freeze_path, pages, max_requests=1)


def test_run_uses_current_threshold_and_keeps_only_private_score_receipts(
    tmp_path, monkeypatch
):
    runner, freeze_path, pages, _ = _freeze(
        tmp_path, "a concrete evidence passage"
    )
    receipts = tmp_path / "receipts"
    proxy = tmp_path / "proxy.py"
    proxy.write_text("# fake proxy; subprocess is patched below\n")

    def fake_run(command, **kwargs):
        request = json.loads(kwargs["input"])
        assert request["model"] == "jev-1.13.0"
        assert request["state"]["query"] == "Does this passage help answer the question?"
        assert "passage" in request["state"]
        body = {
            "model": "jev-1.13.0",
            "answers": {"material_contribution": {"type": "noul", "noul": 0.04}},
            "_elapsed_ms": 11,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run(freeze_path, pages, receipts, proxy, max_requests=50)
    source = result["sources"][0]
    assert result["requests_used"] == 1
    assert source["material_contribution_score"] == 0.04
    assert source["retained_by_adr0090"] is False
    receipt_path = receipts / "call-001.json"
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    rendered = receipt_path.read_text()
    assert "a concrete evidence passage" not in rendered
    assert "0.04" in rendered


def test_failed_call_fails_open_without_persisting_raw_error(tmp_path, monkeypatch):
    runner, freeze_path, pages, _ = _freeze(tmp_path, "relevant page")
    receipts = tmp_path / "receipts"
    proxy = tmp_path / "proxy.py"
    proxy.write_text("# fake proxy; subprocess is patched below\n")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="private provider detail"
        ),
    )
    result = runner.run(freeze_path, pages, receipts, proxy, max_requests=50)
    assert result["failed_or_invalid_calls"] == 1
    assert result["sources"][0]["material_contribution_score"] is None
    assert result["sources"][0]["retained_by_adr0090"] is True
    assert "private provider detail" not in (receipts / "call-001.json").read_text()


def test_modified_frozen_page_is_rejected_before_any_run(tmp_path):
    runner, freeze_path, pages, _ = _freeze(tmp_path, "frozen text")
    (pages / next(p.name for p in pages.iterdir())).write_text("changed")
    with pytest.raises(ValueError, match="content changed"):
        runner.plan(freeze_path, pages, max_requests=50)
