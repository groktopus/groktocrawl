"""
GitHub adapter — extracts file content, READMEs, and directory listings
from github.com and raw.githubusercontent.com URLs via the GitHub API
and raw content CDN.

Fallback chain per URL type (cheapest authoritative source first):

  raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}
    → Direct HTTP fetch of raw content (zero rate-limit cost)
    → API /repos/{owner}/{repo}/contents/{path}?ref={ref}
    → Generic tier pipeline

  github.com/{owner}/{repo}/blob/{ref}/{path}
    → Rewrite to raw.githubusercontent.com URL, fetch directly
    → API /repos/{owner}/{repo}/contents/{path}?ref={ref}
    → Generic tier pipeline

  github.com/{owner}/{repo}  (repo root)
    → API /repos/{owner}/{repo}/readme
    → API /repos/{owner}/{repo}  (metadata only)
    → Generic tier pipeline

  github.com/{owner}/{repo}/tree/{ref}/{path}
    → API /repos/{owner}/{repo}/contents/{path}?ref={ref}
    → Generic tier pipeline

  github.com/{owner}/{repo}/issues/{number}
  github.com/{owner}/{repo}/pull/{number}
    → Handled by the ``github-discussion`` adapter (priority 190)
    → Falls through to generic tier only if both GraphQL and REST fail

Auth: GITHUB_TOKEN env var (optional, 5000 req/hr with, 60 req/hr without).
Rate limits: per-endpoint sliding window, graceful degradation via
fallback chains. Never fail-fast.
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx

from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)


# ── Extension allowlist for binary detection ─────────────────────
# raw.githubusercontent.com returns application/octet-stream for
# most files, so Content-Type is unreliable. We use an allowlist of
# known-textual extensions instead.
TEXT_EXTENSIONS: set[str] = {
    # Source code
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".rs", ".go", ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".java", ".rb", ".php", ".swift", ".kt", ".scala", ".clj",
    ".lua", ".r", ".m", ".mm", ".dart", ".elm", ".ex", ".exs",
    ".zig", ".nim", ".cbl", ".cpy", ".sas",
    # Web
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".xml", ".svg",
    # Config / data
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".envrc", ".lock", ".gitignore", ".editorconfig",
    ".dockerfile", ".tf", ".tfvars", ".hcl",
    # Docs
    ".md", ".mdx", ".rst", ".txt", ".org", ".adoc",
    # Shell / scripts
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".graphql", ".gql",
    # Python-specific
    ".cfg", ".ini", ".pot", ".po", ".mo",
    ".whl",  # metadata only
    # Misc text
    ".csv", ".tsv", ".log",
    ".diff", ".patch",
    ".nix",
}

# Extensionless filenames that are always text
TEXT_FILENAMES: set[str] = {
    "Dockerfile", "dockerfile",
    "Makefile", "makefile", "GNUmakefile",
    ".env", ".envrc",
    "README", "LICENSE", "CHANGELOG", "CONTRIBUTING",
    "Vagrantfile",
    "Procfile",
    ".gitignore", ".gitattributes", ".editorconfig",
    ".prettierrc", ".eslintrc", ".babelrc",
    "Cargo.toml", "Cargo.lock",
    "requirements.txt", "Pipfile", "Pipfile.lock",
    "pyproject.toml", "setup.py", "setup.cfg",
    "go.mod", "go.sum",
    "Gemfile", "Gemfile.lock",
}

# ── API endpoint constants ───────────────────────────────────────
API_BASE = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"

# ── URL patterns ─────────────────────────────────────────────────

# raw.githubusercontent.com URLs
_RAW_URL_PATTERN = re.compile(
    r"^https?://raw\.githubusercontent\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<ref>[^/]+)/(?P<path>.+)"
)

# github.com blob URLs
_BLOB_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)"
)

# github.com tree URLs
_TREE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/tree/(?P<ref>[^/]+)/(?P<path>.+)"
)

# github.com repo root (no blob/tree/issue/pull suffix)
_REPO_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)$"
)

# github.com issue URLs — matched but NOT handled (falls through)
_ISSUE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/\d+"
)

# github.com PR URLs — matched but NOT handled (falls through)
_PULL_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/\d+"
)


# ── Rate limit tracking ──────────────────────────────────────────

class _RateLimitTracker:
    """Per-endpoint sliding window for burst protection.

    Tracks requests per endpoint type (raw, contents, repo, etc.)
    in a sliding window to avoid triggering GitHub's secondary rate
    limits.  Uses a simple list of timestamps per endpoint.
    """

    def __init__(self):
        self._endpoints: dict[str, list[float]] = {}
        # Default burst limits per endpoint type
        self._burst_limits: dict[str, int] = {
            "raw": 10,       # raw.githubusercontent.com — generous
            "contents": 5,   # /repos/*/contents endpoint
            "repo": 5,       # /repos/* endpoint
            "readme": 5,     # /repos/*/readme endpoint
        }

    def can_call(self, endpoint: str) -> bool:
        """Check if a call to *endpoint* is within burst limits.

        Uses a 60-second sliding window by default.
        """

        now = time.time()
        window = 60.0
        max_burst = self._burst_limits.get(endpoint, 5)

        history = self._endpoints.get(endpoint, [])
        # Prune entries outside the window
        history = [t for t in history if now - t < window]
        self._endpoints[endpoint] = history

        return len(history) < max_burst

    def record_call(self, endpoint: str) -> None:
        """Record a call to *endpoint*."""

        if endpoint not in self._endpoints:
            self._endpoints[endpoint] = []
        self._endpoints[endpoint].append(time.time())

    @property
    def remaining_budget(self) -> dict[str, int]:
        """Return remaining burst budget per endpoint."""
        result = {}
        for ep, limit in self._burst_limits.items():
            used = len([t for t in self._endpoints.get(ep, [])
                        if time.time() - t < 60.0])
            result[ep] = limit - used
        return result


_rate_tracker = _RateLimitTracker()


# ── API helper ────────────────────────────────────────────────────

def _get_token() -> str:
    """Return the GitHub API token from GITHUB_TOKEN env var, or empty string."""
    return os.environ.get("GITHUB_TOKEN", "")


def _api_headers() -> dict[str, str]:
    """Build headers for GitHub API requests.

    Uses standard v3 JSON media type (not raw) so responses come
    back as structured JSON with base64-encoded content that we
    decode ourselves.  The ``raw`` media type would return raw
    text content directly, which breaks our JSON parsing.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GroktoCrawl/0.6.0",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ── URL type classification ──────────────────────────────────────

