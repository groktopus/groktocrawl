"""
GitHub Issues & PRs adapter — extracts structured discussion content
via the GitHub GraphQL API (v4).

Extracts issue/PR body, comments, metadata (labels, state, author),
and (for PRs) reviews, diff stats, and merge status — all in a single
GraphQL query per resource.

Auth requirement: GITHUB_TOKEN with `repo` (private) or `public_repo`
(public) scope.  The GraphQL API does NOT support unauthenticated
access, so this adapter falls through to the generic tier when no
token is available.

Fallback chain:
  1. GitHub GraphQL API — single query, rich structured data
  2. GitHub REST API /issues/{n}/comments (paginated) — fallback
  3. Generic tier pipeline (for when token is absent)

PAT scope documentation:
  - Classic token: any token with `repo` scope
  - Fine-grained PAT: `issues:read`, `pull_requests:read`, `metadata:read`
  - Set GITHUB_TOKEN env var (reuses the same variable as the file adapter)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx

from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)


# ── GraphQL endpoint ────────────────────────────────────────────
GRAPHQL_URL = "https://api.github.com/graphql"


# ── URL patterns ────────────────────────────────────────────────

_ISSUE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)

_PULL_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


# ── Issue types ─────────────────────────────────────────────────

class DiscussionType:
    ISSUE = "issue"
    PULL = "pull"


# ── Auth ─────────────────────────────────────────────────────────

def _get_token() -> str:
    """Return the GitHub API token from GITHUB_TOKEN env var, or empty string."""
    return os.environ.get("GITHUB_TOKEN", "")


def _check_auth() -> bool:
    """Check if GITHUB_TOKEN is available for GraphQL access."""
    token = _get_token()
    if not token:
        logger.debug("GitHub discussion adapter: no GITHUB_TOKEN set")
        return False
    return True


# ── GraphQL queries ──────────────────────────────────────────────

_ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      title
      body
      state
      stateReason
      createdAt
      closedAt
      updatedAt
      url
      author { login }
      labels(first: 20) {
        nodes { name color }
      }
      milestone { title }
      assignees(first: 10) {
        nodes { login }
      }
      comments(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          body
          createdAt
          updatedAt
          url
          author { login }
          replies(first: 5) {
            totalCount
            nodes { body createdAt author { login } }
          }
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
      title
      body
      state
      createdAt
      closedAt
      mergedAt
      merged
      updatedAt
      url
      author { login }
      baseRefName
      headRefName
      additions
      deletions
      changedFiles
      mergeable
      labels(first: 20) {
        nodes { name color }
      }
      assignees(first: 10) {
        nodes { login }
      }
      commits { totalCount }
      reviews(first: 20, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount
        nodes {
          state
          body
          createdAt
          url
          author { login }
        }
      }
      comments(first: 100, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          body
          createdAt
          updatedAt
          url
          author { login }
        }
      }
    }
  }
}
"""


# ── GraphQL client ──────────────────────────────────────────────

async def _graphql_query(query: str, variables: dict) -> dict | None:
    """Execute a GraphQL query against the GitHub API.

    Returns the ``data`` dict on success, or ``None`` on any failure
    (network error, API error, GraphQL errors in response).
    """
    token = _get_token()
    if not token:
        logger.debug("GraphQL query skipped: no token available")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "GroktoCrawl/0.6.0",
                },
            )
            if resp.status_code != 200:
                logger.debug("GraphQL returned %d", resp.status_code)
                return None

            body = resp.json()
            # Check for GraphQL-level errors
            if "errors" in body:
                for err in body["errors"]:
                    logger.debug("GraphQL error: %s", err.get("message", ""))
                return None

            return body.get("data")
    except Exception as exc:
        logger.debug("GraphQL request failed: %s", exc)
        return None


# ── REST API fallback (for issues only) ─────────────────────────

