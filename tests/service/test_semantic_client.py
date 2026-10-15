"""Tests for agent-svc/agent/semantic_client.py — SemanticClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSemanticClient:
    @pytest.fixture
    def client(self):
        from agent.semantic_client import SemanticClient

        return SemanticClient(base_url="http://semantic.test:8003")

    def test_timeout_defaults_to_bounded_cold_start_budget(self, monkeypatch):
        from agent.semantic_client import SemanticClient

        monkeypatch.delenv("SEMANTIC_CLIENT_TIMEOUT_SECONDS", raising=False)
        client = SemanticClient(base_url="http://semantic.test:8003")
        assert client.timeout_seconds == 60.0

    def test_timeout_can_be_tuned_by_operator(self, monkeypatch):
        from agent.semantic_client import SemanticClient

        monkeypatch.setenv("SEMANTIC_CLIENT_TIMEOUT_SECONDS", "75.5")
        client = SemanticClient(base_url="http://semantic.test:8003")
        assert client.timeout_seconds == 75.5

    @pytest.mark.asyncio
    async def test_http_client_uses_configured_timeout(self):
        from agent.semantic_client import SemanticClient

        client = SemanticClient(
            base_url="http://semantic.test:8003", timeout_seconds=47
        )
        http_client = MagicMock()
        with patch("httpx.AsyncClient", return_value=http_client) as constructor:
            await client._ensure_client()
        constructor.assert_called_once_with(
            timeout=47,
            headers={"User-Agent": "GroktoCrawl/0.6"},
        )

    @pytest.mark.asyncio
    async def test_embed(self, client):
        """embed() POSTs to /embed and returns embeddings."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        }

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.embed(["hello", "world"])

        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        http_client.post.assert_called_once()
        args, kwargs = http_client.post.call_args
        assert args[0] == "http://semantic.test:8003/embed"
        assert kwargs["json"] == {"input": ["hello", "world"]}

    @pytest.mark.asyncio
    async def test_rerank(self, client):
        """rerank() POSTs to /rerank and returns results."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"index": 0, "score": 0.95}, {"index": 1, "score": 0.80}],
        }

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.rerank("test query", ["doc1", "doc2"], top_k=2)

        assert len(result) == 2
        assert result[0]["score"] == 0.95
        http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_index_page(self, client):
        """index_page() POSTs to /index."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.index_page("https://x.com", "Title", "Content text")

        assert result["success"] is True
        http_client.post.assert_called_once()
        args, kwargs = http_client.post.call_args
        assert args[0] == "http://semantic.test:8003/index"
        assert kwargs["json"]["url"] == "https://x.com"

    @pytest.mark.asyncio
    async def test_index_batch(self, client):
        """index_batch() POSTs to /index/batch."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}

        pages = [{"url": "https://a.com", "title": "A", "content": "content a"}]

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.index_batch(pages)

        assert result["success"] is True
        http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_vector(self, client):
        """search_vector() POSTs to /search/vector."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"url": "https://x.com", "title": "X", "score": 0.95}],
        }

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.search_vector("test query", limit=3)

        assert len(result) == 1
        assert result[0]["url"] == "https://x.com"

    @pytest.mark.asyncio
    async def test_get_model(self, client):
        """get_model() GETs /index/model."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"model": "BAAI/bge-m3", "dim": 1024}

        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.get_model()

        assert result["model"] == "BAAI/bge-m3"
        http_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_migration(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"migration_started": True}

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.start_migration("BAAI/bge-small", 512)

        assert result["migration_started"] is True

    @pytest.mark.asyncio
    async def test_migration_status(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"progress": 50, "status": "in_progress"}

        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.migration_status()

        assert result["progress"] == 50

    @pytest.mark.asyncio
    async def test_cutover(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"cutover": True}

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            result = await client.cutover()

        assert result["cutover"] is True

    @pytest.mark.asyncio
    async def test_close(self, client):
        http_client = MagicMock()
        http_client.post = AsyncMock(
            return_value=MagicMock(
                status_code=200, json=lambda: {"embeddings": [[1.0]]}
            )
        )
        http_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=http_client):
            await client.embed(["test"])

        await client.close()
        http_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_lazy_client_creation(self, client):
        """Client should be None until first request."""
        assert client._client is None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embeddings": [[1.0]]}

        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=http_client):
            await client.embed(["test"])

        assert client._client is not None
