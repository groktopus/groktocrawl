"""Compose provenance uses running-container IDs, not mutable tag lookup."""

import json
import subprocess

import pytest

from scripts import twin_provenance as provenance

IMAGE = "sha256:" + "a" * 64


def test_running_image_survives_empty_compose_images_and_missing_image_tag(monkeypatch):
    commands = []

    def docker(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "compose", "ps"]:
            return "container-1\n"
        if command[:2] == ["docker", "inspect"]:
            return "true " + IMAGE + "\n"
        if command[:3] == ["docker", "image", "inspect"]:
            # Untagged/raced image metadata may be gone; container.Image is still exact.
            raise subprocess.CalledProcessError(1, command)
        raise AssertionError(command)

    monkeypatch.setattr(provenance.subprocess, "check_output", docker)
    monkeypatch.setattr(provenance, "_git", lambda *_args: "fixture-source")
    monkeypatch.setattr(provenance, "REQUIRED_COMPOSE_IMAGES", ("scraper-svc",))
    (record,) = provenance._image_records("compose")
    assert record["id"] == IMAGE and record["reference"] == IMAGE
    assert record["repo_digest"] is None
    assert not any(c[:3] == ["docker", "compose", "images"] for c in commands)
    assert not any(
        c[-1] == "scraper-svc" and c[:3] == ["docker", "image", "inspect"]
        for c in commands
    )


@pytest.mark.parametrize(
    "receipt",
    [
        "false " + IMAGE,
        "true sha256:short",
        "true " + IMAGE + "\ntrue sha256:" + "b" * 64,
    ],
)
def test_stopped_malformed_or_mixed_replicas_remain_unavailable(monkeypatch, receipt):
    def docker(command, **kwargs):
        if command[:3] == ["docker", "compose", "ps"]:
            return "container-1\ncontainer-2" if "\n" in receipt else "container-1"
        if command[:2] == ["docker", "inspect"]:
            return receipt
        raise AssertionError("No fallback to a mutable service tag is allowed")

    monkeypatch.setattr(provenance.subprocess, "check_output", docker)
    monkeypatch.setattr(provenance, "_git", lambda *_args: "fixture-source")
    monkeypatch.setattr(provenance, "REQUIRED_COMPOSE_IMAGES", ("scraper-svc",))
    (record,) = provenance._image_records("compose")
    assert record["id"] == "unavailable"


def test_exact_running_image_keeps_repository_digest_evidence(monkeypatch):
    digest = "fixture/repository@sha256:" + "c" * 64

    def docker(command, **kwargs):
        if command[:3] == ["docker", "compose", "ps"]:
            return "container-1"
        if command[:2] == ["docker", "inspect"]:
            return "true " + IMAGE
        if command[:3] == ["docker", "image", "inspect"]:
            assert command[-1] == IMAGE
            return json.dumps([digest])
        raise AssertionError(command)

    monkeypatch.setattr(provenance.subprocess, "check_output", docker)
    monkeypatch.setattr(provenance, "_git", lambda *_args: "fixture-source")
    monkeypatch.setattr(provenance, "REQUIRED_COMPOSE_IMAGES", ("qdrant",))
    (record,) = provenance._image_records("compose")
    assert record["id"] == IMAGE and record["repo_digest"] == digest


def test_no_running_container_remains_unavailable_without_tag_lookup(monkeypatch):
    def docker(command, **kwargs):
        assert command[:3] == ["docker", "compose", "ps"]
        assert kwargs["timeout"] == 15
        return ""

    monkeypatch.setattr(provenance.subprocess, "check_output", docker)
    monkeypatch.setattr(provenance, "_git", lambda *_args: "fixture-source")
    monkeypatch.setattr(provenance, "REQUIRED_COMPOSE_IMAGES", ("scraper-svc",))
    (record,) = provenance._image_records("compose")
    assert record["id"] == "unavailable"
