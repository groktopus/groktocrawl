"""
Bluesky adapter — extracts posts and threads via the AT Protocol public API.

Fallback chain:
  1. AT Protocol XRPC API — no auth required, fast
  2. Browser render + DOM extraction — last resort

API docs: https://docs.bsky.app/docs/category/http-reference
Public endpoint: https://public.api.bsky.app
"""

from __future__ import annotations

import contextlib
import logging
import re
from urllib.parse import urlparse

from ..source_http import (
    source_httpx_client,
    trusted_control_base_url,
    trusted_control_httpx_client,
)
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_BSKY_URL_PATTERNS = [
    re.compile(r"^https?://bsky\.app/profile/[^/]+/post/[^/]+"),
    re.compile(r"^https?://staging\.bsky\.app/profile/[^/]+/post/[^/]+"),
]

# ── Constants ────────────────────────────────────────────────────

PUBLIC_API_URL = "https://public.api.bsky.app"

# ── URL parsing ──────────────────────────────────────────────────


def _extract_handle_and_rkey(url: str) -> tuple[str, str] | None:
    """Extract ``(handle, rkey)`` from a Bluesky post URL.

    Accepts ``https://bsky.app/profile/{handle}/post/{rkey}``.
    Returns ``None`` if the URL path doesn't match.
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    # Expected: profile/<handle>/post/<rkey>
    if len(parts) >= 4 and parts[0] == "profile" and parts[2] == "post":
        return parts[1], parts[3]
    return None


# ── AT Protocol API helpers ──────────────────────────────────────


async def _resolve_did(handle: str) -> str | None:
    """Resolve a Bluesky handle (e.g. ``bsky.app``) to a DID.

    Calls ``com.atproto.identity.resolveHandle`` on the public API.
    Returns the DID string or ``None`` on failure.
    """
    url = f"{PUBLIC_API_URL}/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
    try:
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json().get("did")
            logger.debug("resolveHandle returned %d for %s", resp.status_code, handle)
    except Exception as exc:
        logger.debug("resolveHandle failed for %s: %s", handle, exc)
    return None


async def _fetch_post_thread(did: str, rkey: str) -> dict | None:
    """Fetch a Bluesky post thread via the public API.

    Calls ``app.bsky.feed.getPostThread`` with ``depth=1`` for
    immediate replies.  Returns the ``thread`` object from the
    response, or ``None`` on failure.
    """
    at_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
    url = f"{PUBLIC_API_URL}/xrpc/app.bsky.feed.getPostThread?uri={at_uri}&depth=1"
    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json().get("thread")
            logger.debug("getPostThread returned %d for %s", resp.status_code, at_uri)
    except Exception as exc:
        logger.debug("getPostThread failed for %s: %s", at_uri, exc)
    return None


def _format_text_with_facets(record: dict) -> str:
    """Convert Bluesky post text with richtext facets to markdown.

    Handles facet types:
    - ``app.bsky.richtext.facet#link`` → ``[text](uri)``
    - ``app.bsky.richtext.facet#mention`` → ``[@handle](at://did)``
    - ``app.bsky.richtext.facet#tag`` → ``#tag``

    Facet ``byteStart``/``byteEnd`` are UTF-8 byte offsets.  We
    convert them to Python string indexes by counting bytes.
    """
    text = record.get("text", "") or ""
    facets = record.get("facets")
    if not facets:
        return text

    def _byte_offset_to_char_index(s: str, byte_offset: int) -> int:
        """Convert a UTF-8 byte offset to a Python character index."""
        encoded = s.encode("utf-8")
        return len(encoded[:byte_offset].decode("utf-8", errors="replace"))

    # Sort facets by start position so we process left to right
    sorted_facets = sorted(facets, key=lambda f: f["index"]["byteStart"])

    result_parts: list[str] = []
    cursor = 0

    for facet in sorted_facets:
        idx = facet.get("index", {})
        start_byte = idx.get("byteStart", 0)
        end_byte = idx.get("byteEnd", 0)

        start = _byte_offset_to_char_index(text, start_byte)
        end = _byte_offset_to_char_index(text, end_byte)

        # Clamp to text bounds
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))

        # Any plain text before this facet
        if start > cursor:
            result_parts.append(text[cursor:start])

        # The raw text this facet covers
        facet_text = text[start:end]

        # Determine facet type and apply formatting
        features = facet.get("features", [])
        formatted = facet_text
        for feature in features:
            ftype = feature.get("$type", "")
            if "link" in ftype:
                uri = feature.get("uri", "")
                if uri:
                    formatted = f"[{facet_text}]({uri})"
            elif "mention" in ftype:
                did = feature.get("did", "")
                if did:
                    formatted = f"[{facet_text}](at://{did})"
            # Tags just render as plain text (no URL to link to)

        result_parts.append(formatted)
        cursor = end

    # Any remaining text after the last facet
    if cursor < len(text):
        result_parts.append(text[cursor:])

    return "".join(result_parts)


def _extract_post_text(post: dict) -> str:
    """Extract the text content from a Bluesky post record.

    Converts richtext facets (mentions, links, tags) to inline
    markdown where possible.
    """
    record = post.get("record", {})
    if isinstance(record, dict):
        return _format_text_with_facets(record)
    return str(record) if record else ""


def _format_timestamp(post: dict) -> str:
    """Extract the ISO-8601 timestamp from a post, with fallback."""
    record = post.get("record", {})
    if isinstance(record, dict):
        return record.get("createdAt", "")
    return ""


def _format_post_as_markdown(
    post: dict, *, is_root: bool = False, depth: int = 0
) -> str:
    """Format a single post (or reply) as markdown text.

    If ``is_root`` is ``True``, renders as a top-level post with
    heading.  Otherwise renders as a reply with indentation.
    """
    author = post.get("author", {})
    handle = author.get("handle", "unknown")
    display_name = author.get("displayName", handle)
    text = _extract_post_text(post)
    timestamp = _format_timestamp(post)

    prefix = "  " * depth
    lines = []

    if is_root:
        lines.append(f"# {display_name}")
        lines.append(f"**@{handle}** — {timestamp}")
        lines.append("")
        if text:
            lines.append(text)
            lines.append("")
    else:
        lines.append(f"{prefix}**@{display_name}** ({timestamp}):")
        if text:
            for paragraph in text.split("\n"):
                lines.append(f"{prefix}{paragraph}")
        lines.append("")

    return "\n".join(lines)


def _format_replies(replies: list, depth: int = 1) -> str:
    """Format a list of reply posts as markdown.

    Only renders ``depth=1`` (immediate replies, not nested).
    """
    if not replies:
        return ""
    lines = []
    for reply_wrapper in replies:
        reply_post = reply_wrapper.get("post", {})
        if reply_post:
            lines.append(_format_post_as_markdown(reply_post, depth=depth))
    return "\n".join(lines)


def _format_thread(thread: dict) -> tuple[str, dict]:
    """Convert a Bluesky thread API response to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    post = thread.get("post", {})
    author = post.get("author", {})
    post.get("record", {})

    # Root post text
    _extract_post_text(post)
    timestamp = _format_timestamp(post)

    # Counts come from the post view, not the record
    like_count = post.get("likeCount", 0)
    reply_count = post.get("replyCount", 0)
    repost_count = post.get("repostCount", 0)

    # Handle rich embeds
    embed = post.get("embed")
    embed_markdown = ""
    if embed:
        embed_type = embed.get("$type", "")
        if "external" in embed_type:
            ext = embed.get("external", {})
            embed_markdown = (
                f"\n[🔗 {ext.get('title', 'Link')}]({ext.get('uri', '')})\n"
                f"> {ext.get('description', '')}\n"
            )
        elif "images" in embed_type or "image" in embed_type:
            images = embed.get("images", [])
            if not images and "image" in embed:
                images = [embed["image"]]
            embed_markdown = (
                "\n"
                + "\n".join(
                    f"![{img.get('alt', 'Image')}]({img.get('image', {}).get('ref', {}).get('$link', '') or img.get('ref', '')})"
                    for img in images
                )
                + "\n"
                if images
                else ""
            )
        elif "record" in embed_type:
            quoted = embed.get("record", {})
            quoted_author = quoted.get("author", {})
            quoted_text = _extract_post_text(quoted)
            embed_markdown = (
                f"\n> **@{quoted_author.get('handle', 'unknown')}**\n> {quoted_text}\n"
            )

    # Build metadata
    metadata = {
        "author": author.get("displayName", author.get("handle", "")),
        "handle": author.get("handle", ""),
        "did": post.get("uri", "").split("/")[2] if post.get("uri") else "",
        "post_id": post.get("uri", "").split("/")[-1] if post.get("uri") else "",
        "timestamp": timestamp,
        "reply_count": reply_count,
        "like_count": like_count,
        "repost_count": repost_count,
        "source": "bluesky-adapter",
    }

    # Build markdown body
    body_parts = [_format_post_as_markdown(post, is_root=True)]

    if embed_markdown:
        body_parts.append(embed_markdown)

    # Replies
    replies = thread.get("replies", [])
    if replies:
        body_parts.append("---\n## Replies\n")
        body_parts.append(_format_replies(replies))

    markdown = "\n".join(body_parts).strip()
    return markdown, metadata