class UrlType:
    """Enum-like constants for URL type classification."""

    RAW = "raw"
    BLOB = "blob"
    TREE = "tree"
    REPO_ROOT = "repo_root"
    ISSUE = "issue"
    PULL = "pull"
    UNKNOWN = "unknown"


def _classify_url(url: str) -> tuple[str, dict[str, str] | None]:
    """Classify a URL and extract its path components.

    Returns (UrlType, {owner, repo, ref, path} or None).
    Returns UrlType.UNKNOWN if no pattern matches.
    """
    m = _RAW_URL_PATTERN.match(url)
    if m:
        return UrlType.RAW, m.groupdict()

    m = _BLOB_URL_PATTERN.match(url)
    if m:
        return UrlType.BLOB, m.groupdict()

    m = _TREE_URL_PATTERN.match(url)
    if m:
        return UrlType.TREE, m.groupdict()

    m = _REPO_URL_PATTERN.match(url)
    if m:
        return UrlType.REPO_ROOT, m.groupdict()

    m = _ISSUE_URL_PATTERN.match(url)
    if m:
        return UrlType.ISSUE, m.groupdict()

    m = _PULL_URL_PATTERN.match(url)
    if m:
        return UrlType.PULL, m.groupdict()

    return UrlType.UNKNOWN, None


