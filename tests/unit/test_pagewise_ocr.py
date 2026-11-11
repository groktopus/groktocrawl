"""Page receipts, source isolation and real process fencing tests."""

import subprocess
import sys
import time

import pytest
from parse_svc import pagewise
from parse_svc.checkpoints import Checkpoints
from PIL import Image


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PARSE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PARSE_OCR_BACKEND", "tesseract")
    return Checkpoints(tmp_path, 10**7, 604800)


def fake_pdf(monkeypatch, texts):
    monkeypatch.setattr(
        pagewise,
        "native_page",
        lambda source, number, timeout: (
            {"pages": len(texts)} if number == 0 else {"text": texts[number - 1]}
        ),
    )
    monkeypatch.setattr(
        pagewise, "render_page", lambda *a: Image.new("RGB", (20, 20), "black")
    )


def test_mixed_long_document_no_truncation_and_resume(state, monkeypatch):
    fake_pdf(monkeypatch, ["native text " * 20] * 11 + [""])
    calls = []
    monkeypatch.setattr(
        pagewise,
        "tesseract_page",
        lambda *a: (calls.append(1) or "scanned page", 0.99, 2),
    )
    result = pagewise.parse_pdf_pages(b"document", "a.pdf", scope="scope")
    assert result["metadata"]["coverage"]["status"] == "complete"
    assert result["metadata"]["coverage"]["native_pages"] == 11
    assert result["metadata"]["coverage"]["tesseract_pages"] == 1
    assert "--- Page 12 ---" in result["markdown"]
    again = pagewise.parse_pdf_pages(b"document", "a.pdf", scope="scope")
    assert again["metadata"]["coverage"]["cached_pages"] == 12
    assert len(calls) == 1
    for r in result["metadata"]["page_results"]:
        assert result["markdown"][r["markdown_start"] : r["markdown_end"]].startswith(
            "--- Page"
        )


def test_scope_and_settings_isolate_receipts(state, monkeypatch):
    fake_pdf(monkeypatch, [""])
    calls = []
    monkeypatch.setattr(
        pagewise, "tesseract_page", lambda *a: (calls.append(1) or "words", 0.99, 1)
    )
    for scope in ["a", "b"]:
        pagewise.parse_pdf_pages(b"doc", "a.pdf", scope=scope)
    monkeypatch.setenv("PARSE_RENDER_MAX_EDGE", "1800")
    pagewise.parse_pdf_pages(b"doc", "a.pdf", scope="a")
    assert len(calls) == 3


def test_empty_ocr_not_blank_and_bad_quality_incomplete(state, monkeypatch):
    fake_pdf(monkeypatch, ["", ""])
    monkeypatch.setattr(pagewise, "tesseract_page", lambda *a: ("", None, 0))
    result = pagewise.parse_pdf_pages(b"doc", "a.pdf")
    assert result["metadata"]["coverage"]["status"] == "failed"
    assert result["metadata"]["coverage"]["blank_pages"] == 0


def test_render_proven_blank(state, monkeypatch):
    fake_pdf(monkeypatch, [""])
    monkeypatch.setattr(
        pagewise, "render_page", lambda *a: Image.new("RGB", (20, 20), "white")
    )
    result = pagewise.parse_pdf_pages(b"doc", "a.pdf")
    assert result["metadata"]["coverage"]["blank_pages"] == 1
    assert result["metadata"]["coverage"]["status"] == "complete"


