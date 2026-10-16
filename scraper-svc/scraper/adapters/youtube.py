"""
YouTube adapter — extracts video transcripts and metadata.

Fallback chain:
  1. youtube_transcript_api — no API key required, fast
  2. Browser render + DOM extraction — last resort

Metadata sources:
  - oEmbed API (https://www.youtube.com/oembed?format=json) for title,
    author_name, author_url, thumbnail_url
  - Page HTML LD+JSON or meta description for video description text
  - Browser DOM parsing for view count, publish date (fallback)
"""

from __future__ import annotations

import logging
import random
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..source_http import (
    capture_egress_proxy_url,
    source_httpx_client,
    source_requests_session,
    trusted_control_base_url,
    trusted_control_httpx_client,
)
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)


class _YouTubeCooldownError(Exception):
    """Raised when YouTube is in a host-level cooldown window."""


class _YouTubeRequestGate:
    """Process-wide pacing and cooldown for the shared YouTube egress."""

    def __init__(self) -> None:
        import os

        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._cooldown_until = 0.0
        self.min_interval = float(os.getenv("YOUTUBE_MIN_INTERVAL", "10"))
        self.max_interval = float(os.getenv("YOUTUBE_MAX_INTERVAL", "20"))
        self.request_interval = float(os.getenv("YOUTUBE_REQUEST_INTERVAL", "0.75"))
        self.subtitle_interval = float(os.getenv("YOUTUBE_SUBTITLE_INTERVAL", "5"))
        self.cooldown_seconds = float(os.getenv("YOUTUBE_RATE_LIMIT_COOLDOWN", "3600"))

    def wait(self, interval: float | None = None, jitter: float | None = None) -> None:
        """Reserve a request slot, sleeping outside the mutex."""
        interval = self.request_interval if interval is None else interval
        jitter = interval if jitter is None else jitter
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                raise _YouTubeCooldownError
            target = max(now, self._next_allowed)
            self._next_allowed = target + interval + random.uniform(0, jitter)
        if target > now:
            time.sleep(target - now)

    def mark_rate_limited(self) -> None:
        with self._lock:
            self._cooldown_until = max(
                self._cooldown_until, time.monotonic() + self.cooldown_seconds
            )


_YOUTUBE_GATE = _YouTubeRequestGate()


def _is_youtube_rate_limit(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in ("429", "ipblocked", "requestblocked", "rate-limited")
    )


# ── URL pattern matching ─────────────────────────────────────────

# Matches standard watch URLs, short links, shorts, and embeds.
_YOUTUBE_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?youtube\.com/watch\?v=[\w-]{11}"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/v/[\w-]{11}"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/embed/[\w-]{11}"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/shorts/[\w-]{11}"),
    re.compile(r"^https?://youtu\.be/[\w-]{11}"),
    re.compile(r"^https?://m\.youtube\.com/watch\?v=[\w-]{11}"),
]

# ── Video ID extraction ──────────────────────────────────────────

_VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")


def _extract_video_id(url: str) -> str | None:
    """Extract the 11-character YouTube video ID from any known URL format."""
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        video_id = parsed.path.strip("/")
        return video_id if _VIDEO_ID_RE.match(video_id) else None

    # Standard watch / shorts / embed
    if parsed.hostname and "youtube.com" in parsed.hostname:
        # Try ?v= parameter first
        query = parse_qs(parsed.query)
        if "v" in query:
            video_id = query["v"][0]
            if _VIDEO_ID_RE.match(video_id):
                return video_id
        # Try path-based (shorts, embed, /v/)
        path_parts = parsed.path.strip("/").split("/")
        for part in path_parts:
            if _VIDEO_ID_RE.match(part):
                return part

    return None


# ── oEmbed metadata ──────────────────────────────────────────────


async def _fetch_oembed(video_id: str) -> dict:
    """Fetch video metadata from YouTube's oEmbed endpoint.

    Returns a dict with keys like ``title``, ``author_name``,
    ``author_url``, ``thumbnail_url``, etc.

    Raises ``AdapterError`` on failure.
    """

    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        import asyncio

        await asyncio.to_thread(_YOUTUBE_GATE.wait)
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(oembed_url)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("oEmbed returned %d for video %s", resp.status_code, video_id)
    except Exception as exc:
        logger.debug("oEmbed failed for video %s: %s", video_id, exc)
    return {}


# ── Video description via page HTML ────────────────────────────


