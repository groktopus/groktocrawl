"""Release promotion must verify every source image before writing any tag."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from scripts import promote_release_images as promotion

REVISION = "a" * 40
TAG = "v1.2.3"


def document(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class GitHub:
    def __init__(self):
        self.revision = REVISION
        self.run_revision = REVISION
        self.run_status = "completed"
        self.jobs = list(promotion.IMAGES)
        self.annotated = False
        self.prerelease = False

    def __call__(self, path, *, paginate=False):
        if "/releases/tags/" in path:
            return {"tag_name": TAG, "draft": False, "prerelease": self.prerelease}
        if "/git/ref/tags/" in path:
            return {
                "object": {
                    "type": "tag" if self.annotated else "commit",
                    "sha": self.revision,
                }
            }
        if "/git/tags/" in path:
            return {"object": {"type": "commit", "sha": self.revision}}
        if "/jobs?" in path:
            assert paginate
            return [
                {
                    "jobs": [
                        {"name": name, "status": "completed", "conclusion": "success"}
                        for name in self.jobs
                    ]
                }
            ]
        if "/actions/workflows/docker.yml/runs?" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "head_sha": self.run_revision,
                        "head_branch": "main",
                        "status": self.run_status,
                        "conclusion": "success",
                        "event": "push",
                        "path": ".github/workflows/docker.yml",
                        "repository": {"full_name": promotion.REPOSITORY},
                        "html_url": "https://github.com/groktopus/groktocrawl/actions/runs/123",
                    }
                ]
            }
        raise AssertionError(path)


class FixtureRegistry(promotion.Registry):
    """Exercise real registry digest/size/PUT checks using an in-memory transport."""

    def __init__(
        self,
        package,
        *,
        missing_arch=False,
        revision=REVISION,
        corrupt_readback=False,
        baked_revision=None,
    ):
        self.package = package
        self.token = "anonymous-fixture"
        self.objects = {}
        self.tags = {}
        self.writes = []
        self.corrupt_readback = corrupt_readback
        entries = []
        for arch in ("amd64", "arm64"):
            if missing_arch and arch == "arm64":
                continue
            config = document(
                {
                    "os": "linux",
                    "architecture": arch,
                    "config": {
                        "Labels": {
                            "org.opencontainers.image.revision": revision,
                            "org.opencontainers.image.source": promotion.SOURCE,
                        },
                        "Env": [
                            "GROKTOCRAWL_REVISION=" + (baked_revision or revision),
                            "OTHER=do-not-include-env",
                        ],
                    },
                }
            )
            config_digest = promotion.digest(config)
            self.objects[config_digest] = config
            manifest = document(
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": config_digest, "size": len(config)},
                }
            )
            manifest_digest = promotion.digest(manifest)
            self.objects[manifest_digest] = manifest
            entries.append(
                {
                    "digest": manifest_digest,
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": arch},
                }
            )
        self.index = document(
            {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": entries,
            }
        )
        self.tags["sha-" + REVISION] = self.index

    def _request(self, request):
        parsed = urlsplit(request.full_url)
        if parsed.path == "/token":
            assert "push" in parsed.query
            return b'{"token":"write-fixture"}', {}, 200
        reference = parsed.path.rsplit("/", 1)[-1]
        if request.get_method() == "PUT":
            assert self.token == "write-fixture"
            assert (
                request.get_header("Content-type")
                == "application/vnd.oci.image.index.v1+json"
            )
            self.writes.append((reference, request.data))
            self.tags[reference] = request.data
            return b"", {"Docker-Content-Digest": promotion.digest(request.data)}, 201
        body = self.tags.get(reference, self.objects.get(reference))
        assert body is not None, reference
        if self.corrupt_readback and reference == "stable":
            body = b"{}"
        return body, {"Docker-Content-Digest": promotion.digest(body)}, 200


@pytest.fixture
def github(monkeypatch):
    client = GitHub()
    monkeypatch.setattr(promotion, "gh_api", client)
    monkeypatch.setenv("GITHUB_ACTOR", "release-maintainer")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    return client


def factory(clients, **overrides):
    def create(package):
        options = overrides if package.endswith("protected-flare") else {}
        client = FixtureRegistry(package, **options)
        clients.append(client)
        return client

    return create


@pytest.mark.parametrize(
    "tag",
    ["1.2.3", "v01.2.3", "v1.2", "v1.2.3-rc.1", "v1.2.3+build", "main", "v1.2.3;false"],
)
def test_nonstable_or_malformed_tags_cannot_write(github, tag):
    clients = []
    with pytest.raises(promotion.PromotionError, match="stable"):
        promotion.promote(tag, publish=True, registry_factory=factory(clients))
    assert clients == []


@pytest.mark.parametrize("change", ["sha", "unready", "jobs", "prerelease"])
def test_release_or_publish_gate_failure_precedes_registry_access(github, change):
    if change == "sha":
        github.run_revision = "b" * 40
    elif change == "unready":
        github.run_status = "in_progress"
    elif change == "jobs":
        github.jobs.pop()
    else:
        github.prerelease = True
    clients = []
    with pytest.raises(promotion.PromotionError):
        promotion.promote(TAG, publish=True, registry_factory=factory(clients))
    assert clients == []


@pytest.mark.parametrize("options", [{"missing_arch": True}, {"revision": "b" * 40}])
def test_any_invalid_image_blocks_all_tag_writes(github, options):
    clients = []
    with pytest.raises(promotion.PromotionError):
        promotion.promote(
            TAG, publish=True, registry_factory=factory(clients, **options)
        )
    assert len(clients) == 10  # Last image fails after the first nine validated.
    assert all(
        client.writes == [] and client.token == "anonymous-fixture"
        for client in clients
    )


def test_agent_baked_revision_mismatch_blocks_all_writes(github):
    clients = []

    def create(package):
        client = FixtureRegistry(package, baked_revision="b" * 40)
        clients.append(client)
        return client

    with pytest.raises(promotion.PromotionError, match="baked revision"):
        promotion.promote(TAG, publish=True, registry_factory=create)
    assert all(not client.writes for client in clients)


def test_verification_is_default_and_annotated_tags_resolve(github):
    github.annotated = True
    clients = []
    result = promotion.promote(TAG, registry_factory=factory(clients))
    assert result["revision"] == REVISION
    assert result["published"] is False
    assert len(result["images"]) == 10
    assert all(not client.writes for client in clients)
    assert "do-not-include-env" not in json.dumps(result)


def test_promoted_tags_preserve_original_index_bytes_and_both_architectures(github):
    clients = []
    result = promotion.promote(TAG, publish=True, registry_factory=factory(clients))
    assert result["tags"] == ["stable", "1", "1.2", "1.2.3", TAG]
    assert sum(len(client.writes) for client in clients) == 50
    for client, evidence in zip(clients, result["images"], strict=True):
        assert set(evidence["platforms"]) == {"linux/amd64", "linux/arm64"}
        for tag, body in client.writes:
            assert body == client.index
            assert promotion.digest(client.tags[tag]) == evidence["index_digest"]


def test_registry_readback_mismatch_fails_publication(github):
    clients = []

    def create(package):
        client = FixtureRegistry(package, corrupt_readback=True)
        clients.append(client)
        return client

    with pytest.raises(promotion.PromotionError, match="original index digest"):
        promotion.promote(TAG, publish=True, registry_factory=create)
    assert sum(len(client.writes) for client in clients) == 1


def test_workflow_is_manual_mainline_only_and_uses_job_scoped_package_token():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/promote-release-images.yml").read_text()
    )
    assert set(workflow.get("on", workflow.get(True))) == {"workflow_dispatch"}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["promote"]
    assert job["permissions"] == {
        "contents": "read",
        "actions": "read",
        "packages": "write",
    }
    assert "github.ref == 'refs/heads/main'" in job["if"]
    step = next(step for step in job["steps"] if "run" in step)
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["env"]["RELEASE_TAG"] == "${{ inputs.release_tag }}"
    assert '--release-tag "$RELEASE_TAG" --publish' in step["run"]
    assert "${{ inputs." not in step["run"]
    matrix = yaml.safe_load((root / ".github/workflows/docker.yml").read_text())[
        "jobs"
    ]["build-and-push"]["strategy"]["matrix"]["include"]
    assert {entry["service"]: entry["image"] for entry in matrix} == {
        service: "ghcr.io/groktopus/groktocrawl-" + suffix
        for service, suffix in promotion.IMAGES.items()
    }