def test_real_process_global_lock_serializes(state, tmp_path):
    log = tmp_path / "calls"
    code = """import sys,time
from pathlib import Path
from parse_svc.checkpoints import Checkpoints
s=Checkpoints(Path(sys.argv[1]),10**7,604800)
with s.lane_lock(time.monotonic()+5):
 with open(sys.argv[2],'a') as f: f.write('start\\n');f.flush()
 time.sleep(.15)
 with open(sys.argv[2],'a') as f: f.write('end\\n')
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(state.root), str(log)])
        for _ in range(3)
    ]
    assert all(p.wait(timeout=10) == 0 for p in processes)
    assert log.read_text().splitlines() == ["start", "end"] * 3


def test_disconnected_skips_unstarted_pages(state, monkeypatch):
    import threading

    fake_pdf(monkeypatch, ["native " * 20] * 3)
    event = threading.Event()
    event.set()
    result = pagewise.parse_pdf_pages(b"doc", "a.pdf", cancelled=event)
    assert result["metadata"]["coverage"]["skipped_pages"] == 3
    assert result["metadata"]["coverage"]["status"] == "failed"


def test_capacity_fail_closed(tmp_path):
    from parse_svc.checkpoints import CheckpointError

    store = Checkpoints(tmp_path, 100, 604800)
    with pytest.raises(CheckpointError, match="capacity"):
        store.admit("test", "scope", b"x" * 101, "policy", 1)


def test_partial_resume_reuses_completed_pages_only(state, monkeypatch):
    fake_pdf(monkeypatch, ["native " * 20, ""])
    monkeypatch.setattr(pagewise, "tesseract_page", lambda *a: ("unclear", 0.2, 1))
    first = pagewise.parse_pdf_pages(b"doc", "a.pdf", scope="one")
    assert first["metadata"]["coverage"]["status"] == "partial"
    monkeypatch.setattr(
        pagewise, "tesseract_page", lambda *a: ("now readable", 0.99, 2)
    )
    second = pagewise.parse_pdf_pages(b"doc", "a.pdf", scope="one")
    assert second["metadata"]["coverage"]["status"] == "complete"
    assert second["metadata"]["coverage"]["cached_pages"] == 1


def test_142_page_metadata_compact_and_no_transcript_duplication(state, monkeypatch):
    import json

    fake_pdf(monkeypatch, ["native text " * 1000] * 142)
    result = pagewise.parse_pdf_pages(b"doc", "long.pdf", scope="one")
    metadata = result["metadata"]
    assert len(json.dumps(metadata).encode()) < 40 * 1024
    assert metadata["page_results_omitted"] == 0
    assert len(metadata["page_results"]) == 142
    assert all(
        "text" not in r and "source_sha256" not in r for r in metadata["page_results"]
    )


def test_native_worker_hard_deadline(tmp_path, monkeypatch):
    # A deliberately stalled parser import runs in a real, killable child.
    import os

    from parse_svc.checkpoints import DeadlineExceededError

    (tmp_path / "pypdf.py").write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setenv(
        "PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    started = time.monotonic()
    with pytest.raises(DeadlineExceededError):
        pagewise.native_page(tmp_path / "irrelevant.pdf", 0, 0.2)
    assert time.monotonic() - started < 2


def test_renderer_hard_deadline_without_pdfinfo(tmp_path, monkeypatch):
    import os

    from parse_svc.checkpoints import DeadlineExceededError
    from parse_svc.settings import OcrSettings

    executable = tmp_path / "pdftoppm"
    executable.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(30)\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    start = time.monotonic()
    with pytest.raises(DeadlineExceededError, match="render_timeout"):
        pagewise.render_page(b"pdf", 1, OcrSettings.load(), start + 0.2)
    assert time.monotonic() - start < 2


def test_sparse_native_values_survive_ocr_failures(state, monkeypatch):
    from parse_svc.checkpoints import CheckpointError

    fake_pdf(monkeypatch, ["Revenue 2025 $42M"])

    def fail(*args, **kwargs):
        raise CheckpointError("test_ocr_failure")

    monkeypatch.setattr(pagewise, "tesseract_page", fail)
    result = pagewise.parse_pdf_pages(b"doc", "table.pdf", scope="one")
    assert "Revenue 2025 $42M" in result["markdown"]
    assert result["metadata"]["coverage"]["status"] == "failed"
    assert "native_text_incomplete" in result["metadata"]["page_results"][0]["warnings"]


def test_sparse_native_does_not_replace_partial_ocr(state, monkeypatch):
    fake_pdf(monkeypatch, ["Revenue $42M"])
    monkeypatch.setattr(
        pagewise,
        "tesseract_page",
        lambda *a: ("Revenue $42M and operating profit $9M", 0.3, 7),
    )
    result = pagewise.parse_pdf_pages(b"doc", "table.pdf", scope="one")
    assert "operating profit $9M" in result["markdown"]
    assert result["markdown"].count("Revenue $42M") == 1
    assert result["metadata"]["coverage"]["status"] == "failed"


def test_cpu_attempt_budget_survives_resubmission(state, monkeypatch):
    fake_pdf(monkeypatch, [""])
    calls = []

    def fail(*args):
        calls.append(1)
        raise subprocess.CalledProcessError(1, "tesseract")

    monkeypatch.setattr(pagewise, "tesseract_page", fail)
    for _ in range(2):
        result = pagewise.parse_pdf_pages(b"doc", "a.pdf", scope="same")
        assert result["metadata"]["coverage"]["status"] == "failed"
    assert len(calls) == 2


def test_cpu_checkpoint_source_and_started_attempt_retained(state):
    state.admit("test", "scope", b"exact source", "policy", 1)
    state.begin_attempt("test", 1, 2)
    assert state.attempts("test", 1)[0]["outcome"] == "started"
    with state.connect() as db:
        assert (
            db.execute("SELECT source FROM executions").fetchone()[0] == b"exact source"
        )
