"""Tests for scraper-svc/scraper/meta.py — lightweight meta tag extraction."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFetchMetaTags:
    @pytest.mark.asyncio
    async def test_extracts_title_and_description(self):
        from scraper.meta import fetch_meta_tags

        html = """<html><head>
            <title>Test Page</title>
            <meta name="description" content="A test page description">
            <meta property="og:description" content="OG description">
        </head></html>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await fetch_meta_tags("https://example.com")

        assert result["title"] == "Test Page"
        assert result["description"] == "A test page description"
        assert result["og_description"] == "OG description"

    @pytest.mark.asyncio
    async def test_missing_meta_returns_none(self):
        from scraper.meta import fetch_meta_tags

        html = "<html><head></head><body><p>No meta here</p></body></html>"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await fetch_meta_tags("https://example.com")

        assert result["title"] is None
        assert result["description"] is None
        assert result["og_description"] is None

    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self):
        from scraper.meta import fetch_meta_tags

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await fetch_meta_tags("https://example.com/missing")

        assert result["title"] is None

    @pytest.mark.asyncio
    async def test_handles_connection_error(self):
        import httpx
        from scraper.meta import fetch_meta_tags

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await fetch_meta_tags("https://example.com")

        assert result["title"] is None

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_values(self):
        from scraper.meta import fetch_meta_tags

        html = """<html><head>
            <title>  Spaced Title  </title>
            <meta name="description" content="  Spaced description  ">
        </head></html>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await fetch_meta_tags("https://example.com")

        assert result["title"] == "Spaced Title"
        assert result["description"] == "Spaced description"


class TestScrapeMetaDestinationGuard:
    @pytest.mark.asyncio
    async def test_private_destination_is_denied_before_http_fetch(self, monkeypatch):
        from types import SimpleNamespace

        import scraper.app as app
        import scraper.fetch as fetch
        from scraper.exceptions import InvalidRequestError

        monkeypatch.setattr(fetch._settings, "scraper_private_url_allowlist", "")

        async def private_url(_url):
            return True, "private target"

        monkeypatch.setattr(fetch, "_is_private_url", private_url)
        fetches = []

        async def unexpected_fetch(url):
            fetches.append(url)
            raise AssertionError("private target must not reach the HTTP client")

        monkeypatch.setattr(app, "fetch_meta_tags", unexpected_fetch)
        with pytest.raises(InvalidRequestError) as exc_info:
            await app.scrape_meta(SimpleNamespace(url="http://127.0.0.1/metadata"))

        assert exc_info.value.details == {"error_code": "PRIVATE_URL_BLOCKED"}
        assert fetches == []

    @pytest.mark.asyncio
    async def test_policy_oracle_outage_returns_unavailable_without_fetch(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        import scraper.app as app
        import scraper.fetch as fetch
        from scraper.exceptions import DestinationValidationUnavailableError
        from scraper.source_http import SourceDestinationPolicyUnavailableError

        monkeypatch.setattr(fetch._settings, "scraper_private_url_allowlist", "")

        async def unavailable(_url):
            raise SourceDestinationPolicyUnavailableError("test-outage")

        async def unexpected_fetch(_url):
            raise AssertionError("unverified destination must not be fetched")

        monkeypatch.setattr(fetch, "_is_private_url", unavailable)
        monkeypatch.setattr(app, "fetch_meta_tags", unexpected_fetch)
        with pytest.raises(DestinationValidationUnavailableError):
            await app.scrape_meta(SimpleNamespace(url="https://example.test/meta"))

    @pytest.mark.asyncio
    async def test_explicit_private_allowlist_still_allows_meta_fetch(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        import scraper.app as app
        import scraper.fetch as fetch

        monkeypatch.setattr(
            fetch._settings, "scraper_private_url_allowlist", "private.test"
        )

        async def unexpected_private_check(_url):
            raise AssertionError("allowlist should bypass private check")

        monkeypatch.setattr(fetch, "_is_private_url", unexpected_private_check)
        fetched = []

        async def fake_fetch(url):
            fetched.append(url)
            return {"title": "Allowed", "description": None, "og_description": None}

        monkeypatch.setattr(app, "fetch_meta_tags", fake_fetch)
        result = await app.scrape_meta(
            SimpleNamespace(url="http://private.test/metadata")
        )

        assert result.success is True
        assert result.title == "Allowed"
        assert fetched == ["http://private.test/metadata"]
