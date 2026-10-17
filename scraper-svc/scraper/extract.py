"""Post-extraction quality gates for boilerplate detection, completeness, and block page detection.

Runs after readability/markdownify conversion. Returns a quality score (0.0–1.0)
and structured breakdown. Non-blocking — consumers set their own tolerance.
"""

import contextlib
import logging
import re
from dataclasses import dataclass

from .settings import load_settings

logger = logging.getLogger(__name__)

# Default thresholds — overridable via env vars
_ext_settings = load_settings()
MIN_CONTENT_CHARS = _ext_settings.qa_min_content_chars
MIN_TITLE_CHARS = _ext_settings.qa_min_title_chars
MAX_BOILERPLATE_RATIO = _ext_settings.qa_max_boilerplate_ratio

# Block page signatures — patterns that indicate a page rendered but isn't useful content.
# These catch barriers that made it past pre-extraction pattern matching (ADR-0015).
BLOCK_PAGE_PATTERNS: list[re.Pattern] = [
    # JavaScript requirements
    re.compile(r"please enable javascript"),
    re.compile(r"enable javascript to continue"),
    re.compile(r"javascript is required"),
    re.compile(r"javascript is disabled"),
    re.compile(r"please turn javascript on"),
    # Bot challenges
    re.compile(r"please verify you are (?:a )?human"),
    re.compile(r"we need to make sure you(?:'re| are) not a robot"),
    re.compile(r"checking your browser"),
    re.compile(r"we are checking your browser"),
    # Access control
    re.compile(r"access denied"),
    re.compile(r"you have been blocked"),
    re.compile(r"your (?:IP|access) has been blocked"),
    # Rate limiting
    re.compile(r"too many requests"),
    re.compile(r"rate limit"),
    # Session
    re.compile(r"your session has expired"),
    re.compile(r"session timed? ?out"),
    # Generic block page
    re.compile(r"cloudflare"),
    re.compile(r"attention required"),
    re.compile(r"just a moment"),
    # Cookies
    re.compile(r"cookies are required"),
    re.compile(r"please accept cookies"),
    re.compile(r"cookie consent"),
    re.compile(r"this site uses cookies"),
    # Geo-restriction
    re.compile(r"not available in your country"),
    re.compile(r"not available in your region"),
    re.compile(r"geo.?restriction"),
    re.compile(r"content (?:is )?not available"),
    # Paywalls
    re.compile(r"subscribe to continue"),
    re.compile(r"sign up to read more"),
    re.compile(r"members.?only"),
    re.compile(r"this content is for (?:members|subscribers)"),
    # Error pages
    re.compile(r"404 not found"),
    re.compile(r"403 forbidden"),
    re.compile(r"500 error"),
    re.compile(r"internal server error"),
    re.compile(r"page not found"),
    re.compile(r"something went wrong"),
    # CAPTCHA
    re.compile(r"hcaptcha"),
    re.compile(r"recaptcha"),
    re.compile(r"captcha"),
    # Maintenance
    re.compile(r"under maintenance"),
    re.compile(r"temporarily unavailable"),
    # Fastly JS-challenge interstitial (#586) — combined with the patterns
    # above it pushes the interstitial to >=2 matches, i.e. a blocking "fail".
    re.compile(r"a required part of this site could(?:n[o'\u2019]| no)t load"),
    re.compile(r"/_fs-ch-"),
]


@dataclass
class QualityGateResult:
    """Result of running all quality gates on extracted content.

    Attributes:
        score: Composite quality score (0.0–1.0), higher is better.
        checks: Per-gate status: "pass", "warn", or "fail".
        detail: Human-readable explanation.
    """

    score: float
    checks: dict[str, str]
    detail: str = ""