async def _rest_issue_fallback(
    owner: str, repo: str, number: int
) -> dict | None:
    """Fallback: fetch issue via REST API when GraphQL is unavailable.

    Returns a dict with the same structure as the GraphQL response
    fields, or None on failure.

    Works unauthenticated (60 req/hr) but doesn't include rich
    metadata like labels, milestones, or review state.
    """
    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GroktoCrawl/0.6.0",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            # Fetch issue body
            issue_resp = await client.get(
                f"{base_url}/issues/{number}", headers=headers
            )
            if issue_resp.status_code != 200:
                logger.debug("REST issue fetch returned %d", issue_resp.status_code)
                return None

            issue_data = issue_resp.json()

            # Fetch comments
            comments_resp = await client.get(
                f"{base_url}/issues/{number}/comments",
                headers=headers,
                params={"per_page": 100, "page": 1},
            )
            comments_data = comments_resp.json() if comments_resp.status_code == 200 else []

            return {
                "title": issue_data.get("title", ""),
                "body": issue_data.get("body", "") or "",
                "state": issue_data.get("state", ""),
                "createdAt": issue_data.get("created_at", ""),
                "closedAt": issue_data.get("closed_at", ""),
                "author": {"login": issue_data.get("user", {}).get("login", "")},
                "labels": [{"name": l["name"], "color": l.get("color", "")}
                           for l in issue_data.get("labels", [])],
                "comments": [
                    {
                        "body": c.get("body", "") or "",
                        "createdAt": c.get("created_at", ""),
                        "author": {"login": c.get("user", {}).get("login", "")},
                    }
                    for c in comments_data
                ],
                "comment_count": len(comments_data),
                "source": "rest-api-fallback",
            }
    except Exception as exc:
        logger.debug("REST fallback failed: %s", exc)
        return None


async def _rest_pr_fallback(
    owner: str, repo: str, number: int
) -> dict | None:
    """Fallback: fetch PR via REST API when GraphQL is unavailable.

    Returns structured data similar to ``_rest_issue_fallback`` but
    with PR-specific fields.
    """
    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GroktoCrawl/0.6.0",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            # Fetch PR
            pr_resp = await client.get(
                f"{base_url}/pulls/{number}", headers=headers
            )
            if pr_resp.status_code != 200:
                logger.debug("REST PR fetch returned %d", pr_resp.status_code)
                return None

            pr_data = pr_resp.json()

            # Fetch review comments (different endpoint from issue comments)
            review_comments_resp = await client.get(
                f"{base_url}/pulls/{number}/comments",
                headers=headers,
                params={"per_page": 100, "page": 1},
            )
            review_comments = (
                review_comments_resp.json()
                if review_comments_resp.status_code == 200
                else []
            )

            # Fetch issue comments (PR discussions are also issues)
            comments_resp = await client.get(
                f"{base_url}/issues/{number}/comments",
                headers=headers,
                params={"per_page": 100, "page": 1},
            )
            comments_data = comments_resp.json() if comments_resp.status_code == 200 else []

            # Fetch files changed
            files_resp = await client.get(
                f"{base_url}/pulls/{number}/files",
                headers=headers,
                params={"per_page": 30},
            )
            files_data = files_resp.json() if files_resp.status_code == 200 else []

            return {
                "title": pr_data.get("title", ""),
                "body": pr_data.get("body", "") or "",
                "state": pr_data.get("state", ""),
                "createdAt": pr_data.get("created_at", ""),
                "closedAt": pr_data.get("closed_at", ""),
                "mergedAt": pr_data.get("merged_at", ""),
                "merged": pr_data.get("merged", False),
                "author": {"login": pr_data.get("user", {}).get("login", "")},
                "baseRefName": pr_data.get("base", {}).get("ref", ""),
                "headRefName": pr_data.get("head", {}).get("ref", ""),
                "additions": pr_data.get("additions", 0),
                "deletions": pr_data.get("deletions", 0),
                "changedFiles": pr_data.get("changed_files", 0),
                "labels": [{"name": l["name"], "color": l.get("color", "")}
                           for l in pr_data.get("labels", [])],
                "comments": [
                    {
                        "body": c.get("body", "") or "",
                        "createdAt": c.get("created_at", ""),
                        "author": {"login": c.get("user", {}).get("login", "")},
                    }
                    for c in comments_data
                ],
                "comment_count": len(comments_data),
                "review_comments": [
                    {
                        "body": c.get("body", "") or "",
                        "path": c.get("path", ""),
                        "createdAt": c.get("created_at", ""),
                        "author": {"login": c.get("user", {}).get("login", "")},
                    }
                    for c in review_comments
                ],
                "files": [
                    {
                        "filename": f.get("filename", ""),
                        "status": f.get("status", ""),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                    }
                    for f in files_data[:20]  # cap at 20 files
                ],
                "source": "rest-api-fallback",
            }
    except Exception as exc:
        logger.debug("REST PR fallback failed: %s", exc)
        return None


