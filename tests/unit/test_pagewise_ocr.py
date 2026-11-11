"""Page receipts, source isolation and real process fencing tests."""

import subprocess
import sys
import time

import pytest
from parse_svc import pagewise
from parse_svc.checkpoints import Checkpoints, LaneUncertainError
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


def test_real_process_crash_leaves_unknown_marker_and_source(state):
    state.admit("a", "scope", b"original source", "settings", 1)
    code = """import sys,time
from pathlib import Path
from parse_svc.checkpoints import Checkpoints
s=Checkpoints(Path(sys.argv[1]),10**7,604800)
with s.lane_lock(time.monotonic()+3):
 s.begin_dispatch('a',1,{'started_at':time.time()})
 print('dispatched',flush=True)
 time.sleep(60)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(state.root)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "dispatched"
        proc.kill()
        proc.wait(timeout=5)
        with state.lane_lock(time.monotonic() + 2):
            with pytest.raises(LaneUncertainError):
                state.begin_dispatch("a", 1, {})
        assert state.attempts("a", 1)[0]["outcome"] == "unknown"
        with state.connect() as db:
            assert (
                db.execute("SELECT source FROM executions").fetchone()[0]
                == b"original source"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


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


@pytest.fixture
def model(state, monkeypatch):
    from parse_svc.vision_ocr import OcrSettings

    monkeypatch.setenv("PARSE_OCR_BACKEND", "gemma")
    monkeypatch.setenv("PARSE_OCR_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("PARSE_OCR_MODEL", "test-gemma")
    monkeypatch.setenv("PARSE_OCR_MODEL_REVISION", "test-revision")
    state.admit("test", "scope", b"original", "policy", 1)
    return OcrSettings.load()


def test_gemma_single_image_receipt_and_no_history(state, model, monkeypatch):
    import httpx
    from parse_svc import vision_ocr

    calls = []

    def post(payload, timeout):
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "faithful words"}, "finish_reason": "stop"}
                ]
            },
        )

    monkeypatch.setattr("parse_svc.vision_ocr.model_post", post)
    result = vision_ocr.transcribe(
        Image.new("RGB", (20, 20)),
        model,
        state,
        "test",
        1,
        time.monotonic() + 5,
        {"page_number": 1},
    )
    assert result["status"] == "complete"
    assert result["model_revision"] == "test-revision"
    assert len(calls[0]["messages"]) == 1
    assert len(calls[0]["messages"][0]["content"]) == 2
    assert calls[0]["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert "tools" not in calls[0]
    assert state.page("test", 1)["text"] == "faithful words"
    assert state.lane_marker() is None


def test_timeout_blocks_subsequent_dispatch_across_execution(state, model, monkeypatch):
    import httpx
    from parse_svc.vision_ocr import transcribe

    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("secret provider detail")

    monkeypatch.setattr("parse_svc.vision_ocr.model_post", post)
    for _ in range(2):
        with pytest.raises(LaneUncertainError):
            transcribe(
                Image.new("RGB", (20, 20)),
                model,
                state,
                "test",
                1,
                time.monotonic() + 5,
                {},
            )
    assert len(calls) == 1
    assert state.lane_marker()["outcome"] == "unknown"


def test_known_retry_budget_persisted(state, model, monkeypatch):
    import httpx
    from parse_svc.checkpoints import CheckpointError
    from parse_svc.vision_ocr import transcribe

    calls = []

    def post(*a, **k):
        calls.append(1)
        return httpx.Response(503)

    monkeypatch.setattr("parse_svc.vision_ocr.model_post", post)
    for _ in range(2):
        with pytest.raises(CheckpointError):
            transcribe(
                Image.new("RGB", (20, 20)),
                model,
                state,
                "test",
                1,
                time.monotonic() + 5,
                {},
            )
    assert len(calls) == 2
    assert len(state.attempts("test", 1)) == 2
    assert state.lane_marker() is None


def test_truncated_receipt_not_complete(state, model, monkeypatch):
    import httpx
    from parse_svc.vision_ocr import transcribe

    monkeypatch.setattr(
        "parse_svc.vision_ocr.model_post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "partial"}, "finish_reason": "length"}
                ]
            },
        ),
    )
    result = transcribe(
        Image.new("RGB", (20, 20)), model, state, "test", 1, time.monotonic() + 5, {}
    )
    assert result["status"] == "truncated"
    assert state.page("test", 1)["status"] == "truncated"


def test_disconnected_skips_unstarted_pages(state, monkeypatch):
    import threading

    fake_pdf(monkeypatch, ["native " * 20] * 3)
    event = threading.Event()
    event.set()
    result = pagewise.parse_pdf_pages(b"doc", "a.pdf", cancelled=event)
    assert result["metadata"]["coverage"]["skipped_pages"] == 3
    assert result["metadata"]["coverage"]["status"] == "failed"


def test_retention_never_deletes_unknown_lane(state):
    state.admit("test", "scope", b"source", "policy", 1)
    state.begin_dispatch("test", 1, {})
    with state.connect() as db:
        db.execute("UPDATE executions SET accessed=0")
    state.cleanup()
    assert state.lane_marker()
    with state.connect() as db:
        assert db.execute("SELECT count(*) FROM executions").fetchone()[0] == 1


def test_reconcile_requires_idle_and_retains_history(state, model, monkeypatch):
    import httpx
    from parse_svc.vision_ocr import reconcile_idle

    state.begin_dispatch(
        "test",
        1,
        {
            "endpoint": model.base_url,
            "model": model.model,
            "model_revision": model.revision,
        },
    )

    def busy(*a, **k):
        return httpx.Response(
            200,
            request=httpx.Request("GET", "http://localhost/metrics"),
            text="vllm:num_requests_running 1\nvllm:num_requests_waiting 0",
        )

    monkeypatch.setattr(httpx.Client, "get", busy)
    with pytest.raises(LaneUncertainError):
        reconcile_idle(state, model)
    assert state.lane_marker()

    def idle(*a, **k):
        return httpx.Response(
            200,
            request=httpx.Request("GET", "http://localhost/metrics"),
            text="vllm:num_requests_running 0\nvllm:num_requests_waiting 0",
        )

    monkeypatch.setattr(httpx.Client, "get", idle)
    assert reconcile_idle(state, model)
    assert state.lane_marker() is None
    assert len(state.attempts("test", 1)) == 1
    with state.connect() as db:
        assert db.execute("SELECT count(*) FROM reconciliations").fetchone()[0] == 1


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


def test_model_worker_slow_body_absolute_deadline(state, model, monkeypatch):
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from parse_svc.vision_ocr import transcribe

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    monkeypatch.setenv("PARSE_OCR_BASE_URL", url)
    model = replace(model, base_url=url)
    started = time.monotonic()
    try:
        with pytest.raises(LaneUncertainError):
            transcribe(
                Image.new("RGB", (20, 20)), model, state, "test", 1, started + 0.7, {}
            )
        assert time.monotonic() - started < 2
        assert state.lane_marker()
    finally:
        server.shutdown()
        server.server_close()


def test_reconciliation_rejects_changed_backend(state, model):
    from parse_svc.vision_ocr import reconcile_idle

    state.begin_dispatch(
        "test",
        1,
        {
            "endpoint": "http://127.0.0.2/v1",
            "model": model.model,
            "model_revision": model.revision,
        },
    )
    with pytest.raises(LaneUncertainError, match="identity_mismatch"):
        reconcile_idle(state, model)
    assert state.lane_marker()