def _check_boilerplate(markdown: str) -> tuple[float, str]:
    """Detect boilerplate-heavy content.

    Analyzes link density and paragraph quality to determine whether
    the content is mostly navigation/templates rather than article text.

    Returns:
        Tuple of (score 0.0–1.0, status "pass"|"warn"|"fail").
    """
    if not markdown or not markdown.strip():
        return 0.0, "fail"

    lines = markdown.strip().split("\n")
    non_empty = [line.strip() for line in lines if line.strip()]

    if not non_empty:
        return 0.0, "fail"

    # Count link-heavy lines
    link_lines = sum(1 for line in non_empty if re.search(r"\[.*?\]\(.*?\)", line))
    link_ratio = link_lines / len(non_empty)

    # Count substantive paragraphs (multi-sentence, non-link lines)
    substantive = sum(
        1
        for line in non_empty
        if len(line) > 60 and not re.search(r"\[.*?\]\(.*?\)", line)
    )

    # Score
    if link_ratio > MAX_BOILERPLATE_RATIO and substantive < 2:
        return 0.2, "fail"
    elif link_ratio > 0.5 and substantive < 2:
        return 0.4, "warn"
    elif substantive >= 5:
        return 1.0, "pass"
    elif substantive >= 3:
        return 0.85, "pass"
    elif substantive >= 1:
        return 0.6, "warn"
    else:
        return 0.3, "fail"


def _check_completeness(
    markdown: str, title: str = "", html: str = ""
) -> tuple[float, str]:
    """Check whether extracted content meets minimum completeness thresholds.

    Args:
        markdown: Extracted markdown content.
        title: Page title if available (from frontmatter or metadata).

    Returns:
        Tuple of (score 0.0–1.0, status "pass"|"warn"|"fail").
    """
    if not markdown or not markdown.strip():
        return 0.0, "fail"

    if _missing_repeated_content_blocks(markdown, html):
        return 0.1, "fail"

    content = markdown.strip()
    content_len = len(content)

    # Check title quality
    title_ok = (
        len(title.strip()) >= MIN_TITLE_CHARS
        if title
        else content_len >= MIN_CONTENT_CHARS
    )

    # Check paragraph structure
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    has_paragraphs = len(paragraphs) >= 2

    if content_len < MIN_CONTENT_CHARS and not has_paragraphs:
        return 0.1, "fail"
    elif content_len < MIN_CONTENT_CHARS:
        return 0.3, "warn"
    elif not has_paragraphs:
        return 0.4, "warn"
    elif content_len >= 1000 and title_ok and has_paragraphs:
        return 1.0, "pass"
    elif content_len >= 500 and has_paragraphs:
        return 0.85, "pass"
    else:
        return 0.6, "warn"


_REPEATED_BLOCK_TAGS = {"article", "div", "li", "section"}
_REPEATED_BLOCK_CHROME = {
    "ad",
    "ads",
    "advert",
    "advertisement",
    "breadcrumb",
    "footer",
    "header",
    "menu",
    "nav",
    "navigation",
    "promo",
    "promotion",
    "related",
    "recommendation",
    "sidebar",
    "sponsor",
    "sponsored",
}


