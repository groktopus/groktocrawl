"""
GitHub social adapter — extracts issues, pull requests, discussions,
releases, and commits via the GitHub GraphQL API (v4) with REST
fallback.

Every resource type uses a single GraphQL query as primary path and
REST API as fallback.  Falls through to the generic tier only when
both paths fail.

Auth: GITHUB_TOKEN with ``public_repo`` scope (public repos) or
``repo`` scope (private repos).  Without a token, falls back to
REST API (60 req/hr) then HTML page scrape via readability-lxml.

Fallback chain for every resource type:
  1. GitHub GraphQL API — single query, rich structured data
  2. GitHub REST API — structured JSON, works without auth
  3. HTML page scrape — readability + markdownify, last resort
  4. Generic tier pipeline — for URLs the adapter doesn't match

PAT scope documentation:
  - Classic token: any token with ``repo`` scope
  - Fine-grained PAT: ``issues:read``, ``pull_requests:read``,
    ``metadata:read`` for most resources
  - Set GITHUB_TOKEN env var (reuses the file adapter's variable)
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

import httpx

from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────
GRAPHQL_URL = "https://api.github.com/graphql"
REST_API = "https://api.github.com"


# ── Resource types ───────────────────────────────────────────────

class ResourceType:
    ISSUE = "issue"
    PULL = "pull"
    DISCUSSION = "discussion"
    RELEASE = "release"
    RELEASE_LIST = "release-list"
    COMMIT = "commit"
    UNKNOWN = "unknown"


# ── URL patterns ────────────────────────────────────────────────

_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)$"
    ), ResourceType.ISSUE),
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)$"
    ), ResourceType.PULL),
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/discussions/(?P<number>\d+)$"
    ), ResourceType.DISCUSSION),
    # /releases/tag/{tag} — specific release
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/tag/(?P<tag>.+)$"
    ), ResourceType.RELEASE),
    # /releases/latest — latest release
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/latest$"
    ), ResourceType.RELEASE),
    # /releases — release list
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/?$"
    ), ResourceType.RELEASE_LIST),
    # /commit/{sha}
    (re.compile(
        r"^https?://(?:www\.)?github\.com/"
        r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/commit/"
        r"(?P<sha>[a-fA-F0-9]{6,40})$"
    ), ResourceType.COMMIT),
]


def _classify_url(url: str) -> tuple[str, dict[str, str] | None]:
    """Classify a GitHub URL and extract path components.

    Returns (ResourceType, {owner, repo, ...} or None).
    """
    for pattern, rtype in _URL_PATTERNS:
        m = pattern.match(url)
        if m:
            return rtype, m.groupdict()
    return ResourceType.UNKNOWN, None


# ── Auth ─────────────────────────────────────────────────────────

def _get_token() -> str:
    return os.environ.get("GITHUB_TOKEN", "")


def _check_graphql() -> bool:
    return bool(_get_token())


def _rest_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GroktoCrawl/0.6.0",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ── GraphQL client ──────────────────────────────────────────────

async def _graphql(query: str, variables: dict) -> dict | None:
    """Execute a GraphQL query. Returns the ``data`` dict or None."""
    token = _get_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}", "User-Agent": "GroktoCrawl/0.6.0"},
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            if "errors" in body:
                for err in body["errors"]:
                    logger.debug("GraphQL error: %s", err.get("message", ""))
                return None
            return body.get("data")
    except Exception as exc:
        logger.debug("GraphQL request failed: %s", exc)
        return None


# ── HTML scrape fallback (when GraphQL + REST both fail) ────────

async def _html_scrape(url: str) -> dict | None:
    """Fetch a GitHub page's HTML and extract readable content.

    Tier 3 fallback for every resource type.  Uses readability-lxml
    (already installed in scraper-svc) to extract the main content,
    then markdownify to convert to markdown.

    This catches cases where GraphQL is unavailable and REST is rate-
    limited, still returning something useful from the rendered page.
    GitHub pages are server-side rendered, so the HTML always
    contains the full content.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            })
            if resp.status_code != 200:
                logger.debug("HTML scrape returned %d for %s", resp.status_code, url)
                return None

            html = resp.text
            if not html or len(html) < 500:
                return None

            # readability-lxml extracts the main content from HTML
            from readability import Document
            doc = Document(html)
            content_html = doc.summary()
            title = doc.title()

            if not content_html or len(content_html) < 100:
                return None

            # markdownify converts HTML to markdown
            from markdownify import markdownify as md
            content_md = md(content_html, heading_style="ATX")

            return {
                "title": title or "",
                "body": content_md or "",
                "source": "html-scrape",
            }
    except ImportError:
        logger.debug("readability-lxml or markdownify not available")
        return None
    except Exception as exc:
        logger.debug("HTML scrape failed for %s: %s", url, exc)
        return None


