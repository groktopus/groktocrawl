#!/usr/bin/env python3
"""Verify a published release's OCI indexes and optionally promote their tags.

Reads manifests and image configuration only; no layers, rebuilds, or Git tag
changes. Publication requires GH_TOKEN with package write access and GITHUB_ACTOR.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "groktopus/groktocrawl"
SOURCE = f"https://github.com/{REPOSITORY}"
IMAGES = {
    "agent-svc": "agent",
    "scraper-svc": "scraper",
    "browser-svc": "browser",
    "semantic-svc": "semantic",
    "portal-svc": "portal",
    "parse-svc": "parse",
    "mcp-svc": "mcp",
    "capture-egress-svc": "capture-egress",
    "flare-control": "flare-control",
    "flare-protected": "protected-flare",
}
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
ACCEPT = ", ".join(sorted(INDEX_TYPES | MANIFEST_TYPES))
MAX_METADATA = 4 * 1024 * 1024


class PromotionError(RuntimeError):
    """A release or image failed a required provenance/publication check."""


def digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def gh_api(path: str, *, paginate: bool = False):
    command = ["gh", "api"] + (["--paginate", "--slurp"] if paginate else []) + [path]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PromotionError("GitHub metadata query could not complete") from exc
    if result.returncode:
        raise PromotionError("GitHub metadata query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PromotionError("GitHub metadata was not valid JSON") from exc


def resolve_release(tag: str) -> str:
    if not RELEASE_TAG.fullmatch(tag):
        raise PromotionError("release tag must be a stable vMAJOR.MINOR.PATCH version")
    release = gh_api(f"repos/{REPOSITORY}/releases/tags/{tag}")
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise PromotionError("an existing published stable release is required")
    obj = gh_api(f"repos/{REPOSITORY}/git/ref/tags/{tag}").get("object", {})
    for _ in range(5):
        value = obj.get("sha", "")
        if not SHA.fullmatch(value):
            raise PromotionError("release tag does not resolve to a valid commit")
        if obj.get("type") == "commit":
            return value
        if obj.get("type") != "tag":
            break
        obj = gh_api(f"repos/{REPOSITORY}/git/tags/{value}").get("object", {})
    raise PromotionError("release tag does not resolve to a commit")


def published_run(revision: str) -> dict:
    response = gh_api(
        f"repos/{REPOSITORY}/actions/workflows/docker.yml/runs?event=push&head_sha={revision}&per_page=100"
    )
    for run in response.get("workflow_runs", []):
        expected = {
            "head_sha": revision,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "event": "push",
            "path": ".github/workflows/docker.yml",
        }
        if any(run.get(key) != value for key, value in expected.items()):
            continue
        if run.get("repository", {}).get("full_name") != REPOSITORY:
            continue
        pages = gh_api(
            f"repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?per_page=100",
            paginate=True,
        )
        successful = {
            job.get("name")
            for page in pages
            for job in page.get("jobs", [])
            if job.get("status") == "completed" and job.get("conclusion") == "success"
        }
        if not set(IMAGES).issubset(successful):
            continue
        return {
            "id": run["id"],
            "url": run["html_url"],
            "revision": revision,
            "attempt": run.get("run_attempt"),
            "image_build_jobs": sorted(IMAGES),
        }
    raise PromotionError(
        "no successful official mainline Docker publish run with all ten images exists at the release commit"
    )


class MetadataRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise PromotionError("registry metadata redirect is not HTTPS")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if (
            redirected is not None
            and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


@dataclass(frozen=True)
class Document:
    body: bytes
    content: dict

    @property
    def digest(self) -> str:
        return digest(self.body)


class Registry:
    def __init__(self, package: str):
        self.package = package
        self.opener = build_opener(MetadataRedirect())
        self.token = self._token()

    def _request(self, request: Request):
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(MAX_METADATA + 1)
                headers, status = response.headers, response.status
        except HTTPError as exc:
            raise PromotionError(f"registry metadata HTTP {exc.code}") from None
        except (URLError, OSError):
            raise PromotionError(
                "registry metadata request could not complete"
            ) from None
        if len(body) > MAX_METADATA:
            raise PromotionError("registry metadata exceeds the bounded size limit")
        return body, headers, status

    def _token(self, actor: str | None = None, password: str | None = None) -> str:
        scope = "pull,push" if password else "pull"
        query = urlencode(
            {"service": "ghcr.io", "scope": f"repository:{self.package}:{scope}"}
        )
        headers = {"Accept": "application/json"}
        if password:
            credentials = base64.b64encode(f"{actor}:{password}".encode()).decode()
            headers["Authorization"] = "Basic " + credentials
        body, _, _ = self._request(
            Request("https://ghcr.io/token?" + query, headers=headers)
        )
        try:
            token = json.loads(body).get("token")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PromotionError("registry token response was invalid") from exc
        if not isinstance(token, str) or not token:
            raise PromotionError("registry token is unavailable")
        return token

    def authorize_push(self, actor: str, password: str):
        self.token = self._token(actor, password)

    def fetch(
        self, kind: str, reference: str, expected_size: int | None = None
    ) -> Document:
        if kind not in {"manifests", "blobs"}:
            raise PromotionError("only manifests and config metadata are permitted")
        body, headers, _ = self._request(
            Request(
                f"https://ghcr.io/v2/{self.package}/{kind}/{reference}",
                headers={"Accept": ACCEPT, "Authorization": "Bearer " + self.token},
            )
        )
        actual = digest(body)
        if headers.get("Docker-Content-Digest", actual) != actual or (
            DIGEST.fullmatch(reference) and actual != reference
        ):
            raise PromotionError("registry metadata content digest mismatch")
        if expected_size is not None and len(body) != expected_size:
            raise PromotionError("OCI descriptor size mismatch")
        try:
            content = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PromotionError("registry metadata was not valid JSON") from exc
        if not isinstance(content, dict):
            raise PromotionError("registry metadata is not an object")
        return Document(body, content)

    def put_index(self, tag: str, document: Document):
        _body, headers, status = self._request(
            Request(
                f"https://ghcr.io/v2/{self.package}/manifests/{tag}",
                data=document.body,
                method="PUT",
                headers={
                    "Content-Type": document.content["mediaType"],
                    "Authorization": "Bearer " + self.token,
                },
            )
        )
        if status != 201 or headers.get("Docker-Content-Digest") != document.digest:
            raise PromotionError(
                "registry did not confirm the original index digest after publication"
            )
        if self.fetch("manifests", tag).digest != document.digest:
            raise PromotionError(
                "published tag does not resolve to the original index digest"
            )


def descriptor(entry: dict) -> tuple[str, int]:
    value, size = entry.get("digest"), entry.get("size")
    if (
        not isinstance(value, str)
        or not DIGEST.fullmatch(value)
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_METADATA
    ):
        raise PromotionError("invalid OCI metadata descriptor")
    return value, size


@dataclass
class VerifiedImage:
    service: str
    registry: Registry
    index: Document
    platforms: dict

    def evidence(self) -> dict:
        return {
            "service": self.service,
            "image": "ghcr.io/" + self.registry.package,
            "index_digest": self.index.digest,
            "platforms": self.platforms,
        }


def verify_image(
    service: str, suffix: str, revision: str, registry_factory=Registry
) -> VerifiedImage:
    registry = registry_factory(f"groktopus/groktocrawl-{suffix}")
    index = registry.fetch("manifests", "sha-" + revision)
    if index.content.get("mediaType") not in INDEX_TYPES:
        raise PromotionError(f"{service} has no multi-platform index")
    supported = {}
    for item in index.content.get("manifests", []):
        platform = item.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") in {
            "amd64",
            "arm64",
        }:
            architecture = platform["architecture"]
            if architecture in supported:
                raise PromotionError(f"{service} has ambiguous platform manifests")
            supported[architecture] = item
    if set(supported) != {"amd64", "arm64"}:
        raise PromotionError(f"{service} is missing linux/amd64 or linux/arm64")
    evidence = {}
    for architecture, item in sorted(supported.items()):
        manifest_digest, size = descriptor(item)
        manifest = registry.fetch("manifests", manifest_digest, size)
        if manifest.content.get("mediaType") not in MANIFEST_TYPES:
            raise PromotionError(f"{service} platform is not an image manifest")
        config_digest, config_size = descriptor(manifest.content.get("config", {}))
        config = registry.fetch("blobs", config_digest, config_size).content
        if config.get("os") != "linux" or config.get("architecture") != architecture:
            raise PromotionError(f"{service} config platform mismatch")
        settings = config.get("config", {})
        labels = settings.get("Labels") or {}
        if (
            labels.get("org.opencontainers.image.revision") != revision
            or labels.get("org.opencontainers.image.source") != SOURCE
        ):
            raise PromotionError(f"{service} OCI source/revision mismatch")
        baked = [
            x.split("=", 1)[1]
            for x in settings.get("Env", [])
            if isinstance(x, str) and x.startswith("GROKTOCRAWL_REVISION=")
        ]
        if service == "agent-svc" and baked != [revision]:
            raise PromotionError("agent baked revision mismatch")
        evidence["linux/" + architecture] = {
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "oci_revision": revision,
            "oci_source": SOURCE,
        }
    return VerifiedImage(service, registry, index, evidence)


def promote(tag: str, *, publish: bool = False, registry_factory=Registry) -> dict:
    revision = resolve_release(tag)
    workflow = published_run(revision)
    # Finish every validation before requesting write credentials or writing any tag.
    images = [
        verify_image(service, suffix, revision, registry_factory)
        for service, suffix in IMAGES.items()
    ]
    match = RELEASE_TAG.fullmatch(tag)
    major, minor, patch = match.groups()
    tags = ["stable", major, f"{major}.{minor}", f"{major}.{minor}.{patch}", tag]
    if publish:
        actor, token = os.getenv("GITHUB_ACTOR"), os.getenv("GH_TOKEN")
        if not actor or not token:
            raise PromotionError(
                "publication requires GITHUB_ACTOR and GH_TOKEN with package write access"
            )
        for image in images:
            image.registry.authorize_push(actor, token)
        for image in images:
            for target in tags:
                image.registry.put_index(target, image.index)
    return {
        "schema": 1,
        "release_tag": tag,
        "revision": revision,
        "workflow": workflow,
        "published": publish,
        "tags": tags,
        "image_layers_downloaded": False,
        "images": [image.evidence() for image in images],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument(
        "--publish", action="store_true", help="write verified stable/semver image tags"
    )
    parser.add_argument(
        "--report", type=Path, default=Path("release-image-provenance.json")
    )
    args = parser.parse_args()
    report = promote(args.release_tag, publish=args.publish)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    sys.stdout.write(
        f"release-images={'published' if args.publish else 'verified'} images=10 layers_downloaded=0\n"
    )


if __name__ == "__main__":
    try:
        main()
    except PromotionError as exc:
        sys.stderr.write(f"release image promotion failed: {exc}\n")
        raise SystemExit(1) from None