def _missing_repeated_content_blocks(markdown: str, html: str) -> list[str]:
    """Find substantial repeated sibling blocks omitted from extracted text.

    Readability can drop individual records from a card or result list while
    returning enough text to pass length-based completeness checks. This
    signal is deliberately limited to repeated, text-heavy siblings that
    account for at least half of the non-script body text. Navigation, page
    chrome, sidebars, and advertising containers are excluded.
    """
    if not markdown or not html:
        return []

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    def excluded(element) -> bool:
        for ancestor in [element, *element.parents]:
            if getattr(ancestor, "name", None) in {
                "aside",
                "footer",
                "header",
                "nav",
                "script",
                "style",
            }:
                return True
            tokens = {str(token).lower() for token in (ancestor.get("class") or [])}
            if tokens & _REPEATED_BLOCK_CHROME or any(
                token.startswith(
                    (
                        "ad-",
                        "ad_",
                        "advert",
                        "promo",
                        "related",
                        "sidebar",
                        "sponsor",
                    )
                )
                for token in tokens
            ):
                return True
        return False

    groups: dict[tuple[int, str, tuple[str, ...]], list[str]] = {}
    for parent in soup.find_all(True):
        for child in parent.find_all(recursive=False):
            if child.name not in _REPEATED_BLOCK_TAGS or not child.get("class"):
                continue
            if excluded(child):
                continue
            classes = tuple(sorted(str(token).lower() for token in child["class"]))
            text = re.sub(r"\s+", " ", child.get_text(" ", strip=True)).strip()
            if len(text) < 60:
                continue
            key = (id(parent), child.name, classes)
            groups.setdefault(key, []).append(text)

    if not groups:
        return []

    body = soup.body or soup
    for tag in body(["script", "style"]):
        tag.decompose()
    body_text = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()
    body_size = len(body_text)
    markdown_without_link_targets = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)

    def comparable_text(value: str) -> str:
        # HTML emphasis becomes Markdown delimiters (for example <strong> to
        # **text**). Compare the words and digits so formatting and punctuation
        # changes do not make a fully retained record look absent.
        return "".join(
            character.casefold() for character in value if character.isalnum()
        )

    markdown_text = comparable_text(markdown_without_link_targets)

    relevant_groups = [
        texts
        for texts in groups.values()
        if len(texts) >= 4 and sum(map(len, texts)) >= 0.5 * body_size
    ]
    if not relevant_groups:
        return []

    texts = max(relevant_groups, key=lambda group: sum(map(len, group)))
    return [text for text in texts if comparable_text(text) not in markdown_text]


def _check_block_page(
    markdown: str, url: str = "", *, captcha_resolved: bool = False
) -> tuple[float, str]:
    """Detect block pages, error pages, and other non-content responses.

    Uses pattern matching against known block page signatures. This complements
    the pre-extraction barrier detection in fetch.py by catching barriers that
    rendered as text and made it through to the content pipeline.

    Args:
        markdown: Extracted markdown content.
        url: Source URL (for context, not currently used in pattern matching).
        captcha_resolved: Browser-verified solve with cleared widget DOM;
            suppress only neutral CAPTCHA names, never other barrier phrases.

    Returns:
        Tuple of (score 0.0–1.0, status "pass"|"warn"|"fail").
        Higher score = less likely to be a block page.
    """
    if not markdown or not markdown.strip():
        return 0.0, "fail"

    content_lower = markdown.lower()
    matched_patterns = []

    for pattern in BLOCK_PAGE_PATTERNS:
        # A browser-observed solve with cleared widget DOM may leave a neutral
        # article mentioning CAPTCHA. All other interstitial markers still apply.
        if captcha_resolved and pattern.pattern in {"hcaptcha", "recaptcha", "captcha"}:
            continue
        if pattern.search(content_lower):
            matched_patterns.append(pattern.pattern)

    if matched_patterns:
        count = len(matched_patterns)
        if count >= 3:
            return 0.05, "fail"
        elif count >= 2:
            return 0.15, "fail"
        else:
            return 0.3, "warn"

    # Short content with error keywords
    if len(markdown.strip()) < MIN_CONTENT_CHARS:
        error_words = [
            "error",
            "not found",
            "forbidden",
            "unauthorized",
            "timeout",
            "failed",
            "unavailable",
            "exception",
        ]
        error_count = sum(1 for w in error_words if w in content_lower)
        if error_count >= 2:
            return 0.2, "fail"

    return 1.0, "pass"


# ── Volume-comparison gate (#587) ────────────────────────────────
# Sources at or above MIN_LOW_YIELD_SOURCE_CHARS whose readable output is
# either under VOLUME_YIELD_RATIO_FLOOR of the source size or under
# VOLUME_MIN_OUTPUT_CHARS absolute are anomalously thin. Calibrated so the
# issue #587 repro (768B intro-only markdown from 94,978B HTML, ratio 0.8%)
# trips the check while typical article yields (~10%+) never do.
MIN_LOW_YIELD_SOURCE_CHARS = 10000
VOLUME_YIELD_RATIO_FLOOR = 0.02
VOLUME_MIN_OUTPUT_CHARS = 2048


