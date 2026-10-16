#!/usr/bin/env python3
"""Prefetch a small, source-pinned set of public Docker Hub CI fixture images.

This helper never changes daemon configuration. It renders Compose config in
memory, skips buildable services, and pulls remaining images before stack start.
Pinned Docker Hub fixture refs are fetched by digest from mirror.gcr.io and
verified before their existing Compose tags are updated.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

PIN_EVIDENCE_RECEIPT_SHA256 = (
    "a4c12eb54885cda82b99adde0415a49d9042159ca57fd69909be48bc7d5bf5d3"
)
PIN_EVIDENCE_OBSERVED_AT = "2026-10-09T21:53:32.839839+00:00"
PIN_GROUPS: dict[str, dict[str, str]] = {
    "runtime": {
        "mcuadros/ofelia:latest": "sha256:efcbe2c5cf658a25de6443c1462d653f9cc03791d642e01fc6c638a00f97e492",
        "qdrant/qdrant:v1.18.2": "sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c",
        "valkey/valkey:8-alpine": "sha256:081c2f5cb575efc901aa80ff9cdbd1ec6a301682fd35e1ebb4b0990a4a4a8507",
    },
    "storage": {
        "pgvector/pgvector:pg17": "sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d",
        "postgres:17.11-bookworm": "sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826",
        "valkey/valkey:8-alpine": "sha256:081c2f5cb575efc901aa80ff9cdbd1ec6a301682fd35e1ebb4b0990a4a4a8507",
    },
    # The scale-out workflow starts its Actions Valkey service before steps.
    # This group intentionally pulls only these two fixture images and never
    # restarts Docker or inspects/touches that already-running service.
    "topology": {
        "python:3.12-alpine": "sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4",
        "haproxy:3.0-alpine": "sha256:56b887da77428b7a6621e59e480cdbd330cc805c22d3cedb66ceea76ffdea2c6",
    },
}

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PrefetchError(RuntimeError):
    """A bounded, sanitized prefetch failure."""


def _run(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout: int = 600,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            list(command),
            check=not allow_failure,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrefetchError("Docker image prefetch command failed") from exc
    if not allow_failure and result.returncode != 0:
        raise PrefetchError("Docker image prefetch command failed")
    return result


def _compose_command(
    *,
    compose_files: Sequence[str],
    profiles: Sequence[str],
) -> list[str]:
    command = ["docker", "compose"]
    for filename in compose_files:
        command.extend(["-f", filename])
    for profile in profiles:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])
    return command


def _active_profiles(
    profiles: Sequence[str], environment: Mapping[str, str]
) -> set[str]:
    selected = set(profiles)
    selected.update(
        value.strip()
        for value in environment.get("COMPOSE_PROFILES", "").split(",")
        if value.strip()
    )
    return selected


def _compose_images(
    config_text: str,
    *,
    profiles: Sequence[str],
    environment: Mapping[str, str],
) -> tuple[set[str], int]:
    try:
        config = json.loads(config_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PrefetchError("Compose image inventory was malformed") from exc
    if type(config) is not dict or type(config.get("services")) is not dict:
        raise PrefetchError("Compose image inventory was malformed")

    active_profiles = _active_profiles(profiles, environment)
    images: set[str] = set()
    buildable_count = 0
    for service in config["services"].values():
        if type(service) is not dict:
            raise PrefetchError("Compose service inventory was malformed")
        service_profiles = service.get("profiles", [])
        if type(service_profiles) is not list or any(
            type(p) is not str for p in service_profiles
        ):
            raise PrefetchError("Compose service inventory was malformed")
        if service_profiles and not active_profiles.intersection(service_profiles):
            continue
        if service.get("build") is not None:
            buildable_count += 1
            continue
        image = service.get("image")
        if image is None:
            continue
        if type(image) is not str or not image or "\n" in image or "\r" in image:
            raise PrefetchError("Compose image reference was malformed")
        images.add(image)
    return images, buildable_count


def _canonical_hub_repository(image: str) -> str | None:
    """Return a mirror.gcr.io repository path for an unqualified Docker Hub ref."""
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return None
    name = image.split("@", 1)[0]
    slash = name.rfind("/")
    colon = name.rfind(":")
    repository = name[:colon] if colon > slash else name
    if not repository or repository == "scratch":
        return None
    if "/" not in repository:
        repository = f"library/{repository}"
    return repository


def _inspect_image(
    reference: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    allow_missing: bool = False,
) -> tuple[str | None, list[str]]:
    id_result = _run(
        ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
        runner=runner,
        timeout=30,
        allow_failure=allow_missing,
    )
    if id_result.returncode != 0:
        return None, []
    digests_result = _run(
        ["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"],
        runner=runner,
        timeout=30,
    )
    image_id = id_result.stdout.strip()
    try:
        repo_digests = json.loads(digests_result.stdout or "null") or []
    except (json.JSONDecodeError, TypeError) as exc:
        raise PrefetchError("Docker image inspection was malformed") from exc
    if (
        type(image_id) is not str
        or len(image_id) > 71
        or not _IMAGE_ID_RE.fullmatch(image_id)
    ):
        raise PrefetchError("Docker image identifier was malformed")
    if type(repo_digests) is not list or any(
        type(item) is not str for item in repo_digests
    ):
        raise PrefetchError("Docker repository digest inspection was malformed")
    return image_id, repo_digests


def _pull_pinned(
    canonical_ref: str,
    digest: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, str]:
    if not _SHA256_RE.fullmatch(digest):
        raise PrefetchError("Pinned Docker image digest was malformed")
    if not any(pins.get(canonical_ref) == digest for pins in PIN_GROUPS.values()):
        raise PrefetchError("Image reference was not in the verified pin set")
    repository = _canonical_hub_repository(canonical_ref)
    if repository is None:
        raise PrefetchError("Pinned image was not a canonical Docker Hub reference")
    mirror_ref = f"mirror.gcr.io/{repository}@{digest}"
    _run(["docker", "pull", "--quiet", mirror_ref], runner=runner)
    image_id, repo_digests = _inspect_image(mirror_ref, runner=runner)
    if image_id is None or mirror_ref not in repo_digests:
        raise PrefetchError("Pinned mirror digest verification failed")

    previous_id, _ = _inspect_image(canonical_ref, runner=runner, allow_missing=True)
    _run(["docker", "tag", mirror_ref, canonical_ref], runner=runner, timeout=30)
    tagged_id, _ = _inspect_image(canonical_ref, runner=runner)
    if tagged_id != image_id:
        if previous_id is None:
            _run(
                ["docker", "image", "rm", canonical_ref],
                runner=runner,
                timeout=30,
                allow_failure=True,
            )
        else:
            _run(
                ["docker", "tag", previous_id, canonical_ref], runner=runner, timeout=30
            )
        raise PrefetchError("Pinned image ID changed while tagging canonical reference")
    return {
        "canonical_ref": canonical_ref,
        "digest": digest,
        "mirror_ref": mirror_ref,
        "image_id": image_id,
    }


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp_path.unlink(missing_ok=True)


def prefetch_images(
    group: str,
    *,
    compose_files: Sequence[str] = (),
    profiles: Sequence[str] = (),
    receipt_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    if group not in PIN_GROUPS:
        raise PrefetchError("Unknown CI image pin group")
    env = os.environ if environment is None else environment
    config_result = _run(
        _compose_command(compose_files=compose_files, profiles=profiles),
        runner=runner,
        timeout=60,
    )
    images, buildable_count = _compose_images(
        config_result.stdout,
        profiles=profiles,
        environment=env,
    )
    pins = PIN_GROUPS[group]
    missing_pins = sorted(set(pins).difference(images))
    if missing_pins:
        raise PrefetchError("Expected pinned CI fixture image was absent from Compose")

    if group == "topology":
        # Keep the precreated Actions Valkey service untouched; only pull the
        # two overlay fixture images needed by the scale-out Compose profile.
        selected_images = set(pins)
    else:
        selected_images = images

    pinned_receipts = []
    other_remote_count = 0
    for image in sorted(selected_images):
        digest = pins.get(image)
        if digest is not None:
            pinned_receipts.append(_pull_pinned(image, digest, runner=runner))
        else:
            _run(["docker", "pull", "--quiet", image], runner=runner)
            other_remote_count += 1

    result: dict[str, object] = {
        "schema_version": 1,
        "group": group,
        "observed_at": now().astimezone(UTC).isoformat(),
        "pin_evidence_observed_at": PIN_EVIDENCE_OBSERVED_AT,
        "pin_evidence_receipt_sha256": PIN_EVIDENCE_RECEIPT_SHA256,
        "compose_image_count": len(images),
        "buildable_service_count_skipped": buildable_count,
        "other_remote_images_pulled": other_remote_count,
        "pinned_images": pinned_receipts,
    }
    if receipt_path is not None:
        _write_receipt(receipt_path, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=sorted(PIN_GROUPS))
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if not (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
    ):
        print(
            "CI public image prefetch is restricted to GitHub-hosted Linux.",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = prefetch_images(
            args.group,
            compose_files=args.compose_file,
            profiles=args.profile,
            receipt_path=args.receipt,
        )
    except PrefetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "CI public image prefetch complete: "
        f"{len(receipt['pinned_images'])} digest-verified fixture images, "
        f"{receipt['other_remote_images_pulled']} other remote images."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