async def _fetch_description(video_id: str) -> str | None:
    """Fetch the video description from the YouTube page HTML.

    Strategy:
    1. Parse ``"description":{"simpleText":"..."}`` from the page's
       embedded JSON data (contains the full description text)
    2. Fallback to ``<meta name="description">`` content attribute
       (truncated to ~168 chars, but always present)

    Uses a lightweight ``httpx.get()`` — no browser rendering needed.
    Returns the description text or ``None``.
    """

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        import asyncio

        await asyncio.to_thread(_YOUTUBE_GATE.wait)
        async with source_httpx_client(timeout=10) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                logger.debug(
                    "Description fetch returned %d for %s", resp.status_code, video_id
                )
                return None

            html = resp.text

            # Strategy 1: description.simpleText from embedded JSON data
            # Format: "description":{"simpleText":"..."}
            for marker in (
                '"description":{"simpleText":"',
                '"description": {"simpleText": "',
            ):
                idx = html.find(marker)
                if idx >= 0:
                    start = idx + len(marker)
                    result = []
                    i = start
                    while i < len(html) and i < start + 20000:
                        ch = html[i]
                        if ch == "\\":
                            if i + 1 < len(html):
                                nxt = html[i + 1]
                                if nxt == "n":
                                    result.append("\n")
                                elif nxt == '"':
                                    result.append('"')
                                elif nxt == "\\":
                                    result.append("\\")
                                elif nxt == "r":
                                    pass  # skip \r
                                elif nxt == "t":
                                    result.append("\t")
                                elif nxt == "/":
                                    result.append("/")
                                elif nxt == "u":
                                    # Unicode escape — skip 4 hex digits
                                    i += 5
                                    continue
                                else:
                                    result.append(nxt)
                                i += 2
                            else:
                                break
                        elif ch == '"':
                            break
                        else:
                            result.append(ch)
                            i += 1

                    desc = "".join(result).strip()
                    if desc:
                        logger.debug(
                            "Description extracted via simpleText for %s (%d chars)",
                            video_id,
                            len(desc),
                        )
                        return desc

            # Strategy 2: meta description tag (truncated fallback)
            meta_match = re.search(
                r'<meta\s+[^>]*name="description"[^>]*content="([^"]*)"',
                html,
                re.IGNORECASE,
            )
            if meta_match:
                desc = meta_match.group(1)
                desc = (
                    desc.replace("&#39;", "'")
                    .replace("&amp;", "&")
                    .replace("&quot;", '"')
                )
                if desc:
                    logger.debug(
                        "Description extracted via meta tag for %s (%d chars)",
                        video_id,
                        len(desc),
                    )
                    return desc

    except Exception as exc:
        logger.debug("Description fetch failed for %s: %s", video_id, exc)

    return None


def _description_to_markdown(desc: str) -> str:
    """Convert a YouTube video description to basic markdown.

    - Preserves paragraph breaks (double newlines)
    - Wraps bare URLs in ``<>`` autolink syntax
    - Passes through all other text unchanged
    """
    # URL pattern: http:// or https:// followed by non-whitespace
    url_re = re.compile(r"https?://[^\s<>]+")

    def _autolink(m):
        url = m.group(0)
        # Strip trailing punctuation that's not part of the URL
        url = url.rstrip(".,;:!?)]}>")
        return f"<{url}>"

    # Process each paragraph
    paragraphs = desc.split("\n\n")
    processed = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Autolink URLs in this paragraph
        para = url_re.sub(_autolink, para)
        processed.append(para)

    return "\n\n".join(processed)


# ── Transcript via youtube_transcript_api ────────────────────────


@dataclass
class _TranscriptFetch:
    """Transcript text plus the acquisition provenance needed downstream."""

    text: str
    language: str
    translated_to: str | None = None


