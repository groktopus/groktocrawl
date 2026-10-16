from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import configure_ci_dockerhub_mirror as mirror


def test_hosted_linux_only_and_config_preservation(tmp_path: Path) -> None:
    hosted = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
    }
    assert mirror.hosted_linux_runner(hosted)
    for changed in (
        {**hosted, "GITHUB_ACTIONS": "false"},
        {**hosted, "RUNNER_ENVIRONMENT": "self-hosted"},
        {**hosted, "RUNNER_OS": "macOS"},
    ):
        assert not mirror.hosted_linux_runner(changed)

    path = tmp_path / "docker" / "daemon.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"debug": True, "insecure-registries": ["registry.example:5000"]})
    )
    restarts: list[bool] = []
    assert mirror.configure_daemon(
        path,
        restart=lambda: restarts.append(True),
        has_running_containers=lambda: False,
    )
    config = json.loads(path.read_text())
    assert config["debug"] is True
    assert config["insecure-registries"] == ["registry.example:5000"]
    assert config["registry-mirrors"] == [mirror.MIRROR]
    assert restarts == [True]
    assert not mirror.configure_daemon(
        path,
        restart=lambda: restarts.append(True),
        has_running_containers=lambda: False,
    )
    assert restarts == [True]


def test_non_hosted_runner_does_not_touch_daemon_or_restart(tmp_path: Path) -> None:
    path = tmp_path / "daemon.json"
    path.write_text('{"debug":true}\n')
    original = path.read_bytes()
    restarts: list[bool] = []
    assert not mirror.configure_for_runner(
        {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "self-hosted",
            "RUNNER_OS": "Linux",
        },
        path,
        restart=lambda: restarts.append(True),
        has_running_containers=lambda: False,
    )
    assert path.read_bytes() == original
    assert restarts == []


@pytest.mark.parametrize("raw", [b"[]", b'{"registry-mirrors":"bad"}', b"{"])
def test_invalid_daemon_config_fails_without_restart(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "daemon.json"
    path.write_bytes(raw)
    restarts: list[bool] = []
    with pytest.raises((ValueError, json.JSONDecodeError)):
        mirror.configure_daemon(
            path,
            restart=lambda: restarts.append(True),
            has_running_containers=lambda: False,
        )
    assert path.read_bytes() == raw
    assert restarts == []


def test_running_container_refuses_change_before_write_or_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "daemon.json"
    original = b'{"debug":true}\n'
    path.write_bytes(original)
    restarts: list[bool] = []
    with pytest.raises(RuntimeError, match="containers are running"):
        mirror.configure_daemon(
            path,
            restart=lambda: restarts.append(True),
            has_running_containers=lambda: True,
        )
    assert path.read_bytes() == original
    assert restarts == []


def test_docker_runtime_diagnostics_are_bounded_and_allowlisted() -> None:
    outputs = {
        "{{.Server.Version}}": "28.0.4\n",
        "{{.Driver}}": "overlay2\n",
        "{{json .DriverStatus}}": '[["Backing Filesystem","extfs"],["driver-type","io.containerd.snapshotter.v1"]]\n',
        "{{json .RegistryConfig.Mirrors}}": '["https://user:secret@mirror.gcr.io/cache?token=private","https://mirror.gcr.io"]\n',
    }
    commands: list[list[str]] = []
    options: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        options.append(kwargs)
        return subprocess.CompletedProcess(command, 0, outputs[command[-1]], "")

    observed = mirror.docker_runtime_info(run=fake_run)
    assert observed == {
        "server_version": "28.0.4",
        "storage_driver": "overlay2",
        "containerd_snapshotter": "io.containerd.snapshotter.v1",
        "public_mirror_configured": True,
    }
    assert all("--format" in command for command in commands)
    assert all(command[-1] in outputs for command in commands)
    assert all(
        option == {"check": True, "capture_output": True, "text": True, "timeout": 5}
        for option in options
    )
    assert "secret" not in json.dumps(observed)
    assert "token" not in json.dumps(observed)
    assert set(observed) == {
        "server_version",
        "storage_driver",
        "containerd_snapshotter",
        "public_mirror_configured",
    }


@pytest.mark.parametrize(
    "mirror_value",
    [
        "https://user@mirror.gcr.io",
        "https://mirror.gcr.io:444",
        "https://mirror.gcr.io/private",
        "https://mirror.gcr.io/?token=secret",
        "https://mirror.gcr.io/#fragment",
        "http://mirror.gcr.io",
    ],
)
def test_runtime_diagnostics_reject_noncanonical_mirror(mirror_value: str) -> None:
    outputs = {
        "{{.Server.Version}}": "28.0.4\n",
        "{{.Driver}}": "overlay2\n",
        "{{json .DriverStatus}}": "null\n",
        "{{json .RegistryConfig.Mirrors}}": json.dumps([mirror_value]),
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, outputs[command[-1]], "")

    assert mirror.docker_runtime_info(run=fake_run)["public_mirror_configured"] is False


def test_runtime_workflow_mirror_precedes_compose_and_keeps_scope() -> None:
    runtime = Path(".github/workflows/runtime.yml").read_text()
    integration_match = re.search(
        r"(?ms)^  integration-tests:\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", runtime
    )
    assert integration_match is not None
    integration = integration_match.group(1)
    assert integration.index(
        "name: Configure hosted-runner Docker Hub cache"
    ) < integration.index("name: Prefetch verified public image fixtures")
    storage_match = re.search(
        r"(?ms)^  research-storage:\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", runtime
    )
    assert storage_match is not None
    storage = storage_match.group(1)
    assert storage.index(
        "name: Configure hosted-runner Docker Hub cache"
    ) < storage.index("docker compose")


def test_topology_precreated_service_does_not_restart_docker() -> None:
    topology = Path(".github/workflows/scraper-scaleout.yml").read_text()
    match = re.search(r"(?ms)^  topology:\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", topology)
    assert match is not None
    job = match.group(1)
    assert "services:" in job
    assert "Configure hosted-runner Docker Hub cache" not in job
