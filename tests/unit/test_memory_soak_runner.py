"""The operational runner must stop safely and publish no private inputs."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "memory_soak_runner", Path(__file__).parents[2] / "scripts/measure_memory_soak.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("scenario", ["complete", "memory_stop", "execute_failure"])
def test_bounded_workload_cleanup_and_redaction(monkeypatch, tmp_path, scenario):
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--compose-file",
            "private-compose",
            "--env-file",
            "private-env",
            "--base-url",
            "http://private-endpoint",
            "--scrape-url",
            "https://private-url",
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    deletes = []
    sample = {
        "used": 90 if scenario == "memory_stop" else 20,
        "limit": "100",
        "rss": 20,
        "anon": 15,
        "file": 5,
        "oom": 0,
        "oom_kill": 0,
        "sessions": 0,
        "healthy": True,
        "pids_current": 10,
        "pids_limit": "256",
        "zombies": 0,
    }

    def command(parts, **kwargs):
        if parts[:2] == ["docker", "inspect"]:
            return json.dumps(
                [{"State": {"StartedAt": "private-time"}, "RestartCount": 0}]
            )
        if "printenv" in parts:
            return "private-key"
        if "ps" in parts:
            return "private-container"
        return json.dumps(sample)

    class Response:
        def __init__(self, data):
            self.data = json.dumps(data).encode()

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, **kwargs):
        if request.full_url.endswith("/health"):
            return Response({"runtime": {"revision": "test-revision", "model": "test"}})
        if request.get_method() == "DELETE":
            deletes.append(request.full_url)
        if request.full_url.endswith("/execute") and scenario == "execute_failure":
            raise RuntimeError("private failure details")
        return Response({"success": True, "id": "private-session"})

    monkeypatch.setattr(runner.subprocess, "check_output", command)
    monkeypatch.setattr(runner.urllib.request, "urlopen", urlopen)
    result = runner.main()
    data = json.loads(output.read_text())
    assert "private" not in output.read_text()
    if scenario == "complete":
        assert result == 0
        assert data["blocks_completed"] == 6
        assert data["browser_cycles"] == data["scrapes"] == 60
        assert len(deletes) == 60
        assert len([s for s in data["samples"] if s["phase"] == "idle"]) == 6
    else:
        assert result == 1
        assert data["browser_cycles"] == data["scrapes"] == 0
        assert len(deletes) == (1 if scenario == "execute_failure" else 0)
