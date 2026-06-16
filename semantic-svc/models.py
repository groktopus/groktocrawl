"""Pydantic request/response schemas for semantic-svc.

All models extracted from app.py per ADR-0037.
"""

from pydantic import BaseModel


class EmbedRequest(BaseModel):
    model: str = "BGE-M3"
    input: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int = 5


class RerankResult(BaseModel):
    index: int
    score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


class IndexRequest(BaseModel):
    url: str
    title: str = ""
    content: str


class IndexResponse(BaseModel):
    status: str
    url_hash: int


class IndexBatchRequest(BaseModel):
    pages: list[IndexRequest]


class IndexBatchResponse(BaseModel):
    status: str
    count: int


class VectorSearchRequest(BaseModel):
    query: str
    limit: int = 5


class VectorSearchResult(BaseModel):
    url: str
    title: str
    score: float


class VectorSearchResponse(BaseModel):
    results: list[VectorSearchResult]


class IndexStatsResponse(BaseModel):
    total_docs: int
    max_docs: int


class ModelInfoResponse(BaseModel):
    current_model: str
    current_dim: int
    active_named_vector: str
    collection: str
    total_docs: int
    max_docs: int
    migration: dict


class MigrationStartRequest(BaseModel):
    target_model: str
    target_dim: int


class MigrationStatusResponse(BaseModel):
    status: str
    source_model: str
    source_dim: int
    target_model: str
    target_dim: int
    docs_processed: int
    docs_total: int
    started_at: str
    completed_at: str
