"""Full-page passage coverage for the private Jev research harness."""

import importlib.util
import json
from pathlib import Path

import pytest


def _runner():
    path = Path(__file__).resolve().parents[2] / "scripts/run_jev_passage_rethink.py"
    spec = importlib.util.spec_from_file_location("run_jev_passage_rethink", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_cover_late_text_without_a_first_excerpt_cutoff():
    runner = _runner()
    markdown = "a" * 10000 + "DECISIVE LATE PASSAGE" + "z" * 2500
    windows = runner._windows(markdown)
    assert windows[0][0] == 0
    assert windows[-1][1] == len(markdown)
    assert all(windows[i][0] <= windows[i - 1][1] for i in range(1, len(windows)))
    assert any("DECISIVE LATE PASSAGE" in text for _, _, text in windows)


def test_freeze_accounts_for_success_and_failure_without_source_quota(tmp_path):
    runner = _runner()
    hits = [
        {"id": f"url-{i:02}", "url": f"https://example.org/{i}", "title": str(i)}
        for i in range(1, 29)
    ]
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"research_question": "What changed?", "results": hits})
    )
    for hit in hits:
        success = hit["id"] != "url-26"
        (tmp_path / f"large-scrape-{hit['id']}.json").write_text(
            json.dumps(
                {
                    "url": hit["url"],
                    "result": {
                        "success": success,
                        "data": {"markdown": "evidence " * 300} if success else None,
                    },
                }
            )
        )
    packet_path = tmp_path / "packet.json"
    runner.freeze(index, tmp_path, packet_path)
    packet = json.loads(packet_path.read_text())
    assert len(packet["pages"]) == 28
    assert len({passage["page_id"] for passage in packet["passages"]}) == 27
    assert packet["pages"][25]["status"] == "failed"
    assert packet["questions"] == runner.QUESTIONS


@pytest.mark.asyncio
async def test_no_key_makes_no_provider_call(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps(
            {"model": runner.MODEL, "questions": runner.QUESTIONS, "passages": []}
        )
    )
    with pytest.raises(ValueError, match="API key absent"):
        await runner.run(packet, tmp_path / "receipts", None)
    assert not (tmp_path / "receipts").exists()