# ── Markdown rendering ──────────────────────────────────────────

def _render_issue(data: dict) -> tuple[str, dict]:
    """Render an issue data dict into markdown + frontmatter metadata.

    Handles both GraphQL and REST response shapes.
    """
    parts = []

    # Title
    title = data.get("title", "Untitled Issue")
    parts.append(f"# {title}\n")

    # Metadata line
    state = data.get("state", "unknown")
    state_icon = "✅" if state == "closed" else "🟢"
    author = data.get("author", {}).get("login", "unknown")
    created = data.get("createdAt", "")[:10] if data.get("createdAt") else ""

    meta_parts = [f"{state_icon} **{state}** by **@{author}**"]
    if created:
        meta_parts.append(f"_{created}_")
    parts.append(" · ".join(meta_parts))

    # Labels
    labels = data.get("labels", [])
    if labels:
        label_text = " ".join(f"`{l.get('name', '')}`" for l in labels)
        parts.append(f"\n🏷️  {label_text}")

    parts.append("")

    # Body
    body = data.get("body", "") or ""
    if body:
        parts.append("---\n## Description\n")
        parts.append(body)
        parts.append("")

    # Comments
    comments = data.get("comments", [])
    if isinstance(comments, list) and comments:
        parts.append("---\n## Comments  ({count})".format(
            count=len(comments)
        ))
        parts.append("")
        for i, comment in enumerate(comments):
            c_author = comment.get("author", {}).get("login", "unknown")
            c_created = (comment.get("createdAt", "") or "")[:10]
            c_body = comment.get("body", "") or ""

            parts.append(f"### @{c_author}  _({c_created})_\n")
            if c_body:
                parts.append(c_body)
                parts.append("")

            # Nested replies (from GraphQL)
            replies_node = comment.get("replies")
            if replies_node and isinstance(replies_node, dict):
                replies = replies_node.get("nodes", [])
                for reply in replies:
                    r_author = reply.get("author", {}).get("login", "unknown")
                    r_created = (reply.get("createdAt", "") or "")[:10]
                    r_body = reply.get("body", "") or ""
                    parts.append(f"> **@{r_author}** _({r_created})_\n")
                    for line in r_body.strip().split("\n"):
                        parts.append(f"> {line}")
                    parts.append("")

    comment_count = data.get("comment_count", len(comments))
    metadata = {
        "source": "github-discussion-adapter",
        "discussion_type": DiscussionType.ISSUE,
        "title": title,
        "state": state,
        "author": author,
        "created": created,
        "comment_count": comment_count,
    }

    if labels:
        metadata["labels"] = [l.get("name", "") for l in labels]

    return "\n".join(parts).strip(), metadata


