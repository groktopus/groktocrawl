from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import ci_public_image_prefetch as prefetch


class FakeDocker:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.commands: list[list[str]] = []
        self.images: dict[str, tuple[str, list[str]]] = {}
        self.mismatch_after_tag: str | None = None
        self.bad_repo_digest: str | None = None

    def __call__(
        self, command: Sequence[str], **kwargs
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        self.commands.append(argv)
        if argv[:3] == ["docker", "compose", "config"] or argv[-3:] == [
            "config",
            "--format",
            "json",
        ]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.config), "")
        if argv[:3] == ["docker", "pull", "--quiet"]:
            ref = argv[3]
            if ref.startswith("mirror.gcr.io/"):
                image_id = "sha256:" + ("a" * 64)
                repo_digests = [ref]
                if self.bad_repo_digest == ref:
                    repo_digests = [ref.rsplit("@", 1)[0] + "@sha256:" + ("b" * 64)]
                self.images[ref] = (image_id, repo_digests)
            else:
                image_id = "sha256:" + ("c" * 64)
                self.images[ref] = (image_id, [ref + "@sha256:" + ("d" * 64)])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["docker", "image", "inspect"]:
            ref = argv[3]
            if ref not in self.images:
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            image_id, digests = self.images[ref]
            if argv[-1] == "{{.Id}}":
                return subprocess.CompletedProcess(argv, 0, image_id + "\n", "")
            if argv[-1] == "{{json .RepoDigests}}":
                return subprocess.CompletedProcess(argv, 0, json.dumps(digests), "")
        if argv[:2] == ["docker", "tag"]:
            source, target = argv[2], argv[3]
            source_image = self.images[source]
            if self.mismatch_after_tag == target:
                source_image = ("sha256:" + ("e" * 64), source_image[1])
            self.images[target] = source_image
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["docker", "image", "rm"]:
            self.images.pop(argv[3], None)
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command shape: {argv[:4]}")


def compose_config(
    images: Sequence[str],
    *,
    buildable: Sequence[str] = (),
    inactive: Sequence[str] = (),
) -> dict[str, object]:
    services: dict[str, object] = {}
    for index, image in enumerate(images):
        services[f"service-{index}"] = {"image": image}
    for index, image in enumerate(buildable):
        services[f"buildable-{index}"] = {"image": image, "build": {"context": "."}}
    for index, image in enumerate(inactive):
        services[f"inactive-{index}"] = {"image": image, "profiles": ["not-selected"]}
    return {"services": services}


def test_runtime_prefetch_verifies_pins_and_preserves_other_remote_images(
    tmp_path: Path,
) -> None:
    pins = prefetch.PIN_GROUPS["runtime"]
    images = [*pins, "ghcr.io/example/public-fixture:v1"]
    runner = FakeDocker(compose_config(images, buildable=["example/app:local"]))
    receipt_path = tmp_path / "prefetch.json"

    receipt = prefetch.prefetch_images(
        "runtime",
        profiles=["indexing", "fixture"],
        receipt_path=receipt_path,
        runner=runner,
        now=lambda: datetime(2026, 10, 9, tzinfo=UTC),
    )

    assert {item["canonical_ref"] for item in receipt["pinned_images"]} == set(pins)
    assert receipt["other_remote_images_pulled"] == 1
    assert receipt["buildable_service_count_skipped"] == 1
    assert json.loads(receipt_path.read_text()) == receipt
    commands = [" ".join(command) for command in runner.commands]
    for image, digest in pins.items():
        repo = prefetch._canonical_hub_repository(image)
        mirror_ref = f"mirror.gcr.io/{repo}@{digest}"
        assert any(
            command == f"docker pull --quiet {mirror_ref}" for command in commands
        )
        assert any(
            command == f"docker tag {mirror_ref} {image}" for command in commands
        )
        assert not any(
            command == f"docker pull --quiet {image}" for command in commands
        )
        assert runner.images[image][0] == runner.images[mirror_ref][0]
    assert "docker pull --quiet ghcr.io/example/public-fixture:v1" in commands
    assert not any("example/app:local" in command for command in commands)


def test_digest_mismatch_fails_before_canonical_tag_mutation() -> None:
    image = next(iter(prefetch.PIN_GROUPS["runtime"]))
    digest = prefetch.PIN_GROUPS["runtime"][image]
    mirror_ref = f"mirror.gcr.io/{prefetch._canonical_hub_repository(image)}@{digest}"
    runner = FakeDocker(compose_config([*prefetch.PIN_GROUPS["runtime"]]))
    runner.bad_repo_digest = mirror_ref

    with pytest.raises(prefetch.PrefetchError, match="digest verification"):
        prefetch.prefetch_images("runtime", runner=runner)

    assert not any(command[:2] == ["docker", "tag"] for command in runner.commands)
    assert image not in runner.images


