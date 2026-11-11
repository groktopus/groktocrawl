"""Actual child lifetime, parent crash and serialization of CPU OCR."""

import ast
import json
import os
import subprocess
import sys
import time

import pytest

PARENT = """
import sys
from pathlib import Path
from PIL import Image
from parse_svc.pagewise import tesseract_page
print(tesseract_page(Image.new('RGB',(10,10)),4,Path(sys.argv[1])),flush=True)
"""
FAKE_TESSERACT = """
import os,sys,time,json
from pathlib import Path
root=Path(os.environ['OCR_TEST_ROOT'])
pid=os.getpid()
with (root/'events').open('a') as f: f.write(json.dumps(['start',pid,time.monotonic()])+'\\n')
(root/str(pid)).touch()
sys.stdin.buffer.read()
time.sleep(float(os.environ.get('OCR_TEST_SLEEP','0.5')))
with (root/'events').open('a') as f: f.write(json.dumps(['end',pid,time.monotonic()])+'\\n')
print('block_num\\tpar_num\\tline_num\\tconf\\ttext\\n1\\t1\\t1\\t95\\tHello\\n1\\t1\\t1\\t85\\tworld')
"""


@pytest.fixture
def cpu(tmp_path):
    executable = tmp_path / "tesseract"
    executable.write_text("#!" + sys.executable + "\n" + FAKE_TESSERACT)
    executable.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "OCR_TEST_ROOT": str(tmp_path),
    }

    def start():
        return subprocess.Popen(
            [sys.executable, "-c", PARENT, str(tmp_path / "ocr.lock")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    return tmp_path, env, start


def wait_started(root):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (root / "events").exists():
            return json.loads((root / "events").read_text().splitlines()[0])[1]
        time.sleep(0.02)
    pytest.fail("Tesseract never started")


def test_cpu_actual_children_serialized(cpu):
    root, _env, start = cpu
    one, two = start(), start()
    for proc in (one, two):
        output, error = proc.communicate(timeout=10)
        assert proc.returncode == 0, error
        assert "Hello world" in output
        assert ast.literal_eval(output)[1] == pytest.approx(0.9)
    events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
    assert [e[0] for e in events] == ["start", "end", "start", "end"]


def test_killed_parent_does_not_release_actual_child_lane(cpu):
    root, env, start = cpu
    env["OCR_TEST_SLEEP"] = "1"
    first = start()
    child = wait_started(root)
    first.kill()
    first.wait(timeout=3)
    # Child remains running after the request process is gone.
    os.kill(child, 0)
    second = start()
    output, error = second.communicate(timeout=10)
    assert second.returncode == 0, error
    events = [json.loads(line) for line in (root / "events").read_text().splitlines()]
    assert [e[0] for e in events] == ["start", "end", "start", "end"]
    assert events[0][1] == child
    assert "Hello world" in output


def test_supervisor_kills_timed_out_child_then_releases_lane(cpu):
    root, env, start = cpu
    env["OCR_TEST_SLEEP"] = "10"
    proc = start()
    child = wait_started(root)
    proc.communicate(timeout=8)
    assert proc.returncode != 0
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
    env["OCR_TEST_SLEEP"] = "0"
    successor = start()
    output, error = successor.communicate(timeout=6)
    assert successor.returncode == 0, error
    assert "Hello world" in output