def _is_binary(path: str) -> bool:
    """Check if a file path looks like binary content using extension allowlist."""
    _, ext = os.path.splitext(path)
    if ext:
        return ext.lower() not in TEXT_EXTENSIONS
    # Extensionless files: check against known-text filenames
    basename = os.path.basename(path)
    return basename not in TEXT_FILENAMES


def _build_raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    """Build a raw.githubusercontent.com URL from components."""
    return f"{RAW_BASE}/{owner}/{repo}/{ref}/{path}"


# ── Content extraction functions ──────────────────────────────────

async def _fetch_raw_content(
    url: str, ctx: AdapterContext
) -> dict | None:
    """Tier 1: fetch raw content via raw.githubusercontent.com.

    Returns {markdown, source, metadata} or None on failure.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": "GroktoCrawl/0.6.0",
            })
            if resp.status_code != 200:
                logger.debug("Raw fetch returned %d for %s", resp.status_code, url)
                return None

            content = resp.text
            if not content:
                return None

            return {
                "markdown": f"```\n{content}\n```",
                "source": "raw.githubusercontent.com",
                "metadata": {
                    "size": len(content),
                    "encoding": "utf-8",
                },
            }
    except Exception as exc:
        logger.debug("Raw fetch failed for %s: %s", url, exc)
        return None


async def _fetch_via_contents_api(
    owner: str, repo: str, path: str, ref: str | None = None
) -> dict | None:
    """Tier 2: fetch content via GitHub Contents API.

    Returns {markdown, source, metadata} or None on failure.
    The API returns base64-encoded content capped at 1MB.
    """
    import base64

    url = f"{API_BASE}/repos/{owner}/{repo}/contents/{path}"
    params: dict[str, str] = {}
    if ref:
        params["ref"] = ref

    headers = _api_headers()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15
        ) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                logger.debug("Contents API returned %d for %s/%s/%s",
                             resp.status_code, owner, repo, path)
                return None

            data = resp.json()
            # Check rate limit
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            logger.debug("Rate limit remaining after Contents API: %s", remaining)

            if isinstance(data, list):
                # Directory listing
                entries = []
                for item in data:
                    name = item.get("name", "")
                    item_type = item.get("type", "")
                    size = item.get("size", 0)
                    download_url = item.get("download_url", "") or ""
                    entries.append({
                        "name": name,
                        "type": item_type,
                        "size": size,
                        "download_url": download_url,
                    })

                md_lines = [f"# {path}", "", f"*{len(entries)} items*", ""]
                # Sort: directories first, then files, alphabetical
                dirs = [e for e in entries if e["type"] == "dir"]
                files = [e for e in entries if e["type"] == "file"]
                for d in dirs:
                    md_lines.append(f"📁 **{d['name']}/**")
                for f in files:
                    md_lines.append(f"📄 {f['name']}  _({f['size']} bytes)_")
                md_lines.append("")

                return {
                    "markdown": "\n".join(md_lines),
                    "source": "github-contents-api",
                    "metadata": {
                        "item_count": len(entries),
                        "directories": len(dirs),
                        "files": len(files),
                    },
                }

            elif isinstance(data, dict):
                # Single file
                encoding = data.get("encoding", "")
                content_b64 = data.get("content", "")
                size = data.get("size", 0)
                name = data.get("name", "")
                language = data.get("language", "") or ""

                if encoding == "base64" and content_b64:
                    try:
                        decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                    except Exception:
                        decoded = content_b64
                else:
                    decoded = content_b64

                md = f"```{language.lower() if language else ''}\n{decoded}\n```"
                return {
                    "markdown": md,
                    "source": "github-contents-api",
                    "metadata": {
                        "size": size,
                        "language": language,
                        "encoding": encoding,
                    },
                }
    except Exception as exc:
        logger.debug("Contents API failed for %s/%s/%s: %s", owner, repo, path, exc)
    return None


async def _fetch_readme(owner: str, repo: str) -> dict | None:
    """Fetch the repo README via the GitHub Readme API.

    Returns {markdown, source, metadata} or None on failure.
    """
    url = f"{API_BASE}/repos/{owner}/{repo}/readme"
    headers = _api_headers()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                logger.debug("No README for %s/%s", owner, repo)
                return None
            if resp.status_code != 200:
                logger.debug("Readme API returned %d for %s/%s",
                             resp.status_code, owner, repo)
                return None

            data = resp.json()
            encoding = data.get("encoding", "")
            content_b64 = data.get("content", "")
            name = data.get("name", "README.md")
            size = data.get("size", 0)

            import base64
            if encoding == "base64" and content_b64:
                try:
                    decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                except Exception:
                    decoded = content_b64
            else:
                decoded = content_b64

            return {
                "markdown": decoded,
                "source": "github-readme-api",
                "metadata": {
                    "file": name,
                    "size": size,
                },
            }
    except Exception as exc:
        logger.debug("Readme API failed for %s/%s: %s", owner, repo, exc)
    return None


async def _fetch_repo_metadata(owner: str, repo: str) -> dict | None:
    """Fetch repo metadata via the GitHub Repos API.

    Returns a dict of metadata fields or None on failure.
    """
    url = f"{API_BASE}/repos/{owner}/{repo}"
    headers = _api_headers()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None

            data = resp.json()
            return {
                "description": data.get("description", ""),
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "language": data.get("language", ""),
                "topics": data.get("topics", []),
                "license": data.get("license", {}).get("spdx_id", "") if data.get("license") else "",
                "default_branch": data.get("default_branch", ""),
            }
    except Exception as exc:
        logger.debug("Repo API failed for %s/%s: %s", owner, repo, exc)
    return None


# ── Frontmatter builder ──────────────────────────────────────────

def _build_frontmatter(url_type: str, parts: dict | None,
                       extra: dict | None = None) -> dict:
    """Build compact frontmatter (<20 lines) for any result type."""
    meta: dict = {
        "source": "github-adapter",
        "url_type": url_type,
    }

    if parts:
        meta["owner"] = parts.get("owner", "")
        meta["repo"] = parts.get("repo", "")
        if parts.get("ref"):
            meta["ref"] = parts["ref"]
        if parts.get("path"):
            meta["path"] = parts["path"]

    if extra:
        # Keep it compact — only add meaningful metadata
        for key in ("size", "language", "item_count", "stars",
                    "forks", "description", "source", "encoding"):
            if key in extra and extra[key]:
                meta[key] = extra[key]

    return meta


# ── Adapter class ────────────────────────────────────────────────

@adapter
class GitHubAdapter(SiteAdapter):
    """Extract file content, READMEs, and directory listings from GitHub URLs.

    Fallback chain per URL type (cheapest authoritative source first).
    Issues and PRs are NOT handled in MVP — they fall through to the
    generic tier pipeline.
    """

    name = "github"

    patterns = [
        # raw.githubusercontent.com
        re.compile(r"^https?://raw\.githubusercontent\.com/"),
        # github.com (all subtypes)
        re.compile(r"^https?://(?:www\.)?github\.com/"),
    ]

    # High priority — GitHub URLs are unambiguous and we want to
    # own them before the generic pipeline
    priority = 200

    def __init__(self):
        # Track remaining API rate limit
        self._api_remaining: int | None = None

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        url_type, parts = _classify_url(url)

        logger.info("GitHub adapter: url_type=%s for %s", url_type, url)

        if url_type == UrlType.RAW:
            return await self._handle_raw(url, parts, ctx)
        elif url_type == UrlType.BLOB:
            return await self._handle_blob(url, parts, ctx)
        elif url_type == UrlType.TREE:
            return await self._handle_tree(url, parts, ctx)
        elif url_type == UrlType.REPO_ROOT:
            return await self._handle_repo_root(url, parts, ctx)
        elif url_type in (UrlType.ISSUE, UrlType.PULL):
            # Not handled in MVP — raise AdapterError so dispatch()
            # falls through to the generic tier pipeline
            logger.info("GitHub adapter: %s not handled in MVP, falling through", url_type)
            raise AdapterError(f"{url_type} extraction not yet implemented — falling through to generic tier")
        else:
            raise AdapterError(f"Unknown GitHub URL type: {url}")

    async def _handle_raw(
        self, url: str, parts: dict | None, ctx: AdapterContext
    ) -> AdapterResult:
        """Handle raw.githubusercontent.com URLs."""
        if not parts:
            raise AdapterError(f"Could not parse raw URL: {url}")

        path = parts.get("path", "")
        # Binary check on the path before fetching
        if _is_binary(path):
            logger.info("GitHub adapter: binary file detected via extension, skipping content: %s", path)
            metadata = _build_frontmatter(UrlType.RAW, parts, {
                "size": "binary",
                "note": "Binary file — content not extracted",
            })
            return AdapterResult(
                success=True,
                markdown=f"*Binary file: `{path}`*",
                metadata=metadata,
                source="github-adapter-binary-skip",
                url=url,
            )

        # Track rate limit for raw endpoint
        if not _rate_tracker.can_call("raw"):
            logger.warning("GitHub adapter: raw endpoint burst limit reached")
            # Fall through to generic tier
            raise AdapterError("Raw endpoint rate limit reached")

        _rate_tracker.record_call("raw")
        result = await _fetch_raw_content(url, ctx)
        if result:
            metadata = _build_frontmatter(UrlType.RAW, parts, result.get("metadata"))
            return AdapterResult(
                success=True,
                markdown=result["markdown"],
                metadata=metadata,
                source=result["source"],
                url=url,
            )

        # Fallback: Contents API
        logger.info("GitHub adapter: raw fetch failed, trying Contents API")
        api_result = await _fetch_via_contents_api(
            parts["owner"], parts["repo"], path, parts.get("ref")
        )
        if api_result:
            metadata = _build_frontmatter(UrlType.RAW, parts, api_result.get("metadata"))
            return AdapterResult(
                success=True,
                markdown=api_result["markdown"],
                metadata=metadata,
                source=api_result["source"],
                url=url,
            )

        raise AdapterError(f"Could not extract raw content from {url}")

    async def _handle_blob(
        self, url: str, parts: dict | None, ctx: AdapterContext
    ) -> AdapterResult:
        """Handle github.com blob URLs by rewriting to raw.githubusercontent.com."""
        if not parts:
            raise AdapterError(f"Could not parse blob URL: {url}")

        owner = parts["owner"]
        repo = parts["repo"]
        ref = parts["ref"]
        path = parts["path"]

        # Binary check
        if _is_binary(path):
            logger.info("GitHub adapter: binary file detected via extension: %s", path)
            metadata = _build_frontmatter(UrlType.BLOB, parts, {
                "size": "binary",
                "note": "Binary file — content not extracted",
            })
            return AdapterResult(
                success=True,
                markdown=f"*Binary file: `{path}`*",
                metadata=metadata,
                source="github-adapter-binary-skip",
                url=url,
            )

        # Tier 1: Rewrite to raw.githubusercontent.com
        raw_url = _build_raw_url(owner, repo, ref, path)
        if _rate_tracker.can_call("raw"):
            _rate_tracker.record_call("raw")
            raw_result = await _fetch_raw_content(raw_url, ctx)
            if raw_result:
                metadata = _build_frontmatter(UrlType.BLOB, parts, raw_result.get("metadata"))
                return AdapterResult(
                    success=True,
                    markdown=raw_result["markdown"],
                    metadata=metadata,
                    source=raw_result["source"],
                    url=url,
                )
        else:
            logger.debug("GitHub adapter: raw endpoint rate limited, skipping raw fetch")

        # Tier 2: Contents API
        api_result = await _fetch_via_contents_api(owner, repo, path, ref)
        if api_result:
            metadata = _build_frontmatter(UrlType.BLOB, parts, api_result.get("metadata"))
            return AdapterResult(
                success=True,
                markdown=api_result["markdown"],
                metadata=metadata,
                source=api_result["source"],
                url=url,
            )

        raise AdapterError(f"Could not extract blob content from {url}")

    async def _handle_tree(
        self, url: str, parts: dict | None, ctx: AdapterContext
    ) -> AdapterResult:
        """Handle github.com tree URLs via Contents API."""
        if not parts:
            raise AdapterError(f"Could not parse tree URL: {url}")

        owner = parts["owner"]
        repo = parts["repo"]
        ref = parts["ref"]
        path = parts.get("path", "")

        # Tier 1: Contents API (only option for directory listings)
        if not _rate_tracker.can_call("contents"):
            raise AdapterError("Contents API rate limit reached for tree listing")

        _rate_tracker.record_call("contents")
        api_result = await _fetch_via_contents_api(owner, repo, path, ref)
        if api_result:
            metadata = _build_frontmatter(UrlType.TREE, parts, api_result.get("metadata"))
            return AdapterResult(
                success=True,
                markdown=api_result["markdown"],
                metadata=metadata,
                source=api_result["source"],
                url=url,
            )

        raise AdapterError(f"Could not extract tree listing from {url}")

    async def _handle_repo_root(
        self, url: str, parts: dict | None, ctx: AdapterContext
    ) -> AdapterResult:
        """Handle github.com repo root URLs: README + metadata."""
        if not parts:
            raise AdapterError(f"Could not parse repo URL: {url}")

        owner = parts["owner"]
        repo = parts["repo"]

        # Tier 1: Readme API
        if _rate_tracker.can_call("readme"):
            _rate_tracker.record_call("readme")
            readme = await _fetch_readme(owner, repo)
        else:
            readme = None
            logger.debug("GitHub adapter: readme endpoint rate limited")

        # Tier 2: Repo metadata (runs in parallel or after)
        repo_meta = None
        if _rate_tracker.can_call("repo"):
            _rate_tracker.record_call("repo")
            repo_meta = await _fetch_repo_metadata(owner, repo)

        # Build markdown
        md_parts = []
        readme_md = ""
        if readme:
            readme_md = readme.get("markdown", "")

        if repo_meta:
            desc = repo_meta.get("description", "")
            stars = repo_meta.get("stars", 0)
            forks = repo_meta.get("forks", 0)
            lang = repo_meta.get("language", "")
            topics = repo_meta.get("topics", [])
            license_s = repo_meta.get("license", "")
            default_branch = repo_meta.get("default_branch", "")

            md_parts.append(f"# {owner}/{repo}")
            if desc:
                md_parts.append(f"\n> {desc}\n")
            stats = []
            if stars:
                stats.append(f"⭐ {stars} stars")
            if forks:
                stats.append(f"🍴 {forks} forks")
            if lang:
                stats.append(f"🔤 {lang}")
            if license_s:
                stats.append(f"📜 {license_s}")
            stats.append(f"🌿 {default_branch}")
            if stats:
                md_parts.append(" | ".join(stats))
            if topics:
                md_parts.append(f"\n🏷️  {', '.join(f'`{t}`' for t in topics)}")
            md_parts.append("")
        else:
            md_parts.append(f"# {owner}/{repo}")
            md_parts.append("")

        if readme_md:
            md_parts.append("---")
            md_parts.append("## README")
            md_parts.append("")
            md_parts.append(readme_md)

        markdown = "\n".join(md_parts).strip()

        extra_meta = {}
        if repo_meta:
            extra_meta = repo_meta.copy()
        if readme:
            extra_meta.update(readme.get("metadata", {}))

        metadata = _build_frontmatter(UrlType.REPO_ROOT, parts, extra_meta)

        return AdapterResult(
            success=True,
            markdown=markdown or f"# {owner}/{repo}\n\n*Empty repository*",
            metadata=metadata,
            source="github-adapter",
            url=url,
        )