def _render_pull(data: dict) -> tuple[str, dict]:
    """Render a PR data dict into markdown + frontmatter metadata."""
    parts = []

    # Title
    title = data.get("title", "Untitled PR")
    parts.append(f"# {title}\n")

    # Metadata line
    state = data.get("state", "unknown")
    merged = data.get("merged", False)
    if state == "merged" or merged:
        state_icon = "✅"
        state_label = "merged"
    elif state == "closed":
        state_icon = "❌"
        state_label = "closed"
    else:
        state_icon = "🟢"
        state_label = "open"

    author = data.get("author", {}).get("login", "unknown")
    created = data.get("createdAt", "")[:10] if data.get("createdAt") else ""
    base = data.get("baseRefName", "")
    head = data.get("headRefName", "")

    meta_parts = [
        f"{state_icon} **{state_label}** by **@{author}**",
        f"`{base} ← {head}`",
    ]
    if created:
        meta_parts.append(f"_{created}_")
    parts.append(" · ".join(meta_parts))

    # Labels
    labels = data.get("labels", [])
    if labels:
        label_text = " ".join(f"`{l.get('name', '')}`" for l in labels)
        parts.append(f"\n🏷️  {label_text}")

    # Diff stats
    additions = data.get("additions", 0)
    deletions = data.get("deletions", 0)
    changed_files = data.get("changedFiles", 0)
    mergeable = data.get("mergeable", "")
    commit_count = data.get("commits", {}).get("totalCount", "?") if isinstance(data.get("commits"), dict) else "?"

    parts.append(f"\n📊 **+{additions} / -{deletions}** across {changed_files} files · {commit_count} commits")
    if mergeable and mergeable != "UNKNOWN":
        merge_status = "✅ mergeable" if mergeable == "MERGEABLE" else "⚠️ " + mergeable.lower()
        parts.append(f"🔀 {merge_status}")

    parts.append("")

    # Body
    body = data.get("body", "") or ""
    if body:
        parts.append("---\n## Description\n")
        parts.append(body)
        parts.append("")

    # Changed files
    files = data.get("files", [])
    if files:
        parts.append("---\n## Changed Files\n")
        parts.append("")
        for f in files:
            filename = f.get("filename", "")
            status = f.get("status", "")
            f_add = f.get("additions", 0)
            f_del = f.get("deletions", 0)
            status_icons = {
                "added": "✅", "modified": "📝", "removed": "🗑️",
                "renamed": "📎", "copied": "📋",
            }
            icon = status_icons.get(status, "📄")
            parts.append(f"- {icon} `{filename}` (+{f_add}/-{f_del})")
        parts.append("")

    # Reviews
    reviews_data = data.get("reviews", {})
    if isinstance(reviews_data, dict):
        reviews = reviews_data.get("nodes", [])
    else:
        reviews = []
    if reviews:
        parts.append("---\n## Reviews\n")
        parts.append("")
        for review in reviews:
            r_state = review.get("state", "")
            r_author = review.get("author", {}).get("login", "unknown")
            r_body = review.get("body", "") or ""
            r_created = (review.get("createdAt", "") or "")[:10]

            state_labels = {
                "APPROVED": "✅ Approved",
                "CHANGES_REQUESTED": "❌ Changes Requested",
                "COMMENTED": "💬 Commented",
                "DISMISSED": "🔇 Dismissed",
            }
            label = state_labels.get(r_state, f"📝 {r_state}")

            parts.append(f"### {label} by @{r_author}  _({r_created})_\n")
            if r_body:
                parts.append(r_body)
                parts.append("")

    # Comments
    comments = data.get("comments", [])
    if isinstance(comments, list) and comments:
        parts.append("---\n## Comments  ({count})".format(
            count=len(comments)
        ))
        parts.append("")
        for comment in comments:
            c_author = comment.get("author", {}).get("login", "unknown")
            c_created = (comment.get("createdAt", "") or "")[:10]
            c_body = comment.get("body", "") or ""

            parts.append(f"### @{c_author}  _({c_created})_\n")
            if c_body:
                parts.append(c_body)
                parts.append("")

    comment_count = data.get("comment_count", len(comments))
    metadata = {
        "source": "github-discussion-adapter",
        "discussion_type": DiscussionType.PULL,
        "title": title,
        "state": state_label if 'state_label' in dir() else state,
        "author": author,
        "created": created,
        "additions": additions,
        "deletions": deletions,
        "changed_files": changed_files,
        "comment_count": comment_count,
    }

    if merged:
        metadata["merged"] = True
        metadata["merged_at"] = (data.get("mergedAt", "") or "")[:10]

    if labels:
        metadata["labels"] = [l.get("name", "") for l in labels]

    return "\n".join(parts).strip(), metadata


# ── Adapter class ────────────────────────────────────────────────

