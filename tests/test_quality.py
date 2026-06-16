"""Tests for the extraction quality gate functions.

Unit tests — no Docker needed. Run directly:
    python -m pytest tests/test_quality.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper-svc"))

from scraper.extract import (
    _check_boilerplate,
    _check_completeness,
    _check_block_page,
    assess_quality,
    BLOCK_PAGE_PATTERNS,
)


# ── _check_boilerplate ──────────────────────────────────────────


def test_boilerplate_empty_content():
    score, status = _check_boilerplate("")
    assert status == "fail"
    assert score == 0.0


def test_boilerplate_substantive_article():
    """A genuine article with several multi-sentence paragraphs passes."""
    content = "\n\n".join([
        "This is the first paragraph of a real article. It has multiple sentences. "
        "Enough text to count as substantive content that is clearly not boilerplate.",
        "Here is a second paragraph with more detailed analysis and interesting "
        "observations about the topic at hand. It continues with further examples.",
        "A third paragraph that provides additional context and supporting evidence "
        "for the claims made earlier in the article. More than sixty characters here.",
        "A fourth paragraph wrapping up the argument with concluding remarks and "
        "a call to action for the reader to think about the implications.",
        "Finally, a fifth paragraph with a strong closing statement that leaves "
        "the reader with something to consider about the broader implications.",
    ])
    score, status = _check_boilerplate(content)
    assert status == "pass"
    assert score == 1.0


def test_boilerplate_link_heavy():
    """Pages with >70% link lines and no paragraphs fail."""
    lines = "\n".join([f"[Link {i}](https://example.com/{i})" for i in range(20)])
    score, status = _check_boilerplate(lines)
    assert status == "fail"
    assert score <= 0.4


def test_boilerplate_nav_page():
    """Navigation/list pages with many links but some content warn or fail."""
    lines = "\n".join([f"[Link {i}](https://example.com/{i})" for i in range(15)])
    lines += "\n\nA single line of explanation that is not a link."
    score, status = _check_boilerplate(lines)
    assert status in ("warn", "fail")


# ── _check_completeness ─────────────────────────────────────────


def test_completeness_empty():
    score, status = _check_completeness("")
    assert status == "fail"
    assert score == 0.0


def test_completeness_short_content_no_title():
    """Very short content (<200 chars) with no paragraphs fails."""
    score, status = _check_completeness("Hello world")
    assert status == "fail"


def test_completeness_good_content():
    """Content over 1000 chars with paragraphs and good title passes."""
    content = "\n\n".join([
        "This is paragraph one with enough text to make it substantive. "
        "Several sentences here to ensure we have real content.",
        "This is paragraph two with additional text that carries the "
        "narrative forward and provides more depth.",
    ])
    content = content * 5  # Make it >1000 chars
    score, status = _check_completeness(content, title="A Real Article Title Here")
    assert status == "pass"
    assert score >= 0.85


def test_completeness_paragraph_threshold():
    """Content with adequate length but only one paragraph warns."""
    long_single_para = "Sentence one. " * 50  # Single paragraph, >500 chars
    score, status = _check_completeness(long_single_para)
    assert status == "warn"


# ── _check_block_page ───────────────────────────────────────────


def test_block_page_normal_content():
    """Normal article content should pass."""
    content = "This is a normal article about something interesting. It has real content."
    score, status = _check_block_page(content)
    assert status == "pass"
    assert score == 1.0


def test_block_page_cloudflare():
    """Cloudflare challenge text detected as block page."""
    content = (
        "Please enable JavaScript to view this page.\n"
        "We need to make sure you're not a robot.\n"
        "Your browser is being checked for cookies."
    )
    score, status = _check_block_page(content)
    assert status == "fail"
    assert score < 0.2


def test_block_page_geo_restriction():
    """Geo-restriction message detected."""
    content = (
        "Sorry, this content is not available in your country.\n"
        "Due to licensing restrictions, access is geo-blocked."
    )
    score, status = _check_block_page(content)
    assert status == "fail"


def test_block_page_paywall():
    """Paywall/subscription wall detected."""
    content = (
        "Subscribe to continue reading this article.\n"
        "This content is for members only."
    )
    score, status = _check_block_page(content)
    assert status == "fail"


def test_block_page_single_pattern_warns():
    """A single block page pattern match returns warn, not fail."""
    content = "We use cookies to improve your experience. By continuing you accept cookies."
    score, status = _check_block_page(content)
    assert status == "warn" or (status == "pass" and score == 1.0)


# ── assess_quality (integration of all gates) ────────────────────


def test_assess_quality_good_article():
    """A well-formed article gets a high quality score."""
    markdown = "# Real Article Title\n\n"
    markdown += "\n\n".join([
        "This is a substantive paragraph with multiple sentences that form a "
        "coherent narrative about an interesting topic worth discussing at length. "
        "It continues with another sentence to add depth and context to the "
        "discussion, ensuring readers have a thorough understanding.",
        "Here is a second paragraph that continues the discussion with more "
        "detail and supporting evidence for the claims being made. It adds "
        "further analysis and context to build a stronger argument overall.",
        "A third paragraph that provides additional context and wraps up the "
        "main argument of the article with a concluding thought. It offers a "
        "final perspective that ties the discussion together neatly."
        "The final sentence serves as a strong closing statement for this section.",
    ])
    # Ensure total > 500 chars
    while len(markdown) < 500:
        markdown += "\n\nThis is an extra paragraph to push the content length past "
        "the 500-character threshold for a passing completeness score. It has "
        "multiple sentences to feel like real content."
    result = assess_quality(markdown, title="Real Article Title Here")
    assert result["score"] >= 0.7
    assert result["checks"]["boilerplate"] == "pass"
    assert result["checks"]["completeness"] == "pass"
    assert result["checks"]["block_detected"] == "pass"


def test_assess_quality_empty():
    """Empty content gets a very low score."""
    result = assess_quality("")
    assert result["score"] <= 0.1
    assert result["checks"]["block_detected"] == "fail"


def test_assess_quality_block_page():
    """Block page content gets penalized by both block and boilerplate gates."""
    content = (
        "Please enable JavaScript to view this page.\n"
        "We need to check your browser before continuing.\n"
        "This page uses Cloudflare for security.\n"
        "You will be redirected shortly."
    )
    result = assess_quality(content)
    assert result["score"] < 0.4
    assert result["checks"]["block_detected"] == "fail"


def test_assess_quality_boilerplate_dominated():
    """Content that's mostly links gets a low boilerplate score."""
    lines = "\n".join([f"[Link {i}](https://example.com/{i})" for i in range(30)])
    result = assess_quality(lines)
    assert result["checks"]["boilerplate"] in ("fail", "warn")


