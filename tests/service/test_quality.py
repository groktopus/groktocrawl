"""Tests for the extraction quality gate functions.

Unit tests — no Docker needed. Run directly:
    python -m pytest tests/test_quality.py -v
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper-svc"))

from scraper.extract import (
    _check_block_page,
    _check_boilerplate,
    _check_completeness,
    assess_quality,
)

# ── _check_boilerplate ──────────────────────────────────────────


def test_boilerplate_empty_content():
    score, status = _check_boilerplate("")
    assert status == "fail"
    assert score == 0.0


def test_boilerplate_substantive_article():
    """A genuine article with several multi-sentence paragraphs passes."""
    content = "\n\n".join(
        [
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
        ]
    )
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
    _score, status = _check_boilerplate(lines)
    assert status in ("warn", "fail")


# ── _check_completeness ─────────────────────────────────────────


def test_completeness_empty():
    score, status = _check_completeness("")
    assert status == "fail"
    assert score == 0.0


def test_completeness_short_content_no_title():
    """Very short content (<200 chars) with no paragraphs fails."""
    _score, status = _check_completeness("Hello world")
    assert status == "fail"


def test_completeness_good_content():
    """Content over 1000 chars with paragraphs and good title passes."""
    content = "\n\n".join(
        [
            "This is paragraph one with enough text to make it substantive. "
            "Several sentences here to ensure we have real content.",
            "This is paragraph two with additional text that carries the "
            "narrative forward and provides more depth.",
        ]
    )
    content = content * 5  # Make it >1000 chars
    score, status = _check_completeness(content, title="A Real Article Title Here")
    assert status == "pass"
    assert score >= 0.85


def test_completeness_paragraph_threshold():
    """Content with adequate length but only one paragraph warns."""
    long_single_para = "Sentence one. " * 50  # Single paragraph, >500 chars
    _score, status = _check_completeness(long_single_para)
    assert status == "warn"


# ── _check_block_page ───────────────────────────────────────────


def test_block_page_normal_content():
    """Normal article content should pass."""
    content = (
        "This is a normal article about something interesting. It has real content."
    )
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
    _score, status = _check_block_page(content)
    assert status == "fail"


def test_block_page_paywall():
    """Paywall/subscription wall detected."""
    content = (
        "Subscribe to continue reading this article.\nThis content is for members only."
    )
    _score, status = _check_block_page(content)
    assert status == "fail"


def test_block_page_single_pattern_warns():
    """A single block page pattern match returns warn, not fail."""
    content = (
        "We use cookies to improve your experience. By continuing you accept cookies."
    )
    score, status = _check_block_page(content)
    assert status == "warn" or (status == "pass" and score == 1.0)


# ── assess_quality (integration of all gates) ────────────────────


def test_assess_quality_good_article():
    """A well-formed article gets a high quality score."""
    markdown = "# Real Article Title\n\n"
    markdown += "\n\n".join(
        [
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
        ]
    )
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
    from scraper.fetch_quality import _quality_acceptable

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
    from scraper.fetch_quality import QA_MIN_QUALITY_THRESHOLD, _quality_acceptable

    # Exactly at threshold should pass
    result = {"quality": {"score": QA_MIN_QUALITY_THRESHOLD}}
    assert _quality_acceptable(result) is True


# ── html_to_markdown structural fallback ────────────────────────


def test_html_to_markdown_normal_article():
    """Normal article HTML should use readability path with no fallback."""
    from scraper.fetch_quality import html_to_markdown

    html = """<html><head><title>My Article</title></head><body>
    <article><h1>My Article</h1>
    <p>This is a well-formed article with substantial content that should be easily
    extracted by readability-lxml. It has multiple sentences and enough depth to
    produce meaningful markdown output from the converter pipeline.</p>
    <p>A second paragraph with additional context and information that builds on
    the first paragraph and provides further detail about the topic.</p>
    </article></body></html>"""
    result = html_to_markdown(html)
    assert len(result) > 50
    assert "My Article" in result


def test_html_to_markdown_real_readability_filters_page_chrome():
    """The real Readability fragment keeps article metadata, not page chrome."""
    from scraper.fetch_quality import html_to_markdown

    html = """<html><head><title>Site</title></head><body>
    <header>Site Header</header>
    <nav><a href="/">Home</a><a href="/docs">Docs</a></nav>
    <article>
      <header><h1>Article Title</h1><p>By Example Author</p></header>
      <p>This is a substantive article paragraph with enough detail to make the
      Readability result meaningful and useful for a real extraction test.</p>
      <p>A second paragraph adds context, examples, and supporting information
      for the reader.</p>
      <footer>Sources: Example Research Institute</footer>
    </article>
    <footer>Page Copyright</footer>
    </body></html>"""

    result = html_to_markdown(html)

    assert "Article Title" in result
    assert "By Example Author" in result
    assert "Sources: Example Research Institute" in result
    assert "Site Header" not in result
    assert "Page Copyright" not in result
    assert "[Home](/)" not in result
    assert "[Docs](/docs)" not in result


def test_html_to_markdown_preserves_article_header_and_footer(monkeypatch):
    """Readability content headers and footers carry article metadata and notes."""
    from scraper.fetch_quality import html_to_markdown

    summary = """<article>
    <header><h1>Article Title</h1><p>By Example Author</p></header>
    <p>This is a well-formed article with substantial content that should be easily
    extracted by readability-lxml. It has multiple sentences and enough depth to
    produce meaningful markdown output from the converter pipeline.</p>
    <p>A second paragraph with additional context and information that builds on
    the first paragraph and provides further detail about the topic.</p>
    <footer><p>Sources: Example Research Institute</p></footer>
    </article>"""

    class ReadabilityDocument:
        def __init__(self, _html):
            pass

        def summary(self):
            return summary

    monkeypatch.setitem(
        sys.modules, "readability", SimpleNamespace(Document=ReadabilityDocument)
    )

    result = html_to_markdown("<html><body>ignored</body></html>")

    assert "Article Title" in result
    assert "By Example Author" in result
    assert "Sources: Example Research Institute" in result


def test_html_to_markdown_preserves_div_content_header_and_footer(monkeypatch):
    """Readability content fragments need not use article or main elements."""
    from scraper.fetch_quality import html_to_markdown

    summary = """<div class="readability-content">
    <header><h1>Div Article Title</h1><p>By Example Author</p></header>
    <nav aria-label="Contents"><a href="#div-setup">Div Setup</a></nav>
    <p>This div-based article has enough substantive content for the normal
    readability path and does not depend on semantic HTML container elements.</p>
    <footer><p>Sources: Div Content Research Institute</p></footer>
    </div>"""

    class ReadabilityDocument:
        def __init__(self, _html):
            pass

        def summary(self):
            return summary

    monkeypatch.setitem(
        sys.modules, "readability", SimpleNamespace(Document=ReadabilityDocument)
    )

    result = html_to_markdown(
        "<html><body><header>Site Header</header>ignored"
        "<footer>Page Copyright</footer></body></html>"
    )

    assert "Div Article Title" in result
    assert "By Example Author" in result
    assert "Sources: Div Content Research Institute" in result
    assert "[Div Setup](#div-setup)" in result
    assert "Site Header" not in result
    assert "Page Copyright" not in result


def test_html_to_markdown_preserves_in_article_nav(monkeypatch):
    """Navigation inside Readability content can be article structure."""
    from scraper.fetch_quality import html_to_markdown

    summary = """<article>
    <h1>Guide to Example Systems</h1>
    <nav aria-label="Contents"><a href="#setup">Setup</a>
    <a href="#usage">Usage</a></nav>
    <p>This guide contains enough substantive content for the normal readability
    path and explains how example systems are configured and operated in detail.</p>
    <h2 id="setup">Setup</h2>
    <p>Follow these setup steps to prepare the system before using its features.</p>
    <h2 id="usage">Usage</h2>
    <p>Use the configured system to complete the workflow and review its results.</p>
    </article>"""

    class ReadabilityDocument:
        def __init__(self, _html):
            pass

        def summary(self):
            return summary

    monkeypatch.setitem(
        sys.modules, "readability", SimpleNamespace(Document=ReadabilityDocument)
    )

    result = html_to_markdown("<html><body>ignored</body></html>")

    assert "[Setup](#setup)" in result
    assert "[Usage](#usage)" in result


def test_html_to_markdown_spa_shell_falls_back_to_structural():
    """SPA shell HTML (no article content) should use structural fallback."""
    from scraper.fetch_quality import html_to_markdown

    # Simulate SPA shell HTML like what FlareSolverr returns for 1337x.to
    html = """<html><head>
    <title>1337x | Torrent Search Engine</title>
    <meta name="description" content="1337x is a search engine for torrents.">
    </head><body>
    <nav><a href="/">Home</a><a href="/top">Top 100</a></nav>
    <div id="app"></div>
    <footer>2024 1337x</footer>
    </body></html>"""
    result = html_to_markdown(html)
    assert len(result) > 50
    assert "1337x" in result
    assert "Torrent Search Engine" in result


def test_html_to_markdown_empty_html():
    """Empty HTML should return empty string."""
    from scraper.fetch_quality import html_to_markdown

    result = html_to_markdown("")
    assert result == ""


def test_html_to_markdown_minimal_html():
    """Minimal HTML with only a title and one word should still produce output."""
    from scraper.fetch_quality import html_to_markdown

    html = "<html><head><title>Page</title></head><body><p>Hello</p></body></html>"
    result = html_to_markdown(html)
    # Even minimal HTML should produce some text via structural fallback
    assert len(result) > 0
    assert "Page" in result or "Hello" in result


def test_html_to_markdown_structural_extracts_meta_description():
    """Structural fallback should extract meta description."""
    from scraper.fetch_quality import html_to_markdown

    html = """<html><head>
    <title>Search Results</title>
    <meta name="description" content="Browse and discover torrent files easily.">
    </head><body>
    <div id="root"></div>
    </body></html>"""
    result = html_to_markdown(html)
    assert "Browse and discover torrent files easily" in result


def test_html_to_markdown_structural_strips_non_content_tags(monkeypatch):
    """Structural fallback should strip script, style, nav, footer, header."""
    from scraper.fetch_quality import html_to_markdown

    class ReadabilityDocument:
        def __init__(self, _html):
            pass

        def summary(self):
            # Force the structural fallback rather than relying on a particular
            # readability-lxml scoring result for this shell page.
            return "<div>short readability fragment</div>"

    monkeypatch.setitem(
        sys.modules, "readability", SimpleNamespace(Document=ReadabilityDocument)
    )

    html = """<html><head><title>Test</title>
    <script>console.log('hidden')</script>
    <style>.hidden { display: none; }</style>
    </head><body>
    <nav>Skip this nav text</nav>
    <header>Skip this header text</header>
    <div id="root"></div>
    <footer>Skip this footer text</footer>
    </body></html>"""
    result = html_to_markdown(html)
    assert "Test" in result
    assert "console.log" not in result
    assert ".hidden" not in result
    assert "Skip this nav text" not in result
    assert "Skip this header text" not in result
    assert "Skip this footer text" not in result
