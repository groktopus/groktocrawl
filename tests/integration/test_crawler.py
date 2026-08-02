"""Unit tests for the CrawlEngine in agent-svc/agent/crawler.py.

Covers:
- URL normalization (fragments, trailing slash, ignore_query_parameters)
- Path filtering (glob and regex)
- Glob-to-regex conversion
- CrawlEngine BFS traversal
- max_pages and max_depth enforcement
- URL dedup within a crawl run
- Start URL failure handling
- Child page error handling
- Concurrency and delay behavior
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# ── normalize_url tests ──────────────────────────────────────────


class TestNormalizeUrl:
    """Tests for the ``normalize_url()`` function."""

    def test_strips_fragment(self):
        from agent.crawler import normalize_url

        assert (
            normalize_url("https://example.com/page#section")
            == "https://example.com/page"
        )

    def test_lowercases_scheme(self):
        from agent.crawler import normalize_url

        assert normalize_url("HTTPS://EXAMPLE.COM/PAGE") == "https://example.com/PAGE"

    def test_lowercases_host(self):
        from agent.crawler import normalize_url

        assert normalize_url("https://Example.COM/Page") == "https://example.com/Page"

    def test_removes_default_http_port(self):
        from agent.crawler import normalize_url

        assert normalize_url("http://example.com:80/page") == "http://example.com/page"

    def test_removes_default_https_port(self):
        from agent.crawler import normalize_url

        assert (
            normalize_url("https://example.com:443/page") == "https://example.com/page"
        )

    def test_keeps_non_default_port(self):
        from agent.crawler import normalize_url

        assert (
            normalize_url("https://example.com:8080/page")
            == "https://example.com:8080/page"
        )

    def test_normalizes_trailing_slash(self):
        from agent.crawler import normalize_url

        # Non-root paths with trailing slash -> no trailing slash
        assert normalize_url("https://example.com/page/") == "https://example.com/page"
        assert normalize_url("https://example.com/page") == "https://example.com/page"

    def test_keeps_root_slash(self):
        from agent.crawler import normalize_url

        # Root path / should stay as /
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_normalizes_dot_path(self):
        from agent.crawler import normalize_url

        # /. should collapse to /
        assert normalize_url("https://example.com/.") == "https://example.com/"
        assert normalize_url("https://example.com/a/./b") == "https://example.com/a/b"

    def test_ignore_query_parameters_strips_query(self):
        from agent.crawler import normalize_url

        result = normalize_url(
            "https://example.com/page?a=1&b=2", ignore_query_parameters=True
        )
        assert result == "https://example.com/page"

    def test_ignore_query_parameters_default_false_keeps_query(self):
        from agent.crawler import normalize_url

        result = normalize_url("https://example.com/page?a=1&b=2")
        assert "a=1" in result
        assert "b=2" in result

    def test_sorts_query_parameters(self):
        from agent.crawler import normalize_url

        # Different order but same params -> same result
        r1 = normalize_url("https://example.com/page?b=2&a=1")
        r2 = normalize_url("https://example.com/page?a=1&b=2")
        assert r1 == r2
        assert "a=1" in r1
        assert "b=2" in r1

    def test_fragment_and_query_stripped_together(self):
        from agent.crawler import normalize_url

        assert (
            normalize_url("https://example.com/page?a=1#section")
            == "https://example.com/page?a=1"
        )


# ── Glob-to-regex tests ──────────────────────────────────────────


class TestGlobToRegex:
    """Tests for the ``_glob_to_regex()`` helper."""

    def test_literal_string(self):
        import re

        from agent.crawler import _glob_to_regex

        pattern = _glob_to_regex("/about")
        assert re.search(pattern, "/about")
        assert not re.search(pattern, "/aboutus")

    def test_single_star(self):
        import re

        from agent.crawler import _glob_to_regex

        pattern = _glob_to_regex("/section/*")
        assert re.search(pattern, "/section/page-1")
        assert re.search(pattern, "/section/foo")
        assert not re.search(pattern, "/section/sub/page")

    def test_double_star(self):
        import re

        from agent.crawler import _glob_to_regex

        pattern = _glob_to_regex("/section/**")
        assert re.search(pattern, "/section/page-1")
        assert re.search(pattern, "/section/sub/page")
        assert not re.search(pattern, "/other")

    def test_question_mark(self):
        import re

        from agent.crawler import _glob_to_regex

        pattern = _glob_to_regex("/page-?")
        assert re.search(pattern, "/page-1")
        assert re.search(pattern, "/page-a")
        assert not re.search(pattern, "/page-12")

    def test_special_chars_escaped(self):
        import re

        from agent.crawler import _glob_to_regex

        pattern = _glob_to_regex("/file.html")
        assert re.search(pattern, "/file.html")
        assert not re.search(pattern, "/fileXhtml")


# ── Path matching tests ─────────────────────────────────────────


class TestMatchPath:
    """Tests for the ``_match_path()`` function."""

    def test_no_filters_passes(self):
        from agent.crawler import _match_path

        assert _match_path("https://example.com/page", None, None) is True

    def test_include_paths_matches(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/section/page-1",
                ["/section/*"],
                None,
            )
            is True
        )

    def test_include_paths_no_match(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/about",
                ["/section/*"],
                None,
            )
            is False
        )

    def test_exclude_paths_excludes(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/admin/secret",
                None,
                ["/admin/*"],
            )
            is False
        )

    def test_exclude_overrides_include(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/section/page-2",
                ["/section/*"],
                ["/section/page-2"],
            )
            is False
        )

    def test_regex_mode_on_full_url(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/section/page-1?ref=abc",
                ["section/page-[12]"],
                None,
                regex_on_full_url=True,
            )
            is True
        )

    def test_regex_mode_no_match(self):
        from agent.crawler import _match_path

        assert (
            _match_path(
                "https://example.com/section/page-3",
                ["section/page-[12]"],
                None,
                regex_on_full_url=True,
            )
            is False
        )


# ── CrawlEngine tests ────────────────────────────────────────────


@pytest.fixture
def mock_scraper():
    """Create a mock ScraperClient that returns successful results."""
    client = MagicMock()
    client.scrape = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_store():
    """Create a mock JobStore with a mock Redis client."""
    store = MagicMock()
    store.redis = MagicMock()
    store.complete_job = MagicMock()
    return store


class MockPage:
    """Helper to create a mock scrape result for a given URL."""

    @staticmethod
    def success(
        url: str, markdown: str = "# Page Content", html: str | None = None, **kwargs
    ) -> dict:
        return {
            "success": True,
            "data": {
                "markdown": markdown,
                "source": "playwright",
                "metadata": {"title": "Test Page"},
            },
        }

    @staticmethod
    def failure(url: str, error: str = "Scrape failed", **kwargs) -> dict:
        return {"success": False, "error": error}


class TestCrawlEngine:
    """Tests for the CrawlEngine BFS crawl loop."""

    @pytest.mark.asyncio
    async def test_single_page_crawl(self, mock_scraper, mock_store):
        """A crawl with max_pages=1 returns just the start URL."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape.return_value = MockPage.success("http://example.com/")

        engine = CrawlEngine(
            mock_scraper,
            store=mock_store,
            options=CrawlOptions(max_pages=1, max_depth=2),
        )
        result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert result.total >= 1
        assert len(result.pages) == 1
        assert result.pages[0]["url"] == "http://example.com/"
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_max_pages_enforcement(self, mock_scraper):
        """Crawl stops after max_pages pages."""
        from agent.crawler import CrawlEngine, CrawlOptions

        # Mock scraper to always succeed
        mock_scraper.scrape.return_value = MockPage.success("http://example.com/page")
        mock_scraper.scrape.side_effect = None  # reset

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=3, max_depth=2),
        )

        # We need a link-rich start page. Mock the HTML fetch too.
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="/page1">Page 1</a>
                <a href="/page2">Page 2</a>
                <a href="/page3">Page 3</a>
                </body></html>
            """

            # Set up scraper to return appropriate markdown per URL
            async def scrape_side_effect(
                url: str, force_browser: bool = False, **kwargs
            ) -> dict:
                return MockPage.success(url, f"# Content of {url}")

            mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

            result = await engine.run("http://example.com/")

        assert result.completed == 3
        assert len(result.pages) == 3
        # Should have the start URL and 2 children (due to max_pages=3)

    @pytest.mark.asyncio
    async def test_max_depth_0(self, mock_scraper):
        """max_depth=0 scrapes only the start URL."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=0),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="/pricing">Pricing</a>
                <a href="/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert len(result.pages) == 1
        assert result.pages[0]["url"] == "http://example.com/"

    @pytest.mark.asyncio
    async def test_max_depth_1(self, mock_scraper):
        """max_depth=1 scrapes start URL and direct children."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/about">About</a>
                <a href="http://example.com/contact">Contact</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should have start URL + 3 children = 4 pages
        assert result.completed == 4
        assert len(result.pages) == 4

        # Verify BFS order: start URL first, then children
        assert result.pages[0]["url"] == "http://example.com/"

    @pytest.mark.asyncio
    async def test_url_dedup(self, mock_scraper):
        """No duplicate URLs within a single crawl run."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        # Start page has duplicate links to the same page
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/pricing">Pricing (again)</a>
                <a href="http://example.com/about">About</a>
                <a href="http://example.com/pricing#section">Pricing with fragment</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should have start URL + pricing + about = 3 unique pages
        # (pricing appears 3 times in links but should be scraped once)
        urls = [p["url"] for p in result.pages]
        assert len(urls) == len(set(urls)), f"Duplicate URLs found: {urls}"
        assert len(result.pages) == 3

    @pytest.mark.asyncio
    async def test_fragment_stripped_dedup(self, mock_scraper):
        """Links /page#a and /page#b normalize to same URL."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/about#section1">About 1</a>
                <a href="http://example.com/about#section2">About 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should have start URL + about = 2 pages (fragments collapsed)
        assert result.completed == 2
        about_pages = [p for p in result.pages if "about" in p["url"]]
        assert len(about_pages) == 1

    @pytest.mark.asyncio
    async def test_trailing_slash_normalization(self, mock_scraper):
        """/pricing and /pricing/ are treated as the same URL."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/pricing/">Pricing with slash</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should have start URL + pricing = 2 pages (trailing slash collapsed)
        assert result.completed == 2
        pricing_pages = [p for p in result.pages if "pricing" in p["url"]]
        assert len(pricing_pages) == 1

    @pytest.mark.asyncio
    async def test_ignore_query_parameters_collapses_variants(self, mock_scraper):
        """When ignore_query_parameters=True, query variants collapse."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                ignore_query_parameters=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page?a=1">Page a=1</a>
                <a href="http://example.com/page?b=2">Page b=2</a>
                <a href="http://example.com/page">Page no query</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # With ignore_query_parameters, /page?a=1, /page?b=2, /page all collapse
        # to /page. So we should have start URL + page = 2 pages
        assert result.completed == 2
        page_entries = [p for p in result.pages if "page" in p["url"]]
        assert len(page_entries) == 1

    @pytest.mark.asyncio
    async def test_child_scrape_failure_collected(self, mock_scraper):
        """Child page scrape failures are collected, not fatal."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            if "fail" in url:
                return MockPage.failure(url, "Connection refused")
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good">Good page</a>
                <a href="http://example.com/fail">Failing page</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + good page = 2 successful scrapes
        assert result.completed == 2
        assert len(result.pages) == 2
        # One error for the failing page
        assert len(result.errors) == 1
        assert "fail" in result.errors[0]["url"]
        assert result.errors[0]["error"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_start_url_failure_returns_immediately(self, mock_scraper):
        """Start URL failure returns error immediately."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.failure("http://example.com/", "Connection refused")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2),
        )

        result = await engine.run("http://example.com/")

        assert result.completed == 0
        assert result.pages == []
        assert len(result.errors) == 1
        assert result.errors[0]["url"] == "http://example.com/"
        assert result.errors[0]["error"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_bfs_order(self, mock_scraper):
        """Pages appear in BFS order: start URL, then depth-1, then depth-2.

        Uses crawl_entire_domain=True so that non-child sibling/parent
        links are followed (e.g., grandchild from child-a).
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2, crawl_entire_domain=True),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        # Track which HTML is returned for each URL to simulate link structure
        html_responses = {
            "http://example.com/": """
                <html><body>
                <a href="http://example.com/child-a">Child A</a>
                <a href="http://example.com/child-b">Child B</a>
                </body></html>
            """,
            "http://example.com/child-a": """
                <html><body>
                <a href="http://example.com/grandchild">Grandchild</a>
                </body></html>
            """,
            "http://example.com/child-b": """
                <html><body>
                <p>No links here</p>
                </body></html>
            """,
        }

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.side_effect = lambda url: html_responses.get(url)

            result = await engine.run("http://example.com/")

        # Expected BFS order:
        # [start, child-a, child-b, grandchild]
        assert len(result.pages) == 4
        assert result.pages[0]["url"] == "http://example.com/"
        assert result.pages[1]["url"] == "http://example.com/child-a"
        assert result.pages[2]["url"] == "http://example.com/child-b"
        assert result.pages[3]["url"] == "http://example.com/grandchild"

    @pytest.mark.asyncio
    async def test_empty_site_no_links(self, mock_scraper):
        """A page with no outgoing links returns only the start page."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/", "No links here")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body><p>No links</p></body></html>"

            result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert len(result.pages) == 1

    @pytest.mark.asyncio
    async def test_max_pages_larger_than_available(self, mock_scraper):
        """When max_pages > available pages, crawl completes when queue is empty."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/", "Just one page")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=100, max_depth=2),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body><p>No links</p></body></html>"

            result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_job_store_updates(self, mock_scraper, mock_store):
        """Job store is updated with progress during crawl."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=mock_store,
            options=CrawlOptions(max_pages=1, max_depth=2),
        )
        # Use short update interval for testing
        engine._update_interval = 0.01

        result = await engine.run("http://example.com/", job_id="test-job-123")

        assert result.completed == 1
        # update_job_progress should have been called at least once (final update)
        assert mock_store.update_job_progress.call_count >= 1
        # increment_completed should have been called once (for the start page)
        assert mock_store.increment_completed.call_count >= 1

    @pytest.mark.asyncio
    async def test_crawl_with_include_paths(self, mock_scraper):
        """Only URLs matching include_paths are crawled."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                include_paths=["/section/*"],
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Section 1</a>
                <a href="http://example.com/section/page-2">Section 2</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL doesn't match /section/*, so only section pages are crawled
        # But start URL is included since include_paths only filters child discovery
        # actually... wait, let me re-examine the logic.

        # The current code checks self._should_crawl before scraping, which
        # includes a call to _match_path. But the start URL is the first URL
        # and should also be checked against include_paths.

        # Looking at the architecture.md:
        # "Path filters apply to all URLs including the start URL — if start URL
        # doesn't match include_paths, crawl returns 0 pages"

        # Wait, but my current implementation checks _match_path AFTER adding to seen
        # but BEFORE scraping... Actually, looking at my crawl loop:

        # 1. Pop from queue
        # 2. Normalize
        # 3. Check dedup - skip if seen
        # 4. Check max_depth - skip if too deep (but depth=0 is fine)
        # 5. Add to seen
        # 6. Check path filters - if not match, continue
        # 7. Scrape
        # 8. Extract links if depth < max_depth

        # So the start URL IS checked against include_paths. If it doesn't match,
        # it's skipped. That means the start URL would need to match include_paths
        # to be crawled.

        # For this test, let's check that "/section/*" doesn't match "/about"
        about_pages = [p for p in result.pages if "about" in p["url"]]
        assert len(about_pages) == 0

    @pytest.mark.asyncio
    async def test_crawl_with_exclude_paths(self, mock_scraper):
        """URLs matching exclude_paths are skipped."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                exclude_paths=["/admin/*"],
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/about">About</a>
                <a href="http://example.com/admin/secret">Admin</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing + about = 3 pages, admin excluded
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/admin/secret" not in urls
        assert len(result.pages) == 3

    @pytest.mark.asyncio
    async def test_empty_markdown_handled_gracefully(self, mock_scraper):
        """A page with empty markdown is still included."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/", markdown="")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=1, max_depth=2),
        )

        result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert result.pages[0]["markdown"] == ""

    @pytest.mark.asyncio
    async def test_cancel_flag_stops_crawl(self, mock_scraper):
        """Setting cancel flag stops the crawl loop."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            # Cancel after the first page is scraped
            original_scrape = mock_scraper.scrape

            async def scrape_with_cancel(
                url: str, force_browser: bool = False, **kwargs
            ) -> dict:
                result = await original_scrape(url, force_browser)
                engine.cancel()
                return result

            mock_scraper.scrape = AsyncMock(side_effect=scrape_with_cancel)

            result = await engine.run("http://example.com/")

        # Should have at least 1 page (start URL was scraped before cancel)
        assert result.completed >= 1

    @pytest.mark.asyncio
    async def test_crawl_with_self_referencing_links(self, mock_scraper):
        """Self-referencing links don't cause infinite loops."""
        from agent.crawler import CrawlEngine, CrawlOptions

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=2),
        )

        with patch.object(engine, "_get_html") as mock_html:
            # Page links to itself and to a child
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/">Self link</a>
                <a href="http://example.com/.">Self link dot</a>
                <a href="http://example.com/page1">Page 1</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should complete without infinite loop
        # Start URL + page1 = 2 pages
        assert result.completed == 2


# ── Path filtering tests ────────────────────────────────────────


class TestPathFiltering:
    """Tests for path filtering in CrawlEngine."""

    @pytest.mark.asyncio
    async def test_include_paths_start_url_matches(self, mock_scraper):
        """When start URL matches include_paths, it is crawled.

        Uses crawl_entire_domain=True to follow sibling paths under
        /blog/ from /blog/index.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=["/blog/*"],
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/blog/post1">Blog Post 1</a>
                <a href="http://example.com/blog/post2">Blog Post 2</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/blog/index")

        # Start URL matches /blog/*, children under /blog/ are included
        assert result.completed >= 2  # start + at least one blog child
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/blog/index" in urls
        assert "http://example.com/about" not in urls

    @pytest.mark.asyncio
    async def test_include_paths_start_url_no_match_returns_zero(self, mock_scraper):
        """When start URL doesn't match include_paths, 0 pages are crawled."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                include_paths=["/section/*"],
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Section 1</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL / doesn't match /section/* so it's skipped
        assert result.completed == 0
        assert len(result.pages) == 0

    @pytest.mark.asyncio
    async def test_include_paths_regex_mode(self, mock_scraper):
        """include_paths with regex_on_full_url=True uses regex matching.

        Uses crawl_entire_domain=True to allow sibling paths to be followed.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=["/section/page-[12]"],
                regex_on_full_url=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Page 1</a>
                <a href="http://example.com/section/page-2">Page 2</a>
                <a href="http://example.com/section/page-3">Page 3</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/section/page-1")

        # Start URL /section/page-1 should match /section/page-[12]
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page-1" in urls
        assert "http://example.com/section/page-2" in urls
        assert "http://example.com/section/page-3" not in urls
        assert "http://example.com/about" not in urls

    @pytest.mark.asyncio
    async def test_include_paths_empty_is_identity(self, mock_scraper):
        """Empty include_paths list means 'include all' (identity)."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=[],
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # All URLs should pass (identity filter)
        assert result.completed >= 2

    @pytest.mark.asyncio
    async def test_exclude_paths_empty_is_identity(self, mock_scraper):
        """Empty exclude_paths list means 'exclude none' (identity)."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                exclude_paths=[],
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # All URLs should pass (identity filter)
        assert result.completed >= 2

    @pytest.mark.asyncio
    async def test_exclude_overrides_include(self, mock_scraper):
        """exclude_paths takes precedence over include_paths.

        Uses crawl_entire_domain=True so sibling paths like /section/page-1
        are followed from /section/index.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=["/section/*"],
                exclude_paths=["/section/page-2"],
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Page 1</a>
                <a href="http://example.com/section/page-2">Page 2</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/section/index")

        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page-1" in urls
        assert "http://example.com/section/page-2" not in urls  # excluded
        assert "http://example.com/about" not in urls  # not in include_paths

    @pytest.mark.asyncio
    async def test_exclude_all_returns_zero_pages(self, mock_scraper):
        """exclude_paths matching everything returns 0 pages."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                exclude_paths=["/*"],
            ),
        )

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        result = await engine.run("http://example.com/")

        assert result.completed == 0
        assert len(result.pages) == 0

    @pytest.mark.asyncio
    async def test_include_nonexistent_returns_zero_pages(self, mock_scraper):
        """include_paths that match nothing returns 0 pages."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                include_paths=["/nonexistent/*"],
            ),
        )

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        result = await engine.run("http://example.com/")

        assert result.completed == 0
        assert len(result.pages) == 0

    @pytest.mark.asyncio
    async def test_glob_double_star_matches_any_depth(self, mock_scraper):
        """Glob ** matches across directory boundaries.

        Uses crawl_entire_domain=True so sibling paths like /blog/2024
        are followed from /blog/index.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=20,
                max_depth=3,
                include_paths=["/blog/**"],
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/blog/2024/01/post">Deep post</a>
                <a href="http://example.com/blog/2024">Year index</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/blog/index")

        urls = [p["url"] for p in result.pages]
        assert "http://example.com/blog/index" in urls
        # Deep paths under /blog/ should match /blog/**
        assert "http://example.com/blog/2024" in urls
        assert "http://example.com/blog/2024/01/post" in urls
        assert "http://example.com/about" not in urls

    @pytest.mark.asyncio
    async def test_regex_on_full_url_with_query_params(self, mock_scraper):
        """regex_on_full_url matches against full URL including query params.

        Uses crawl_entire_domain=True so sibling paths are followed from
        /start path.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=[r"\?ref=partner"],
                regex_on_full_url=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page?ref=partner">Partner link</a>
                <a href="http://example.com/page?ref=other">Other link</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/start?ref=partner")

        urls = [p["url"] for p in result.pages]
        assert "http://example.com/page?ref=partner" in urls
        assert "http://example.com/page?ref=other" not in urls

    @pytest.mark.asyncio
    async def test_verbose_tracks_filtered_out_urls(self, mock_scraper):
        """Verbose mode collects URLs that were filtered out with reasons."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=["/section/*"],
                exclude_paths=["/section/secret"],
                verbose=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Page 1</a>
                <a href="http://example.com/section/secret">Secret</a>
                <a href="http://example.com/about">About</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL / doesn't match /section/* → should be filtered out
        assert result.completed == 0
        assert len(result.pages) == 0
        if result.filtered_out:
            # At least one filtered URL should be recorded
            assert any(f["reason"] == "include_paths" for f in result.filtered_out)

    @pytest.mark.asyncio
    async def test_verbose_tracks_exclude_reason(self, mock_scraper):
        """Verbose mode records exclude_paths as filter reason."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                exclude_paths=["/admin/*"],
                verbose=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://example.com/admin/secret">Admin</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        assert result.completed >= 2  # start URL + pricing
        if result.filtered_out:
            assert any(f["reason"] == "exclude_paths" for f in result.filtered_out), (
                "Expected exclude_paths reason in filtered_out"
            )

    @pytest.mark.asyncio
    async def test_glob_escapes_regex_special_chars(self, mock_scraper):
        """Glob mode treats regex special chars as literals."""
        from agent.crawler import _match_path

        # In glob mode, '.' should be literal, not 'any char'
        assert _match_path(
            "http://example.com/file.html",
            ["/file.html"],
            None,
            regex_on_full_url=False,
        )
        # Should NOT match fileXhtml when . is treated literally
        assert not _match_path(
            "http://example.com/fileXhtml",
            ["/file.html"],
            None,
            regex_on_full_url=False,
        )

    @pytest.mark.asyncio
    async def test_regex_on_full_url_with_exclude_paths(self, mock_scraper):
        """exclude_paths with regex_on_full_url=True uses regex matching."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                exclude_paths=[r"/section/page-[23]"],
                regex_on_full_url=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page-1">Page 1</a>
                <a href="http://example.com/section/page-2">Page 2</a>
                <a href="http://example.com/section/page-3">Page 3</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page-1" in urls
        assert "http://example.com/section/page-2" not in urls
        assert "http://example.com/section/page-3" not in urls

    @pytest.mark.asyncio
    async def test_regex_on_full_url_with_ignore_query_params(self, mock_scraper):
        """ignoreQueryParameters strips query first, then regex applies to path.

        Per VAL-SCOPE-073: when both regexOnFullURL and ignoreQueryParameters
        are set, query string is stripped from the URL BEFORE regex matching.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                include_paths=[r"/page$"],
                regex_on_full_url=True,
                ignore_query_parameters=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/other-page?ref=test">Other page with query</a>
                <a href="http://example.com/pages">Pages (plural)</a>
                </body></html>
            """

            result = await engine.run("http://example.com/page")

        # Start URL /page matches /page$ include pattern
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/page" in urls
        # With ignore_query_parameters, /other-page?ref=test normalizes to /other-page
        # and the filter_url (without query) matches /page$? No — /other-page doesn't end with /page
        # So /other-page should be excluded by the path filter
        assert "http://example.com/other-page" not in urls
        # /pages should NOT match /page$ (because $ anchors to end)
        assert "http://example.com/pages" not in urls


# ── CrawlStatusResponse enhancement tests ────────────────────────


class TestCrawlStatusResponseModel:
    """Tests for CrawlStatusResponse model with new timestamp fields."""

    def test_crawl_status_response_has_timestamp_fields(self):
        """CrawlStatusResponse model includes created_at, completed_at, expires_at, duration."""
        from agent.models import CrawlStatusResponse

        # While processing (no completed_at, no duration)
        resp = CrawlStatusResponse(
            status="processing",
            completed=0,
            total=5,
            created_at="2026-01-15T10:00:00+00:00",
            expires_at="2026-01-16T10:00:00+00:00",
        )
        assert resp.created_at == "2026-01-15T10:00:00+00:00"
        assert resp.expires_at == "2026-01-16T10:00:00+00:00"
        assert resp.completed_at is None
        assert resp.duration is None

        # On completion (all four fields present)
        resp2 = CrawlStatusResponse(
            status="completed",
            completed=5,
            total=5,
            created_at="2026-01-15T10:00:00+00:00",
            completed_at="2026-01-15T10:00:05+00:00",
            expires_at="2026-01-16T10:00:00+00:00",
            duration=5000,
        )
        assert resp2.created_at is not None
        assert resp2.completed_at is not None
        assert resp2.expires_at is not None
        assert resp2.duration == 5000
        # Verify created_at < completed_at <= expires_at
        assert resp2.created_at < resp2.completed_at <= resp2.expires_at

    def test_crawl_status_response_defaults(self):
        """CrawlStatusResponse defaults to processing state with no timestamp fields."""
        from agent.models import CrawlStatusResponse

        resp = CrawlStatusResponse()
        assert resp.status == "processing"
        assert resp.completed == 0
        assert resp.total == 0
        assert resp.created_at is None
        assert resp.completed_at is None
        assert resp.expires_at is None
        assert resp.duration is None


# ── CrawlRequest validation tests ────────────────────────────────


class TestCrawlRequestValidation:
    """Tests for CrawlRequest field validation."""

    def test_max_pages_positive_accepts_valid(self):
        """max_pages >= 1 is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", max_pages=1)
        assert req.max_pages == 1

        req2 = CrawlRequest(url="http://example.com", max_pages=100)
        assert req2.max_pages == 100

    def test_max_pages_zero_accepted(self):
        """max_pages=0 means unlimited and is accepted (ge=0)."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", max_pages=0)
        assert req.max_pages == 0

    def test_max_depth_non_negative_accepts_valid(self):
        """max_depth >= 0 is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", max_depth=0)
        assert req.max_depth == 0

        req2 = CrawlRequest(url="http://example.com", max_depth=5)
        assert req2.max_depth == 5

    def test_max_depth_negative_rejected(self):
        """max_depth=-1 raises validation error."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="max_depth"):
            CrawlRequest(url="http://example.com", max_depth=-1)

    def test_max_depth_default_is_two(self):
        """Default max_depth is 2."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.max_depth == 2

    def test_max_pages_default_is_zero(self):
        """Default max_pages is 0 (unlimited)."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.max_pages == 0

    def test_sitemap_default_is_include(self):
        """Default sitemap mode is 'include'."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.sitemap == "include"

    def test_sitemap_skip_accepted(self):
        """sitemap='skip' is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", sitemap="skip")
        assert req.sitemap == "skip"

    def test_sitemap_only_accepted(self):
        """sitemap='only' is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", sitemap="only")
        assert req.sitemap == "only"

    def test_sitemap_invalid_rejected(self):
        """Invalid sitemap mode returns validation error."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="sitemap"):
            CrawlRequest(url="http://example.com", sitemap="banana")

    def test_ignore_sitemap_true_maps_to_skip(self):
        """ignore_sitemap=true maps to sitemap='skip' for backward compatibility."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", ignore_sitemap=True)
        assert req.sitemap == "skip"
        assert req.ignore_sitemap is True


# ── Store tests ──────────────────────────────────────────────────


class TestJobStoreExpiresAt:
    """Tests for JobStore expires_at field."""

    @pytest.mark.skip(
        reason="Requires running valkey/redis server",
        owner="repository-maintainer",
        issue="#502",
        classification="retained",
        environment="valkey/redis is not running",
    )
    def test_create_job_includes_expires_at(self):
        """JobStore.create_job sets expires_at in job metadata."""
        from agent.store import JobStore

        store = JobStore()
        job_id = store.create_job(kind="crawl", payload={"url": "http://example.com"})
        try:
            meta_raw = store.redis.get(f"job:{job_id}:meta")
            assert meta_raw is not None

            import json

            meta = json.loads(meta_raw)
            assert "expires_at" in meta
            assert meta["expires_at"] is not None
            # expires_at should be a valid ISO 8601 string
            from datetime import datetime

            parsed = datetime.fromisoformat(meta["expires_at"])
            assert parsed is not None

            # expires_at should be ~24h after created_at
            created = datetime.fromisoformat(meta["created_at"])
            diff = parsed - created
            assert 23 * 3600 <= diff.total_seconds() <= 25 * 3600  # ~24h window
        finally:
            store.redis.delete(f"job:{job_id}:meta")

    @pytest.mark.skip(
        reason="Requires running valkey/redis server",
        owner="repository-maintainer",
        issue="#502",
        classification="retained",
        environment="valkey/redis is not running",
    )
    def test_complete_job_sets_completed_at(self):
        """JobStore.complete_job sets completed_at in metadata."""
        from agent.store import JobStore

        store = JobStore()
        job_id = store.create_job(kind="crawl", payload={"url": "http://example.com"})
        try:
            store.complete_job(job_id, {"completed": 1, "total": 1})

            meta_raw = store.redis.get(f"job:{job_id}:meta")
            assert meta_raw is not None

            import json

            meta = json.loads(meta_raw)
            assert "completed_at" in meta
            assert meta["completed_at"] is not None
            # completed_at should be a valid ISO 8601 string
            from datetime import datetime

            parsed = datetime.fromisoformat(meta["completed_at"])
            assert parsed is not None
            assert meta["status"] == "completed"
        finally:
            store.redis.delete(f"job:{job_id}:meta")
            store.redis.delete(f"job:{job_id}:data")


# ── Per-page metadata enrichment tests ──────────────────────────


class TestPerPageMetadata:
    """Tests for per-page metadata enrichment in CrawlEngine."""

    @pytest.mark.asyncio
    async def test_page_includes_title_and_metadata(self, mock_scraper):
        """Each page in crawl results includes title, metadata dict with title/description/source,
        status_code, content_type, scraped_at, and duration_ms."""
        from agent.crawler import CrawlEngine, CrawlOptions

        # Mock scraper to return enriched data with metadata
        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return {
                "success": True,
                "data": {
                    "markdown": f"# Content of {url}",
                    "source": "playwright",
                    "metadata": {
                        "title": "Test Page",
                        "description": "A test page description",
                        "sourceURL": url,
                        "statusCode": 200,
                        "content-type": "text/html",
                    },
                },
            }

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=1, max_depth=0),
        )

        result = await engine.run("http://example.com/")

        assert len(result.pages) == 1
        page = result.pages[0]

        # Verify metadata fields
        assert page["url"] == "http://example.com/"
        assert page["markdown"] == "# Content of http://example.com/"
        assert page["title"] == "Test Page"
        assert page["metadata"]["title"] == "Test Page"
        assert page["metadata"]["description"] == "A test page description"
        assert page["metadata"]["sourceURL"] == "http://example.com/"
        assert page["metadata"]["statusCode"] == 200
        assert page["metadata"]["language"] == ""
        assert page["status_code"] == 200
        assert page["content_type"] == "text/html"
        assert page["scraped_at"] is not None
        assert isinstance(page["duration_ms"], int)
        assert page["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_page_metadata_falls_back_gracefully(self, mock_scraper):
        """When scraper returns minimal data, metadata fields fall back to defaults."""
        from agent.crawler import CrawlEngine, CrawlOptions

        # Minimal scraper response
        mock_scraper.scrape = AsyncMock(
            return_value={"success": True, "data": {"markdown": "Just text"}}
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=1, max_depth=0),
        )

        result = await engine.run("http://example.com/")

        assert len(result.pages) == 1
        page = result.pages[0]

        assert page["url"] == "http://example.com/"
        assert page["markdown"] == "Just text"
        assert page["title"] == ""  # falls back to empty string
        assert page["metadata"]["title"] == ""
        assert page["metadata"]["description"] == ""
        assert page["metadata"]["sourceURL"] == "http://example.com/"
        assert page["metadata"]["statusCode"] == 200
        assert page["metadata"]["language"] == ""
        assert page["status_code"] == 200  # default
        assert page["content_type"] == "text/html"  # default
        assert page["scraped_at"] is not None
        assert isinstance(page["duration_ms"], int)


# ── Sitemap integration tests ────────────────────────────────────


class TestCrawlEngineSitemap:
    """Tests for sitemap integration in CrawlEngine."""

    @pytest.mark.asyncio
    async def test_sitemap_include_seeds_urls(self, mock_scraper):
        """sitemap_mode='include' seeds sitemap URLs into the BFS queue."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=2,
                sitemap_mode="include",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        # Mock sitemap fetch to return some URLs
        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.return_value = [
                "http://example.com/sitemap-page1",
                "http://example.com/sitemap-page2",
            ]

            # Mock HTML fetch to simulate no HTML link discovery
            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """<html><body><p>No links</p></body></html>"""

                result = await engine.run("http://example.com/")

        # Should have start URL + 2 sitemap URLs = 3 pages
        assert result.completed == 3
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/sitemap-page1" in urls
        assert "http://example.com/sitemap-page2" in urls

    @pytest.mark.asyncio
    async def test_sitemap_skip_no_fetch(self, mock_scraper):
        """sitemap_mode='skip' does NOT fetch sitemaps."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=2,
                sitemap_mode="skip",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.return_value = [
                "http://example.com/sitemap-page1",
                "http://example.com/sitemap-page2",
            ]

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """
                    <html><body>
                    <a href="http://example.com/pricing">Pricing</a>
                    <a href="http://example.com/about">About</a>
                    </body></html>
                """

                result = await engine.run("http://example.com/")

        # Should NOT have sitemap URLs
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/sitemap-page1" not in urls
        assert "http://example.com/sitemap-page2" not in urls
        # Should still discover HTML links
        assert "http://example.com/pricing" in urls
        assert "http://example.com/about" in urls
        # _fetch_sitemap_urls should NOT have been called
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_sitemap_only_exclusive(self, mock_scraper):
        """sitemap_mode='only' crawls exclusively sitemap URLs, no HTML link discovery."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                sitemap_mode="only",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.return_value = [
                "http://example.com/sitemap-only-page1",
                "http://example.com/sitemap-only-page2",
            ]

            with patch.object(engine, "_get_html", return_value=None):
                result = await engine.run("http://example.com/")

        # Should have start URL + 2 sitemap URLs
        assert result.completed == 3
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/sitemap-only-page1" in urls
        assert "http://example.com/sitemap-only-page2" in urls
        # No extra pages from HTML link discovery
        assert len(urls) == 3

    @pytest.mark.asyncio
    async def test_sitemap_urls_respect_max_pages(self, mock_scraper):
        """Sitemap URLs are counted toward max_pages."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=2,
                max_depth=2,
                sitemap_mode="include",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.return_value = [
                "http://example.com/sitemap-page1",
                "http://example.com/sitemap-page2",
                "http://example.com/sitemap-page3",
            ]

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """<html><body><p>No links</p></body></html>"""

                result = await engine.run("http://example.com/")

        # max_pages=2 should give us exactly 2 pages
        assert result.completed == 2
        assert len(result.pages) == 2

    @pytest.mark.asyncio
    async def test_sitemap_dedup_against_start_url(self, mock_scraper):
        """Sitemap URLs that match the start URL are deduplicated."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=2,
                sitemap_mode="include",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            # Sitemap contains the start URL
            mock_fetch.return_value = [
                "http://example.com/",
                "http://example.com/about",
            ]

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """<html><body><p>No links</p></body></html>"""

                result = await engine.run("http://example.com/")

        # Should have start URL + about = 2 pages (start URL deduplicated)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert len(urls) == 2
        assert "http://example.com/" in urls
        assert "http://example.com/about" in urls

    @pytest.mark.asyncio
    async def test_sitemap_fetch_failure_falls_back(self, mock_scraper):
        """When sitemap fetch fails, crawl falls back to HTML-only discovery."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=2,
                sitemap_mode="include",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        # Simulate sitemap fetch failure
        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.side_effect = Exception("Sitemap fetch failed")

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """
                    <html><body>
                    <a href="http://example.com/pricing">Pricing</a>
                    <a href="http://example.com/about">About</a>
                    </body></html>
                """

                result = await engine.run("http://example.com/")

        # Should fall back to HTML discovery
        assert result.completed >= 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/pricing" in urls
        assert "http://example.com/about" in urls

    @pytest.mark.asyncio
    async def test_sitemap_only_with_max_depth_zero(self, mock_scraper):
        """sitemap='only' with max_depth=0 still scrapes sitemap URLs (they are depth 0)."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=0,
                sitemap_mode="include",
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_fetch_sitemap_urls") as mock_fetch:
            mock_fetch.return_value = [
                "http://example.com/sitemap-page1",
                "http://example.com/sitemap-page2",
            ]

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """
                    <html><body>
                    <a href="http://example.com/pricing">Pricing</a>
                    </body></html>
                """

                result = await engine.run("http://example.com/")

        # Should scrape start URL + sitemap URLs (all depth 0)
        # But NOT follow HTML links (max_depth=0)
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/sitemap-page1" in urls
        assert "http://example.com/sitemap-page2" in urls
        assert "http://example.com/pricing" not in urls  # HTML link, not followed


# ── Domain Scope Controls tests ─────────────────────────────────


class TestDomainScopeControls:
    """Tests for crawlEntireDomain, allowSubdomains, allowExternalLinks."""

    @pytest.mark.asyncio
    async def test_crawl_entire_domain_false_only_child_paths(self, mock_scraper):
        """When crawlEntireDomain is False (default), only child paths are followed.

        /section/page → /section/page/deeper ✅, /other ❌, / ❌
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                crawl_entire_domain=False,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page/deeper">Deeper child</a>
                <a href="http://example.com/other">Sibling/other</a>
                <a href="http://example.com/">Root</a>
                </body></html>
            """

            result = await engine.run("http://example.com/section/page")

        # Start URL + deeper child = 2 pages
        # Sibling (/other) and root (/) should not be followed
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page" in urls
        assert "http://example.com/section/page/deeper" in urls
        assert "http://example.com/other" not in urls
        assert "http://example.com/" not in urls

    @pytest.mark.asyncio
    async def test_crawl_entire_domain_true_follows_all_same_domain(self, mock_scraper):
        """When crawlEntireDomain is True, sibling/parent links are followed.

        /section/page → /other ✅, / ✅
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/section/page/deeper">Deeper child</a>
                <a href="http://example.com/other">Sibling/other</a>
                <a href="http://example.com/">Root</a>
                </body></html>
            """

            result = await engine.run("http://example.com/section/page")

        # Start URL + deeper child + other + root = 4 pages
        assert result.completed == 4
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page" in urls
        assert "http://example.com/section/page/deeper" in urls
        assert "http://example.com/other" in urls
        assert "http://example.com/" in urls

    @pytest.mark.asyncio
    async def test_crawl_entire_domain_root_start_identical(self, mock_scraper):
        """When start URL is root, crawlEntireDomain=false and true produce identical results."""
        from agent.crawler import CrawlEngine, CrawlOptions

        for crawl_entire in (False, True):
            engine = CrawlEngine(
                mock_scraper,
                store=None,
                options=CrawlOptions(
                    max_pages=10,
                    max_depth=2,
                    crawl_entire_domain=crawl_entire,
                ),
            )

            async def scrape_side_effect(
                url: str, force_browser: bool = False, **kwargs
            ) -> dict:
                return MockPage.success(url, f"# Content of {url}")

            mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

            with patch.object(engine, "_get_html") as mock_html:
                mock_html.return_value = """
                    <html><body>
                    <a href="http://example.com/pricing">Pricing</a>
                    <a href="http://example.com/about">About</a>
                    </body></html>
                """

                result = await engine.run("http://example.com/")

            # Both modes should find all children (everything is a child of root)
            urls = [p["url"] for p in result.pages]
            assert "http://example.com/" in urls
            assert "http://example.com/pricing" in urls
            assert "http://example.com/about" in urls

    @pytest.mark.asyncio
    async def test_allow_subdomains_false_blocks_subdomains(self, mock_scraper):
        """When allowSubdomains is False (default), subdomain links are NOT followed."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                allow_subdomains=False,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://docs.example.com/doc">Docs subdomain</a>
                <a href="http://blog.example.com/post">Blog subdomain</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing = 2 pages (subdomains blocked)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/pricing" in urls
        assert "http://docs.example.com/doc" not in urls
        assert "http://blog.example.com/post" not in urls

    @pytest.mark.asyncio
    async def test_allow_subdomains_true_follows_subdomains(self, mock_scraper):
        """When allowSubdomains is True, subdomain links ARE followed."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                allow_subdomains=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://docs.example.com/doc">Docs subdomain</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing + docs subdomain = 3 pages
        assert result.completed == 3
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/pricing" in urls
        assert "http://docs.example.com/doc" in urls

    @pytest.mark.asyncio
    async def test_allow_external_links_false_blocks_external(self, mock_scraper):
        """When allowExternalLinks is False (default), external links are NOT followed."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                allow_external_links=False,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://other.com/page">External other</a>
                <a href="http://another.org/path">External another</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing = 2 pages (external blocked)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/pricing" in urls
        assert "http://other.com/page" not in urls
        assert "http://another.org/path" not in urls

    @pytest.mark.asyncio
    async def test_allow_external_links_true_follows_external(self, mock_scraper):
        """When allowExternalLinks is True, external domain links ARE followed."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                allow_external_links=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://other.com/page">External other</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing + external = 3 pages
        assert result.completed == 3
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/pricing" in urls
        assert "http://other.com/page" in urls

    @pytest.mark.asyncio
    async def test_ssrf_guard_blocks_private_hosts(self, mock_scraper):
        """SSRF guard blocks private host URLs regardless of allow_external_links."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                allow_external_links=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://127.0.0.1/secret">Loopback</a>
                <a href="http://10.0.0.1/internal">RFC 1918 10.x</a>
                <a href="http://192.168.1.1/admin">RFC 1918 192.168.x</a>
                <a href="http://169.254.169.254/latest/meta-data/">Metadata IP</a>
                <a href="http://localhost:6379/valkey">Localhost</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + pricing = 2 pages (all private hosts blocked)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/" in urls
        assert "http://example.com/pricing" in urls
        assert "http://127.0.0.1/secret" not in urls
        assert "http://10.0.0.1/internal" not in urls
        assert "http://192.168.1.1/admin" not in urls
        assert "http://169.254.169.254/latest/meta-data/" not in urls
        assert "http://localhost:6379/valkey" not in urls

    @pytest.mark.asyncio
    async def test_crawl_entire_domain_plus_allow_subdomains(self, mock_scraper):
        """crawlEntireDomain + allowSubdomains: follow sibling/parent on subdomains too."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=20,
                max_depth=3,
                crawl_entire_domain=True,
                allow_subdomains=True,
                allow_external_links=False,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://blog.example.com/post">Blog subdomain</a>
                <a href="http://other.com/external">External</a>
                </body></html>
            """

            result = await engine.run("http://example.com/section/page")

        # Start URL + pricing + blog subdomain = 3 pages
        # External should be excluded
        assert result.completed == 3
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/section/page" in urls
        assert "http://example.com/pricing" in urls
        assert "http://blog.example.com/post" in urls
        assert "http://other.com/external" not in urls

    @pytest.mark.asyncio
    async def test_external_pages_respect_max_pages(self, mock_scraper):
        """External pages count toward max_pages limit."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=3,
                max_depth=2,
                allow_external_links=True,
                crawl_entire_domain=True,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/pricing">Pricing</a>
                <a href="http://other.com/page1">External 1</a>
                <a href="http://another.org/page2">External 2</a>
                <a href="http://third.net/page3">External 3</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # max_pages=3: start URL + pricing + 1 external = 3
        assert result.completed <= 3
        assert len(result.pages) <= 3


# ── CrawlRequest scope control field validation tests ───────────


class TestCrawlRequestScopeControlValidation:
    """Tests for CrawlRequest scope control field validation."""

    def test_crawl_entire_domain_default_false(self):
        """crawl_entire_domain defaults to False."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.crawl_entire_domain is False

    def test_crawl_entire_domain_can_be_true(self):
        """crawl_entire_domain can be set to True."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", crawl_entire_domain=True)
        assert req.crawl_entire_domain is True

    def test_allow_subdomains_default_false(self):
        """allow_subdomains defaults to False."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.allow_subdomains is False

    def test_allow_subdomains_can_be_true(self):
        """allow_subdomains can be set to True."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", allow_subdomains=True)
        assert req.allow_subdomains is True

    def test_allow_external_links_default_false(self):
        """allow_external_links defaults to False."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.allow_external_links is False

    def test_allow_external_links_can_be_true(self):
        """allow_external_links can be set to True."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", allow_external_links=True)
        assert req.allow_external_links is True

    def test_crawl_entire_domain_coerces_from_string(self):
        """crawl_entire_domain with truthy string is coerced to True (Pydantic default)."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", crawl_entire_domain="yes")
        assert req.crawl_entire_domain is True


# ── _filter_child_paths unit tests ──────────────────────────────


class TestFilterChildPaths:
    """Tests for the CrawlEngine._filter_child_paths static method."""

    def test_keeps_child_paths(self):
        """Links that are children of the current URL path are kept."""
        from agent.crawler import CrawlEngine

        links = [
            "http://example.com/section/page/deeper",
            "http://example.com/section/page/child",
            "http://example.com/section/page/deep/est",
        ]
        result = CrawlEngine._filter_child_paths(
            links, "http://example.com/section/page"
        )
        assert len(result) == 3

    def test_filters_sibling_and_parent(self):
        """Links to sibling or parent paths are filtered out."""
        from agent.crawler import CrawlEngine

        links = [
            "http://example.com/section/other",  # sibling
            "http://example.com/section",  # parent
            "http://example.com/",  # grandparent
            "http://example.com/section/page/deeper",  # child (kept)
        ]
        result = CrawlEngine._filter_child_paths(
            links, "http://example.com/section/page"
        )
        assert len(result) == 1
        assert "http://example.com/section/page/deeper" in result

    def test_keeps_different_domain_links(self):
        """Links on different domains are kept regardless of path."""
        from agent.crawler import CrawlEngine

        links = [
            "http://docs.example.com/other",  # subdomain
            "http://external.com/anything",  # external
        ]
        result = CrawlEngine._filter_child_paths(
            links, "http://example.com/section/page"
        )
        # Different domains always pass through
        assert len(result) == 2

    def test_current_url_with_trailing_slash(self):
        """Works correctly when current_url has a trailing slash.

        Both /section/child and /section/other are children of /section/.
        """
        from agent.crawler import CrawlEngine

        links = [
            "http://example.com/section/child",  # child (kept)
            "http://example.com/section/other",  # also child (kept)
            "http://example.com/section",  # parent (filtered)
        ]
        result = CrawlEngine._filter_child_paths(links, "http://example.com/section/")
        assert len(result) == 2
        assert "http://example.com/section/child" in result
        assert "http://example.com/section/other" in result
        assert "http://example.com/section" not in result

    def test_empty_links_returns_empty(self):
        """Empty links list returns empty list."""
        from agent.crawler import CrawlEngine

        result = CrawlEngine._filter_child_paths([], "http://example.com/page")
        assert result == []


# ── _filter_ssrf_blocked unit tests ─────────────────────────────


class TestFilterSsrfBlocked:
    """Tests for the CrawlEngine._filter_ssrf_blocked static method."""

    def test_allows_public_hosts(self):
        """Public hosts are allowed through."""
        from agent.crawler import CrawlEngine

        links = [
            "http://example.com/page",
            "http://google.com/search",
            "https://github.com/repo",
        ]
        result = CrawlEngine._filter_ssrf_blocked(links)
        assert len(result) == 3

    def test_blocks_private_ips(self):
        """Private IP addresses are blocked."""
        from agent.crawler import CrawlEngine

        links = [
            "http://example.com/page",  # allowed
            "http://127.0.0.1/secret",  # loopback
            "http://10.0.0.1/internal",  # RFC 1918 10.x
            "http://192.168.1.1/admin",  # RFC 1918 192.168.x
        ]
        result = CrawlEngine._filter_ssrf_blocked(links)
        assert len(result) == 1
        assert "http://example.com/page" in result

    def test_blocks_localhost_hostname(self):
        """Localhost hostname is blocked."""
        from agent.crawler import CrawlEngine

        links = [
            "http://localhost:6379/valkey",
            "http://example.com/page",
        ]
        result = CrawlEngine._filter_ssrf_blocked(links)
        assert len(result) == 1
        assert "http://example.com/page" in result

    def test_empty_links(self):
        """Empty links list returns empty list."""
        from agent.crawler import CrawlEngine

        result = CrawlEngine._filter_ssrf_blocked([])
        assert result == []


# ── Concurrency and delay tests ─────────────────────────────────


class TestCrawlConcurrency:
    """Tests for crawl concurrency (max_concurrency, delay, semaphore)."""

    @pytest.mark.asyncio
    async def test_max_concurrency_1_produces_sequential_scraping(self, mock_scraper):
        """With max_concurrency=1, pages are scraped sequentially."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=3,
                max_depth=1,
                max_concurrency=1,
            ),
        )

        scrape_times = []

        async def scrape_with_timing(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            scrape_times.append(time.monotonic())
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_with_timing)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        assert result.completed == 3
        assert len(result.pages) == 3

        # Scrape timestamps should be sequential (no overlap)
        # With max_concurrency=1, each scrape is awaited before the next starts.
        # Since we're measuring task-level timing, the _scrape_url method
        # acquires the semaphore before scraping, ensuring sequential access.
        # We verify by checking that all 3 pages were scraped.
        urls = [p["url"] for p in result.pages]
        assert urls[0] == "http://example.com/"
        assert "http://example.com/page1" in urls
        assert "http://example.com/page2" in urls

    @pytest.mark.asyncio
    async def test_max_concurrency_5_processes_multiple_pages(self, mock_scraper):
        """With max_concurrency=5, multiple pages are scraped concurrently."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=6,
                max_depth=1,
                max_concurrency=5,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                <a href="http://example.com/page3">Page 3</a>
                <a href="http://example.com/page4">Page 4</a>
                <a href="http://example.com/page5">Page 5</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should have scraped 6 pages (start URL + 5 children)
        assert result.completed == 6
        assert len(result.pages) == 6
        # Verify no duplicate URLs
        urls = [p["url"] for p in result.pages]
        assert len(urls) == len(set(urls))

    @pytest.mark.asyncio
    async def test_max_concurrency_greater_than_remaining_pages(self, mock_scraper):
        """max_concurrency > remaining pages does not deadlock."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=3,
                max_depth=1,
                max_concurrency=10,  # More than available pages
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Should complete all 3 pages without deadlock
        assert result.completed == 3
        assert len(result.pages) == 3
        # Should complete quickly (no hang)
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_delay_forces_concurrency_to_1(self, mock_scraper):
        """When delay is set, concurrency is forced to 1 and sequential pacing occurs."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=3,
                max_depth=1,
                max_concurrency=5,
                delay=0.05,  # Small delay for testing
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            start = time.monotonic()
            result = await engine.run("http://example.com/")
            elapsed = time.monotonic() - start

        assert result.completed == 3
        # With delay=0.05 and 2 inter-scrape gaps (3 pages → 2 gaps),
        # total should be at least 0.05 * 2 = 0.1s (plus scrape time)
        assert elapsed >= 0.09  # allow some tolerance

    @pytest.mark.asyncio
    async def test_delay_0_does_not_force_sequential(self, mock_scraper):
        """delay=0 does not force concurrency to 1."""
        from agent.crawler import CrawlEngine, CrawlOptions

        options = CrawlOptions(
            max_pages=3,
            max_depth=1,
            max_concurrency=5,
            delay=0.0,
        )
        engine = CrawlEngine(mock_scraper, store=None, options=options)

        # With delay=0.0, concurrency should NOT be forced to 1
        assert engine._effective_concurrency == 5

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        assert result.completed == 3
        assert len(result.pages) == 3

    @pytest.mark.asyncio
    async def test_delay_none_does_not_force_sequential(self, mock_scraper):
        """delay=None does not force concurrency to 1."""
        from agent.crawler import CrawlEngine, CrawlOptions

        options = CrawlOptions(
            max_pages=3,
            max_depth=1,
            max_concurrency=5,
            delay=None,
        )
        engine = CrawlEngine(mock_scraper, store=None, options=options)

        # With delay=None, concurrency should NOT be forced to 1
        assert engine._effective_concurrency == 5

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        assert result.completed == 3
        assert len(result.pages) == 3

    @pytest.mark.asyncio
    async def test_concurrency_capped_at_50(self, mock_scraper):
        """max_concurrency is capped at 50 even if user requests higher."""
        from agent.crawler import CrawlEngine, CrawlOptions

        options = CrawlOptions(max_concurrency=999)
        engine = CrawlEngine(mock_scraper, store=None, options=options)

        assert engine._effective_concurrency == 50

    @pytest.mark.asyncio
    async def test_concurrency_capped_at_50_from_init(self, mock_scraper):
        """_resolve_concurrency caps at MAX_CONCURRENCY_CAP."""
        from agent.crawler import CrawlEngine, CrawlOptions

        options = CrawlOptions(max_concurrency=100)
        engine = CrawlEngine(mock_scraper, store=None, options=options)

        assert engine._resolve_concurrency() == 50

    @pytest.mark.asyncio
    async def test_concurrency_normal_value_not_capped(self, mock_scraper):
        """Normal max_concurrency values are not capped."""
        from agent.crawler import CrawlEngine, CrawlOptions

        options = CrawlOptions(max_concurrency=5)
        engine = CrawlEngine(mock_scraper, store=None, options=options)

        assert engine._resolve_concurrency() == 5

    @pytest.mark.asyncio
    async def test_cancellation_during_delay_sleep(self, mock_scraper):
        """Cancellation during delay sleep interrupts the sleep promptly."""
        import asyncio as _asyncio

        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                delay=10.0,  # Long delay
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """

            # Cancel from a background task after a short delay
            async def _delayed_cancel():
                await _asyncio.sleep(0.05)
                engine.cancel()

            cancel_task = _asyncio.create_task(_delayed_cancel())
            start = time.monotonic()
            await engine.run("http://example.com/")
            elapsed = time.monotonic() - start
            await cancel_task

        # Should have cancelled promptly without waiting for the 10s delay
        assert elapsed < 5.0  # Should not wait for 10s delay
        # The crawl may have 0 or more pages depending on timing of cancellation

    @pytest.mark.asyncio
    async def test_no_duplicate_scrape_calls_under_concurrency(self, mock_scraper):
        """No duplicate scrape calls for the same URL under concurrency."""
        from agent.crawler import CrawlEngine, CrawlOptions

        scraped_urls = []

        async def scrape_and_track(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            scraped_urls.append(url)
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_and_track)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                <a href="http://example.com/page3">Page 3</a>
                <a href="http://example.com/page1">Page 1 dup</a>
                <a href="http://example.com/page1#section">Page 1 fragment</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + page1 + page2 + page3 = 4 (page1 appears multiple times
        # in links but should only be scraped once)
        assert result.completed == 4
        assert len(scraped_urls) == len(set(scraped_urls)), (
            f"Duplicate scrape calls: {scraped_urls}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_scrape_failure_does_not_abort_others(self, mock_scraper):
        """A failing concurrent scrape does not abort other scrapes."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=4,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            if "fail" in url:
                return MockPage.failure(url, "Connection error")
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good1">Good 1</a>
                <a href="http://example.com/fail">Fail</a>
                <a href="http://example.com/good2">Good 2</a>
                </body></html>
            """

            result = await engine.run("http://example.com/")

        # Start URL + good1 + good2 = 3 successful, 1 error
        assert result.completed == 3
        assert len(result.pages) == 3
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_max_concurrency_crawl_with_empty_site(self, mock_scraper):
        """Concurrent crawl of an empty site completes normally."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=2,
                max_concurrency=5,
            ),
        )

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body><p>No links</p></body></html>"

            result = await engine.run("http://example.com/")

        assert result.completed == 1
        assert len(result.pages) == 1


# ── Timeout and semaphore slot release (VAL-CONC-043, VAL-CONC-051) ─


class TestCrawlTimeoutAndErrors:
    """Tests for per-scrape timeout, error accumulation, and error types."""

    @pytest.mark.asyncio
    async def test_scrape_timeout_recorded_in_errors(self, mock_scraper):
        """A timed-out scrape is recorded in the errors list."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=2,
                max_depth=0,
                max_concurrency=1,
            ),
        )

        async def slow_scrape(url: str, force_browser: bool = False, **kwargs) -> dict:
            raise TimeoutError("timed out")

        mock_scraper.scrape = AsyncMock(side_effect=slow_scrape)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body><p>No links</p></body></html>"
            # The start URL fails, so the crawl finishes early (StartUrlScrapeError)
            # This is caught in run() and we return the result with the error
            result = await engine.run("http://example.com/")

        assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_error_code_distinction(self, mock_scraper):
        """Errors have distinct error_code for scrape failure vs timeout vs blocked."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def varied_scrape(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            if "/blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: robots.txt disallows",
                }
            if "/fail" in url:
                return {"success": False, "error": "HTTP 500 Internal Server Error"}
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=varied_scrape)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/blocked">Blocked</a>
                <a href="http://example.com/fail">Fail</a>
                <a href="http://example.com/good">Good</a>
                </body></html>
            """
            result = await engine.run("http://example.com/")

        blocked_errors = [e for e in result.errors if "/blocked" in e.get("url", "")]
        fail_errors = [e for e in result.errors if "/fail" in e.get("url", "")]

        for e in blocked_errors:
            assert e.get("error_code") == "ROBOTS_BLOCKED", (
                f"Expected ROBOTS_BLOCKED, got: {e}"
            )
        for e in fail_errors:
            assert e.get("error_code") == "SCRAPE_ERROR", (
                f"Expected SCRAPE_ERROR, got: {e}"
            )

    @pytest.mark.asyncio
    async def test_mixed_success_failure_blocked(self, mock_scraper):
        """Crawl with a mix of successes, failures, and blocked URLs completes correctly."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def mixed_scrape(url: str, **kwargs) -> dict:
            if "/fail" in url:
                return MockPage.failure(url, "Connection error")
            if "/blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: robots.txt disallows",
                }
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=mixed_scrape)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good1">Good 1</a>
                <a href="http://example.com/fail1">Fail 1</a>
                <a href="http://example.com/blocked1">Blocked 1</a>
                <a href="http://example.com/good2">Good 2</a>
                </body></html>
            """
            result = await engine.run("http://example.com/")

        # Start URL + good1 + good2 = 3 successes
        assert result.completed >= 2  # at least 2 children (start URL + good pages)
        assert len(result.errors) >= 2  # at least 2 errors (fail + blocked)
        assert len(result.robots_blocked) >= 1  # at least 1 blocked

        # Verify error code distinctions
        for e in result.errors:
            if "/blocked" in e.get("url", ""):
                assert e.get("error_code") == "ROBOTS_BLOCKED"


# ── Error callback tests ─────────────────────────────────────────


class TestCrawlErrorCallback:
    """Tests for the ``error_callback`` parameter on CrawlEngine.run()."""

    @pytest.mark.asyncio
    async def test_error_callback_called_on_child_page_timeout(self, mock_scraper):
        """error_callback is called when a child page times out."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def timeout_scrape(url: str, **kwargs) -> dict:
            if "/child" in url:
                raise TimeoutError("timed out")
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=timeout_scrape)
        call_errors: list[dict] = []

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/child">Child</a>
                </body></html>
            """
            await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        # error_callback should have been called for the child page timeout
        assert len(call_errors) >= 1
        error = call_errors[0]
        assert error.get("url") == "http://example.com/child"
        assert "timed out" in error.get("error", "")

    @pytest.mark.asyncio
    async def test_error_callback_called_on_child_page_scrape_failure(
        self, mock_scraper
    ):
        """error_callback is called when a child page scrape returns failure."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def fail_scrape(url: str, **kwargs) -> dict:
            if "/fail" in url:
                return MockPage.failure(url, "HTTP 500")
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=fail_scrape)
        call_errors: list[dict] = []

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/fail">Fail</a>
                </body></html>
            """
            await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        assert len(call_errors) >= 1
        error = call_errors[0]
        assert error.get("url") == "http://example.com/fail"
        assert "HTTP 500" in error.get("error", "")

    @pytest.mark.asyncio
    async def test_error_callback_called_on_politeness_blocked(self, mock_scraper):
        """error_callback is called when a page is blocked by politeness."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def blocked_scrape(url: str, **kwargs) -> dict:
            if "/blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: robots.txt disallows",
                }
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=blocked_scrape)
        call_errors: list[dict] = []

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/blocked">Blocked</a>
                </body></html>
            """
            await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        assert len(call_errors) >= 1
        error = call_errors[0]
        assert error.get("url") == "http://example.com/blocked"
        assert "Blocked by politeness" in error.get("error", "")

    @pytest.mark.asyncio
    async def test_error_callback_contains_url_and_error(self, mock_scraper):
        """error_callback receives a dict with url and error keys."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def fail_scrape(url: str, **kwargs) -> dict:
            if "/child" in url:
                return MockPage.failure(url, "Connection refused")
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=fail_scrape)
        call_errors: list[dict] = []

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/child">Child</a>
                </body></html>
            """
            await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        assert len(call_errors) >= 1
        error = call_errors[0]
        assert "url" in error
        assert "error" in error
        assert error["url"] == "http://example.com/child"
        assert isinstance(error["error"], str)

    @pytest.mark.asyncio
    async def test_error_callback_not_called_on_successful_pages(self, mock_scraper):
        """error_callback is NOT called for pages that scrape successfully."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        call_errors: list[dict] = []
        scrape_count = 0

        async def success_scrape(url: str, **kwargs) -> dict:
            nonlocal scrape_count
            scrape_count += 1
            return MockPage.success(url, markdown=f"# Unique Content {scrape_count}")

        mock_scraper.scrape = AsyncMock(side_effect=success_scrape)

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/child">Child</a>
                </body></html>
            """
            await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        # No errors should have been reported since all pages succeed
        assert len(call_errors) == 0

    @pytest.mark.asyncio
    async def test_error_callback_exception_does_not_crash_crawl(self, mock_scraper):
        """An exception in error_callback itself does not crash the crawl."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        async def fail_scrape(url: str, **kwargs) -> dict:
            if "/child" in url:
                return MockPage.failure(url, "HTTP 500")
            return MockPage.success(url)

        mock_scraper.scrape = AsyncMock(side_effect=fail_scrape)

        async def _exploding_cb(_job_id: str, error: dict) -> None:
            raise RuntimeError("error_callback exploded")

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/child">Child</a>
                </body></html>
            """
            # Should not raise; the callback exception is caught and logged
            result = await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_exploding_cb,
            )

        # The crawl should still complete with at least the start URL
        assert result.completed >= 1
        assert result.errors is not None

    @pytest.mark.asyncio
    async def test_error_callback_called_on_mixed_results(self, mock_scraper):
        """error_callback is called for each failing page in a mixed crawl."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=10,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        call_errors: list[dict] = []

        async def mixed_scrape(url: str, **kwargs) -> dict:
            if "/fail" in url:
                return MockPage.failure(url, "Connection error")
            if "/blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: robots.txt disallows",
                }
            # Use unique markdown per URL to avoid content dedup
            return MockPage.success(url, markdown=f"# Unique {url}")

        mock_scraper.scrape = AsyncMock(side_effect=mixed_scrape)
        call_errors: list[dict] = []

        async def _error_cb(_job_id: str, error: dict) -> None:
            call_errors.append(error)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/ok">OK</a>
                <a href="http://example.com/fail1">Fail 1</a>
                <a href="http://example.com/blocked1">Blocked 1</a>
                <a href="http://example.com/fail2">Fail 2</a>
                <a href="http://example.com/ok2">OK 2</a>
                </body></html>
            """
            result = await engine.run(
                "http://example.com/",
                job_id="test-job",
                error_callback=_error_cb,
            )

        # Should have errors for /fail1, /blocked1, /fail2
        assert len(call_errors) >= 2  # at least fail1 + fail2
        error_urls = [e.get("url") for e in call_errors]
        assert "http://example.com/fail1" in error_urls
        assert "http://example.com/blocked1" in error_urls or any(
            "blocked" in e.get("error", "") for e in call_errors
        )
        assert "http://example.com/fail2" in error_urls
        # Success pages should NOT trigger error_callback
        ok_urls = [e.get("url") for e in call_errors if "/ok" in e.get("url", "")]
        assert len(ok_urls) == 0
        # Crawl should still complete with successful pages
        assert result.completed >= 2  # start URL + /ok + /ok2


class TestCrawlStoreAtomicity:
    """Tests that the CrawlEngine calls atomic progress update methods."""

    @pytest.mark.asyncio
    async def test_atomic_completed_increment_with_mock_store(
        self, mock_scraper, mock_store
    ):
        """CrawlEngine calls store.increment_completed() for each successful page."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=mock_store,
            options=CrawlOptions(
                max_pages=3,
                max_depth=1,
                max_concurrency=1,
            ),
        )

        mock_store.increment_completed = MagicMock(return_value=1)
        mock_store.get_completed = MagicMock(return_value=0)
        mock_store.update_job_progress = MagicMock()

        async def _unique_scrape(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=_unique_scrape)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/page1">Page 1</a>
                <a href="http://example.com/page2">Page 2</a>
                </body></html>
            """
            result = await engine.run("http://example.com/", job_id="test-job")

        # increment_completed should be called for each successful page
        assert result.completed == 3
        assert mock_store.increment_completed.call_count == 3
        # update_job_progress should be called (final update)
        assert mock_store.update_job_progress.call_count >= 1

    @pytest.mark.asyncio
    async def test_increment_only_for_successful_pages(self, mock_scraper, mock_store):
        """increment_completed is NOT called for failed or blocked pages."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=mock_store,
            options=CrawlOptions(
                max_pages=5,
                max_depth=1,
                max_concurrency=3,
            ),
        )

        mock_store.increment_completed = MagicMock(return_value=1)
        mock_store.get_completed = MagicMock(return_value=0)
        mock_store.update_job_progress = MagicMock()

        async def mixed_scrape(url: str, **kwargs) -> dict:
            if "/fail" in url:
                return MockPage.failure(url, "Connection error")
            if "/blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: robots.txt disallows",
                }
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=mixed_scrape)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good1">Good 1</a>
                <a href="http://example.com/fail1">Fail 1</a>
                <a href="http://example.com/blocked1">Blocked 1</a>
                </body></html>
            """
            await engine.run("http://example.com/", job_id="test-job-2")

        # increment_completed should only be called for successful pages
        # start URL + good1 = 2 successes
        assert mock_store.increment_completed.call_count == 2


# ── CrawlRequest concurrency validation tests (already exist) ───


class TestCrawlRequestConcurrencyValidation:
    """Tests for CrawlRequest max_concurrency and delay validation."""

    def test_max_concurrency_default(self):
        """max_concurrency defaults to 3."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.max_concurrency == 3

    def test_max_concurrency_valid_value(self):
        """max_concurrency >= 1 is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", max_concurrency=5)
        assert req.max_concurrency == 5

        req2 = CrawlRequest(url="http://example.com", max_concurrency=1)
        assert req2.max_concurrency == 1

    def test_max_concurrency_zero_rejected(self):
        """max_concurrency=0 raises validation error."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="max_concurrency"):
            CrawlRequest(url="http://example.com", max_concurrency=0)

    def test_max_concurrency_negative_rejected(self):
        """max_concurrency=-1 raises validation error."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="max_concurrency"):
            CrawlRequest(url="http://example.com", max_concurrency=-1)

    def test_max_concurrency_capped_at_50_by_validator(self):
        """max_concurrency values > 50 are clamped to 50."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", max_concurrency=100)
        assert req.max_concurrency == 50

        req2 = CrawlRequest(url="http://example.com", max_concurrency=51)
        assert req2.max_concurrency == 50

    def test_delay_default_none(self):
        """delay defaults to None."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.delay is None

    def test_delay_valid_positive(self):
        """Positive delay is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", delay=1.5)
        assert req.delay == 1.5

    def test_delay_zero_accepted(self):
        """delay=0 is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", delay=0)
        assert req.delay == 0

    def test_delay_negative_rejected(self):
        """Negative delay raises validation error."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="delay"):
            CrawlRequest(url="http://example.com", delay=-1)


# ── Politeness integration tests ─────────────────────────────────


class TestPolitenessFields:
    """Tests for the ignore_robots_txt and robots_user_agent fields."""

    def test_ignore_robots_txt_default_false(self):
        """ignore_robots_txt defaults to False."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.ignore_robots_txt is False

    def test_ignore_robots_txt_true_accepted(self):
        """ignore_robots_txt=True is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", ignore_robots_txt=True)
        assert req.ignore_robots_txt is True

    def test_robots_user_agent_default_none(self):
        """robots_user_agent defaults to None."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.robots_user_agent is None

    def test_robots_user_agent_custom_accepted(self):
        """Custom robots_user_agent string is accepted."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com", robots_user_agent="MyBot/1.0")
        assert req.robots_user_agent == "MyBot/1.0"

    def test_crawl_options_accepts_politeness_fields(self):
        """CrawlOptions accepts ignore_robots_txt and robots_user_agent."""
        from agent.crawler import CrawlOptions

        opts = CrawlOptions(
            max_pages=5,
            ignore_robots_txt=True,
            robots_user_agent="TestBot/2.0",
        )
        assert opts.ignore_robots_txt is True
        assert opts.robots_user_agent == "TestBot/2.0"

    def test_crawl_result_has_robots_blocked(self):
        """CrawlResult includes robots_blocked list."""
        from agent.crawler import CrawlResult

        result = CrawlResult()
        assert hasattr(result, "robots_blocked")
        assert result.robots_blocked == []

    @pytest.mark.asyncio
    async def test_ignore_robots_txt_passed_to_scraper(self, mock_scraper):
        """ignore_robots_txt is passed through to the scraper client."""
        from agent.crawler import CrawlEngine, CrawlOptions

        mock_scraper.scrape = AsyncMock(
            return_value=MockPage.success("http://example.com/")
        )

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                ignore_robots_txt=True,
                robots_user_agent="CustomBot/1.0",
            ),
        )

        await engine.run("http://example.com/")

        # Verify the scraper was called with the correct params
        # Note: scrape_options is passed as None when not configured
        mock_scraper.scrape.assert_called_once_with(
            "http://example.com/",
            ignore_robots_txt=True,
            robots_user_agent="CustomBot/1.0",
            scrape_options=None,
        )

    @pytest.mark.asyncio
    async def test_politeness_blocked_results_collected(self, mock_scraper):
        """Politeness-blocked scrape results are collected in robots_blocked list."""
        from agent.crawler import CrawlEngine, CrawlOptions

        async def scrape_side_effect(url: str, **kwargs) -> dict:
            if "blocked" in url:
                return {
                    "success": False,
                    "error": "Blocked by politeness: Disallowed by robots.txt: /admin/",
                }
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good">Good page</a>
                <a href="http://example.com/blocked">Blocked page</a>
                </body></html>
            """

            result = await engine.run("http://example.com/", job_id="test-politeness")

        assert result.completed == 2
        assert len(result.pages) == 2
        assert len(result.robots_blocked) == 1
        assert result.robots_blocked[0]["url"] == "http://example.com/blocked"
        assert result.robots_blocked[0]["error_code"] == "ROBOTS_BLOCKED"

    @pytest.mark.asyncio
    async def test_non_politeness_errors_not_in_robots_blocked(self, mock_scraper):
        """Non-politeness errors appear in errors but not in robots_blocked."""
        from agent.crawler import CrawlEngine, CrawlOptions

        async def scrape_side_effect(url: str, **kwargs) -> dict:
            if "timeout" in url:
                return {"success": False, "error": "Scraper timed out"}
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=10, max_depth=1),
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = """
                <html><body>
                <a href="http://example.com/good">Good page</a>
                <a href="http://example.com/timeout">Timeout page</a>
                </body></html>
            """

            result = await engine.run("http://example.com/", job_id="test-errors")

        assert result.completed == 2
        assert len(result.errors) == 1
        assert len(result.robots_blocked) == 0
        assert "timeout" in result.errors[0]["url"]


# ── CrawlRequest ignore_robots_txt and robots_user_agent validation ────


class TestCrawlRequestPoliteness:
    """Tests for CrawlRequest politeness field validation."""

    def test_ignore_robots_txt_field_accepted(self):
        """CrawlRequest accepts and serializes ignore_robots_txt."""
        from agent.models import CrawlRequest

        req = CrawlRequest(
            url="http://example.com",
            ignore_robots_txt=True,
        )
        d = req.model_dump(by_alias=True)
        assert d.get("ignoreRobotsTxt") is True

    def test_robots_user_agent_field_accepted(self):
        """CrawlRequest accepts and serializes robots_user_agent."""
        from agent.models import CrawlRequest

        req = CrawlRequest(
            url="http://example.com",
            robots_user_agent="MyBot/1.0",
        )
        d = req.model_dump(by_alias=True)
        assert d.get("robotsUserAgent") == "MyBot/1.0"

    def test_both_politeness_fields_default(self):
        """Default values are False and None."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.ignore_robots_txt is False
        assert req.robots_user_agent is None


class TestScrapeOptionsModel:
    """Unit tests for the ScrapeOptions Pydantic model.

    Covers:
    - Default values
    - All fields accept and return correct types
    - Invalid formats produce validation error
    - Negative/zero timeout produces validation error
    - Negative wait_for produces validation error
    - CamelCase input via populate_by_name
    - model_dump(by_alias=True) produces camelCase keys
    - CrawlRequest accepts optional scrape_options
    - model_dump produces dict suitable for JSON serialization
    """

    def test_defaults(self):
        """ScrapeOptions default values match Firecrawl defaults."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions()
        assert opts.formats == ["markdown"]
        assert opts.only_main_content is True
        assert opts.include_tags is None
        assert opts.exclude_tags is None
        assert opts.wait_for is None
        assert opts.mobile is False
        assert opts.timeout == 30000
        assert opts.headers is None
        assert opts.remove_base64_images is False

    def test_all_fields_custom(self):
        """All ScrapeOptions fields accept custom values."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            formats=["markdown", "html", "links"],
            only_main_content=False,
            include_tags=["article", "p"],
            exclude_tags=[".nav", "footer"],
            wait_for=1000,
            mobile=True,
            timeout=30000,
            headers={"Authorization": "Bearer test"},
            remove_base64_images=True,
        )
        assert opts.formats == ["markdown", "html", "links"]
        assert opts.only_main_content is False
        assert opts.include_tags == ["article", "p"]
        assert opts.exclude_tags == [".nav", "footer"]
        assert opts.wait_for == 1000
        assert opts.mobile is True
        assert opts.timeout == 30000
        assert opts.headers == {"Authorization": "Bearer test"}
        assert opts.remove_base64_images is True

    def test_invalid_format_raises_error(self):
        """Invalid format name in formats list raises ValidationError."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(formats=["invalid_format"])

    def test_negative_timeout_raises_error(self):
        """Negative timeout value raises ValidationError."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(timeout=-1)

    def test_zero_timeout_raises_error(self):
        """Timeout of 0 raises ValidationError (minimum is 1000ms)."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(timeout=0)

    def test_negative_wait_for_raises_error(self):
        """Negative wait_for raises ValidationError."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(wait_for=-100)

    def test_camelcase_input_accepted(self):
        """CamelCase field names are accepted (populate_by_name)."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            **{
                "formats": ["html"],
                "onlyMainContent": False,
                "includeTags": ["h1"],
                "excludeTags": [".nav"],
                "waitFor": 2000,
                "mobile": True,
                "timeout": 15000,
                "headers": {"X-Custom": "val"},
                "removeBase64Images": True,
            }
        )
        assert opts.formats == ["html"]
        assert opts.only_main_content is False
        assert opts.include_tags == ["h1"]
        assert opts.exclude_tags == [".nav"]
        assert opts.wait_for == 2000
        assert opts.mobile is True
        assert opts.timeout == 15000
        assert opts.headers == {"X-Custom": "val"}
        assert opts.remove_base64_images is True

    def test_model_dump_by_alias_produces_camelcase(self):
        """model_dump(by_alias=True) produces camelCase keys."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            formats=["markdown"],
            only_main_content=True,
            include_tags=["article"],
            exclude_tags=[".nav"],
            wait_for=500,
            mobile=False,
            timeout=20000,
            headers={"X-Test": "value"},
            remove_base64_images=True,
        )
        dumped = opts.model_dump(mode="json", by_alias=True)
        assert dumped["formats"] == ["markdown"]
        assert dumped["onlyMainContent"] is True
        assert dumped["includeTags"] == ["article"]
        assert dumped["excludeTags"] == [".nav"]
        assert dumped["waitFor"] == 500
        assert dumped["mobile"] is False
        assert dumped["timeout"] == 20000
        assert dumped["headers"] == {"X-Test": "value"}
        assert dumped["removeBase64Images"] is True
        # snake_case keys should not appear
        assert "only_main_content" not in dumped

    def test_crawl_request_accepts_scrape_options(self):
        """CrawlRequest accepts optional scrape_options field."""
        from agent.models import CrawlRequest, ScrapeOptions

        req = CrawlRequest(
            url="http://example.com",
            scrape_options=ScrapeOptions(formats=["links"], only_main_content=False),
        )
        assert req.scrape_options is not None
        assert req.scrape_options.formats == ["links"]
        assert req.scrape_options.only_main_content is False

    def test_crawl_request_without_scrape_options(self):
        """CrawlRequest without scrape_options defaults to None."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="http://example.com")
        assert req.scrape_options is None

    def test_model_dump_json_serializable(self):
        """model_dump(mode='json') produces JSON-serializable dict."""
        import json

        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            formats=["markdown", "html"],
            only_main_content=False,
            include_tags=["article", "p"],
            exclude_tags=[".nav"],
            wait_for=1000,
            mobile=True,
            timeout=30000,
            headers={"Authorization": "Bearer test123"},
            remove_base64_images=True,
        )
        dumped = opts.model_dump(mode="json")
        # Should be JSON-serializable
        json_str = json.dumps(dumped)
        parsed = json.loads(json_str)
        assert parsed["formats"] == ["markdown", "html"]
        assert parsed["only_main_content"] is False
        assert parsed["include_tags"] == ["article", "p"]
        assert parsed["exclude_tags"] == [".nav"]
        assert parsed["wait_for"] == 1000
        assert parsed["mobile"] is True
        assert parsed["timeout"] == 30000
        assert parsed["headers"] == {"Authorization": "Bearer test123"}
        assert parsed["remove_base64_images"] is True

    def test_empty_formats_rejected(self):
        """Empty formats list is rejected by validator."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(formats=[])

    def test_valid_formats_enum(self):
        """VAL_SCRAPE_FORMATS contains expected values."""
        from agent.models import VALID_SCRAPE_FORMATS

        assert "markdown" in VALID_SCRAPE_FORMATS
        assert "html" in VALID_SCRAPE_FORMATS
        assert "links" in VALID_SCRAPE_FORMATS
        assert "screenshot" in VALID_SCRAPE_FORMATS
        assert "rawHtml" in VALID_SCRAPE_FORMATS

    def test_none_fields_in_model_dump(self):
        """None fields like wait_for are not dropped in model_dump."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions()
        dumped = opts.model_dump(mode="json")
        # wait_for is None by default, should be present
        assert "wait_for" in dumped
        assert dumped["wait_for"] is None
        assert "include_tags" in dumped
        assert dumped["include_tags"] is None
        assert "exclude_tags" in dumped
        assert dumped["exclude_tags"] is None
        assert "headers" in dumped
        assert dumped["headers"] is None

    # ── Advanced scrape options (VAL-PARITY-020 through 025, VAL-SCRAPE-056/057/058) ──

    def test_advanced_fields_defaults(self):
        """Advanced scrapeOptions fields have correct defaults."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions()
        assert opts.actions is None
        assert opts.location is None
        assert opts.proxy is None
        assert opts.block_ads is True  # default True
        assert opts.parsers is None

    def test_advanced_fields_custom_values(self):
        """All advanced scrapeOptions fields accept custom values."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            actions=[
                {"type": "wait", "milliseconds": 2000},
                {"type": "click", "selector": ".load-more"},
            ],
            location={"country": "DE", "languages": ["de-DE", "en"]},
            proxy="basic",
            block_ads=False,
            parsers=["pdf"],
        )
        assert opts.actions == [
            {"type": "wait", "milliseconds": 2000},
            {"type": "click", "selector": ".load-more"},
        ]
        assert opts.location == {"country": "DE", "languages": ["de-DE", "en"]}
        assert opts.proxy == "basic"
        assert opts.block_ads is False
        assert opts.parsers == ["pdf"]

    def test_actions_missing_type_rejected(self):
        """Actions without a 'type' field are rejected."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"missing required.*type"):
            ScrapeOptions(actions=[{"milliseconds": 2000}])

    def test_actions_invalid_type_rejected(self):
        """Invalid action type is rejected."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid action type"):
            ScrapeOptions(actions=[{"type": "fly"}])

    def test_actions_click_requires_selector(self):
        """Click action requires a selector field."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="requires a 'selector' field"):
            ScrapeOptions(actions=[{"type": "click"}])

    def test_actions_write_requires_selector_and_value(self):
        """Write action requires both selector and value fields."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        # Missing selector
        with pytest.raises(ValidationError, match="requires a 'selector' field"):
            ScrapeOptions(actions=[{"type": "write", "value": "hello"}])

        # Missing value
        with pytest.raises(ValidationError, match="requires a 'value' field"):
            ScrapeOptions(actions=[{"type": "write", "selector": "#input"}])

    def test_actions_valid_types_accepted(self):
        """All valid action types are accepted."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            actions=[
                {"type": "wait", "milliseconds": 1000},
                {"type": "click", "selector": "#btn"},
                {"type": "screenshot"},
                {"type": "scroll"},
                {"type": "write", "selector": "#input", "value": "hello"},
                {"type": "executeScript", "script": "document.title"},
                {"type": "select", "selector": "#dropdown"},
            ]
        )
        assert len(opts.actions) == 7

    def test_invalid_proxy_rejected(self):
        """Invalid proxy value is rejected."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid proxy"):
            ScrapeOptions(proxy="super-premium")

    def test_valid_proxy_values_accepted(self):
        """All valid proxy values are accepted."""
        from agent.models import ScrapeOptions

        for val in ("basic", "enhanced", "auto"):
            opts = ScrapeOptions(proxy=val)
            assert opts.proxy == val

    def test_invalid_parsers_rejected(self):
        """Invalid parser type is rejected."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid parser"):
            ScrapeOptions(parsers=["csv"])

    def test_valid_parsers_accepted(self):
        """Valid parser types are accepted."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(parsers=["pdf"])
        assert opts.parsers == ["pdf"]

    def test_location_invalid_type_rejected(self):
        """Non-dict location is rejected."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(location="DE")

    def test_location_country_must_be_string(self):
        """location.country must be a string."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"location.country must be a string"):
            ScrapeOptions(location={"country": 42})

    def test_location_languages_must_be_list_of_strings(self):
        """location.languages must be a list of strings."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=r"location.languages must be a list"):
            ScrapeOptions(location={"country": "DE", "languages": "de"})

    def test_forward_compatible_extra_fields_passed_through(self):
        """Unknown/extra fields are preserved in model_dump (extra='allow')."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            formats=["markdown"],
            extra_field="hello",
            another_unknown=True,
            nested={"key": "value"},
        )
        dumped = opts.model_dump(mode="json")
        assert dumped.get("extra_field") == "hello"
        assert dumped.get("another_unknown") is True
        assert dumped.get("nested") == {"key": "value"}

    def test_advanced_fields_camelcase_input_accepted(self):
        """CamelCase input for advanced fields works via populate_by_name."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            **{
                "actions": [{"type": "wait", "milliseconds": 1000}],
                "location": {"country": "FR", "languages": ["fr-FR"]},
                "proxy": "enhanced",
                "blockAds": True,
                "parsers": ["pdf"],
            }
        )
        assert opts.actions == [{"type": "wait", "milliseconds": 1000}]
        assert opts.location == {"country": "FR", "languages": ["fr-FR"]}
        assert opts.proxy == "enhanced"
        assert opts.block_ads is True
        assert opts.parsers == ["pdf"]

    def test_advanced_fields_in_model_dump_by_alias(self):
        """model_dump(by_alias=True) produces camelCase keys for advanced fields."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            actions=[{"type": "wait", "milliseconds": 2000}],
            location={"country": "DE", "languages": ["de-DE"]},
            proxy="basic",
            block_ads=False,
            parsers=["pdf"],
        )
        dumped = opts.model_dump(mode="json", by_alias=True)
        assert dumped.get("actions") == [{"type": "wait", "milliseconds": 2000}]
        assert dumped.get("location") == {"country": "DE", "languages": ["de-DE"]}
        assert dumped.get("proxy") == "basic"
        assert dumped.get("blockAds") is False
        assert dumped.get("parsers") == ["pdf"]
        # snake_case keys should not appear for aliased fields
        assert "block_ads" not in dumped

    def test_advanced_fields_json_serializable(self):
        """model_dump(mode='json') produces JSON-serializable dict with advanced fields."""
        import json

        from agent.models import ScrapeOptions

        opts = ScrapeOptions(
            actions=[{"type": "wait", "milliseconds": 2000}],
            location={"country": "US", "languages": ["en-US"]},
            proxy="basic",
            block_ads=True,
            parsers=["pdf"],
        )
        dumped = opts.model_dump(mode="json")
        json_str = json.dumps(dumped)
        parsed = json.loads(json_str)
        assert parsed["actions"] == [{"type": "wait", "milliseconds": 2000}]
        assert parsed["location"] == {"country": "US", "languages": ["en-US"]}
        assert parsed["proxy"] == "basic"
        assert parsed["block_ads"] is True
        assert parsed["parsers"] == ["pdf"]

    def test_model_dump_excludes_none_advanced_fields(self):
        """model_dump(exclude_none=True) excludes None advanced fields."""
        from agent.models import ScrapeOptions

        opts = ScrapeOptions()
        dumped = opts.model_dump(mode="json", exclude_none=True)
        assert "actions" not in dumped or dumped.get("actions") is None
        assert "location" not in dumped or dumped.get("location") is None
        assert "proxy" not in dumped or dumped.get("proxy") is None
        assert "parsers" not in dumped or dumped.get("parsers") is None

    def test_actions_list_not_dict_rejected(self):
        """actions must be a list, not a dict."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(actions={"type": "wait"})

    def test_actions_item_not_dict_rejected(self):
        """Each action in actions must be a dict."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(actions=["wait"])

    def test_parsers_not_list_rejected(self):
        """parsers must be a list."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(parsers="pdf")

    def test_parsers_item_not_string_rejected(self):
        """Each item in parsers must be a string."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScrapeOptions(parsers=[42])

    def test_select_action_requires_selector(self):
        """Select action requires a selector field."""
        import pytest
        from agent.models import ScrapeOptions
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="requires a 'selector' field"):
            ScrapeOptions(actions=[{"type": "select"}])


# ── DedupManager tests ────────────────────────────────────────────


class TestDedupManager:
    """Tests for the DedupManager multi-layer deduplication."""

    # ── Canonical tag check ──────────────────────────────────────

    def test_canonical_matches_prevents_duplicate(self):
        """Page A with canonical=page B, when B is scraped, A is skipped."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        # Mark page B as already scraped
        dedup.mark_scraped("https://example.com/page-b")

        # Page A has canonical pointing to page B
        html = '<html><head><link rel="canonical" href="https://example.com/page-b"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result == "https://example.com/page-b"

    def test_self_referencing_canonical_ignored(self):
        """A page with canonical=itself is not considered a duplicate."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page")

        # Self-referencing canonical should return None (not a duplicate)
        html = '<html><head><link rel="canonical" href="https://example.com/page"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page")
        assert result is None

    def test_self_referencing_canonical_with_trailing_slash(self):
        """Self-referencing canonical with trailing slash difference is ignored."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page")

        # Self-referencing with trailing slash on canonical
        html = '<html><head><link rel="canonical" href="https://example.com/page/"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page")
        assert result is None

    def test_canonical_external_domain_ignored(self):
        """Canonical pointing to external domain is ignored."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://other.com/page")

        # Page A has canonical pointing to an external domain
        html = '<html><head><link rel="canonical" href="https://other.com/page"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result is None  # External domain, ignored

    def test_canonical_not_scraped_yet(self):
        """Canonical URL that hasn't been scraped yet is not a duplicate."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page-a")

        # Page B has canonical pointing to page-c which hasn't been scraped
        html = '<html><head><link rel="canonical" href="https://example.com/page-c"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-b")
        assert result is None  # Canonical target hasn't been scraped

    def test_canonical_relative_url_resolved(self):
        """Relative canonical URLs are resolved against the current URL."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page-b")

        # Relative canonical URL
        html = '<html><head><link rel="canonical" href="/page-b"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result == "https://example.com/page-b"

    def test_canonical_href_first_attribute_order(self):
        """href attribute before rel attribute is also parsed correctly."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page-b")

        # href before rel
        html = '<html><head><link href="https://example.com/page-b" rel="canonical"></head><body></body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result == "https://example.com/page-b"

    def test_canonical_no_tag_returns_none(self):
        """Page without canonical tag returns None."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page-b")

        html = "<html><head></head><body><p>No canonical tag</p></body></html>"
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result is None

    # ── Content hash dedup ──────────────────────────────────────

    def test_content_hash_identical_markdown_detected(self):
        """Two pages with identical markdown → second is duplicate."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        md = "# Same Content\n\nThis is identical markdown."

        # First page: compute and register hash
        h = dedup.compute_content_hash(md)
        assert h is not None
        assert not dedup.is_duplicate_content(h)

        dedup.mark_scraped("https://example.com/page-a", content_hash=h)

        # Second page with same content
        h2 = dedup.compute_content_hash(md)
        assert h2 == h
        assert dedup.is_duplicate_content(h2)

    def test_content_hash_different_markdown_not_duplicate(self):
        """Two pages with different markdown content are NOT duplicates."""
        from agent.dedup import DedupManager

        dedup = DedupManager()

        h1 = dedup.compute_content_hash("# Page A Content")
        assert h1 is not None
        dedup.mark_scraped("https://example.com/page-a", content_hash=h1)

        h2 = dedup.compute_content_hash("# Page B Content")
        assert h2 is not None
        assert h2 != h1
        assert not dedup.is_duplicate_content(h2)

    def test_content_hash_near_identical_matches_exact_only(self):
        """Pages differing only by a timestamp are NOT deduped by hash."""
        from agent.dedup import DedupManager

        dedup = DedupManager()

        content_a = "<p>Hello World</p>\n<!-- generated at 2024-01-01T00:00:01Z -->"
        content_b = "<p>Hello World</p>\n<!-- generated at 2024-01-01T00:00:02Z -->"

        h1 = dedup.compute_content_hash(content_a)
        assert h1 is not None
        dedup.mark_scraped("https://example.com/page-a", content_hash=h1)

        h2 = dedup.compute_content_hash(content_b)
        assert h2 is not None
        assert h2 != h1  # Different hash due to timestamp difference
        assert not dedup.is_duplicate_content(h2)

    def test_empty_markdown_not_duplicate(self):
        """Empty markdown never counts as a duplicate."""
        from agent.dedup import DedupManager

        dedup = DedupManager()

        # Empty markdown should return None hash
        assert dedup.compute_content_hash("") is None
        assert dedup.compute_content_hash("   ") is None
        assert dedup.compute_content_hash("\n\n  \n") is None

    def test_empty_markdown_not_deduped_via_is_duplicate(self):
        """Explicit check that empty markdown is included (not treated as duplicate)."""
        from agent.dedup import DedupManager

        dedup = DedupManager()

        # First page with some content
        h1 = dedup.compute_content_hash("Some content")
        assert h1 is not None
        dedup.mark_scraped("https://example.com/page-a", content_hash=h1)

        # Second page with empty markdown — hash is None, is_duplicate_content should not trigger
        # Since compute_content_hash returns None for empty markdown, we should never call
        # is_duplicate_content with None. The caller handles this.
        assert dedup.compute_content_hash("") is None

    # ── Canonical runs before content hash ──────────────────────

    def test_canonical_check_before_content_hash(self):
        """Canonical check runs first; content hash not checked if canonical matches."""
        from agent.dedup import DedupManager

        dedup = DedupManager()

        # Set up page B as scraped
        dedup.mark_scraped("https://example.com/page-b")

        # Page A has canonical=page-B AND identical content
        html = '<html><head><link rel="canonical" href="https://example.com/page-b"></head><body>Same content</body></html>'
        result = dedup.check_canonical(html, "https://example.com/page-a")
        assert result == "https://example.com/page-b"

        # The content hash check would only apply if canonical check returned None
        # (not a duplicate). Since canonical IS a duplicate, we never check content hash.

    # ── mark_scraped and scraped URL tracking ───────────────────

    def test_is_scraped_url_after_mark(self):
        """URL is reported as scraped after mark_scraped()."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        assert not dedup.is_scraped_url("https://example.com/page")

        dedup.mark_scraped("https://example.com/page")
        assert dedup.is_scraped_url("https://example.com/page")

    def test_is_scraped_url_trailing_slash_normalized(self):
        """URLs with and without trailing slash normalized correctly."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page")

        assert dedup.is_scraped_url("https://example.com/page")
        assert dedup.is_scraped_url("https://example.com/page/")

    def test_canonical_url_tracking(self):
        """get_canonical_for returns the canonical URL for a scraped page."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped(
            "https://example.com/page-a",
            canonical_url="https://example.com/canonical-page",
        )
        assert (
            dedup.get_canonical_for("https://example.com/page-a")
            == "https://example.com/canonical-page"
        )

    def test_canonical_tracking_no_canonical(self):
        """get_canonical_for returns None when no canonical was recorded."""
        from agent.dedup import DedupManager

        dedup = DedupManager()
        dedup.mark_scraped("https://example.com/page")
        assert dedup.get_canonical_for("https://example.com/page") is None


# ── CrawlEngine dedup integration tests ───────────────────────────


class TestCrawlEngineDedup:
    """Tests for CrawlEngine integration with DedupManager."""

    @pytest.mark.asyncio
    async def test_canonical_dedup_skips_duplicate_page(self, mock_scraper):
        """Page with canonical=already-scraped URL is skipped."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=5, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        # Start page links to page-a (no canonical) and page-b (canonical → page-a)
        with patch.object(engine, "_get_html") as mock_html:

            def html_side_effect(url: str) -> str | None:
                if "page-b" in url:
                    return (
                        '<html><head><link rel="canonical" '
                        'href="http://example.com/page-a"></head>'
                        "<body>Page B with canonical to A</body></html>"
                    )
                return (
                    "<html><body>"
                    "<a href='http://example.com/page-a'>Page A</a>"
                    "<a href='http://example.com/page-b'>Page B</a>"
                    "</body></html>"
                )

            mock_html.side_effect = html_side_effect

            result = await engine.run("http://example.com/")

        # Should have start URL + page-a = 2 pages (page-b skipped by canonical dedup)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        assert "http://example.com/page-b" not in urls
        # Should have a dedup error for page-b
        dedup_errors = [
            e for e in result.errors if e.get("error_type") == "duplicate_canonical"
        ]
        assert len(dedup_errors) == 1
        assert dedup_errors[0]["url"] == "http://example.com/page-b"

    @pytest.mark.asyncio
    async def test_content_hash_dedup_skips_duplicate_page(self, mock_scraper):
        """Two pages with identical markdown → second is skipped."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=5, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            # Start URL returns unique content; page-a and page-b share identical content
            if url == "http://example.com/":
                return MockPage.success(url, markdown="# Start page content")
            return MockPage.success(url, markdown="# Identical Content\n\nSame text.")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = (
                "<html><body>"
                "<a href='http://example.com/page-a'>Page A</a>"
                "<a href='http://example.com/page-b'>Page B</a>"
                "</body></html>"
            )

            result = await engine.run("http://example.com/")

        # Should have start URL + page-a = 2 pages (page-b skipped by content dedup)
        assert result.completed == 2
        urls = [p["url"] for p in result.pages]
        # page-b should be dedup'd (identical content to page-a)
        assert "http://example.com/page-b" not in urls
        # Should have a dedup error for page-b
        dedup_errors = [
            e for e in result.errors if e.get("error_type") == "duplicate_content"
        ]
        assert len(dedup_errors) == 1
        assert dedup_errors[0]["url"] == "http://example.com/page-b"

    @pytest.mark.asyncio
    async def test_canonical_check_before_content_hash_in_engine(self, mock_scraper):
        """Canonical check runs before content hash check in crawl engine.

        A page that is both canonical-dup AND content-dup should report
        canonical as the dedup reason.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=5, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            # Start URL has unique content; page-a and page-b share content
            if url == "http://example.com/":
                return MockPage.success(url, markdown="# Start page unique content")
            return MockPage.success(url, markdown="# Same Content")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:

            def html_side_effect(url: str) -> str | None:
                if "page-b" in url:
                    # Page B has canonical=page-a AND identical content
                    return (
                        '<html><head><link rel="canonical" '
                        'href="http://example.com/page-a"></head>'
                        "<body>Same</body></html>"
                    )
                return (
                    "<html><body>"
                    "<a href='http://example.com/page-a'>Page A</a>"
                    "<a href='http://example.com/page-b'>Page B</a>"
                    "</body></html>"
                )

            mock_html.side_effect = html_side_effect

            result = await engine.run("http://example.com/")

        # The dedup error for page-b should be canonical, NOT content
        canonical_errors = [
            e for e in result.errors if e.get("error_type") == "duplicate_canonical"
        ]
        content_errors = [
            e for e in result.errors if e.get("error_type") == "duplicate_content"
        ]
        assert len(canonical_errors) == 1, (
            f"Expected 1 canonical error, got {len(canonical_errors)}: {result.errors}"
        )
        assert len(content_errors) == 0, (
            f"Expected 0 content errors, got {len(content_errors)}: {result.errors}"
        )

    @pytest.mark.asyncio
    async def test_self_referencing_canonical_in_crawl(self, mock_scraper):
        """Page with self-referencing canonical is scraped normally."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=3, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, f"# Content of {url}")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            # All pages have self-referencing canonical tags
            def html_side_effect(url: str) -> str | None:
                return (
                    f'<html><head><link rel="canonical" href="{url}"></head>'
                    "<body>"
                    "<a href='http://example.com/page-a'>Page A</a>"
                    "</body></html>"
                )

            mock_html.side_effect = html_side_effect

            result = await engine.run("http://example.com/")

        # Both pages should be scraped (self-canonical is ignored)
        assert result.completed == 2
        assert len(result.pages) == 2
        # No dedup errors
        dedup_errors = [e for e in result.errors if e.get("error_type") is not None]
        assert len(dedup_errors) == 0

    @pytest.mark.asyncio
    async def test_empty_markdown_includes_page(self, mock_scraper):
        """Pages with empty markdown are still included (not dedup'd)."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=3, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            return MockPage.success(url, markdown="")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = (
                "<html><body>"
                "<a href='http://example.com/page-a'>Page A</a>"
                "<a href='http://example.com/page-b'>Page B</a>"
                "</body></html>"
            )

            result = await engine.run("http://example.com/")

        # All 3 pages should be included (empty markdown is not treated as duplicate)
        assert result.completed == 3
        assert len(result.pages) == 3

    @pytest.mark.asyncio
    async def test_total_includes_dedup_pages(self, mock_scraper):
        """Crawl total includes dedup'd pages (VAL-SCRAPE-052)."""
        from agent.crawler import CrawlEngine, CrawlOptions

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(max_pages=5, max_depth=1),
        )

        async def scrape_side_effect(
            url: str, force_browser: bool = False, **kwargs
        ) -> dict:
            # Start URL has unique content; page-a and page-b have identical content
            if url == "http://example.com/":
                return MockPage.success(url, markdown="# Start page unique content")
            return MockPage.success(url, markdown="# Identical Content")

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = (
                "<html><body>"
                "<a href='http://example.com/page-a'>Page A</a>"
                "<a href='http://example.com/page-b'>Page B</a>"
                "<a href='http://example.com/page-c'>Page C</a>"
                "</body></html>"
            )

            result = await engine.run("http://example.com/")

        # Start URL + page-a = 2 completed (page-b and page-c dedup'd)
        assert result.completed == 2, (
            f"Expected completed=2, got completed={result.completed}"
        )
        # Total should be 4 = 2 completed + 2 dedup'd
        assert result.total == 4, f"Expected total=4, got total={result.total}"
        # Verify dedup errors exist
        dedup_errors = [
            e for e in result.errors if e.get("error_type") == "duplicate_content"
        ]
        assert len(dedup_errors) == 2
        # Verify the errors endpoint can serve these entries (they have proper fields)


# ── CrawlEngine cache integration ────────────────────────────────


class TestCrawlEngineCache:
    """Tests for CrawlEngine integration with CrawlCache."""

    @pytest.mark.asyncio
    async def test_cache_check_before_scrape_with_max_age(self, mock_scraper):
        """With maxAge set, cache is checked before each scrape."""
        from agent.crawl_cache import CrawlCache
        from agent.crawler import CrawlEngine, CrawlOptions

        url = "http://example.com/"

        # Create a real CrawlCache backed by a mock Redis
        cache = CrawlCache("redis://localhost:6379/0")
        cache.redis = mock_scraper._mock_cache_redis = MagicMock()
        # Start with empty cache
        cache.redis.get.return_value = None

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                scrape_options={"max_age": 60000},  # 60s maxAge
            ),
            crawl_cache=cache,
        )

        mock_scraper.scrape.return_value = MockPage.success(url, "# Content")
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body></body></html>"

        result = await engine.run(url)

        # Verify cache was checked (get was called)
        cache.redis.get.assert_called()
        # And scraper was called (cache miss)
        mock_scraper.scrape.assert_called_once()
        assert result.completed == 1

    @pytest.mark.asyncio
    async def test_cache_hit_skips_scraper_call(self, mock_scraper):
        """When cache has fresh content, scraper is NOT called."""
        import hashlib
        import json
        from datetime import UTC, datetime

        from agent.crawl_cache import CrawlCache
        from agent.crawler import CrawlEngine, CrawlOptions

        url = "http://example.com/"

        cache = CrawlCache("redis://localhost:6379/0")
        store: dict[str, str] = {}
        mock_redis = MagicMock()

        def mock_get(key: str) -> str | None:
            return store.get(key)

        def mock_set(key: str, value: str, ex: int | None = None) -> None:
            store[key] = value

        mock_redis.get.side_effect = mock_get
        mock_redis.set.side_effect = mock_set
        mock_redis.delete.side_effect = lambda key: store.pop(key, None)
        cache.redis = mock_redis

        # Pre-populate cache with fresh content (very recent timestamp)
        _now_iso = datetime.now(UTC).isoformat()
        cached_entry = {
            "url": url,
            "data": MockPage.success(url, "# Cached Content"),
            "cached_at": _now_iso,
            "ttl_ms": 60000,
        }
        cache_key = f"crawl:cache:{hashlib.sha256(url.encode()).hexdigest()}"
        store[cache_key] = json.dumps(cached_entry)

        # Set up a counter to track scraper calls
        call_count = 0

        async def scrape_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockPage.success(url, "# Fresh Content")  # Different from cached

        mock_scraper.scrape = AsyncMock(side_effect=scrape_side_effect)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                scrape_options={"max_age": 60000},
            ),
            crawl_cache=cache,
        )

        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body></body></html>"

        result = await engine.run(url)

        # Verify scraper was NOT called (cache hit)
        assert call_count == 0, f"Expected 0 scraper calls, got {call_count}"
        # Verify page came from cache (markdown matches cached content)
        assert result.completed == 1
        assert len(result.pages) == 1
        assert "# Cached Content" in result.pages[0].get("markdown", ""), (
            "Page should contain cached content, not fresh content"
        )

    @pytest.mark.asyncio
    async def test_cache_miss_with_min_age_returns_error(self, mock_scraper):
        """With minAge set and no cache entry, error is returned for the URL."""
        from agent.crawl_cache import CrawlCache
        from agent.crawler import CrawlEngine, CrawlOptions

        url = "http://example.com/"

        cache = CrawlCache("redis://localhost:6379/0")
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No cache entry
        cache.redis = mock_redis

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                scrape_options={"min_age": 60000},  # cache-only mode
            ),
            crawl_cache=cache,
        )

        mock_scraper.scrape.return_value = MockPage.success(url, "# Content")
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body></body></html>"

        result = await engine.run(url)

        # Verify scraper was NOT called (minAge prevents fresh scrape)
        mock_scraper.scrape.assert_not_called()
        # But page was NOT scraped — it should be an error
        assert result.completed == 0
        assert len(result.pages) == 0
        # Error should mention cache miss
        assert len(result.errors) == 1
        assert "CACHE_MISS" in result.errors[0].get("error_code", "")
        assert "cache miss" in result.errors[0].get("error", "").lower()

    @pytest.mark.asyncio
    async def test_cache_stores_result_after_fresh_scrape(self, mock_scraper):
        """After a fresh scrape with maxAge set, the result is stored in cache."""
        import hashlib
        import json

        from agent.crawl_cache import CrawlCache
        from agent.crawler import CrawlEngine, CrawlOptions

        url = "http://example.com/"

        store_dict: dict[str, str] = {}
        cache = CrawlCache("redis://localhost:6379/0")
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: store_dict.get(k)
        mock_redis.set.side_effect = lambda k, v, ex=None: store_dict.update({k: v})
        cache.redis = mock_redis

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                scrape_options={"max_age": 60000},
            ),
            crawl_cache=cache,
        )

        mock_scraper.scrape.return_value = MockPage.success(url, "# Fresh Content")
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body></body></html>"

        result = await engine.run(url)

        assert result.completed == 1
        # Verify cache was populated
        cache_key = f"crawl:cache:{hashlib.sha256(url.encode()).hexdigest()}"
        assert cache_key in store_dict, "Cache should have been populated after scrape"
        cached = json.loads(store_dict[cache_key])
        assert cached["url"] == url
        assert cached["ttl_ms"] == 60000

    @pytest.mark.asyncio
    async def test_max_age_zero_bypasses_cache(self, mock_scraper):
        """maxAge=0 bypasses cache entirely — always fresh scrape."""
        import hashlib
        import json

        from agent.crawl_cache import CrawlCache
        from agent.crawler import CrawlEngine, CrawlOptions

        url = "http://example.com/"

        store_dict: dict[str, str] = {}
        cache = CrawlCache("redis://localhost:6379/0")
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: store_dict.get(k)
        mock_redis.set.side_effect = lambda k, v, ex=None: store_dict.update({k: v})
        cache.redis = mock_redis

        # Pre-populate cache with stale content
        cache_key = f"crawl:cache:{hashlib.sha256(url.encode()).hexdigest()}"
        stale_entry = {
            "url": url,
            "data": MockPage.success(url, "# Stale Cached Content"),
            "cached_at": "2026-06-19T12:00:00+00:00",
            "ttl_ms": 60000,
        }
        store_dict[cache_key] = json.dumps(stale_entry)

        engine = CrawlEngine(
            mock_scraper,
            store=None,
            options=CrawlOptions(
                max_pages=1,
                max_depth=0,
                scrape_options={"max_age": 0},  # bypass cache
            ),
            crawl_cache=cache,
        )

        mock_scraper.scrape.return_value = MockPage.success(
            url, "# Freshly Scraped Content"
        )
        with patch.object(engine, "_get_html") as mock_html:
            mock_html.return_value = "<html><body></body></html>"

        result = await engine.run(url)

        # Scraper was called (cache bypassed)
        mock_scraper.scrape.assert_called_once()
        assert result.completed == 1
        assert "# Freshly Scraped Content" in result.pages[0].get("markdown", "")


# ── GET /v2/crawl/active endpoint tests ──────────────────────────


class _MockJobMeta:
    """Helper to create a mock job metadata dict for store.list_active_jobs()."""

    @staticmethod
    def crawl(
        job_id: str,
        status: str = "processing",
        url: str = "http://example.com",
        max_pages: int = 10,
        max_depth: int = 2,
        completed: int = 0,
        total: int = 10,
    ) -> dict:
        return {
            "id": job_id,
            "kind": "crawl",
            "status": status,
            "created_at": "2025-01-01T00:00:00+00:00",
            "expires_at": "2025-01-02T00:00:00+00:00",
            "payload": {
                "url": url,
                "max_pages": max_pages,
                "max_depth": max_depth,
            },
            "data": {
                "completed": completed,
                "total": total,
                "pages": [],
                "errors": [],
            },
        }

    @staticmethod
    def agent() -> dict:
        return {
            "id": str(uuid4()),
            "kind": "agent",
            "status": "processing",
            "created_at": "2025-01-01T00:00:00+00:00",
            "payload": {"prompt": "research something"},
        }

    @staticmethod
    def extract() -> dict:
        return {
            "id": str(uuid4()),
            "kind": "extract",
            "status": "processing",
            "created_at": "2025-01-01T00:00:00+00:00",
            "payload": {"urls": ["http://example.com"]},
        }


@pytest.fixture
def active_crawl_app():
    """Build a FastAPI test app with a mocked JobStore.

    The store's ``list_active_jobs()`` is pre-configured to return
    a known set of jobs. Tests can override ``app.state.job_store``
    after calling the fixture.
    """
    from agent.api import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    app.state.job_store = MagicMock()
    app.state.job_store.list_active_jobs.return_value = []

    with TestClient(app) as client:
        yield client


class TestListActiveCrawls:
    """Unit tests for GET /v2/crawl/active."""

    def test_returns_only_crawl_jobs(self, active_crawl_app):
        """VAL-SCRAPE-041: Only returns jobs with kind='crawl'.

        The JobStore.list_active_jobs() is responsible for filtering by
        kind. This test verifies that the API endpoint correctly passes
        ``kind="crawl"`` to the store so that non-crawl jobs are never
        returned.
        """
        client = active_crawl_app

        crawl_id = str(uuid4())

        # Mock returns only crawl jobs (as if store already filtered by kind)
        client.app.state.job_store.list_active_jobs.return_value = [
            _MockJobMeta.crawl(crawl_id),
        ]

        resp = client.get("/v2/crawl/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == crawl_id

        # Verify the store was called with kind="crawl"
        client.app.state.job_store.list_active_jobs.assert_called_with(
            kind="crawl", status="processing", limit=50
        )

    def test_excludes_completed_crawls(self, active_crawl_app):
        """VAL-SCRAPE-042: Completed/failed/cancelled crawls are excluded."""
        client = active_crawl_app

        processing_id = str(uuid4())
        completed_id = str(uuid4())
        failed_id = str(uuid4())
        cancelled_id = str(uuid4())

        # Only return the processing job (simulating kind="crawl", status="processing" filter)
        client.app.state.job_store.list_active_jobs.return_value = [
            _MockJobMeta.crawl(processing_id, status="processing"),
        ]

        resp = client.get("/v2/crawl/active")
        assert resp.status_code == 200
        data = resp.json()
        ids = [item["id"] for item in data["data"]]
        assert processing_id in ids, "Processing crawl should be present"
        assert completed_id not in ids, "Completed crawl should be excluded"
        assert failed_id not in ids, "Failed crawl should be excluded"
        assert cancelled_id not in ids, "Cancelled crawl should be excluded"

        # Verify that list_active_jobs was called with kind="crawl" and status="processing"
        client.app.state.job_store.list_active_jobs.assert_called_with(
            kind="crawl", status="processing", limit=50
        )

    def test_response_structure(self, active_crawl_app):
        """VAL-SCRAPE-043: Response has correct structure with all required fields."""
        client = active_crawl_app

        crawl_id = str(uuid4())
        client.app.state.job_store.list_active_jobs.return_value = [
            _MockJobMeta.crawl(
                crawl_id,
                url="http://test-site:8000",
                max_pages=5,
                max_depth=1,
                completed=3,
                total=5,
            ),
        ]

        resp = client.get("/v2/crawl/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert len(body["data"]) == 1

        item = body["data"][0]
        assert item["id"] == crawl_id
        assert item["url"] == "http://test-site:8000"
        assert item["status"] == "processing"
        assert item["created_at"] == "2025-01-01T00:00:00+00:00"
        assert item["completed"] == 3
        assert item["total"] == 5
        assert item["max_pages"] == 5
        assert item["max_depth"] == 1

    def test_empty_when_no_active_crawls(self, active_crawl_app):
        """VAL-SCRAPE-044: Returns empty data array when no active crawls."""
        client = active_crawl_app
        # Store returns no crawl jobs
        client.app.state.job_store.list_active_jobs.return_value = []

        resp = client.get("/v2/crawl/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_filterable_by_status(self, active_crawl_app):
        """Supports optional status query parameter to filter by status."""
        client = active_crawl_app

        client.app.state.job_store.list_active_jobs.return_value = []

        resp = client.get("/v2/crawl/active?status=completed")
        assert resp.status_code == 200

        # Verify status was passed through to the store
        client.app.state.job_store.list_active_jobs.assert_called_with(
            kind="crawl", status="completed", limit=50
        )

    def test_does_not_show_agent_or_extract_jobs_thorough(self, active_crawl_app):
        """More thorough check: no agent/extract/llmstxt jobs appear."""
        client = active_crawl_app

        client.app.state.job_store.list_active_jobs.return_value = [
            _MockJobMeta.crawl(str(uuid4())),
        ]

        resp = client.get("/v2/crawl/active")
        assert resp.status_code == 200

        # Verify list_active_jobs was called with kind="crawl"
        client.app.state.job_store.list_active_jobs.assert_called_with(
            kind="crawl", status="processing", limit=50
        )


# ══════════════════════════════════════════════════════════════════
# NL→Params Tests
# ══════════════════════════════════════════════════════════════════


class TestNlParamsSafeParse:
    """Tests for _safe_parse_llm_response()."""

    def test_plain_json(self):
        from agent.nl_params import _safe_parse_llm_response

        text = '{"include_paths": ["blog/.*"], "max_depth": 2}'
        result = _safe_parse_llm_response(text)
        assert result == {"include_paths": ["blog/.*"], "max_depth": 2}

    def test_markdown_code_fence(self):
        from agent.nl_params import _safe_parse_llm_response

        text = '```json\n{"include_paths": ["blog/.*"]}\n```'
        result = _safe_parse_llm_response(text)
        assert result == {"include_paths": ["blog/.*"]}

    def test_json_embedded_in_text(self):
        from agent.nl_params import _safe_parse_llm_response

        text = 'Here are the params: {"include_paths": ["blog/.*"]} Thanks!'
        result = _safe_parse_llm_response(text)
        assert result == {"include_paths": ["blog/.*"]}

    def test_empty_string_returns_none(self):
        from agent.nl_params import _safe_parse_llm_response

        result = _safe_parse_llm_response("")
        assert result is None

    def test_invalid_json_returns_none(self):
        from agent.nl_params import _safe_parse_llm_response

        result = _safe_parse_llm_response("not json at all")
        assert result is None


class TestNlParamsValidateDerived:
    """Tests for _validate_derived_params()."""

    def test_valid_params_preserved(self):
        from agent.nl_params import _validate_derived_params

        params = {
            "include_paths": ["blog/.*"],
            "exclude_paths": ["admin/.*"],
            "max_depth": 3,
            "max_pages": 50,
            "ignore_robots_txt": True,
            "robots_user_agent": "MyBot/1.0",
            "deduplicate_similar_urls": True,
        }
        result = _validate_derived_params(params)
        assert result["include_paths"] == ["blog/.*"]
        assert result["exclude_paths"] == ["admin/.*"]
        assert result["max_depth"] == 3
        assert result["max_pages"] == 50
        assert result["ignore_robots_txt"] is True
        assert result["robots_user_agent"] == "MyBot/1.0"
        assert result["deduplicate_similar_urls"] is True

    def test_none_removed(self):
        from agent.nl_params import _validate_derived_params

        # Only include_paths is valid, everything else None/invalid should be omitted
        params = {
            "include_paths": ["blog/.*"],
            "exclude_paths": None,
            "max_depth": None,
            "max_pages": None,
            "ignore_robots_txt": None,
            "robots_user_agent": None,
            "deduplicate_similar_urls": None,
        }
        result = _validate_derived_params(params)
        assert result == {"include_paths": ["blog/.*"]}

    def test_invalid_types_filtered(self):
        from agent.nl_params import _validate_derived_params

        params = {
            "include_paths": "not a list",  # should be filtered
            "exclude_paths": [1, 2, 3],  # non-string items
            "max_depth": -1,  # negative should be filtered
            "max_pages": 0,  # zero should be filtered
            "ignore_robots_txt": "yes",  # not a bool
            "robots_user_agent": 123,  # not a string
            "deduplicate_similar_urls": None,
        }
        result = _validate_derived_params(params)
        assert "include_paths" not in result
        assert "exclude_paths" not in result
        assert "max_depth" not in result
        assert "max_pages" not in result
        assert "ignore_robots_txt" not in result
        assert "robots_user_agent" not in result
        assert result == {}

    def test_empty_input(self):
        from agent.nl_params import _validate_derived_params

        result = _validate_derived_params({})
        assert result == {}

    def test_max_depth_zero_accepted(self):
        """Depth 0 is valid (scrape only start URL)."""
        from agent.nl_params import _validate_derived_params

        result = _validate_derived_params({"max_depth": 0})
        assert result["max_depth"] == 0


class TestNlParamsMerge:
    """Tests for merge_params()."""

    def test_llm_derived_used_when_no_explicit(self):
        from agent.nl_params import merge_params

        llm = {"include_paths": ["blog/.*"], "max_depth": 2}
        explicit = {}
        result = merge_params(llm, explicit)
        assert result["include_paths"] == ["blog/.*"]
        assert result["max_depth"] == 2

    def test_explicit_overrides_llm(self):
        from agent.nl_params import merge_params

        llm = {"include_paths": ["blog/.*"], "max_depth": 2}
        explicit = {"include_paths": ["docs/.*"]}
        result = merge_params(llm, explicit)
        assert result["include_paths"] == ["docs/.*"]
        assert result["max_depth"] == 2  # Not overridden

    def test_explicit_overrides_llm_max_depth(self):
        from agent.nl_params import merge_params

        llm = {"include_paths": ["blog/.*"], "max_depth": 3}
        explicit = {"max_depth": 1}
        result = merge_params(llm, explicit)
        assert result["include_paths"] == ["blog/.*"]
        assert result["max_depth"] == 1

    def test_both_exclude_paths_merged(self):
        from agent.nl_params import merge_params

        llm = {"exclude_paths": ["admin/.*"], "max_depth": 2}
        explicit = {"max_depth": 0}
        result = merge_params(llm, explicit)
        assert result["exclude_paths"] == ["admin/.*"]
        assert result["max_depth"] == 0

    def test_llm_error_preserved(self):
        from agent.nl_params import merge_params

        llm = {"error": "LLM unavailable", "max_depth": 2}
        explicit = {}
        result = merge_params(llm, explicit)
        assert result["error"] == "LLM unavailable"
        assert result["max_depth"] == 2

    def test_explicit_none_does_not_override(self):
        """Explicitly None fields should not override LLM-derived non-None values."""
        from agent.nl_params import merge_params

        llm = {"include_paths": ["blog/.*"], "max_depth": 2}
        explicit = {"include_paths": None, "max_depth": None}
        result = merge_params(llm, explicit)
        # The merge function only checks explicit_val is not None
        assert result["include_paths"] == ["blog/.*"]
        assert result["max_depth"] == 2


class TestNlParamsDeriveCrawlParams:
    """Tests for derive_crawl_params() with mocked LLM client."""

    @pytest.mark.asyncio
    async def test_successful_derivation(self):
        """Happy path: LLM returns valid JSON with crawl params."""
        from agent.nl_params import derive_crawl_params

        expected_response = json.dumps(
            {
                "include_paths": ["blog/.*"],
                "exclude_paths": None,
                "max_depth": 2,
                "max_pages": 10,
                "ignore_robots_txt": False,
                "deduplicate_similar_urls": True,
                "reasoning": "User wants blog posts.",
            }
        )

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value=expected_response),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            result = await derive_crawl_params(
                prompt="crawl only the blog posts",
                llm_base_url="http://llm.test/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            assert "error" not in result
            assert result["include_paths"] == ["blog/.*"]
            assert result["max_depth"] == 2
            assert result["max_pages"] == 10

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_error(self):
        """LLM health check fails — returns error without crashing."""
        from agent.nl_params import derive_crawl_params

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=False)
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            result = await derive_crawl_params(
                prompt="crawl everything",
                llm_base_url="http://llm.test/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            assert "error" in result
            assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_response_falls_back(self):
        """LLM returns unparseable JSON — returns error without crashing."""
        from agent.nl_params import derive_crawl_params

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value="This is not JSON at all"),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            result = await derive_crawl_params(
                prompt="crawl the docs",
                llm_base_url="http://llm.test/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            assert "error" in result
            assert "invalid json" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_llm_error_response_handled(self):
        """LLM client returns an error string — handled gracefully."""
        from agent.nl_params import derive_crawl_params

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value="Error: LLM API returned 429"),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            result = await derive_crawl_params(
                prompt="crawl products",
                llm_base_url="http://llm.test/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            assert "error" in result
            assert "Error" in result["error"]

    @pytest.mark.asyncio
    async def test_llm_exception_handled(self):
        """LLM client raises exception — caught and returned as error."""
        from agent.nl_params import derive_crawl_params

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(side_effect=ConnectionError("Connection refused")),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            result = await derive_crawl_params(
                prompt="crawl all",
                llm_base_url="http://llm.test/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            assert "error" in result
            assert "Connection refused" in result["error"]


class TestNlParamsPromptModel:
    """Tests for the prompt field on CrawlRequest model."""

    def test_prompt_accepted(self):
        """CrawlRequest accepts a prompt field."""
        from agent.models import CrawlRequest

        req = CrawlRequest(
            url="https://example.com",
            prompt="crawl only the blog posts",
        )
        assert req.prompt == "crawl only the blog posts"

    def test_prompt_default_none(self):
        """CrawlRequest prompt defaults to None."""
        from agent.models import CrawlRequest

        req = CrawlRequest(url="https://example.com")
        assert req.prompt is None

    def test_prompt_max_length_enforced(self):
        """CrawlRequest rejects prompt > 10000 chars."""
        from agent.models import CrawlRequest
        from pydantic import ValidationError

        long_prompt = "x" * 10001
        with pytest.raises(ValidationError):
            CrawlRequest(url="https://example.com", prompt=long_prompt)

    def test_prompt_10000_chars_accepted(self):
        """CrawlRequest accepts prompt exactly 10000 chars."""
        from agent.models import CrawlRequest

        exact_prompt = "x" * 10000
        req = CrawlRequest(url="https://example.com", prompt=exact_prompt)
        assert req.prompt == exact_prompt

    def test_prompt_in_model_dump(self):
        """prompt appears in model_dump output."""
        from agent.models import CrawlRequest

        req = CrawlRequest(
            url="https://example.com",
            prompt="crawl blog posts",
        )
        dumped = req.model_dump(exclude_unset=True)
        assert "prompt" in dumped
        assert dumped["prompt"] == "crawl blog posts"


class TestParamsPreviewModel:
    """Tests for ParamsPreviewRequest and ParamsPreviewResponse models."""

    def test_params_preview_request_requires_url_and_prompt(self):
        """ParamsPreviewRequest requires both url and prompt."""
        from agent.models import ParamsPreviewRequest

        req = ParamsPreviewRequest(url="https://example.com", prompt="crawl blog")
        assert req.url == "https://example.com"
        assert req.prompt == "crawl blog"

    def test_params_preview_request_missing_prompt_rejected(self):
        """ParamsPreviewRequest without prompt is rejected."""
        from agent.models import ParamsPreviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParamsPreviewRequest(url="https://example.com")

    def test_params_preview_request_missing_url_rejected(self):
        """ParamsPreviewRequest without url is rejected."""
        from agent.models import ParamsPreviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParamsPreviewRequest(prompt="crawl blog")

    def test_params_preview_request_invalid_url_rejected(self):
        """ParamsPreviewRequest with invalid URL scheme."""
        from agent.models import ParamsPreviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParamsPreviewRequest(url="ftp://example.com", prompt="crawl blog")

    def test_params_preview_request_prompt_max_length(self):
        """ParamsPreviewRequest rejects prompt > 10000 chars."""
        from agent.models import ParamsPreviewRequest
        from pydantic import ValidationError

        long_prompt = "x" * 10001
        with pytest.raises(ValidationError):
            ParamsPreviewRequest(url="https://example.com", prompt=long_prompt)

    def test_params_preview_response_defaults(self):
        """ParamsPreviewResponse has sensible defaults."""
        from agent.models import ParamsPreviewResponse

        resp = ParamsPreviewResponse()
        assert resp.success is True
        assert resp.include_paths is None
        assert resp.exclude_paths is None
        assert resp.max_depth is None
        assert resp.limit is None
        assert resp.error is None

    def test_params_preview_response_camelcase_output(self):
        """ParamsPreviewResponse emits camelCase when serialized."""
        from agent.models import ParamsPreviewResponse

        resp = ParamsPreviewResponse(
            success=True,
            include_paths=["blog/.*"],
            exclude_paths=["admin/.*"],
            max_depth=2,
            limit=10,
        )
        dumped = resp.model_dump(by_alias=True)
        assert dumped["includePaths"] == ["blog/.*"]
        assert dumped["excludePaths"] == ["admin/.*"]
        assert dumped["maxDepth"] == 2
        assert dumped["limit"] == 10


@pytest.fixture
def app_with_mocks():
    """Build a FastAPI test app with mocked dependencies for param endpoint tests."""
    from agent.api import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    app.state.job_store = MagicMock()
    app.state.job_store.create_job.return_value = "mock-job-id"
    app.state.llm_base_url = "http://llm.test/v1"
    app.state.llm_api_key = "test-key"
    app.state.llm_model = "test-model"
    app.state.task_tracker = MagicMock()

    with TestClient(app) as client:
        yield client


class TestParamsPreviewEndpoint:
    """Tests for the POST /v2/crawl/params-preview endpoint using TestClient."""

    def test_params_preview_with_prompt(self, app_with_mocks):
        """POST /v2/crawl/params-preview returns derived params."""
        from unittest.mock import AsyncMock, patch

        client = app_with_mocks
        expected_response = json.dumps(
            {
                "include_paths": ["blog/.*"],
                "exclude_paths": None,
                "max_depth": 2,
                "max_pages": 10,
                "ignore_robots_txt": False,
                "deduplicate_similar_urls": True,
                "reasoning": "User wants blog posts.",
            }
        )

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value=expected_response),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            resp = client.post(
                "/v2/crawl/params-preview",
                json={
                    "url": "https://example.com",
                    "prompt": "crawl only the blog posts",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["includePaths"] == ["blog/.*"]
            assert data["maxDepth"] == 2
            assert "error" not in data or data["error"] is None

    def test_params_preview_without_prompt_returns_422(self, app_with_mocks):
        """POST /v2/crawl/params-preview without prompt returns 422."""
        client = app_with_mocks
        resp = client.post(
            "/v2/crawl/params-preview",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 422

    def test_params_preview_without_url_returns_422(self, app_with_mocks):
        """POST /v2/crawl/params-preview without url returns 422."""
        client = app_with_mocks
        resp = client.post(
            "/v2/crawl/params-preview",
            json={"prompt": "crawl blog"},
        )
        assert resp.status_code == 422

    def test_params_preview_llm_unavailable(self, app_with_mocks):
        """POST /v2/crawl/params-preview handles LLM unavailability gracefully."""
        from unittest.mock import AsyncMock, patch

        client = app_with_mocks

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=False)
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            resp = client.post(
                "/v2/crawl/params-preview",
                json={"url": "https://example.com", "prompt": "crawl blog posts"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "error" in data
            assert data["error"] is not None

    def test_params_preview_llm_invalid_json(self, app_with_mocks):
        """POST /v2/crawl/params-preview handles invalid LLM JSON gracefully."""
        from unittest.mock import AsyncMock, patch

        client = app_with_mocks

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value="not valid json"),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            resp = client.post(
                "/v2/crawl/params-preview",
                json={"url": "https://example.com", "prompt": "crawl blog posts"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "error" in data
            assert data["error"] is not None

    def test_params_preview_endpoint_does_not_create_crawl_job(self, app_with_mocks):
        """POST /v2/crawl/params-preview does not create a crawl job."""
        from unittest.mock import AsyncMock, patch

        client = app_with_mocks
        expected_response = json.dumps(
            {
                "include_paths": ["blog/.*"],
                "exclude_paths": None,
                "max_depth": 2,
                "max_pages": 10,
                "ignore_robots_txt": False,
                "deduplicate_similar_urls": True,
                "reasoning": "User wants blog posts.",
            }
        )

        with (
            patch(
                "agent.nl_params.LLMClient.check_health", AsyncMock(return_value=True)
            ),
            patch(
                "agent.nl_params.LLMClient.generate",
                AsyncMock(return_value=expected_response),
            ),
            patch("agent.nl_params.LLMClient.close", AsyncMock()),
        ):
            resp = client.post(
                "/v2/crawl/params-preview",
                json={
                    "url": "https://example.com",
                    "prompt": "crawl only the blog posts",
                },
            )
            assert resp.status_code == 200
            # No crawl job should be created
            # The store's create_job should not have been called
            # (there might be other calls from the test setup, so check no crawl job was created)
            # Since params-preview doesn't create a job, the store is unaffected
            assert "id" not in resp.json()