# ── Browser fallback ─────────────────────────────────────────────


async def _fetch_via_browser(url: str, ctx: AdapterContext) -> tuple[str, dict] | None:
    """Fallback: render Bluesky page in browser, extract text + metadata.

    Returns ``(markdown, metadata)`` or ``None``.
    """
    browser_svc_url = trusted_control_base_url(
        ctx.config.get("BROWSER_SVC_URL", "http://browser-svc:8012"), "browser"
    )
    session_id = None
    try:
        async with trusted_control_httpx_client(service="browser", timeout=30) as client:
            # Create session
            create_resp = await client.post(
                f"{browser_svc_url}/browsers",
                json={"ttl": 60},
            )
            if create_resp.status_code != 200:
                return None
            session_id = create_resp.json().get("id")
            if not session_id:
                return None

            # Navigate
            nav_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={"action": "navigate", "url": url, "timeout": 45000},
            )
            if not nav_resp.json().get("success"):
                return None

            # Extract page text
            text_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": "document.body.innerText",
                },
            )
            body_text = ""
            if text_resp.json().get("success"):
                body_text = (
                    text_resp.json().get("result", {}).get("script_result", "") or ""
                )

            # Extract metadata from DOM
            meta_resp = await client.post(
                f"{browser_svc_url}/browsers/{session_id}/execute",
                json={
                    "action": "executeScript",
                    "script": ("JSON.stringify({title: document.title,})"),
                },
            )
            metadata: dict = {"source": "bluesky-adapter"}
            if meta_resp.json().get("success"):
                import json

                raw = (
                    meta_resp.json().get("result", {}).get("script_result", "{}")
                    or "{}"
                )
                with contextlib.suppress(json.JSONDecodeError):
                    metadata.update(json.loads(raw))

            if not body_text:
                return None

            markdown = f"# {metadata.get('title', 'Bluesky Post')}\n\n{body_text}"
            return markdown, metadata

    except Exception as exc:
        logger.debug("Browser fallback failed for %s: %s", url, exc)
        return None
    finally:
        if session_id:
            try:
                async with trusted_control_httpx_client(service="browser", timeout=5) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception:
                pass