# ── REST helpers ────────────────────────────────────────────────

async def _rest_get(path: str, params: dict | None = None) -> dict | list | None:
    """Make a REST GET request to the GitHub API."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(
                f"{REST_API}{path}",
                headers=_rest_headers(),
                params=params or {},
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:
        logger.debug("REST GET %s failed: %s", path, exc)
        return None


async def _rest_get_text(path: str) -> str | None:
    """Make a REST GET that returns raw text (not JSON)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(
                f"{REST_API}{path}",
                headers={**_rest_headers(), "Accept": "application/vnd.github.v3.raw"},
            )
            if resp.status_code != 200:
                return None
            return resp.text
    except Exception as exc:
        logger.debug("REST GET raw %s failed: %s", path, exc)
        return None


# ═══════════════════════════════════════════════════════════════════
# GRAPHQL QUERIES
# ═══════════════════════════════════════════════════════════════════

_ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      title body state stateReason createdAt closedAt updatedAt url
      author { login }
      labels(first: 20) { nodes { name color } }
      milestone { title }
      assignees(first: 10) { nodes { login } }
      comments(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes {
          body createdAt updatedAt url author { login }
          replies(first: 5) { totalCount nodes { body createdAt author { login } } }
        }
      }
    }
  }
}
"""

_PULL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title body state createdAt closedAt mergedAt merged updatedAt url
      author { login }
      baseRefName headRefName additions deletions changedFiles mergeable
      labels(first: 20) { nodes { name color } }
      assignees(first: 10) { nodes { login } }
      commits { totalCount }
      reviews(first: 20, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount
        nodes { state body createdAt url author { login } }
      }
      comments(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { body createdAt updatedAt url author { login } }
      }
    }
  }
}
"""

