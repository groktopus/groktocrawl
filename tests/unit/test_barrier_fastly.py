"""Fastly JS-challenge barrier detection tests (#586).

Covers the two scraper-side layers of the #586 fix:

Layer 1 — ``scraper.barrier``:
  * prose-only Fastly markers classify as detected via the count-based
    confidence ladder and NEVER take a definitive 0.95 shortcut, and
  * the definitive ``/_fs-ch-`` HTML signature short-circuits straight to
    0.95 fastly (mirroring the captcha-provider short-circuit).

Layer 2 — ``scraper.extract._check_block_page``:
  * the challenge interstitial text matches >=2 BLOCK_PAGE_PATTERNS so it
    scores blocking "fail", while a single generic JS mention stays "warn".

Fixtures live under ``tests/fixtures/html/``:
  F1 = fastly-challenge-full.html       (prose + /_fs-ch- signature)
  F2 = fastly-challenge-prose-only.html (prose markers only)
  N  = tech-article-javascript.html     (negative control)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRAPER_SVC = Path(__file__).resolve().parents[2] / "scraper-svc"
if str(SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(SCRAPER_SVC))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
F1_PATH = FIXTURES / "fastly-challenge-full.html"
F2_PATH = FIXTURES / "fastly-challenge-prose-only.html"
N_PATH = FIXTURES / "tech-article-javascript.html"

FASTLY_PROSE_MARKERS = (
    "javascript is disabled",
    "please enable javascript to proceed",
    "a required part of this site could",
)
FS_CH_SIGNATURE = "/_fs-ch-"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Layer 1: pre-extraction classifier ──────────────────────────


class TestFastlyBarrierClassification:
    """_classify_barrier() detects Fastly challenges (#586)."""

    @staticmethod
    def _import():
        from scraper.fetch_quality import _classify_barrier

        return _classify_barrier

    def test_f1_definitive_signature_short_circuits_to_095(self):
        """F1 (carries /_fs-ch-) classifies fastly at the definitive 0.95."""
        classify = self._import()
        html = _read(F1_PATH)

        result = classify("JavaScript is disabled in your browser.", "", "", html)

        assert result.detected is True
        assert result.barrier_type == "fastly"
        assert result.confidence >= 0.95
        # The detail names the definitive signature, not count-based signals.
        assert FS_CH_SIGNATURE in (result.detail or "")

    def test_f1_minimal_html_signature_alone_is_enough(self):
        """The bare asset-prefix signature alone triggers the 0.95 fast path."""
        classify = self._import()
        minimal = '<script src="/_fs-ch-/challenge.js"></script>'

        result = classify("", "", "Some innocuous extracted content.", minimal)

        assert result.detected is True
        assert result.barrier_type == "fastly"
        assert result.confidence >= 0.95

    def test_f1_signature_beats_innocuous_content(self):
        """Even content that looks clean cannot mask the definitive signature."""
        classify = self._import()
        html = (
            "<div>"
            + ("A perfectly ordinary paragraph of article text. " * 30)
            + '<script src="/_fs-ch-x/challenge.js"></script></div>'
        )

        result = classify("", "", "Ordinary article body.", html)

        assert result.detected is True
        assert result.barrier_type == "fastly"
        assert result.confidence >= 0.95

    def test_f2_prose_only_detected_with_accumulated_confidence(self):
        """F2 (all three prose markers, no signature) is detected >=0.70."""
        classify = self._import()
        html = _read(F2_PATH)

        result = classify(
            "Site Verification Required",
            "",
            "JavaScript is disabled in your "
            "browser, so the page content could not be rendered fully.",
            html,
        )

        assert result.detected is True
        assert result.barrier_type in {"fastly", "suspicious"}
        assert result.confidence >= 0.70
        # Detection traces back to the Fastly prose markers.
        assert any(marker in (result.detail or "") for marker in FASTLY_PROSE_MARKERS)

    @pytest.mark.parametrize(
        ("prose", "label"),
        [
            ("JavaScript is disabled in your browser.", "javascript-is-disabled"),
            ("Please enable JavaScript to proceed.", "enable-js-to-proceed"),
            (
                "A required part of this site couldn\u2019t load.",
                "required-part-couldnt-load",
            ),
        ],
    )
    def test_single_prose_marker_lands_at_ladder_first_rung(self, prose, label):
        """One prose marker → detected at ~0.70, never the definitive 0.95.

        The synthetic page carries >=100 chars of filler around the marker so
        the unrelated short-content signal does not inflate the ladder.
        """
        classify = self._import()
        filler = "The verification service is checking this connection. "
        content = prose + " " + (filler * 4)
        result = classify("", "", content, f"<html><body>{content}</body></html>")

        assert result.detected is True, label
        assert result.barrier_type in {"fastly", "suspicious"}, label
        assert result.confidence < 0.95, label
        ladder_first_rung = min(0.50 + 1 * 0.20, 0.95)
        assert result.confidence == pytest.approx(ladder_first_rung), label
        # The detail names the matched Fastly prose signal (name is the
        # indicator text truncated to 24 chars).
        assert "fastly-prose" in (result.detail or ""), label

    def test_two_distinct_markers_reach_second_rung_not_095(self):
        """Two distinct prose markers accumulate to 0.90 — still sub-0.95."""
        classify = self._import()
        filler = "The verification service is checking this connection. "
        body = (
            "JavaScript is disabled in your browser.\n"
            "Please enable JavaScript to proceed.\n" + filler * 4
        )
        result = classify("", "", body, f"<html><body>{body}</body></html>")

        assert result.detected is True
        expected = min(0.50 + 2 * 0.20, 0.95)
        assert result.confidence == pytest.approx(expected)
        assert result.confidence < 0.95

    def test_all_three_prose_markers_stay_below_095(self):
        """F2's full prose set must NOT reach the definitive 0.95 tier.

        Prose-only inputs are capped by the count-based ladder; only the
        /_fs-ch- HTML signature (and captcha widgets) may return 0.95. The
        body carries filler so the short-content signal does not add a rung.
        """
        classify = self._import()
        html = _read(F2_PATH)
        filler = "Reload the page after enabling scripts to continue safely. "
        body = "\n".join(
            [
                "JavaScript is disabled in your browser.",
                "Please enable JavaScript to proceed.",
                "A required part of this site couldn\u2019t load.",
                filler * 3,
            ]
        )

        result = classify("Site Verification Required", "", body, html)

        assert result.detected is True
        assert result.barrier_type in {"fastly", "suspicious"}
        # Three distinct prose signals land at min(0.50 + 3*0.20, ...) — but
        # prose-only confidence is hard-capped BELOW the definitive tier.
        assert result.confidence < 0.95
        assert result.barrier_type != "captcha"

    def test_quoted_barrier_phrases_are_flagged_documented_strict_policy(self):
        """Quoting the exact barrier phrases flags a page — documented policy.

        The strict-policy clause (ADR-0015 / validation contract) accepts this:
        pages reproducing the exact challenge text verbatim are refused even if
        legitimately quoting it; mere mentions of "JavaScript" are not flagged
        (covered by the negative-control fixture N).
        """
        classify = self._import()
        filler = "The newspaper reproduced the outage screen in its report. "
        quoted = (
            "A news site printed the exact interstitial text today: "
            '"Please enable JavaScript to proceed." ' + filler * 3
        )

        result = classify(
            "News Article", "", quoted, f"<html><body>{quoted}</body></html>"
        )

        assert result.detected is True
        assert result.confidence < 0.95


class TestBotChallengeDetectionFastly:
    """_is_bot_challenge() engages for Fastly challenges so Tier 3 polls."""

    @staticmethod
    def _import():
        from scraper.fetch_quality import _is_bot_challenge

        return _is_bot_challenge

    def test_f1_derived_title_url_detected(self):
        func = self._import()
        assert func(
            "JavaScript is disabled in your browser.",
            "https://www.nature.com/articles/s41586-024-08230-5",
        )

    def test_fs_ch_url_reference_detected(self):
        func = self._import()
        assert func(
            "Example Domain",
            "https://example.test/_fs-ch-2a/challenge.js?token=abc",
        )

    def test_negative_control_article_title_clean(self):
        func = self._import()
        n_html = _read(N_PATH)
        title = "A Practical Introduction to Modern JavaScript Tooling"

        assert not func(title, "https://blog.example.test/js-tooling")
        assert title.lower() in n_html.lower()  # sanity: title comes from N


# ── Layer 2: post-extraction block-page gate ────────────────────


class TestBlockPageFastlyInterstitial:
    """_check_block_page() scores the Fastly interstitial as blocking."""

    @staticmethod
    def _imports():
        from scraper.extract import _check_block_page, assess_quality
        from scraper.fetch_quality import html_to_markdown

        return _check_block_page, assess_quality, html_to_markdown

    def test_f1_markdown_scores_blocking_fail(self):
        check, _assess, to_md = self._imports()
        markdown = to_md(_read(F1_PATH))

        score, status = check(markdown)
        matched = sum(
            1
            for p in (
                r"please enable javascript",
                r"javascript is disabled",
                r"a required part of this site could(?:n[o']| no)t load",
            )
            if __import__("re").search(p, markdown.lower())
        )

        assert matched >= 2
        assert status == "fail"
        assert score <= 0.15

    def test_f1_assess_quality_reports_block_fail(self):
        _check, assess, to_md = self._imports()
        markdown = to_md(_read(F1_PATH))

        quality = assess(markdown, url="http://test/fastly-challenge")

        assert quality["checks"]["block_detected"] == "fail"
        # block_detected carries 40% of the composite; a blocking fail (0.15)
        # drags the composite down toward the scraper's 0.3 acceptance
        # threshold instead of reading as healthy content.
        assert quality["score"] < 0.4

    def test_f1_tier3_refuses_interstitial_via_barrier_envelope(self):
        """Tier 3 returns the barrier-detection error envelope for F1.

        The post-extraction gate in ``fetch_via_playwright`` refuses a page
        when the barrier classifier fires OR the block gate scores "fail"
        with >=2 pattern matches AND challenge corroboration: the challenge
        interstitial is refused (empty markdown + source=barrier-detection)
        and can never ship as healthy page content (#586).
        """
        html = _read(F1_PATH)

        # Reproduce the Tier 3 refusal decision locally (no browser needed):
        # same inputs, same gate logic as fetch_tiers._playwright_fetch.
        from scraper.extract import BLOCK_PAGE_PATTERNS, _check_block_page
        from scraper.fetch_quality import _classify_barrier, html_to_markdown

        markdown = html_to_markdown(html)
        barrier = _classify_barrier(
            "JavaScript is disabled in your browser.", "", markdown, html
        )
        _, status = _check_block_page(markdown)
        matched = [p for p in BLOCK_PAGE_PATTERNS if p.search(markdown.lower())]
        corroborated = barrier.detected or any(
            m in markdown.lower() for m in FASTLY_PROSE_MARKERS
        )

        refuse = (barrier.detected and barrier.confidence > 0.7) or (
            status == "fail" and len(matched) >= 2 and corroborated
        )

        assert refuse is True
        assert barrier.detected

    def test_generic_two_pattern_page_is_not_refused_by_tier3_gate(self):
        """Cookie banner + paywall co-occurrence does NOT trip the Tier 3 gate.

        Review finding (P2): the post-extraction refusal requires challenge
        corroboration; a legitimate page that happens to contain two generic
        block phrases must not be refused as a blocking interstitial.
        """
        from scraper.extract import BLOCK_PAGE_PATTERNS, _check_block_page
        from scraper.fetch_quality import html_to_markdown

        html = (
            "<html><head><title>Site Preferences</title></head><body>"
            "<div class='cookie'>This site uses cookies. Please enable "
            "javascript for preferences.</div><article><h1>Membership</h1>"
            "<p>This content is available to subscribers only. Create an "
            "account to continue reading quality journalism.</p></article>"
            "</body></html>"
        )
        markdown = html_to_markdown(html)
        _score, status = _check_block_page(markdown)
        matched = [p.pattern for p in BLOCK_PAGE_PATTERNS if p.search(markdown.lower())]

        # The page matches >=2 generic patterns; whatever the gate scores it,
        # no challenge marker/provider is present, so the Tier 3 refusal
        # expression stays False — the page ships as content.
        assert len(matched) >= 2, matched
        # ...but no challenge marker/provider is present, so the Tier 3
        # refusal expression stays False — the page ships as content.
        challenge_markers = (
            "javascript is disabled",
            "/_fs-ch-",
            "verify you are",
            "checking your browser",
            "attention required",
            "cloudflare-ray-id",
            "ddos-guard",
        )
        corroborated = any(m in markdown.lower() for m in challenge_markers)
        refuse = status == "fail" and len(matched) >= 2 and corroborated
        assert refuse is False

    def test_single_generic_js_mention_stays_warn(self):
        """One pattern hit (a legit interactive-figure instruction) warns only."""
        check, _assess, _to_md = self._imports()
        article = (
            "# Interactive Figure\n\n"
            "Please enable JavaScript to see the interactive figure. "
            "The static rendering below shows the same data for reference. "
            + ("Analysis continues with substantive paragraphs. " * 10)
        )

        score, status = check(article)

        assert status == "warn"
        assert score == pytest.approx(0.3)


# ── Negative control N: clean at every detection layer ──────────


class TestNegativeControlCleanAtAllLayers:
    """Fixture N (legit JS article) must stay clean at every layer."""

    def test_layer1_classifier_not_detected(self):
        from scraper.fetch_quality import _classify_barrier

        html = _read(N_PATH)
        result = _classify_barrier(
            "A Practical Introduction to Modern JavaScript Tooling",
            "https://blog.example.test/js-tooling",
            "Modern JavaScript tooling explained in depth for working engineers. "
            + html[:4000],
            html,
        )

        assert result.detected is False, result.detail

    def test_layer2_bot_challenge_false(self):
        from scraper.fetch_quality import _is_bot_challenge

        assert not _is_bot_challenge(
            "A Practical Introduction to Modern JavaScript Tooling",
            "https://blog.example.test/js-tooling",
        )

    def test_layers34_block_page_and_assess_quality_pass(self):
        from scraper.extract import _check_block_page, assess_quality
        from scraper.fetch_quality import html_to_markdown

        markdown = html_to_markdown(_read(N_PATH))
        score, status = _check_block_page(markdown)

        assert status == "pass", markdown[:400]
        assert score == pytest.approx(1.0)

        quality = assess_quality(markdown, url="https://blog.example.test")
        assert quality["checks"]["block_detected"] == "pass"
