"""Tests for agent-svc/agent/scraper_client.py — ScraperClient."""

import pytest


@pytest.fixture
def client():
    from agent.scraper_client import ScraperClient

    return ScraperClient(base_url="http://scraper.test:8001")


class TestScraperClient:
    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self):
        from agent.scraper_client import ScraperClient

        c = ScraperClient(base_url="http://scraper.test:8001/")
        assert c.base_url == "http://scraper.test:8001"

    @pytest.mark.asyncio
    async def test_successful_scrape(self, client):
        mock_data = {
            "success": True,
            "data": {
                "markdown": "# Hello World",
                "source": "llms.txt",
            },
        }

        with pytest.MonkeyPatch.context() as mp:

            async def mock_post(url, json=None):
                import types

                r = types.SimpleNamespace()
                r.json = lambda: mock_data
                return r

            mp.setattr(client._client, "post", mock_post)

            result = await client.scrape("https://example.com")
            assert result["success"] is True
            assert result["data"]["markdown"] == "# Hello World"
            assert result["data"]["source"] == "llms.txt"

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, client):
        import httpx

        with pytest.MonkeyPatch.context() as mp:

            async def mock_post(url, json=None):
                raise httpx.TimeoutException("timed out")

            mp.setattr(client._client, "post", mock_post)

            result = await client.scrape("https://example.com")
            assert result["success"] is False
            assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_general_exception_returns_error(self, client):
        with pytest.MonkeyPatch.context() as mp:

            async def mock_post(url, json=None):
                raise ConnectionError("Connection refused")

            mp.setattr(client._client, "post", mock_post)

            result = await client.scrape("https://example.com")
            assert result["success"] is False
            assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_sends_url_in_request_body(self, client):
        captured = {}

        with pytest.MonkeyPatch.context() as mp:

            async def mock_post(url, json=None):
                captured["url"] = url
                captured["json"] = json
                import types

                r = types.SimpleNamespace()
                r.json = lambda: {
                    "success": True,
                    "data": {"markdown": "", "source": "test"},
                }
                return r

            mp.setattr(client._client, "post", mock_post)

            await client.scrape("https://example.com/page")
            assert captured["url"] == "http://scraper.test:8001/scrape"
            assert captured["json"] == {"url": "https://example.com/page"}

    @pytest.mark.asyncio
    async def test_sends_full_page_contents_request(self, client):
        captured = {}

        with pytest.MonkeyPatch.context() as mp:

            async def mock_post(url, json=None):
                captured["url"] = url
                captured["json"] = json
                import types

                response = types.SimpleNamespace()
                response.json = lambda: {"success": True, "data": {"markdown": ""}}
                return response

            mp.setattr(client._client, "post", mock_post)

            await client.scrape(
                "https://example.com/page",
                contents={"text": {"verbosity": "full"}},
            )

        assert captured["json"] == {
            "url": "https://example.com/page",
            "contents": {"text": {"verbosity": "full"}},
        }

    @pytest.mark.asyncio
    async def test_close(self, client):
        closed = False

        async def mock_close():
            nonlocal closed
            closed = True

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(client._client, "aclose", mock_close)
            await client.close()
            assert closed
