"""Content quality assessment and barrier detection for scrape results.

Quality assessment (ADR-0016): post-extraction scoring that determines
whether a scraped page contains substantive content worth returning to
the caller. Consumers set their own tolerance threshold.

Barrier detection (ADR-0015): multi-signal classification of bot challenge
pages (Cloudflare, DDoS-Guard, CAPTCHAs) and Substack redirect frames.

HTML-to-markdown conversion: readability + markdownify pipeline used by
all tiers that produce raw HTML.
"""

import hashlib
import logging
import re

from .barrier import (
    BarrierInfo,  # noqa: F401
    _classify_barrier,
    _is_bot_challenge,  # noqa: F401
    _is_substack_redirect,  # noqa: F401
)
from .extract import (
    MIN_LOW_YIELD_SOURCE_CHARS,  # noqa: F401  (re-exported; see extract.py)
    VOLUME_YIELD_RATIO_FLOOR,
    _missing_repeated_content_blocks,
    assess_quality,
    is_low_yield_text,
)
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

    # Strip head metadata entirely (title/meta were already captured above)
    # so the body dump cannot duplicate them, then drop chrome elements.
    if soup.head is not None:
        soup.head.decompose()
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)

    if body_text:
        parts.append(body_text)

    result = "\n\n".join(parts)
    return result[:10000]


def _is_low_yield(markdown: str, html: str | None) -> bool:
    """Detect anomalously low readable yield relative to the source size.

    Thin wrapper over the shared ``extract.is_low_yield_text`` predicate so
    recovery and quality assessment agree on what counts as thin output.
    """
    return bool(html) and is_low_yield_text(markdown, len(html or ""))


_BLOCK_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "table",
    "blockquote",
    "pre",
}


def _unwrap_anchor_wrapped_cards(soup) -> None:
    """Unwrap ``<a>`` elements that wrap block-level content in place.

    Card-style pages wrap each card's ``<h3>``/``<p>`` inside its parent
    anchor, which zeroes readability's link-density-weighted scores. The
    wrapper is replaced by the block content itself and the href survives
    as a markdown link attached to the first block's text (typically the
    card title), so ``[Title](href)`` is preserved end-to-end. Inline-only
    anchors (normal links inside paragraphs) are left untouched.
    """
    for anchor in list(soup.find_all("a")):
        blocks = [c for c in anchor.find_all(_BLOCK_TAGS) if c is not None]
        if not blocks:
            continue  # inline link — keep it
        href = anchor.get("href")
        # Preserve the href as a markdown-style link on the title text.
        # ] ( ) are backslash-escaped in both parts so titles like
        # "Vector [core] (2024)" and parens-bearing hrefs still render as
        # valid links instead of terminating the link syntax early.
        if href:
            first_block = blocks[0]
            target = first_block.find(["h1", "h2", "h3", "h4", "h5", "h6"]) or (
                first_block.find("p")
            )
            if target is None:
                target = first_block
            link_text = re.sub(r"([\[\]()])", r"\\\1", target.get_text(strip=True))
            link_href = re.sub(r"([()])", r"\\\1", str(href))
            link = soup.new_tag("span")
            link.string = f"[{link_text}]({link_href})"
            target.clear()
            target.append(link)
        anchor.unwrap()


