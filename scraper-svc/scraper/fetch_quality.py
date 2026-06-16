"""Content quality assessment and barrier detection for scrape results.

Quality assessment (ADR-0016): post-extraction scoring that determines
whether a scraped page contains substantive content worth returning to
the caller. Consumers set their own tolerance threshold.

Barrier detection (ADR-0015): multi-signal classification of bot challenge
pages (Cloudflare, DDoS-Guard, CAPTCHAs) and Substack redirect frames.

HTML-to-markdown conversion: readability + markdownify pipeline used by
all tiers that produce raw HTML.
"""

import logging
import re
from dataclasses import dataclass

from .extract import assess_quality
from .metadata import extract_all_metadata
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
QA_MIN_QUALITY_THRESHOLD = _settings.qa_min_quality_threshold

# ── Bot challenge detection (title/URL level) ──────────────────
CLOUDFLARE_INDICATORS = [
    "Just a moment",
    "Checking your browser",
    "DDoS protection by",
    "cf-browser-verification",
    "challenge-platform",
]

DDOS_GUARD_INDICATORS = [
    "DDoS-Guard",
    "DDOS-GUARD",
    "ddos-guard",
    "Checking your browser before accessing",
    ".well-known/ddos-guard",
]

# ── Substack session/channel frame redirect detection ──────────
SUBSTACK_REDIRECT_PATTERNS = [
    "substack.com/session-attribution-frame",
    "substack.com/channel-frame",
    "substack.com/iframe",
    "googletagmanager.com/ns.html",
]

# ── Bot challenge and redirect detection (title/URL level) ─────


def _is_bot_challenge(title: str, url: str) -> bool:
    """Check if the page title or URL indicates a bot challenge page.

    Mirrors browser-svc's _is_bot_challenge() logic.
    """
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "cf_chl" in url.lower() or "challenge-platform" in url.lower():
        return True
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    return "ddos-guard" in url.lower() or "/.well-known/ddos-guard" in url.lower()


def _is_substack_redirect(url: str) -> bool:
    """Check if the URL indicates a Substack session/channel frame redirect."""
    return any(pattern in url.lower() for pattern in SUBSTACK_REDIRECT_PATTERNS)


# ── Barrier classification (replaces _looks_suspicious) ──────────


@dataclass
class BarrierInfo:
    """Structured result of barrier classification on a scraped page."""

    detected: bool
    barrier_type: (
        str | None
    )  # "cloudflare", "ddos-guard", "captcha", "rate-limit", "substack-redirect", "empty", "suspicious", None
    confidence: float
    detail: str = ""
    title: str = ""


def _classify_barrier(
    title: str, url: str, content: str, html: str | None = None
) -> BarrierInfo:
    """Classify whether a scraped page is a barrier/challenge page.

    Replaces the old boolean _looks_suspicious() with structured,
    multi-signal classification. Returns a BarrierInfo dataclass
    with detected flag, barrier type, confidence score, and detail.

    Confidence is derived from the number of distinct matched signals:
      1 signal  → 0.70
      2 signals → 0.85
      3+ signals → 0.95
    """
    if not content and not html:
        return BarrierInfo(
            detected=True,
            barrier_type="empty",
            confidence=0.95,
            detail="No content returned",
            title=title,
        )

    signals: list[str] = []
    content_lower = content.lower() if content else ""
    title_lower = title.lower() if title else ""
    url_lower = url.lower() if url else ""
    html_lower = html.lower() if html else ""

    # ── Signal: Empty content ─────────────────────────────────
    if len(content) < 100:
        signals.append("empty")

    # ── Signal: Title-based Cloudflare detection ──────────────
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title_lower:
            signals.append("cloudflare-title")
            break

    # ── Signal: Explicit title match ──────────────────────────
    if (
        "attention required" in title_lower or "403 forbidden" in title_lower
    ) and "cloudflare" not in signals:
        signals.append("cloudflare-title")

    # ── Signal: URL-based Cloudflare detection ────────────────
    if "cf_chl" in url_lower or "challenge-platform" in url_lower:
        signals.append("cloudflare-url")

    # ── Signal: DDoS-Guard title detection ────────────────────
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title_lower:
            signals.append("ddos-guard-title")
            break

    # ── Signal: DDoS-Guard URL detection ──────────────────────
    if "ddos-guard" in url_lower or "/.well-known/ddos-guard" in url_lower:
        signals.append("ddos-guard-url")

    # ── Signal: Captcha detection in content ──────────────────
    if "hcaptcha" in content_lower or "recaptcha" in content_lower:
        signals.append("captcha")

    # ── Signal: Rate-limit detection in content ───────────────
    if "rate limit" in content_lower or "too many requests" in content_lower:
        signals.append("rate-limit")

    # ── Signal: Substack redirect ─────────────────────────────
    for pattern in SUBSTACK_REDIRECT_PATTERNS:
        if pattern in url_lower or (html and pattern in html_lower):
            signals.append("substack-redirect")
            break

    # ── Signal: Indicator words in content (fallback) ─────────
    if not signals:
        for indicator in (
            CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS + SUBSTACK_REDIRECT_PATTERNS
        ):
            if indicator.lower() in content_lower:
                signals.append("content-match")
                break

    # ── Confidence scoring ────────────────────────────────────
    signal_count = len(set(signals))
    if signal_count == 0:
        return BarrierInfo(
            detected=False,
            barrier_type=None,
            confidence=0.0,
            detail="No barrier signals detected",
            title=title,
        )

    confidence = min(0.50 + (signal_count * 0.20), 0.95)

    # ── Determine the primary barrier type ────────────────────
    barrier_type: str | None = None
    for keyword, btype in [
        ("cloudflare", "cloudflare"),
        ("ddos-guard", "ddos-guard"),
        ("captcha", "captcha"),
        ("rate-limit", "rate-limit"),
        ("substack-redirect", "substack-redirect"),
        ("empty", "empty"),
        ("content-match", "suspicious"),
    ]:
        if any(keyword in s for s in signals):
            barrier_type = btype
            break

    detail_parts = []
    for s in sorted(set(signals)):
        detail_parts.append(s)
    detail = f"Matched signals: {', '.join(detail_parts)}"

    return BarrierInfo(
        detected=True,
        barrier_type=barrier_type,
        confidence=confidence,
        detail=detail,
        title=title,
    )


