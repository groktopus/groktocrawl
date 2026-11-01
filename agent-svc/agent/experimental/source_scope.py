"""Caller-admitted source boundary for bounded durable research."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_URL_CHARS = 2048
_MAX_HOST_CHARS = 253
_MAX_SEEDS = 8
_MAX_ALLOWED_HOSTS = 20
_HOST_LABEL = re.compile(r"[a-z0-9-]+\Z")


class SourceScopeUnavailableError(RuntimeError):
    """No permitted source could be acquired, so no answer may be constructed."""


def _normalize_url_host(value: str) -> str:
    """Lower-case and IDNA-normalize a URL hostname, preserving IP literals."""
    candidate = value.strip().rstrip(".").lower()
    if not candidate:
        raise ValueError("source URL has an empty host")
    try:
        address = ip_address(candidate)
    except ValueError:
        pass
    else:
        return str(address)
    try:
        ascii_host = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("source URL host is not a valid IDNA name") from exc
    if len(ascii_host) > _MAX_HOST_CHARS:
        raise ValueError("source URL host is too long")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not _HOST_LABEL.fullmatch(label)
        for label in labels
    ):
        raise ValueError("source URL host is not a valid DNS name")
    return ascii_host


def canonical_source_url(value: str) -> str:
    """Validate and canonically represent an absolute HTTP(S) source URL."""
    if not isinstance(value, str):
        raise ValueError("source URL must be text")
    raw = value.strip()
    if not raw or len(raw) > _MAX_URL_CHARS or any(ch.isspace() for ch in raw):
        raise ValueError("source URL must be a bounded absolute URL")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("source URL must use http or https")
    if not parsed.hostname:
        raise ValueError("source URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL must not include user information")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL has an invalid port") from exc
    host = _normalize_url_host(parsed.hostname)
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def canonical_allowed_host(value: str) -> str:
    """Validate an allowed host as a DNS name without scheme, port, or path."""
    if not isinstance(value, str):
        raise ValueError("allowed host must be text")
    candidate = value.strip().rstrip(".").lower()
    if (
        not candidate
        or len(candidate) > _MAX_HOST_CHARS
        or any(marker in candidate for marker in "/:@?#[]")
        or "*" in candidate
    ):
        raise ValueError("allowed host must be a bare DNS name")
    host = _normalize_url_host(candidate)
    try:
        ip_address(host)
    except ValueError:
        return host
    raise ValueError("allowed host must be a DNS name, not an IP literal")


def url_host_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Whether *url* is on an allowed host (exact or subdomain)."""
    if not allowed_hosts:
        return True
    try:
        hostname = urlsplit(canonical_source_url(url)).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    host = _normalize_url_host(hostname)
    return any(
        host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts
    )


class SourceScope(BaseModel):
    """A validated caller boundary for discovery and acquisition.

    ``open`` leaves source selection unchanged. ``seeds_only`` acquires exactly
    the supplied URLs and performs no search. ``allowlist`` searches only within
    the allowed hosts and may add seed URLs that are themselves in scope.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["open", "seeds_only", "allowlist"] = "open"
    seed_urls: tuple[str, ...] = Field(default=(), max_length=_MAX_SEEDS)
    allowed_hosts: tuple[str, ...] = Field(default=(), max_length=_MAX_ALLOWED_HOSTS)

    @field_validator("seed_urls")
    @classmethod
    def _validate_seeds(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(canonical_source_url(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("seed URLs must be distinct")
        return normalized

    @field_validator("allowed_hosts")
    @classmethod
    def _validate_allowed_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(canonical_allowed_host(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed hosts must be distinct")
        return normalized

    @model_validator(mode="after")
    def _validate_mode(self) -> SourceScope:
        if self.mode == "open":
            if self.seed_urls or self.allowed_hosts:
                raise ValueError(
                    "open source scope cannot include seeds or allowed hosts"
                )
            return self
        if self.mode == "seeds_only":
            if not self.seed_urls:
                raise ValueError(
                    "seeds_only source scope requires at least one seed URL"
                )
            if self.allowed_hosts:
                raise ValueError("seeds_only source scope cannot include allowed hosts")
            return self
        if not self.allowed_hosts:
            raise ValueError(
                "allowlist source scope requires at least one allowed host"
            )
        for seed in self.seed_urls:
            if not url_host_allowed(seed, self.allowed_hosts):
                raise ValueError("every seed URL must be on an allowed host")
        return self

    @property
    def constrained(self) -> bool:
        return self.mode != "open"

    def permits_url(self, url: str | None) -> bool:
        """Whether a captured or discovered URL is inside this scope."""
        if self.mode == "open":
            return True
        if not url:
            return False
        try:
            canonical = canonical_source_url(url)
        except ValueError:
            return False
        if self.mode == "seeds_only":
            return canonical in self.seed_urls
        return url_host_allowed(canonical, self.allowed_hosts)