# ── Adapter class ────────────────────────────────────────────────


@adapter
class BlueskyAdapter(SiteAdapter):
    """Extract posts and threads from Bluesky URLs."""

    name = "bluesky"

    patterns = _BSKY_URL_PATTERNS

    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        # Parse URL
        result = _extract_handle_and_rkey(url)
        if not result:
            raise AdapterError(f"Could not parse Bluesky URL: {url}")
        handle, rkey = result
        logger.info("Bluesky adapter: handle=%s rkey=%s", handle, rkey)

        # Fallback 1: AT Protocol API
        try:
            did = await ctx.with_timeout(_resolve_did(handle), timeout=8)
            if did:
                thread = await ctx.with_timeout(
                    _fetch_post_thread(did, rkey), timeout=12
                )
                if thread:
                    markdown, metadata = _format_thread(thread)
                    logger.info(
                        "Bluesky adapter: API hit for %s (%d chars)",
                        url,
                        len(markdown),
                    )
                    return AdapterResult(
                        success=True,
                        markdown=markdown,
                        metadata=metadata,
                        source="bluesky-atproto",
                        url=url,
                    )
        except AdapterError:
            logger.debug("AT Protocol API timed out for %s", url)

        # Fallback 2: browser render
        logger.info("Bluesky adapter: trying browser fallback for %s", url)
        browser_result = await _fetch_via_browser(url, ctx)
        if browser_result:
            markdown, dom_meta = browser_result
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=dom_meta,
                source="bluesky-browser",
                url=url,
            )

        raise AdapterError(f"Could not extract content from Bluesky URL: {url}")
