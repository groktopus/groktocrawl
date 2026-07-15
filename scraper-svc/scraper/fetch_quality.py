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

from .barrier import (
    BarrierInfo,  # noqa: F401
    _classify_barrier,  # noqa: F401
    _is_bot_challenge,  # noqa: F401
    _is_substack_redirect,  # noqa: F401
)
from .extract import assess_quality
from .metadata import extract_all_metadata
from .settings import load_settings

logger = logging.getLogger(__name__)

_settings = load_settings()
QA_MIN_QUALITY_THRESHOLD = _settings.qa_min_quality_threshold

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


def _structural_text_extraction(html: str) -> str:
    """Extract visible text from HTML using BeautifulSoup.

    Extracts page title, meta description, and body text, stripping
    non-content elements. Used as fallback when readability-lxml
    produces little or no output (common for SPA-heavy sites where
    the non-JS HTML shell lacks article-like structure).

    Returns text capped at 10,000 chars.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    parts: list[str] = []

    # Page title
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        parts.append(f"# {title_tag.get_text(strip=True)}")

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        content: str = str(meta_desc.get("content", "")).strip()
        if content:
            parts.append(content)

    # Body text — strip non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)

    if body_text:
        parts.append(body_text)

    result = "\n\n".join(parts)
    return result[:10000]


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown using readability + markdownify.

    Falls back to structural BeautifulSoup text extraction when
    readability produces little or no output (common for SPA-heavy
    sites where the non-JS HTML shell lacks article-like structure).
    """
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        # Clean up readability's artifacts
        markdown = md(summary, heading_style="ATX", strip=["script", "style"])
        # Collapse multiple blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        result = markdown.strip()

        # Structural fallback: when readability produces little or no output,
        # extract visible text nodes from the full HTML. This handles sites
        # where FlareSolverr returns real HTML but readability-lxml finds no
        # article-like content (SPA shells, torrent indexes, etc.).
        if not result or len(result) < 50:
            logger.debug(
                "Readability produced %d chars, falling back to structural extraction",
                len(result),
            )
            return _structural_text_extraction(html)

        return result
    except Exception as e:
        logger.error("HTML-to-markdown conversion failed: %s", e)
        # Fallback: try BeautifulSoup for text extraction
        try:
            return _structural_text_extraction(html)
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