def is_low_yield_text(markdown: str, source_size: int) -> bool:
    """Return True when extracted text is anomalously small for its source.

    Shared low-yield predicate used by extraction recovery
    (``fetch_quality.html_to_markdown``) and by this module's quality
    assessment ``volume`` check, so both agree on what counts as thin.
    """
    if source_size <= 0 or source_size < MIN_LOW_YIELD_SOURCE_CHARS:
        return False
    if len(markdown) < VOLUME_MIN_OUTPUT_CHARS:
        return True
    return len(markdown) < VOLUME_YIELD_RATIO_FLOOR * source_size


def assess_quality(
    markdown: str,
    html: str = "",
    url: str = "",
    title: str = "",
    html_size: int = 0,
    *,
    captcha_resolved: bool = False,
) -> dict:
    """Run all quality gates on extracted content.

    Lightweight heuristic assessment — no external APIs or LLM calls.

    Args:
        markdown: Extracted markdown content to assess.
        html: Raw HTML if available (for future heuristics).
        url: Source URL (for context).
        title: Page title if available.
        html_size: Size of the source HTML in characters, when the full
            HTML is unavailable (e.g. tier pipelines keep only markdown).
            Backs the ``volume`` check: anomalously thin extraction from a
            large source is flagged so callers never ship a silent truncation.
        captcha_resolved: Verified browser solve with cleared widget DOM. Only
            neutral CAPTCHA names are exempted from the block-page gate.

    Returns:
        Dict with score, per-check breakdown, and detail:
        {
            "score": 0.95,
            "checks": {"boilerplate": "pass", "completeness": "pass", "block_detected": "pass"},
            "detail": "all checks passed"
        }
    """
    boilerplate_score, boilerplate_status = _check_boilerplate(markdown)
    completeness_score, completeness_status = _check_completeness(markdown, title, html)
    block_score, block_status = _check_block_page(
        markdown, url, captcha_resolved=captcha_resolved
    )

    # Volume gate: pass when no source-size information is available or
    # when the yield is healthy; fail only on anomalously thin output.
    if is_low_yield_text(markdown, html_size):
        _volume_score, volume_status = 0.0, "fail"
    else:
        _volume_score, volume_status = 1.0, "pass"

    # Weighted composite: boilerplate 30%, completeness 30%, block detection 40%
    overall = boilerplate_score * 0.3 + completeness_score * 0.3 + block_score * 0.4

    # Build detail string
    details = []
    if boilerplate_status != "pass":
        details.append(f"boilerplate:{boilerplate_status}")
    if completeness_status != "pass":
        details.append(f"completeness:{completeness_status}")
    if block_status != "pass":
        details.append(f"block:{block_status}")
    if volume_status != "pass":
        details.append(f"volume:{volume_status}")

    return {
        "score": round(overall, 2),
        "checks": {
            "boilerplate": boilerplate_status,
            "completeness": completeness_status,
            "block_detected": block_status,
            "volume": volume_status,
        },
        "detail": ", ".join(details) if details else "all checks passed",
    }


# ── Section filtering ──────────────────────────────────────────

# HTML5 semantic section elements mapped to categories
_SECTION_TAG_MAP: dict[str, str] = {
    "header": "header",
    "nav": "navigation",
    "main": "body",
    "article": "body",
    "section": "body",
    "aside": "sidebar",
    "footer": "footer",
}

# Tags that represent banner-type content
_BANNER_TAGS = {
    "div[role=banner]",
    "div.banner",
    "div.hero",
}