async def _fetch_transcript(video_id: str) -> _TranscriptFetch | None:
    """Fetch the video transcript via ``youtube_transcript_api``.

    Runs the sync API in a thread to avoid blocking the event loop.

    Prefer native English, then explicitly translate a translatable native
    track.  ``fetch(..., languages=["en"])`` does not search translated tracks.
    """
    import asyncio

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        def _get_transcript():
            http_client = source_requests_session()
            try:
                api = (
                    YouTubeTranscriptApi(http_client=http_client)
                    if http_client is not None
                    else YouTubeTranscriptApi()
                )
                _YOUTUBE_GATE.wait()
                transcripts = list(api.list(video_id))

                # Keep native English ahead of translated output, including when
                # the native track is auto-generated.
                english = [t for t in transcripts if t.language_code == "en"]
                candidates = english + [
                    t
                    for t in transcripts
                    if t.language_code != "en" and t.is_translatable
                ]
                for transcript in candidates:
                    translated_to = None
                    selected = transcript
                    if transcript.language_code != "en":
                        try:
                            selected = transcript.translate("en")
                            translated_to = "en"
                        except Exception as exc:
                            logger.debug(
                                "Transcript translation failed for %s (%s -> en): %s",
                                video_id,
                                transcript.language_code,
                                exc,
                            )
                            continue

                    try:
                        _YOUTUBE_GATE.wait(_YOUTUBE_GATE.subtitle_interval)
                        fetched = selected.fetch()
                        text = " ".join(item.text for item in fetched).strip()
                    except _YouTubeCooldownError:
                        raise
                    except Exception as exc:
                        if _is_youtube_rate_limit(exc):
                            _YOUTUBE_GATE.mark_rate_limited()
                            raise _YouTubeCooldownError from exc
                        logger.debug(
                            "Transcript fetch failed for %s (%s): %s",
                            video_id,
                            transcript.language_code,
                            exc,
                        )
                        continue
                    if text:
                        return _TranscriptFetch(
                            text=text,
                            language=transcript.language_code,
                            translated_to=translated_to,
                        )
                return None
            finally:
                if http_client is not None:
                    http_client.close()

        transcript = await asyncio.to_thread(_get_transcript)
        if transcript:
            return transcript
    except ImportError:
        logger.debug("youtube_transcript_api not installed")
    except _YouTubeCooldownError:
        logger.info("YouTube acquisition is in cooldown; deferring %s", video_id)
        return None
    except Exception as exc:
        if _is_youtube_rate_limit(exc):
            _YOUTUBE_GATE.mark_rate_limited()
            logger.info("YouTube rate limit encountered; deferring %s", video_id)
            return None
        logger.debug("youtube_transcript_api failed for %s: %s", video_id, exc)
    return await _fetch_transcript_via_ytdlp(video_id)


async def _fetch_transcript_via_ytdlp(video_id: str) -> _TranscriptFetch | None:
    """Fetch English auto-captions when the transcript API omits translation metadata."""
    import asyncio

    try:
        import yt_dlp
    except ImportError:
        logger.debug("yt-dlp not installed")
        return None

    def _download():
        with tempfile.TemporaryDirectory(prefix="groktocrawl-youtube-") as tmp:
            output = str(Path(tmp) / "transcript.%(ext)s")
            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writeautomaticsub": True,
                "sleep_interval_requests": 0.75,
                "sleep_interval_subtitles": _YOUTUBE_GATE.subtitle_interval,
                "sleep_interval": _YOUTUBE_GATE.min_interval,
                "max_sleep_interval": _YOUTUBE_GATE.max_interval,
                "retries": 1,
                "extractor_retries": 1,
                "subtitleslangs": ["en"],
                "subtitlesformat": "vtt",
                "outtmpl": output,
            }
            proxy_url = capture_egress_proxy_url()
            if proxy_url is not None:
                # Explicit yt-dlp proxy configuration suppresses its fallback
                # to process environment proxies and routes every request here.
                options["proxy"] = proxy_url
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=True
                )
            subtitle = next(Path(tmp).glob("transcript*.vtt"), None)
            if subtitle is None:
                return None
            text = _vtt_to_text(subtitle.read_text(encoding="utf-8"))
            if not text:
                return None
            source_language = "unknown"
            for formats in info.get("automatic_captions", {}).values():
                for item in formats:
                    query = parse_qs(urlparse(item.get("url", "")).query)
                    if query.get("tlang") == ["en"] and query.get("lang"):
                        source_language = query["lang"][0]
                        break
                if source_language != "unknown":
                    break
            return _TranscriptFetch(text, source_language, "en")

    try:
        await asyncio.to_thread(
            _YOUTUBE_GATE.wait,
            random.uniform(_YOUTUBE_GATE.min_interval, _YOUTUBE_GATE.max_interval),
            0,
        )
        return await asyncio.to_thread(_download)
    except Exception as exc:
        logger.debug("yt-dlp transcript failed for %s: %s", video_id, exc)
        return None


def _vtt_to_text(content: str) -> str:
    """Reduce a WebVTT caption file to deduplicated plain text."""
    lines = []
    previous = None
    for line in content.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != previous:
            lines.append(line)
            previous = line
    return " ".join(lines).strip()


# ── Browser fallback ─────────────────────────────────────────────