def test_assess_quality_returns_dict_contract():
    """The return dict matches the documented contract."""
    result = assess_quality("Some content here", url="https://example.com")
    assert "score" in result
    assert "checks" in result
    assert "detail" in result
    assert isinstance(result["score"], float)
    assert 0.0 <= result["score"] <= 1.0
    assert isinstance(result["checks"], dict)
    assert "boilerplate" in result["checks"]
    assert "completeness" in result["checks"]
    assert "block_detected" in result["checks"]


# ── _quality_acceptable ──────────────────────────────────────────


def test_quality_acceptable_above_threshold():
    from scraper.fetch_quality import _quality_acceptable, QA_MIN_QUALITY_THRESHOLD

    # Above default threshold (0.3)
    result = {"quality": {"score": 0.7}}
    assert _quality_acceptable(result) is True


def test_quality_acceptable_below_threshold():
    from scraper.fetch_quality import _quality_acceptable

    # Below default threshold (0.3)
    result = {"quality": {"score": 0.1}}
    assert _quality_acceptable(result) is False


def test_quality_acceptable_no_quality_field():
    from scraper.fetch_quality import _quality_acceptable

    # No quality field — return as-is (barrier detection, etc.)
    result = {"markdown": "some content"}
    assert _quality_acceptable(result) is True


def test_quality_acceptable_at_threshold():
    from scraper.fetch_quality import _quality_acceptable, QA_MIN_QUALITY_THRESHOLD

    # Exactly at threshold should pass
    result = {"quality": {"score": QA_MIN_QUALITY_THRESHOLD}}
    assert _quality_acceptable(result) is True