_DISCUSSION_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    discussion(number: $number) {
      title body createdAt updatedAt url isAnswered upvoteCount
      author { login }
      category { name slug emoji }
      labels(first: 20) { nodes { name color } }
      answer { body createdAt author { login } }
      comments(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes {
          body createdAt updatedAt url author { login }
          replies(first: 5) { totalCount nodes { body createdAt author { login } } }
        }
      }
    }
  }
}
"""

_RELEASE_QUERY = """
query($owner: String!, $repo: String!, $tag: String!) {
  repository(owner: $owner, name: $repo) {
    release(tagName: $tag) {
      name tagName description isPrerelease isDraft url
      publishedAt createdAt
      author { login }
      releaseAssets(first: 20) {
        nodes { name downloadUrl size contentType }
      }
    }
  }
}
"""

_RELEASES_LIST_QUERY = """
query($owner: String!, $repo: String!, $first: Int!) {
  repository(owner: $owner, name: $repo) {
    releases(first: $first, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        name tagName description isPrerelease isDraft url publishedAt
        author { login }
      }
    }
  }
}
"""

_COMMIT_QUERY = """
query($owner: String!, $repo: String!, $sha: GitObjectID!) {
  repository(owner: $owner, name: $repo) {
    object(oid: $sha) {
      ... on Commit {
        oid messageHeadline message
        author { name email date user { login } }
        committer { name email date user { login } }
        committedDate pushedDate
        url
        associatedPullRequests(first: 5) {
          nodes { number title state url }
        }
        parents(first: 2) { nodes { oid url } }
      }
    }
  }
}
"""


# ═══════════════════════════════════════════════════════════════════
# RENDERERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_labels(labels: list) -> str:
    if not labels:
        return ""
    return "  " + " ".join(f"`{l.get('name', '')}`" for l in labels)


def _fmt_body(body: str | None) -> str:
    return (body or "").strip()


def _render_issue(data: dict) -> tuple[str, dict]:
    parts = []
    title = data.get("title", "Untitled Issue")
    state = data.get("state", "unknown")
    state_icon = "✅" if state in ("closed", "merged") else "🟢"
    author = data.get("author", {}).get("login", "unknown")
    created = (data.get("createdAt", "") or "")[:10]

    parts.append(f"# {title}\n")
    parts.append(f"{state_icon} **{state}** by **@{author}** · _{created}_")
    parts.append(_fmt_labels(data.get("labels", [])))
    parts.append("")

    body = _fmt_body(data.get("body"))
    if body:
        parts.append("---\n## Description\n")
        parts.append(body)
        parts.append("")

    comments = data.get("comments", [])
    if isinstance(comments, list) and comments:
        parts.append(f"---\n## Comments  ({len(comments)})\n")
        for c in comments:
            ca = c.get("author", {}).get("login", "unknown")
            cd = (c.get("createdAt", "") or "")[:10]
            cb = _fmt_body(c.get("body"))
            parts.append(f"### @{ca}  _({cd})_\n")
            if cb:
                parts.append(cb)
                parts.append("")
            # Nested replies (GraphQL)
            rn = c.get("replies")
            if isinstance(rn, dict):
                for r in rn.get("nodes", []):
                    ra = r.get("author", {}).get("login", "unknown")
                    rd = (r.get("createdAt", "") or "")[:10]
                    rb = _fmt_body(r.get("body"))
                    parts.append(f"> **@{ra}** _({rd})_\n")
                    for line in rb.split("\n"):
                        parts.append(f"> {line}")
                    parts.append("")

    labels = data.get("labels", [])
    comment_count = data.get("comment_count", len(comments))
    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.ISSUE,
        "title": title, "state": state, "author": author,
        "created": created, "comment_count": comment_count,
    }
    if labels:
        meta["labels"] = [l.get("name", "") for l in labels]
    return "\n".join(parts).strip(), meta


def _render_pull(data: dict) -> tuple[str, dict]:
    parts = []
    title = data.get("title", "Untitled PR")
    state = data.get("state", "unknown")
    merged = data.get("merged", False)
    if state == "merged" or merged:
        state_icon, state_label = "✅", "merged"
    elif state == "closed":
        state_icon, state_label = "❌", "closed"
    else:
        state_icon, state_label = "🟢", "open"

    author = data.get("author", {}).get("login", "unknown")
    created = (data.get("createdAt", "") or "")[:10]
    base = data.get("baseRefName", "")
    head = data.get("headRefName", "")
    adds = data.get("additions", 0)
    dels = data.get("deletions", 0)
    files = data.get("changedFiles", 0)
    commits_total = "?"
    if isinstance(data.get("commits"), dict):
        commits_total = data["commits"].get("totalCount", "?")

    parts.append(f"# {title}\n")
    parts.append(f"{state_icon} **{state_label}** by **@{author}** · `{base} ← {head}` · _{created}_")
    parts.append(_fmt_labels(data.get("labels", [])))
    parts.append(f"\n📊 **+{adds} / -{dels}** across {files} files · {commits_total} commits")

    mergeable = data.get("mergeable", "")
    if mergeable and mergeable not in ("UNKNOWN", "UNKNOWN"):
        parts.append("🔀 ✅ mergeable" if mergeable == "MERGEABLE" else f"🔀 ⚠️ {mergeable.lower()}")
    parts.append("")

    body = _fmt_body(data.get("body"))
    if body:
        parts.append("---\n## Description\n")
        parts.append(body)
        parts.append("")

    # Changed files (REST)
    files_list = data.get("files", [])
    if files_list:
        parts.append("---\n## Changed Files\n")
        icons = {"added": "✅", "modified": "📝", "removed": "🗑️", "renamed": "📎"}
        for f in files_list:
            icon = icons.get(f.get("status", ""), "📄")
            parts.append(f"- {icon} `{f['filename']}` (+{f.get('additions',0)}/-{f.get('deletions',0)})")
        parts.append("")

    # Reviews
    reviews_data = data.get("reviews", {})
    if isinstance(reviews_data, dict):
        reviews = reviews_data.get("nodes", [])
    else:
        reviews = []
    if reviews:
        parts.append("---\n## Reviews\n")
        state_labels = {"APPROVED": "✅ Approved", "CHANGES_REQUESTED": "❌ Changes Requested",
                        "COMMENTED": "💬 Commented", "DISMISSED": "🔇 Dismissed"}
        for rv in reviews:
            rl = state_labels.get(rv.get("state", ""), f"📝 {rv.get('state', '')}")
            ra = rv.get("author", {}).get("login", "unknown")
            rd = (rv.get("createdAt", "") or "")[:10]
            rb = _fmt_body(rv.get("body"))
            parts.append(f"### {rl} by @{ra}  _({rd})_\n")
            if rb:
                parts.append(rb)
                parts.append("")

    # Comments
    comments = data.get("comments", [])
    if isinstance(comments, list) and comments:
        parts.append(f"---\n## Comments  ({len(comments)})\n")
        for c in comments:
            ca = c.get("author", {}).get("login", "unknown")
            cd = (c.get("createdAt", "") or "")[:10]
            cb = _fmt_body(c.get("body"))
            parts.append(f"### @{ca}  _({cd})_\n")
            if cb:
                parts.append(cb)
                parts.append("")

    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.PULL,
        "title": title, "state": state_label, "author": author,
        "created": created, "additions": adds, "deletions": dels,
        "changed_files": files, "comment_count": len(comments),
    }
    if merged:
        meta["merged"] = True
        meta["merged_at"] = (data.get("mergedAt", "") or "")[:10]
    labels = data.get("labels", [])
    if labels:
        meta["labels"] = [l.get("name", "") for l in labels]
    return "\n".join(parts).strip(), meta


def _render_discussion(data: dict) -> tuple[str, dict]:
    parts = []
    title = data.get("title", "Untitled Discussion")
    author = data.get("author", {}).get("login", "unknown")
    created = (data.get("createdAt", "") or "")[:10]
    cat = data.get("category", {})
    cat_name = cat.get("name", "")
    cat_emoji = cat.get("emoji", "")
    upvotes = data.get("upvoteCount", 0)
    answered = data.get("isAnswered", False)

    parts.append(f"# {title}\n")
    parts.append(f"💬 **@{author}** · _{created}_")
    if cat_name:
        parts.append(f"{cat_emoji} {cat_name}")
    parts.append(f"👍 {upvotes} upvotes" + (" · ✅ answered" if answered else ""))
    parts.append(_fmt_labels(data.get("labels", [])))
    parts.append("")

    body = _fmt_body(data.get("body"))
    if body:
        parts.append("---\n## Discussion\n")
        parts.append(body)
        parts.append("")

    # Answer (for Q&A discussions)
    answer = data.get("answer")
    if answer and isinstance(answer, dict):
        aa = answer.get("author", {}).get("login", "unknown")
        ad = (answer.get("createdAt", "") or "")[:10]
        ab = _fmt_body(answer.get("body"))
        parts.append("---\n## ✅ Answer  by @{aa}  _({ad})_\n")
        if ab:
            parts.append(ab)
            parts.append("")

    # Comments
    comments = data.get("comments", [])
    if isinstance(comments, list) and comments:
        parts.append(f"---\n## Comments  ({len(comments)})\n")
        for c in comments:
            ca = c.get("author", {}).get("login", "unknown")
            cd = (c.get("createdAt", "") or "")[:10]
            cb = _fmt_body(c.get("body"))
            parts.append(f"### @{ca}  _({cd})_\n")
            if cb:
                parts.append(cb)
                parts.append("")
            rn = c.get("replies")
            if isinstance(rn, dict):
                for r in rn.get("nodes", []):
                    ra = r.get("author", {}).get("login", "unknown")
                    rd = (r.get("createdAt", "") or "")[:10]
                    rb = _fmt_body(r.get("body"))
                    parts.append(f"> **@{ra}** _({rd})_\n")
                    for line in rb.split("\n"):
                        parts.append(f"> {line}")
                    parts.append("")

    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.DISCUSSION,
        "title": title, "author": author, "created": created,
        "upvote_count": upvotes, "is_answered": answered,
        "comment_count": len(comments) if isinstance(comments, list) else 0,
    }
    if cat_name:
        meta["category"] = cat_name
    labels = data.get("labels", [])
    if labels:
        meta["labels"] = [l.get("name", "") for l in labels]
    return "\n".join(parts).strip(), meta


def _render_release(data: dict) -> tuple[str, dict]:
    parts = []
    name = data.get("name") or data.get("tagName", "Untitled Release")
    tag = data.get("tagName", "")
    author = data.get("author", {}).get("login", "unknown")
    published = (data.get("publishedAt", "") or "")[:10]
    is_prerelease = data.get("isPrerelease", False)
    is_draft = data.get("isDraft", False)
    description = _fmt_body(data.get("description", ""))

    status = []
    if is_draft: status.append("📝 draft")
    if is_prerelease: status.append("⚠️ prerelease")

    parts.append(f"# {name}\n")
    parts.append(f"🏷️ `{tag}` by **@{author}** · _{published}_")
    if status:
        parts.append(" · ".join(status))
    parts.append("")

    if description:
        parts.append("---\n## Release Notes\n")
        parts.append(description)
        parts.append("")

    # Assets
    assets_data = data.get("releaseAssets", {})
    if isinstance(assets_data, dict):
        assets = assets_data.get("nodes", [])
        if assets:
            parts.append("---\n## Assets ({})\n".format(len(assets)))
            for a in assets:
                an = a.get("name", "")
                asize = a.get("size", 0)
                aurl = a.get("downloadUrl", "")
                size_str = f"{asize / 1024:.0f} KB" if asize > 0 else "?"
                parts.append(f"- [{an}]({aurl})  _({size_str})_")
            parts.append("")

    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.RELEASE,
        "name": name, "tag": tag, "author": author,
        "published": published,
        "prerelease": is_prerelease, "draft": is_draft,
    }
    if isinstance(assets_data, dict):
        assets = assets_data.get("nodes", [])
        if assets:
            meta["asset_count"] = len(assets)
    return "\n".join(parts).strip(), meta


def _render_release_list(data: dict) -> tuple[str, dict]:
    parts = []
    releases_data = data.get("releases", {})
    nodes = releases_data.get("nodes", [])
    total = releases_data.get("totalCount", len(nodes))

    parts.append(f"# Releases  ({total} total)\n")
    for r in nodes:
        rn = r.get("name") or r.get("tagName", "?")
        ra = r.get("author", {}).get("login", "?")
        rd = (r.get("publishedAt", "") or "")[:10]
        prerelease = " ⚠️" if r.get("isPrerelease") else ""
        draft = " 📝" if r.get("isDraft") else ""
        desc = _fmt_body(r.get("description", ""))
        parts.append(f"- **{rn}**{prerelease}{draft} by @{ra} · _{rd}_")
        if desc:
            # First line only
            first_line = desc.split("\n")[0][:120]
            parts.append(f"  {first_line}")
    parts.append("")

    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.RELEASE_LIST,
        "total_releases": total, "count": len(nodes),
    }
    return "\n".join(parts).strip(), meta


def _render_commit(data: dict) -> tuple[str, dict]:
    parts = []
    sha = (data.get("oid", "") or "")[:7] if data.get("oid") else "?"
    headline = data.get("messageHeadline", "")
    message = _fmt_body(data.get("message", ""))
    author_obj = data.get("author", {}) or {}
    committer_obj = data.get("committer", {}) or {}
    author_name = author_obj.get("name", author_obj.get("user", {}).get("login", "unknown"))
    author_date = (author_obj.get("date", "") or "")[:10]
    committer_name = committer_obj.get("name", committer_obj.get("user", {}).get("login", ""))
    url = data.get("url", "")
    parents = [p.get("oid", "")[:7] for p in (data.get("parents", {}).get("nodes", []))]

    parts.append(f"# {headline or sha}\n")
    parts.append(f"🔖 `{data.get('oid', '')}` by **{author_name}** · _{author_date}_")
    if parents:
        parts.append(f"👪 parent{'s' if len(parents) > 1 else ''}: {', '.join(parents)}")
    if committer_name and committer_name != author_name:
        parts.append(f"📦 committed by {committer_name}")
    parts.append("")

    if message:
        # Skip headline (already shown) and show the rest
        body_start = message.find("\n\n")
        if body_start > 0:
            body_text = message[body_start:].strip()
            if body_text:
                parts.append("---\n## Message\n")
                parts.append(body_text)
                parts.append("")

    # Associated PRs
    assoc = data.get("associatedPullRequests", {})
    if isinstance(assoc, dict):
        prs = assoc.get("nodes", [])
        if prs:
            parts.append("---\n## Associated Pull Requests\n")
            for pr in prs:
                pn = pr.get("number", "?")
                pt = pr.get("title", "")
                ps = pr.get("state", "")
                pu = pr.get("url", "")
                parts.append(f"- #{pn} {pt} ({ps}) — {pu}")
            parts.append("")

    meta: dict = {
        "source": "github-social-adapter", "resource": ResourceType.COMMIT,
        "sha": data.get("oid", ""), "author": author_name,
        "date": author_date, "headline": headline,
    }
    return "\n".join(parts).strip(), meta


# ═══════════════════════════════════════════════════════════════════
# FETCH FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

async def _fetch_issue(owner: str, repo: str, number: int) -> dict | None:
    # Tier 1: GraphQL
    data = await _graphql(_ISSUE_QUERY, {"owner": owner, "repo": repo, "number": number})
    if data and data.get("repository", {}).get("issue"):
        return data["repository"]["issue"]

    # Tier 2: REST
    logger.debug("GraphQL issue failed, trying REST for %s/%s#%d", owner, repo, number)
    issue = await _rest_get(f"/repos/{owner}/{repo}/issues/{number}")
    if issue:
        comments = await _rest_get(f"/repos/{owner}/{repo}/issues/{number}/comments",
                                   {"per_page": 100})
        if not isinstance(comments, list):
            comments = []
        return {
            "title": issue.get("title", ""), "body": issue.get("body", "") or "",
            "state": issue.get("state", ""), "createdAt": issue.get("created_at", ""),
            "author": {"login": issue.get("user", {}).get("login", "")},
            "labels": [{"name": l["name"], "color": l.get("color", "")} for l in issue.get("labels", [])],
            "comments": [{"body": c.get("body", "") or "", "createdAt": c.get("created_at", ""),
                           "author": {"login": c.get("user", {}).get("login", "")}} for c in comments],
            "comment_count": len(comments),
        }

    # Tier 3: HTML scrape
    logger.debug("REST issue failed, trying HTML scrape for %s/%s#%d", owner, repo, number)
    scrape_url = f"https://github.com/{owner}/{repo}/issues/{number}"
    return await _html_scrape(scrape_url)


async def _fetch_pull(owner: str, repo: str, number: int) -> dict | None:
    # Tier 1: GraphQL
    data = await _graphql(_PULL_QUERY, {"owner": owner, "repo": repo, "number": number})
    if data and data.get("repository", {}).get("pullRequest"):
        return data["repository"]["pullRequest"]

    # Tier 2: REST
    logger.debug("GraphQL PR failed, trying REST for %s/%s#%d", owner, repo, number)
    pr = await _rest_get(f"/repos/{owner}/{repo}/pulls/{number}")
    if pr:
        comments = await _rest_get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100})
        files = await _rest_get(f"/repos/{owner}/{repo}/pulls/{number}/files", {"per_page": 30})
        return {
            "title": pr.get("title", ""), "body": pr.get("body", "") or "",
            "state": pr.get("state", ""), "createdAt": pr.get("created_at", ""),
            "mergedAt": pr.get("merged_at", ""), "merged": pr.get("merged", False),
            "author": {"login": pr.get("user", {}).get("login", "")},
            "baseRefName": pr.get("base", {}).get("ref", ""),
            "headRefName": pr.get("head", {}).get("ref", ""),
            "additions": pr.get("additions", 0), "deletions": pr.get("deletions", 0),
            "changedFiles": pr.get("changed_files", 0),
            "mergeable": pr.get("mergeable_state", ""),
            "labels": [{"name": l["name"], "color": l.get("color", "")} for l in pr.get("labels", [])],
            "comments": [{"body": c.get("body", "") or "", "createdAt": c.get("created_at", ""),
                           "author": {"login": c.get("user", {}).get("login", "")}} for c in (comments or [])],
            "review_comments": [],
            "files": [{"filename": f.get("filename", ""), "status": f.get("status", ""),
                        "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
                       for f in (files or [])[:20]],
            "comment_count": len(comments or []),
        }

    # Tier 3: HTML scrape
    logger.debug("REST PR failed, trying HTML scrape for %s/%s#%d", owner, repo, number)
    scrape_url = f"https://github.com/{owner}/{repo}/pull/{number}"
    return await _html_scrape(scrape_url)


async def _fetch_discussion(owner: str, repo: str, number: int) -> dict | None:
    # Tier 1: GraphQL (discussions have no REST API — fall through)
    data = await _graphql(_DISCUSSION_QUERY, {"owner": owner, "repo": repo, "number": number})
    if data and data.get("repository", {}).get("discussion"):
        return data["repository"]["discussion"]

    # Tier 2: HTML scrape (no REST API for discussions)
    logger.debug("GraphQL discussion failed, trying HTML scrape for %s/%s#%d", owner, repo, number)
    scrape_url = f"https://github.com/{owner}/{repo}/discussions/{number}"
    return await _html_scrape(scrape_url)


async def _fetch_release(owner: str, repo: str, tag: str) -> dict | None:
    # Tier 1: GraphQL
    data = await _graphql(_RELEASE_QUERY, {"owner": owner, "repo": repo, "tag": tag})
    if data and data.get("repository", {}).get("release"):
        return data["repository"]["release"]

    # Tier 2: REST
    logger.debug("GraphQL release failed, trying REST for %s/%s/tag/%s", owner, repo, tag)
    release = await _rest_get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
    if release:
        assets = await _rest_get(f"/repos/{owner}/{repo}/releases/{release.get('id')}/assets")
        return {
            "name": release.get("name", ""), "tagName": release.get("tag_name", ""),
            "description": release.get("body", "") or "",
            "isPrerelease": release.get("prerelease", False),
            "isDraft": release.get("draft", False),
            "publishedAt": release.get("published_at", ""),
            "author": {"login": release.get("author", {}).get("login", "")},
            "releaseAssets": {"nodes": [
                {"name": a.get("name", ""), "downloadUrl": a.get("browser_download_url", ""),
                 "size": a.get("size", 0), "contentType": a.get("content_type", "")}
                for a in (assets or [])
            ]},
        }

    # Tier 3: HTML scrape
    logger.debug("REST release failed, trying HTML scrape for %s/%s/tag/%s", owner, repo, tag)
    scrape_url = f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
    return await _html_scrape(scrape_url)


async def _fetch_release_list(owner: str, repo: str) -> dict | None:
    # Tier 1: GraphQL
    data = await _graphql(_RELEASES_LIST_QUERY, {"owner": owner, "repo": repo, "first": 30})
    if data and data.get("repository", {}).get("releases"):
        return data["repository"]

    # Tier 2: REST
    logger.debug("GraphQL release list failed, trying REST for %s/%s/releases", owner, repo)
    releases = await _rest_get(f"/repos/{owner}/{repo}/releases", {"per_page": 30})
    if isinstance(releases, list) and releases:
        return {
            "releases": {
                "totalCount": len(releases),
                "nodes": [{
                    "name": r.get("name", ""), "tagName": r.get("tag_name", ""),
                    "description": r.get("body", "") or "",
                    "isPrerelease": r.get("prerelease", False),
                    "isDraft": r.get("draft", False),
                    "publishedAt": r.get("published_at", ""),
                    "url": r.get("html_url", ""),
                    "author": {"login": r.get("author", {}).get("login", "")},
                } for r in releases],
            }
        }

    # Tier 3: HTML scrape
    logger.debug("REST release list failed, trying HTML scrape for %s/%s/releases", owner, repo)
    scrape_url = f"https://github.com/{owner}/{repo}/releases"
    return await _html_scrape(scrape_url)


async def _fetch_commit(owner: str, repo: str, sha: str) -> dict | None:
    # Tier 1: GraphQL
    data = await _graphql(_COMMIT_QUERY, {"owner": owner, "repo": repo, "sha": sha})
    if data and data.get("repository", {}).get("object"):
        return data["repository"]["object"]

    # Tier 2: REST
    logger.debug("GraphQL commit failed, trying REST for %s/%s/%s", owner, repo, sha)
    commit = await _rest_get(f"/repos/{owner}/{repo}/commits/{sha}")
    if commit:
        author_info = commit.get("commit", {}).get("author", {}) or {}
        committer_info = commit.get("commit", {}).get("committer", {}) or {}
        message = commit.get("commit", {}).get("message", "")
        headline = message.split("\n")[0] if message else ""

        return {
            "oid": sha,
            "messageHeadline": headline,
            "message": message,
            "author": {"name": author_info.get("name", ""),
                        "date": author_info.get("date", ""),
                        "user": {"login": commit.get("author", {}).get("login", "")}},
            "committer": {"name": committer_info.get("name", ""),
                           "date": committer_info.get("date", ""),
                           "user": {"login": commit.get("committer", {}).get("login", "")}},
            "url": f"https://github.com/{owner}/{repo}/commit/{sha}",
            "associatedPullRequests": {"nodes": []},
            "parents": {"nodes": [{"oid": p["sha"]} for p in commit.get("parents", [])[:2]]},
        }

    # Tier 3: HTML scrape
    logger.debug("REST commit failed, trying HTML scrape for %s/%s/%s", owner, repo, sha)
    scrape_url = f"https://github.com/{owner}/{repo}/commit/{sha}"
    return await _html_scrape(scrape_url)


# ═══════════════════════════════════════════════════════════════════
# ADAPTER
# ═══════════════════════════════════════════════════════════════════

@adapter
class GitHubSocialAdapter(SiteAdapter):
    """Extract issues, PRs, discussions, releases, and commits from GitHub.

    Each resource uses a single GraphQL query as the primary extraction
    path, with REST API fallback.  Falls through to the generic tier
    only when both paths fail.

    Requires GITHUB_TOKEN with ``public_repo`` scope for GraphQL;
    REST fallback works without a token at 60 requests/hr.
    Discussions require GraphQL — they have no REST API.
    """

    name = "github-social"

    patterns = [
        re.compile(r"^https?://(?:www\.)?github\.com/"
                   r"[^/]+/[^/]+/(?:issues|pull|discussions)/\d+"),
        re.compile(r"^https?://(?:www\.)?github\.com/"
                   r"[^/]+/[^/]+/releases(?:/tag/.+|/latest)?$"),
        re.compile(r"^https?://(?:www\.)?github\.com/"
                   r"[^/]+/[^/]+/commit/[a-fA-F0-9]{6,40}"),
    ]

    # Below the file adapter (200) so file URLs are tried first
    priority = 190

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        resource_type, parts = _classify_url(url)
        if not parts:
            raise AdapterError(f"Cannot parse GitHub URL: {url}")

        owner = parts["owner"]
        repo = parts["repo"]

        logger.info("GitHub social adapter: resource=%s for %s", resource_type, url)

        try:
            if resource_type == ResourceType.ISSUE:
                return await self._handle_issue(url, owner, repo, int(parts["number"]))
            elif resource_type == ResourceType.PULL:
                return await self._handle_pull(url, owner, repo, int(parts["number"]))
            elif resource_type == ResourceType.DISCUSSION:
                return await self._handle_discussion(url, owner, repo, int(parts["number"]))
            elif resource_type == ResourceType.RELEASE:
                return await self._handle_release(url, owner, repo, parts["tag"])
            elif resource_type == ResourceType.RELEASE_LIST:
                return await self._handle_release_list(url, owner, repo)
            elif resource_type == ResourceType.COMMIT:
                return await self._handle_commit(url, owner, repo, parts["sha"])
        except AdapterError:
            raise
        except Exception as exc:
            logger.debug("GitHub social adapter error: %s", exc)
            raise AdapterError(str(exc))

        raise AdapterError(f"Unknown GitHub resource type: {url}")

    async def _handle_issue(self, url: str, owner: str, repo: str, number: int) -> AdapterResult:
        data = await _fetch_issue(owner, repo, number)
        if not data:
            raise AdapterError(
                f"Could not extract issue {owner}/{repo}#{number}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_issue(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)

    async def _handle_pull(self, url: str, owner: str, repo: str, number: int) -> AdapterResult:
        data = await _fetch_pull(owner, repo, number)
        if not data:
            raise AdapterError(
                f"Could not extract PR {owner}/{repo}#{number}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_pull(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)

    async def _handle_discussion(self, url: str, owner: str, repo: str, number: int) -> AdapterResult:
        data = await _fetch_discussion(owner, repo, number)
        if not data:
            raise AdapterError(
                f"Could not extract discussion {owner}/{repo}#{number}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_discussion(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)

    async def _handle_release(self, url: str, owner: str, repo: str, tag: str) -> AdapterResult:
        data = await _fetch_release(owner, repo, tag)
        if not data:
            raise AdapterError(
                f"Could not extract release {owner}/{repo}/tag/{tag}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_release(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)

    async def _handle_release_list(self, url: str, owner: str, repo: str) -> AdapterResult:
        data = await _fetch_release_list(owner, repo)
        if not data:
            raise AdapterError(
                f"Could not extract releases for {owner}/{repo}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_release_list(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)

    async def _handle_commit(self, url: str, owner: str, repo: str, sha: str) -> AdapterResult:
        data = await _fetch_commit(owner, repo, sha)
        if not data:
            raise AdapterError(
                f"Could not extract commit {owner}/{repo}@{sha[:7]}. "
                f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access."
            )
        markdown, metadata = _render_commit(data)
        return AdapterResult(success=True, markdown=markdown, metadata=metadata,
                             source="github-social-adapter", url=url)
