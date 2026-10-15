"""Regression coverage for terminal crawl outcome accounting (#351)."""

from unittest.mock import MagicMock

from agent.crawler import CrawlEngine, CrawlOptions


def _engine() -> CrawlEngine:
    return CrawlEngine(MagicMock(), options=CrawlOptions())


def test_zero_page_filter_has_stable_reason_and_counts():
    engine = _engine()
    engine._seen.add("https://example.com/private")
    engine._filtered_out.append(
        {"url": "https://example.com/private", "reason": "not_included"}
    )

    summary = engine._outcome_summary()

    assert summary["reason"] == "all_urls_filtered"
    assert summary["counts"] == {
        "discovered": 1,
        "filtered": 1,
        "robots_blocked": 0,
        "fetch_failed": 0,
        "barrier_rejected": 0,
        "duplicate": 0,
        "retained": 0,
    }


def test_unretained_seed_reports_fetch_failure():
    engine = _engine()
    engine._seen.add("https://example.com")
    engine._errors.append(
        {"url": "https://example.com", "error_type": "scrape_error"}
    )

    summary = engine._outcome_summary()

    assert summary["reason"] == "all_fetches_failed"
    assert summary["counts"]["fetch_failed"] == 1


def test_normal_single_page_has_no_zero_page_reason():
    engine = _engine()
    engine._seen.add("https://example.com")
    engine._pages.append({"url": "https://example.com"})

    summary = engine._outcome_summary()

    assert summary["reason"] is None
    assert summary["counts"]["retained"] == 1