def filter_sections(
    html: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    verbosity: str = "standard",
) -> str:
    """Filter page sections by category and control verbosity of output.

    Parses HTML with BeautifulSoup, identifies semantic sections, and
    applies include/exclude filters before converting to markdown.

    Args:
        html: Raw HTML string from the page.
        include: Section categories to keep (all others stripped).
        exclude: Section categories to strip (all others kept).
        verbosity: Output verbosity — "compact", "standard", or "full".

    Returns:
        Markdown string with filtering applied, or the original HTML
        converted via readability extraction (standard verbosity, no filters).
    """
    try:
        from bs4 import BeautifulSoup, Tag
    except ImportError:
        logger.warning("BeautifulSoup not available for section filtering")
        return html

    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # ── Identify sections ──────────────────────────────────────
    # Build a list of (category, element) tuples
    sections: list[tuple[str, Tag]] = []
    seen_elements: set[int] = set()

    for tag_name, category in _SECTION_TAG_MAP.items():
        for elem in soup.find_all(tag_name):
            elem_id = id(elem)
            if elem_id not in seen_elements:
                sections.append((category, elem))
                seen_elements.add(elem_id)

    # Also identify banner elements
    for css_selector in _BANNER_TAGS:
        tag_part, _, class_or_attr = css_selector.partition(".")
        if class_or_attr and class_or_attr.startswith("class="):
            cls = class_or_attr[6:]
            for elem in soup.find_all(tag_part, class_=cls):
                elem_id = id(elem)
                if elem_id not in seen_elements:
                    sections.append(("banner", elem))
                    seen_elements.add(elem_id)
        elif class_or_attr:
            cls = class_or_attr
            for elem in soup.find_all(tag_part, class_=cls):
                elem_id = id(elem)
                if elem_id not in seen_elements:
                    sections.append(("banner", elem))
                    seen_elements.add(elem_id)

    # If no semantic sections found, treat entire body as "body"
    if not sections:
        body = soup.find("body")
        if body:
            sections = [("body", body)]

    # ── Apply include/exclude filters ───────────────────────────
    if include:
        included_categories = set(include)
        for category, elem in sections:
            if category not in included_categories:
                elem.decompose()

    if exclude:
        excluded_categories = set(exclude)
        for category, elem in sections:
            if category in excluded_categories:
                elem.decompose()

    # ── Verbosity ───────────────────────────────────────────────
    if verbosity == "compact":
        # Extract text from remaining body content, return first ~300 chars
        body_text = soup.get_text(separator="\n", strip=True)
        compact = body_text[:300].strip()
        if len(body_text) > 300:
            compact += "\n..."
        return compact

    if verbosity == "full":
        # Render full page as markdown, preserving structural markup
        try:
            from markdownify import markdownify as md

            # Remove script and style before markdown conversion
            for tag in soup(["script", "style"]):
                tag.decompose()
            return md(str(soup), heading_style="ATX", strip=[]).strip()
        except ImportError:
            logger.warning("markdownify not available for full verbosity")
            return soup.get_text(separator="\n", strip=True)

    # Standard: use the shared html_to_markdown path on the (possibly
    # filtered) HTML. This keeps section-filtered output identical to the
    # main extraction pipeline, including the #587 low-yield recovery for
    # card-style layouts (a private readability copy here would truncate).
    try:
        from .fetch_quality import html_to_markdown

        markdown = html_to_markdown(str(soup))
        return markdown.strip()
    except Exception:
        return soup.get_text(separator="\n", strip=True)


# ── Extras extraction ──────────────────────────────────────────