def test_image_id_mismatch_rolls_back_canonical_tag() -> None:
    image = next(iter(prefetch.PIN_GROUPS["runtime"]))
    runner = FakeDocker(compose_config([*prefetch.PIN_GROUPS["runtime"]]))
    runner.mismatch_after_tag = image

    with pytest.raises(prefetch.PrefetchError, match="image ID changed"):
        prefetch.prefetch_images("runtime", runner=runner)

    assert image not in runner.images
    assert any(command[:3] == ["docker", "image", "rm"] for command in runner.commands)


def test_missing_expected_pin_fails_before_any_image_pull() -> None:
    runner = FakeDocker(
        compose_config(["qdrant/qdrant:v1.18.2", "valkey/valkey:8-alpine"])
    )

    with pytest.raises(prefetch.PrefetchError, match="absent from Compose"):
        prefetch.prefetch_images("runtime", runner=runner)

    assert not any(command[:2] == ["docker", "pull"] for command in runner.commands)


def test_unknown_reference_cannot_be_pinned_or_pulled_from_mirror() -> None:
    runner = FakeDocker(
        compose_config(
            [
                "qdrant/qdrant:v1.18.2",
                "mcuadros/ofelia:latest",
                "valkey/valkey:8-alpine",
            ]
        )
    )

    with pytest.raises(prefetch.PrefetchError, match="not in the verified pin set"):
        prefetch._pull_pinned(
            "unreviewed/image:tag",
            "sha256:" + ("f" * 64),
            runner=runner,
        )

    assert not any(command[:2] == ["docker", "pull"] for command in runner.commands)


def test_topology_prefetch_only_seeds_two_fixture_images_and_leaves_valkey_alone() -> (
    None
):
    runner = FakeDocker(
        compose_config(
            [
                "python:3.12-alpine",
                "haproxy:3.0-alpine",
                "valkey/valkey:8-alpine",
                "ghcr.io/example/app:fixture",
            ]
        )
    )

    receipt = prefetch.prefetch_images("topology", runner=runner)

    assert {item["canonical_ref"] for item in receipt["pinned_images"]} == set(
        prefetch.PIN_GROUPS["topology"]
    )
    assert receipt["other_remote_images_pulled"] == 0
    assert not any("valkey" in " ".join(command) for command in runner.commands)
    assert not any(
        "ghcr.io/example/app" in " ".join(command) for command in runner.commands
    )
    assert not any(
        command[:2] in (["systemctl", "restart"], ["docker", "stop"])
        for command in runner.commands
    )


def test_compose_profile_filter_skips_inactive_and_buildable_services() -> None:
    config = compose_config(
        ["qdrant/qdrant:v1.18.2", "mcuadros/ofelia:latest", "valkey/valkey:8-alpine"],
        buildable=["ghcr.io/example/built:latest"],
        inactive=["postgres:17.11-bookworm"],
    )
    images, buildable_count = prefetch._compose_images(
        json.dumps(config), profiles=["indexing"], environment={}
    )
    assert images == {
        "qdrant/qdrant:v1.18.2",
        "mcuadros/ofelia:latest",
        "valkey/valkey:8-alpine",
    }
    assert buildable_count == 1


def test_workflows_use_prefetch_before_pull_or_start_and_preserve_compose_refs() -> (
    None
):
    runtime = Path(".github/workflows/runtime.yml").read_text()
    assert (
        "docker compose --profile indexing --profile fixture pull --ignore-buildable"
        not in runtime
    )
    assert "scripts/ci_public_image_prefetch.py" in runtime
    assert "--group runtime" in runtime
    assert "--group storage --profile storage" in runtime
    assert "up -d --no-build --pull never" in runtime
    assert "docker compose up -d --wait --pull never research-postgres" in runtime

    topology = Path(".github/workflows/scraper-scaleout.yml").read_text()
    assert topology.index(
        "name: Prefetch pinned scale-out fixture images"
    ) < topology.index("name: Validate gateway configuration and 1/2/4 replicas")
    assert "--group topology" in topology
    assert "services:" in topology and "image: valkey/valkey:8-alpine" in topology

    benchmark = Path("benchmarks/scraper_scaleout.py").read_text()
    assert '"--pull",\n                "missing"' in benchmark


def test_cli_refuses_to_run_outside_hosted_linux(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RUNNER_OS", raising=False)

    assert prefetch.main(["--group", "runtime"]) == 2