@adapter
class GitHubDiscussionAdapter(SiteAdapter):
    """Extract issues and pull requests from GitHub URLs via GraphQL.

    Requires GITHUB_TOKEN with ``public_repo`` scope (for public repos)
    or ``repo`` scope (for private repos).  Falls through to the generic
    tier when no token is available.

    Fallback chain:
      1. GitHub GraphQL API — single query, rich structured data
      2. GitHub REST API — works without auth (60 req/hr), less rich
      3. Generic tier pipeline
    """

    name = "github-discussion"

    patterns = [
        re.compile(r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+/issues/\d+"),
        re.compile(r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+/pull/\d+"),
    ]

    # Slightly lower than the file adapter so file URLs are tried first
    priority = 190

    async def can_handle(self, url: str) -> bool:
        """Only handle if we have auth for GraphQL or REST."""
        return True  # REST fallback works without auth

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        if len(parts) < 4:
            raise AdapterError(f"Cannot parse GitHub URL: {url}")

        owner, repo, resource_type, number_str = parts[0], parts[1], parts[2], parts[3]

        try:
            number = int(number_str)
        except ValueError:
            raise AdapterError(f"Invalid issue/PR number: {number_str}")

        if resource_type == "issues":
            return await self._fetch_issue(url, owner, repo, number)
        elif resource_type == "pull":
            return await self._fetch_pull(url, owner, repo, number)
        else:
            raise AdapterError(f"Unknown resource type: {resource_type}")

    async def _fetch_issue(
        self, url: str, owner: str, repo: str, number: int
    ) -> AdapterResult:
        logger.info("GitHub discussion adapter: issue %s/%s#%d", owner, repo, number)

        # Tier 1: GraphQL (requires token, single query)
        if _check_auth():
            data = await _graphql_query(_ISSUE_QUERY, {
                "owner": owner, "repo": repo, "number": number,
            })
            if data and data.get("repository", {}).get("issue"):
                issue = data["repository"]["issue"]
                markdown, metadata = _render_issue(issue)
                metadata["source_api"] = "graphql"
                return AdapterResult(
                    success=True,
                    markdown=markdown,
                    metadata=metadata,
                    source="github-discussion-graphql",
                    url=url,
                )
            logger.debug("GraphQL issue query returned no data")
        else:
            logger.info("GitHub discussion adapter: no token, trying REST fallback")

        # Tier 2: REST API (works without auth)
        logger.info("GitHub discussion adapter: trying REST fallback for issue %s/%s#%d", owner, repo, number)
        rest_data = await _rest_issue_fallback(owner, repo, number)
        if rest_data:
            markdown, metadata = _render_issue(rest_data)
            metadata["source_api"] = "rest"
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="github-discussion-rest",
                url=url,
            )

        raise AdapterError(
            f"Could not extract issue {owner}/{repo}#{number}. "
            f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access, "
            f"or check that the repo/issue exists."
        )

    async def _fetch_pull(
        self, url: str, owner: str, repo: str, number: int
    ) -> AdapterResult:
        logger.info("GitHub discussion adapter: PR %s/%s#%d", owner, repo, number)

        # Tier 1: GraphQL (requires token, single query)
        if _check_auth():
            data = await _graphql_query(_PULL_QUERY, {
                "owner": owner, "repo": repo, "number": number,
            })
            if data and data.get("repository", {}).get("pullRequest"):
                pr = data["repository"]["pullRequest"]
                markdown, metadata = _render_pull(pr)
                metadata["source_api"] = "graphql"
                return AdapterResult(
                    success=True,
                    markdown=markdown,
                    metadata=metadata,
                    source="github-discussion-graphql",
                    url=url,
                )
            logger.debug("GraphQL PR query returned no data")
        else:
            logger.info("GitHub discussion adapter: no token, trying REST fallback")

        # Tier 2: REST API
        logger.info("GitHub discussion adapter: trying REST fallback for PR %s/%s#%d", owner, repo, number)
        rest_data = await _rest_pr_fallback(owner, repo, number)
        if rest_data:
            markdown, metadata = _render_pull(rest_data)
            metadata["source_api"] = "rest"
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="github-discussion-rest",
                url=url,
            )

        raise AdapterError(
            f"Could not extract PR {owner}/{repo}#{number}. "
            f"Set GITHUB_TOKEN env var with `public_repo` scope for GraphQL access, "
            f"or check that the repo/PR exists."
        )