def extract_extras(html: str, options) -> dict:
    """Extract links, images, and code blocks from raw HTML.

    Args:
        html: Raw HTML string from the page.
        options: ExtrasOptions with max counts for links, imageLinks, codeBlocks.
                 Only keys present in options are included in the output.

    Returns:
        Dict with extracted extras — only keys that were requested appear.
        Example: {"links": [...], "codeBlocks": [...]}
    """
    try:
        from urllib.parse import urljoin

        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup not available for extras extraction")
        return {}

    if not html or not html.strip():
        return {}

    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    # Determine the page's base URL for resolving relative links
    base_tag = soup.find("base", href=True)
    base_url = base_tag["href"] if base_tag else ""

    # ── External links ─────────────────────────────────────────
    if options.links is not None and options.links > 0:
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href: str = str(a["href"]).strip()  # type: ignore
            # Skip non-web links
            if href.startswith(("mailto:", "javascript:", "tel:", "#")):
                continue
            # Resolve relative URLs against base
            resolved = href  # type: ignore
            if base_url:
                with contextlib.suppress(Exception):
                    resolved = urljoin(base_url, href)  # type: ignore
            # Deduplicate
            if resolved not in links:
                links.append(resolved)
            if len(links) >= options.links:
                break
        result["links"] = links

    # ── Image links ────────────────────────────────────────────
    if options.imageLinks is not None and options.imageLinks > 0:
        images: list[str] = []
        for img in soup.find_all("img", src=True):
            src: str = str(img["src"]).strip()  # type: ignore
            if not src or src.startswith("data:"):
                continue
            if src not in images:
                images.append(src)
            if len(images) >= options.imageLinks:
                break
        result["imageLinks"] = images

    # ── Code blocks ────────────────────────────────────────────
    if options.codeBlocks is not None and options.codeBlocks > 0:
        blocks: list[str] = []
        # <pre><code> blocks first
        for pre in soup.find_all("pre"):
            code = pre.find("code")
            text = code.get_text() if code else pre.get_text()
            text = text.strip()
            if text and text not in blocks:
                blocks.append(text)
            if len(blocks) >= options.codeBlocks:
                break
        # Fallback: standalone <code> blocks (not inside <pre>)
        if len(blocks) < options.codeBlocks:
            for code in soup.find_all("code"):
                if code.parent and code.parent.name == "pre":
                    continue  # already captured above
                text = code.get_text().strip()
                if text and text not in blocks:
                    blocks.append(text)
                if len(blocks) >= options.codeBlocks:
                    break
        result["codeBlocks"] = blocks

    return result


# ── Image extraction ───────────────────────────────────────────


def extract_images(
    html: str,
    base_url: str = "",
    remove_base64_images: bool = False,
) -> list[dict]:
    """Extract structured image metadata from raw HTML.

    Parses ``<img>`` tags from the DOM, capturing ``src``, ``alt``,
    ``width``, ``height``, and their document-order position.
    Relative URLs are resolved against ``base_url`` when provided.
    When ``remove_base64_images`` is True, data URIs are excluded
    from the output.

    Args:
        html: Raw HTML string from the page.
        base_url: Page URL used to resolve relative image sources.
        remove_base64_images: When True, skip ``data:image/...`` URIs.

    Returns:
        List of dicts with keys: ``url``, ``alt``, ``width``,
        ``height``, ``position``.
    """
    try:
        from urllib.parse import urljoin

        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup not available for image extraction")
        return []

    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    images: list[dict] = []
    position = 0

    for img in soup.find_all("img"):
        src = str(img.get("src") or "").strip()
        if not src:
            continue

        if remove_base64_images and src.startswith("data:"):
            continue

        if base_url and not src.startswith(("http:", "https:", "data:")):
            with contextlib.suppress(Exception):
                src = urljoin(base_url, src)

        width = None
        height = None
        for attr_name in ("width",):
            raw = img.get(attr_name)
            if raw is not None:
                with contextlib.suppress(ValueError, TypeError):
                    width = int(str(raw))

        for attr_name in ("height",):
            raw = img.get(attr_name)
            if raw is not None:
                with contextlib.suppress(ValueError, TypeError):
                    height = int(str(raw))

        position += 1
        images.append(
            {
                "url": src,
                "alt": str(img.get("alt") or "").strip(),
                "width": width,
                "height": height,
                "position": position,
            }
        )

    return images