async def _fetch_via_browser(
    url: str, video_id: str, ctx: AdapterContext
) -> tuple[str, dict] | None:
    """Fallback: render YouTube page in browser, extract text + metadata.

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
                    "script": (
                        "document.querySelector('#description-inline-expander') "
                        "?.textContent?.trim() || document.body.innerText"
                    ),
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
                    "script": (
                        "JSON.stringify({"
                        "title: document.title,"
                        "channel: document.querySelector('#owner #channel-name')?.textContent?.trim() || '',"
                        "views: document.querySelector('.view-count')?.textContent?.trim() || '',"
                        "})"
                    ),
                },
            )
            metadata: dict = {"video_id": video_id}
            if meta_resp.json().get("success"):
                raw = (
                    meta_resp.json().get("result", {}).get("script_result", "{}")
                    or "{}"
                )
                import json

                try:
                    dom_meta = json.loads(raw)
                    metadata["title"] = dom_meta.get("title", "")
                    metadata["channel"] = dom_meta.get("channel", "")
                    if dom_meta.get("views"):
                        metadata["views"] = dom_meta["views"]
                except json.JSONDecodeError:
                    pass

            if not body_text:
                return None

            markdown = f"# {metadata.get('title', 'YouTube Video')}\n\n{body_text}"
            return markdown, metadata

    except Exception as exc:
        logger.debug("Browser fallback failed for %s: %s", url, exc)
        return None
    finally:
        if session_id:
            try:
                async with trusted_control_httpx_client(service="browser", timeout=5) as c:
                    await c.delete(f"{browser_svc_url}/browsers/{session_id}")
            except Exception as e:
                logger.debug("Session cleanup failed for %s: %s", url, e)


# ── Adapter class ────────────────────────────────────────────────


@adapter
class YouTubeAdapter(SiteAdapter):
    """Extract transcripts and metadata from YouTube video URLs."""

    name = "youtube"

    patterns = _YOUTUBE_URL_PATTERNS

    # YouTube adapter should be checked before generic site-wide adapters
    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        video_id = _extract_video_id(url)
        if not video_id:
            raise AdapterError(f"Could not extract video ID from {url}")

        logger.info("YouTube adapter: video_id=%s", video_id)

        # Fetch metadata from oEmbed (fast, lightweight)
        oembed = {}
        try:
            oembed = await ctx.with_timeout(_fetch_oembed(video_id), timeout=8)
        except AdapterError:
            logger.debug("oEmbed timed out for %s", video_id)

        # Build metadata dict
        metadata: dict = {
            "video_id": video_id,
            "title": oembed.get("title", ""),
            "channel": oembed.get("author_name", ""),
            "channel_url": oembed.get("author_url", ""),
            "thumbnail_url": oembed.get("thumbnail_url", ""),
            "source": "youtube-adapter",
        }

        # Fetch transcript + description in parallel
        import asyncio

        transcript: _TranscriptFetch | None = None
        description = None
        try:
            t_result: object
            d_result: object
            t_result, d_result = await asyncio.gather(
                ctx.with_timeout(_fetch_transcript(video_id), timeout=12),
                ctx.with_timeout(_fetch_description(video_id), timeout=10),
                return_exceptions=True,
            )
            if isinstance(t_result, _TranscriptFetch) and t_result.text:
                transcript = t_result
            elif isinstance(t_result, AdapterError):
                logger.debug("Transcript fetch failed: %s", t_result)

            if isinstance(d_result, str) and d_result:
                description = d_result
            elif isinstance(d_result, AdapterError):
                logger.debug("Description fetch failed: %s", d_result)
        except Exception as exc:
            logger.debug("Parallel fetch error for %s: %s", video_id, exc)

        if transcript:
            metadata["transcript_language"] = transcript.language
            if transcript.translated_to:
                metadata["transcript_translated_to"] = transcript.translated_to
            logger.info(
                "YouTube adapter: transcript hit for %s (%d chars)",
                video_id,
                len(transcript.text),
            )
            title = oembed.get("title", "YouTube Video")
            author = oembed.get("author_name", "")
            markdown = f"# {title}\n\n**Channel:** {author}\n\n"
            # Insert description section if available
            if description:
                desc_md = _description_to_markdown(description)
                markdown += f"---\n\n## Description\n\n{desc_md}\n\n"
            markdown += f"---\n\n## Transcript\n\n{transcript.text}"
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="youtube-transcript-api",
                url=url,
            )

        # Fallback 2: browser render
        logger.info("YouTube adapter: trying browser fallback for %s", video_id)
        browser_result = await _fetch_via_browser(url, video_id, ctx)
        if browser_result:
            markdown, dom_meta = browser_result
            metadata.update(dom_meta)
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="youtube-browser",
                url=url,
            )

        raise AdapterError(f"Could not extract content from YouTube video {video_id}")