def _full_page_markdown(html: str) -> str:
    """Render an entire page to markdown with chrome stripped (uncapped).

    Recovery path for low-yield readability results: unlike
    ``_structural_text_extraction`` this keeps document structure and has
    no output cap, so large card-directory bodies survive intact.
    """
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    # Head metadata (title/meta/link) is chrome for recovery purposes;
    # dropping it also keeps <title> text from leaking into the markdown
    # and colliding with the title heading consumers may prepend.
    if soup.head is not None:
        soup.head.decompose()
    # Div-based chrome survives the semantic strip above; drop the common
    # class-based variants so recovered pages stay clean.
    for selector in ("aside", ".sidebar", ".site-footer", ".footer"):
        for tag in soup.select(selector):
            tag.decompose()
    _unwrap_anchor_wrapped_cards(soup)
    markdown = md(str(soup), heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown using readability + markdownify.

    Falls back to structural BeautifulSoup text extraction when
    readability produces little or no output (common for SPA-heavy
    sites where the non-JS HTML shell lacks article-like structure).

    When readability succeeds but yields anomalously little text relative
    to the source size (card grids whose every card lives under a parent
    anchor score nothing and are silently dropped), recovers the full body
    via an uncapped full-page conversion with chrome stripped (#587).
    """
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        summary_soup = BeautifulSoup(summary, "html.parser")
        for tag in summary_soup(["script", "style"]):
            tag.decompose()
        # Readability can retain site-level navigation in its fragment. Remove
        # only chrome elements that are direct children of Readability's root
        # body; nested div-based article content may contain its own
        # navigation, headers, or footers carrying article metadata.
        fragment_root = summary_soup.find("body", id="readabilityBody")
        if fragment_root is None:
            fragment_root = summary_soup.find("body")
        for tag in summary_soup.find_all(["nav", "footer", "header"]):
            if fragment_root is not None and tag.parent is fragment_root:
                tag.decompose()
        # The summary is Readability's selected content fragment. Keep its
        # structural tags because headers and footers may be article metadata;
        # page-level chrome is filtered above before markdown conversion.
        # Clean up readability's artifacts
        markdown = md(str(summary_soup), heading_style="ATX", strip=["script", "style"])
        # Collapse multiple blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        result = markdown.strip()

        # Low-yield recovery: when readability selects only a small fragment
        # of a large page (e.g. an intro container while a sibling card grid
        # scores zero on link density), re-extract the whole page uncapped.
        if _is_low_yield(result, html):
            recovered = _full_page_markdown(html)
            # Only keep the recovery when it meaningfully beats the
            # readability fragment. A complete short article on a large
            # source gains almost nothing from the full-page conversion,
            # which would otherwise merge sidebars/div-footers into the
            # output; the #587 card-grid case grows ~16x (758 -> 12,472
            # chars) so it still recovers. An empty recovery (nothing
            # survives chrome-stripping) keeps the original fragment too,
            # letting the structural fallback handle SPA shells below.
            if recovered and len(recovered) >= len(result) * 2:
                logger.info(
                    "Low-yield recovery: %d chars from %d-char source -> %d chars",
                    len(result),
                    len(html),
                    len(recovered),
                )
                return recovered
            logger.debug(
                "Low-yield candidate kept as-is: recovery gained too little "
                "(%d -> %d chars); fragment looks complete or unrecoverable",
                len(result),
                len(recovered),
            )

        # Readability can omit individual cards from a repeated content list
        # without producing low enough output volume to trigger the general
        # recovery gate. Recover only when the missing sibling blocks are
        # substantial page content and the filtered full-page conversion
        # restores all of them.
        missing_blocks = _missing_repeated_content_blocks(result, html)
        if missing_blocks:
            recovered = _full_page_markdown(html)
            if recovered and not _missing_repeated_content_blocks(recovered, html):
                logger.info(
                    "Repeated-content recovery restored %d omitted blocks",
                    len(missing_blocks),
                )
                return recovered
            logger.debug(
                "Repeated-content recovery could not restore %d omitted blocks",
                len(missing_blocks),
            )

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

    When the tier result carries ``source_html_size`` (or the caller passes
    raw HTML), the volume-comparison gate can detect anomalously thin output
    relative to the source and surface it as an explicit ``warning`` so a
    truncation is never presented to callers as an unqualified success (#587).
    """
    markdown = result.get("markdown", "")
    url = result.get("url", "")
    # Tiers store ``source_html_size`` natively as int; fall back to the
    # caller-provided raw HTML only when the tier carried no size at all.
    if not html and result.get("source_html_size") is not None:
        html_size = result["source_html_size"]
    else:
        html_size = len(html)
    raw_html = html or result.get("raw_html_start") or ""
    proof = result.get("_captcha_resolution") or {}
    # This receipt is minted only by the browser after a successful solve and
    # widget removal, never from request options. Bind it to the exact retained
    # bytes so a changed/poisoned cache entry cannot reuse an earlier solve.
    captcha_resolved = (
        result.get("source") == "playwright"
        and isinstance(proof, dict)
        and proof.get("verified") is True
        and bool(raw_html)
        and proof.get("markdown_digest")
        == hashlib.sha256(markdown.encode()).hexdigest()
        and proof.get("html_digest") == hashlib.sha256(raw_html.encode()).hexdigest()
        and _classify_barrier("", url, markdown, raw_html).barrier_type != "captcha"
    )
    quality = assess_quality(
        markdown,
        # Browser tiers retain their complete rendered source in
        # raw_html_start. Pass it through so completeness can compare repeated
        # source records with the text that survived readability.
        html=raw_html,
        url=url,
        title=title,
        html_size=html_size,
        captcha_resolved=captcha_resolved,
    )
    # Preserve a pre-existing warning (e.g. a cached entry's barrier flag)
    # unless the fresh assessment produces its own (#586).
    prior_warning = result.get("warning")
    result["quality"] = quality
    volume_status = quality.get("checks", {}).get("volume")
    block_status = quality.get("checks", {}).get("block_detected")
    if block_status in ("warn", "fail") and not (
        prior_warning and "Block-page content detected" in prior_warning
    ):
        # Challenge/interstitial text survived extraction (ADR-0015): surface
        # an explicit warning so consumers refuse it — cache hits included.
        # Block-page precedence (#586): this check runs FIRST so a poisoned
        # entry that is also volume-fail carries the block-page warning, not
        # the low-yield string — consumers key refusal on the block flag, and
        # is_barrier_flagged() refuses any warning anyway.
        result["warning"] = (
            f"Block-page content detected (block_detected={block_status}); "
            "the page may be a challenge or error interstitial."
        )
        logger.warning(
            "Block-page content detected for %s (block_detected=%s)",
            url or "<unknown>",
            block_status,
        )
    elif volume_status == "fail" and not result.get("warning"):
        ratio = (len(markdown) / html_size) if html_size else 0.0
        result["warning"] = (
            f"Low yield: extracted {len(markdown)} chars from a "
            f"{html_size}-char HTML source (ratio {ratio:.4f}, floor "
            f"{VOLUME_YIELD_RATIO_FLOOR}). The page body may be truncated."
        )
        logger.warning(
            "Low-yield extraction for %s: %d chars from %d-char source",
            url or "<unknown>",
            len(markdown),
            html_size,
        )
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

    A block warning/failure always requires recovery regardless of aggregate
    score. Non-blocking low-yield warnings retain the usual score threshold.
    Results without a quality field (e.g., barrier detections) are returned
    as-is without degradation.
    """
    quality = result.get("quality")
    if quality is None:
        return True  # No quality assessment available — return as-is
    if quality.get("checks", {}).get("block_detected") in {"warn", "fail"}:
        return False
    score = quality.get("score", 1.0)
    return score >= QA_MIN_QUALITY_THRESHOLD
