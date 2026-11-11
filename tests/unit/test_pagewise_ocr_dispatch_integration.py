"""Real HTTP and independent-process evidence for the shared OCR admission lane."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from parse_svc.checkpoints import Checkpoints

WORKER = """
import sys,time
from pathlib import Path
from PIL import Image
from parse_svc.checkpoints import Checkpoints,LaneUncertainError
from parse_svc.vision_ocr import OcrSettings,transcribe
store=Checkpoints(Path(sys.argv[1]),10**7,604800)
execution=sys.argv[2]
store.admit(execution,'scope',execution.encode(),'policy',1)
(Path(sys.argv[1])/(execution+'.ready')).touch()
try:
 result=transcribe(Image.new('RGB',(20,20)),OcrSettings.load(),store,
                   execution,1,time.monotonic()+15,{'page_number':1})
 print(result['status'],flush=True)
except LaneUncertainError:
 print('unknown',flush=True)
"""


@pytest.fixture
def dispatch_server():
    class State:
        def __init__(self):
            self.changed = threading.Condition()
            self.release = threading.Event()
            self.received = 0
            self.active = 0
            self.maximum = 0

        def wait_received(self, count):
            with self.changed:
                assert self.changed.wait_for(lambda: self.received >= count, timeout=8)

    state = State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert len(payload["messages"]) == 1
            with state.changed:
                state.received += 1
                state.active += 1
                state.maximum = max(state.maximum, state.active)
                state.changed.notify_all()
            state.release.wait(timeout=12)
            body = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "real HTTP words"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode()
            with state.changed:
                state.active -= 1
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, server.server_port
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def workers(tmp_path, dispatch_server):
    _, port = dispatch_server
    store = Checkpoints(tmp_path, 10**7, 604800)
    processes = []
    env = {
        **os.environ,
        "PARSE_STATE_DIR": str(tmp_path),
        "PARSE_OCR_BACKEND": "gemma",
        "PARSE_OCR_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "PARSE_OCR_MODEL": "test-model",
        "PARSE_OCR_MODEL_REVISION": "test-revision",
        "PARSE_PAGE_TIMEOUT_SECONDS": "10",
    }
    env.pop("PARSE_OCR_API_KEY_FILE", None)

    def start(execution):
        process = subprocess.Popen(
            [sys.executable, "-c", WORKER, str(tmp_path), execution],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        processes.append(process)
        return process

    try:
        yield start, store
    finally:
        # A killed admission owner may leave its HTTP child alive. Clean the whole
        # test-owned group only after checking that the unknown marker blocks peers.
        for process in processes:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_real_http_dispatch_is_serialized_across_processes(dispatch_server, workers):
    state, _ = dispatch_server
    start, store = workers
    first = start("first")
    state.wait_received(1)
    second = start("second")
    # Keep the first model call in flight until the second worker is ready.
    ready = store.root / "second.ready"
    deadline = time.monotonic() + 8
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    time.sleep(0.1)
    assert state.received == 1
    state.release.set()
    for process in (first, second):
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "complete"
    assert state.received == 2
    assert state.maximum == 1
    assert store.lane_marker() is None
    assert store.page("first", 1)["text"] == "real HTTP words"
    assert store.page("second", 1)["text"] == "real HTTP words"


def test_killed_owner_with_live_http_call_blocks_next_worker(dispatch_server, workers):
    state, _ = dispatch_server
    start, store = workers
    owner = start("killed")
    state.wait_received(1)  # Crash after dispatch reached the actual HTTP server.
    owner.kill()  # Deliberately leave possible HTTP subprocess alive for this check.
    owner.wait(timeout=5)
    assert state.active == 1
    successor = start("successor")
    stdout, stderr = successor.communicate(timeout=8)
    assert successor.returncode == 0, stderr
    assert stdout.strip() == "unknown"
    assert state.received == 1
    assert state.maximum == 1
    assert store.lane_marker()["execution"] == "killed"
    assert store.attempts("killed", 1)[0]["outcome"] == "unknown"
    assert store.attempts("successor", 1) == []
    with store.connect() as db:
        assert (
            db.execute("SELECT source FROM executions WHERE id='killed'").fetchone()[0]
            == b"killed"
        )
    state.release.set()