# ── Embedded content detection ─────────────────────────────────
# Extensions and domain patterns that suggest an iframe/embed points
# to downloadable document content rather than another web page.
EMBEDDED_CONTENT_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
}
EMBEDDED_CONTENT_DOMAINS = {
    "sci-hub",
    "sci.bban",
    "docdrop",
    "academia",
    "researchgate",
    "arxiv.org",
    "cdn.",
}


def _has_embedded_content(html: str) -> bool:
    """Check if page HTML contains iframe/embed/object pointing to document content.

    Uses lightweight string matching — no HTML parser needed.
    Returns True if the page appears to be a portal to document content elsewhere.
    """
    if not html:
        return False
    html_lower = html.lower()
    # Quick reject: no iframe, embed, or object tags at all
    if not any(tag in html_lower for tag in ("<iframe", "<embed", "<object")):
        return False
    # Check for document extensions in src/data attributes
    for ext in EMBEDDED_CONTENT_EXTENSIONS:
        if ext in html_lower:
            return True
    # Check for known document-serving domains
    for domain in EMBEDDED_CONTENT_DOMAINS:
        if domain in html_lower:
            return True
    # Check for common document URL patterns
    return "/pdf/" in html_lower or "/download/" in html_lower


def _looks_like_markdown(text: str) -> bool:
    """Heuristic: does the response look like markdown vs HTML?"""
    if not text:
        return False
    # If the first non-whitespace character isn't '<', it's probably not HTML
    stripped = text.strip()
    if not stripped:
        return False
    # Check for markdown indicators: headings, lists, code fences, links
    md_indicators = 0
    for line in stripped[:2000].split("\n"):
        line = line.strip()
        if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            md_indicators += 1
        if line.startswith("- ") or line.startswith("* "):
            md_indicators += 1
        if line.startswith("```"):
            md_indicators += 1
        if re.match(r"^\[.+\]\(.+\)", line):
            md_indicators += 1
    return md_indicators >= 3


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown using readability + markdownify."""
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        # Clean up readability's artifacts
        markdown = md(summary, heading_style="ATX", strip=["script", "style"])
        # Collapse multiple blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()
    except Exception as e:
        logger.error("HTML-to-markdown conversion failed: %s", e)
        # Fallback: try BeautifulSoup for text extraction
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            # Remove script/style
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:10000]  # Limit to 10K chars as fallback
        except Exception:
            return html[:5000]  # Last resort raw truncation


def _add_quality(result: dict, html: str = "", title: str = "") -> dict:
    """Assess content quality and add quality metadata to a scrape result dict.

    Lightweight post-extraction quality check — runs after each successful tier.
    Quality score is non-blocking; consumers set their own tolerance.
    """
    markdown = result.get("markdown", "")
    url = result.get("url", "")
    quality = assess_quality(markdown, html=html, url=url, title=title)
    result["quality"] = quality
    return result


def _enrich_with_metadata(result: dict, html: str = "") -> dict:
    """Extract structured metadata (JSON-LD, OG, Twitter, meta) from raw HTML.

    Pure parsing — no additional fetches. Runs after each tier that produces
    raw HTML. Results without available HTML get empty metadata fields.

    Metadata is best-effort: JSON-LD may be absent, OG tags may be minimal.
    Consumers should treat all fields as optional.
    """
    if not html and not result.get("raw_html_start"):
        result["metadata"] = {"json_ld": [], "og": {}, "twitter": {}, "meta": {}}
        return result

    source_html = html or result.get("raw_html_start", "")
    metadata = extract_all_metadata(source_html)

    # If the full HTML is not available, raw_html_start may be truncated.
    # That's fine — JSON-LD blocks and meta tags are usually in <head>.
    result["metadata"] = metadata
    return result


def _quality_acceptable(result: dict) -> bool:
    """Check if a scrape result's quality is above the degradation threshold.

    Results without a quality field (e.g., barrier detections) are returned
    as-is without degradation.
    """
    quality = result.get("quality")
    if quality is None:
        return True  # No quality assessment available — return as-is
    score = quality.get("score", 1.0)
    return score >= QA_MIN_QUALITY_THRESHOLD
