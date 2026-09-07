"""Request-scoped source artifacts shared by ranking and synthesis.

A ``SourceArtifact`` carries a URL plus the retrieval metadata and, when
available, the scraped Markdown so that ranking and final synthesis can
reuse one fetch instead of re-scraping the same URL. The projection kept in
``research.state.CompactSource`` deliberately excludes ``markdown``; this
artifact is never persisted into the replayable event state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from common.url import extract_domain

# Documents are truncated to this many characters of Markdown when they are
# turned into LLM context blocks, mirroring the historical per-source limit.
DOCUMENT_MAX_CHARS = 8000


@dataclass
class SourceArtifact:
    """One scraped (or reused) source and its optional Markdown content."""

    url: str
    title: str = ""
    relevance: str = ""
    markdown: str | None = None
    source: str = "unknown"
    char_count: int = 0
    cache_state: str | None = None  # None / "live" / "from_cache"
    fetched_at: float | None = None
    # Hybrid-retrieval provenance (ADR-0052). ``retrieval`` records which
    # discovery source(s) produced the URL; ``score`` carries the vector
    # similarity score when one exists; ``cache_age_ms`` is set only when
    # content was reused from the Valkey scrape cache.
    retrieval: str = "web"  # "web" / "vector" / "both"
    score: float | None = None
    cache_age_ms: int | None = None
    # Request options are retained so later stages can validate reuse against
    # the same extraction contract.
    fetch_options: dict[str, Any] | None = None
    contents_options: dict[str, Any] | None = None
    extras: dict[str, Any] | None = None

    def to_document(self, max_chars: int = DOCUMENT_MAX_CHARS) -> str:
        """Render the source into a ``Source: url (domain: ...)`` context block."""
        domain = extract_domain(self.url)
        markdown = self.markdown or ""
        return f"Source: {self.url} (domain: {domain})\n\n{markdown[:max_chars]}"

    def to_source_detail(self) -> dict:
        """Project the artifact into the historical ``source_details`` dict shape."""
        return {
            "url": self.url,
            "source": self.source,
            "char_count": self.char_count,
        }

    def compatible_with(
        self,
        *,
        fetch_options: dict[str, Any] | None = None,
        contents_options: dict[str, Any] | None = None,
    ) -> bool:
        """Return whether this artifact satisfies an extraction contract."""
        return _options_fingerprint(self.fetch_options, self.contents_options) == (
            _options_fingerprint(fetch_options, contents_options)
        )


def normalize_source_url(url: str) -> str:
    """Fold transport-equivalent spelling without changing resource identity."""
    raw = url.strip()
    try:
        parsed = urlsplit(raw)
        host, port = parsed.hostname, parsed.port
        if not host or not parsed.scheme or parsed.username or parsed.password:
            return raw
        scheme = parsed.scheme.lower()
        netloc = f"[{host.lower()}]" if ":" in host else host.lower()
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            netloc += f":{port}"
        return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    except ValueError:
        return raw


def _options_fingerprint(
    fetch_options: dict[str, Any] | None,
    contents_options: dict[str, Any] | None,
) -> str:
    """Build a stable compatibility fingerprint for extraction options."""
    try:
        return json.dumps(
            {"fetch": fetch_options or {}, "contents": contents_options or {}},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return repr((fetch_options, contents_options))


@dataclass
class _RegistryEntry:
    artifact: SourceArtifact
    scrape_options_key: str


class SourceRegistry:
    """Request-scoped successful source artifacts.

    The registry intentionally has no failure entries. A failed or barrier
    refused acquisition therefore remains eligible for a later retry. An
    artifact is reusable only when its extraction-option fingerprint matches
    the requested fingerprint exactly.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _RegistryEntry] = {}

    def key(self, url: str) -> str:
        """Return the normalized identity key for *url*."""
        return normalize_source_url(url)

    def get(
        self, url: str, scrape_options: dict | None = None
    ) -> SourceArtifact | None:
        """Return compatible non-empty content, if present."""
        entry = self._entries.get(self.key(url))
        if entry is None or not entry.artifact.markdown:
            return None
        if entry.scrape_options_key != _options_fingerprint(scrape_options, None):
            return None
        return entry.artifact

    def register(
        self, artifact: SourceArtifact, scrape_options: dict | None = None
    ) -> SourceArtifact:
        """Store a successful artifact and return the stored artifact.

        Empty artifacts are ignored so they never suppress a future retry.
        Existing metadata is retained when a later alias has less metadata.
        """
        if not artifact.markdown:
            return artifact
        if artifact.fetch_options is None:
            artifact.fetch_options = scrape_options
        key = self.key(artifact.url)
        previous = self._entries.get(key)
        if previous is not None:
            old = previous.artifact
            if not artifact.title:
                artifact.title = old.title
            if not artifact.relevance:
                artifact.relevance = old.relevance
        self._entries[key] = _RegistryEntry(
            artifact=artifact,
            scrape_options_key=_options_fingerprint(
                artifact.fetch_options, artifact.contents_options
            ),
        )
        return artifact

    def artifacts(self) -> list[SourceArtifact]:
        """Return unique artifacts in first-seen order."""
        return [
            entry.artifact
            for entry in self._entries.values()
            if entry.artifact.markdown
        ]

    def documents(self) -> list[str]:
        """Render the unique registry contents as synthesis documents."""
        return [artifact.to_document() for artifact in self.artifacts()]

    def context(self) -> str:
        """Render a stable, duplicate-free synthesis context."""
        return "\n\n---\n\n".join(self.documents())


def artifacts_to_documents_and_details(
    artifacts: list[SourceArtifact],
) -> tuple[list[str], list[dict]]:
    """Split artifacts into the legacy ``(documents, source_details)`` pair."""
    return (
        [artifact.to_document() for artifact in artifacts],
        [artifact.to_source_detail() for artifact in artifacts],
    )
