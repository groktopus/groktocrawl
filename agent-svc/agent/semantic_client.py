"""Async HTTP client for semantic-svc."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEMANTIC_UNAVAILABLE = "Semantic service is unavailable"


class SemanticClient:
    """Client for the semantic-svc embedding and reranking service."""

    def __init__(self, base_url: str = "http://semantic-svc:8003"):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": "GroktoCrawl/0.6"},
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/embed",
            json={"input": texts},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]  # type: ignore[no-any-return]

    async def rerank(
        self, query: str, documents: list[str], top_k: int = 5
    ) -> list[dict[Any, Any]]:
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/rerank",
            json={"query": query, "documents": documents, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json()["results"]  # type: ignore[no-any-return]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Phase 2: Vector Index ────────────────────────────────────

    async def index_page(self, url: str, title: str, content: str) -> dict[Any, Any]:
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/index",
            json={"url": url, "title": title, "content": content},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def index_batch(self, pages: list[dict[Any, Any]]) -> dict[Any, Any]:
        """Batch-index multiple pages in a single call.

        Ref: ADR-0030. For large crawls, this is ~200x faster than
        calling index_page() per page.
        """
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/index/batch",
            json={"pages": pages},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def search_vector(self, query: str, limit: int = 5) -> list[dict[Any, Any]]:
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/search/vector",
            json={"query": query, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()["results"]  # type: ignore[no-any-return]

    # ── Phase 4: Model info and migration ─────────────────────────

    async def get_model(self) -> dict[Any, Any]:
        """Return current embedding model config and migration state."""
        client = await self._ensure_client()
        resp = await client.get(f"{self.base_url}/index/model")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def start_migration(
        self, target_model: str, target_dim: int
    ) -> dict[Any, Any]:
        """Start an embedding model migration."""
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/index/migrate/start",
            json={"target_model": target_model, "target_dim": target_dim},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def migration_status(self) -> dict[Any, Any]:
        """Return migration progress."""
        client = await self._ensure_client()
        resp = await client.get(f"{self.base_url}/index/migrate/status")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def cutover(self) -> dict[Any, Any]:
        """Switch queries to the migrated model."""
        client = await self._ensure_client()
        resp = await client.post(f"{self.base_url}/index/migrate/cutover")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
