"""Bot challenge detection and barrier classification.

Detects Cloudflare JS challenges, DDoS-Guard, CAPTCHAs, rate-limit pages,
Fastly JS-challenge interstitials (#586), and Substack redirect frames.
Provides structured ``BarrierInfo`` results via ``_classify_barrier()``, which
replaced the old boolean ``_looks_suspicious()``.
"""

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


# ── Bot challenge detection (title/URL level) ──────────────────
CLOUDFLARE_INDICATORS = [
    "Just a moment",
    "Checking your browser",
    "DDoS protection by",
    "cf-browser-verification",
    "challenge-platform",
    "Verification successful",
    "Enable JavaScript and cookies to continue",
    "security verification",
]

DDOS_GUARD_INDICATORS = [
    "DDoS-Guard",
    "DDOS-GUARD",
    "ddos-guard",
    "Checking your browser before accessing",
    ".well-known/ddos-guard",
]

# ── Fastly JS-challenge detection (#586) ───────────────────────
# Prose rendered by Fastly's browser-challenge interstitials (observed on
# nature.com). Matched case-insensitively against title/content; prose-only
# matches accumulate confidence via the count-based ladder and must never be
# treated as definitive on their own.
FASTLY_CHALLENGE_INDICATORS = [
    "javascript is disabled",
    "please enable javascript to proceed",
    "a required part of this site couldn't load",
    "a required part of this site couldn’t load",
]

# Definitive HTML signature: Fastly challenge assets are served under this
# prefix. Its presence in the raw HTML identifies a challenge page regardless
# of what the extracted text looks like, so it short-circuits classification
# at high confidence (mirroring the captcha-provider widget check).
FASTLY_SIGNATURE_PREFIX = "/_fs-ch-"

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

    Mirrors browser-svc's _is_bot_challenge() logic. Fastly challenge pages
    (#586) are included so the Tier 3 resolution poll engages for them too:
    the poll waits out the interstitial instead of extracting its prose.
    """
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "cf_chl" in url.lower() or "challenge-platform" in url.lower():
        return True
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "ddos-guard" in url.lower() or "/.well-known/ddos-guard" in url.lower():
        return True
    # ── Fastly JS-challenge signals (#586) ─────────────────────
    if FASTLY_SIGNATURE_PREFIX in url.lower():
        return True
    return any(
        indicator.lower() in title.lower() for indicator in FASTLY_CHALLENGE_INDICATORS
    )


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
    )  # "cloudflare", "ddos-guard", "captcha", "fastly", "rate-limit", "substack-redirect", "empty", "suspicious", None
    confidence: float
    detail: str = ""
    title: str = ""
    provider: str | None = None


class _DataDomeFrameParser(HTMLParser):
    """Recognize actual provider frames, not prose or lookalike hostnames."""

    detected = False

    def handle_starttag(self, tag, attrs):
        if tag != "iframe":
            return
        src = dict(attrs).get("src") or ""
        try:
            parsed = urlsplit(src)
        except ValueError:
            return
        if (
            parsed.scheme in ("", "http", "https")
            and parsed.hostname == "geo.captcha-delivery.com"
            and parsed.path.startswith(("/captcha/", "/interstitial/"))
        ):
            self.detected = True


def _classify_barrier(
    title: str, url: str, content: str, html: str | None = None
) -> BarrierInfo:
    """Classify whether a scraped page is a barrier/challenge page.

    Replaces the old boolean _looks_suspicious() with structured,
    multi-signal classification. Returns a BarrierInfo dataclass
    with detected flag, barrier type, confidence score, and detail.

    Definitive signatures short-circuit before count scoring: captcha
    provider widgets and the Fastly ``/_fs-ch-`` asset prefix (#586)
    return immediately at 0.95 confidence.

    Otherwise, confidence is derived from the number of distinct matched
    signals (the count-based ladder — prose-only inputs can never reach
    the definitive 0.95 tier):
      1 signal  → 0.70
      2 signals → 0.90
      3+ signals → capped at 0.95
    """
    html_lower = html.lower() if html else ""

    # ── Definitive Fastly signature (#586) ────────────────────
    # Fastly serves challenge assets under /_fs-ch-; seeing that prefix in
    # the raw HTML identifies a challenge page no matter how the extracted
    # text reads. Short-circuits at 0.95, mirroring the captcha-provider
    # widget check above (same precedence position, before count scoring).
    if FASTLY_SIGNATURE_PREFIX in html_lower or FASTLY_SIGNATURE_PREFIX in url.lower():
        return BarrierInfo(
            True,
            "fastly",
            0.95,
            f"Definitive Fastly challenge signature ({FASTLY_SIGNATURE_PREFIX})",
            title,
            "fastly",
        )

    datadome = _DataDomeFrameParser()
    datadome.feed(html or "")
    if datadome.detected:
        return BarrierInfo(
            True,
            "captcha",
            0.95,
            "DataDome challenge frame; solver unsupported",
            title,
            "datadome",
        )

    captcha_providers = (
        (
            "turnstile",
            (
                "challenges.cloudflare.com/turnstile",
                "cf-turnstile",
                "cf-turnstile-response",
            ),
        ),
        ("recaptcha", ("google.com/recaptcha", "g-recaptcha", "g-recaptcha-response")),
        ("hcaptcha", ("hcaptcha.com", "h-captcha", "h-captcha-response")),
    )
    for provider, signatures in captcha_providers:
        if any(signature in html_lower for signature in signatures):
            return BarrierInfo(
                True, "captcha", 0.95, f"Definitive {provider} widget", title, provider
            )
    generic_widget = (
        "<iframe" in html_lower
        and "sitekey" in html_lower
        and ("captcha" in html_lower or "challenge" in html_lower)
    )
    if generic_widget:
        return BarrierInfo(
            True, "captcha", 0.85, "Definitive generic CAPTCHA widget", title, "generic"
        )

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

    # ── Signal: Fastly challenge prose (#586) ─────────────────
    # Each DISTINCT prose marker counts as its own signal so the count-based
    # ladder accumulates confidence (1 marker → 0.70, 2 → 0.90). Prose-only
    # matches can never reach the definitive 0.95 tier — that requires the
    # /_fs-ch- HTML signature — so a tech article quoting a single phrase is
    # flagged at low confidence rather than treated as definitive.
    for indicator in FASTLY_CHALLENGE_INDICATORS:
        if (
            indicator in content_lower
            or indicator in title_lower
            or (html and indicator.lower() in html_lower)
        ):
            signal_name = f"fastly-prose:{indicator[:24]}"
            if signal_name not in signals:
                signals.append(signal_name)

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

    # ── Prose-only ceiling (#586) ─────────────────────────────
    # The definitive 0.95 tier is reserved for structural signatures
    # (captcha widgets above, the Fastly /_fs-ch- asset prefix). Prose-only
    # matches accumulate through the ladder but cap at 0.90 so a page merely
    # quoting the challenge text can never be classified as definitive.
    prose_only = all(
        s == "empty" or s.startswith("fastly-prose:") or s == "rate-limit"
        for s in signals
    )
    if prose_only:
        confidence = min(confidence, 0.90)

    # ── Determine the primary barrier type ────────────────────
    barrier_type: str | None = None
    for keyword, btype in [
        ("cloudflare", "cloudflare"),
        ("ddos-guard", "ddos-guard"),
        ("captcha", "captcha"),
        ("rate-limit", "rate-limit"),
        ("fastly-prose", "fastly"),
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
