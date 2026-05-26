"""Pydantic models matching the Firecrawl v2 agent API contract."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScrapeRequest(BaseModel):
    url: str
    formats: list[str] = ["markdown"]
    only_main_content: bool = True
    timeout: int = 30000


class DownloadData(BaseModel):
    """Binary content metadata for non-HTML responses."""
    filename: str
    content_type: str
    size: int
    data_url: str | None = None


class ScrapeData(BaseModel):
    markdown: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    download: DownloadData | None = None


class ScrapeResponse(BaseModel):
    success: bool
    data: ScrapeData | None = None
    error: str | None = None


class AgentRequest(BaseModel):
    prompt: str = Field(..., max_length=10000, description="What the agent should research")
    urls: list[str] | None = Field(None, description="Optional seed URLs to constrain research")
    schema_: dict[str, Any] | None = Field(None, alias="schema", description="JSON Schema for structured output")
    model: str = Field(default="default", description="Model hint")
    max_credits: int | None = None
    webhook: dict[str, Any] | None = None
    strict_constrain_to_urls: bool = False

    model_config = ConfigDict(populate_by_name=True)


class AgentCreateResponse(BaseModel):
    success: bool = True
    id: str


class AgentStatusResponse(BaseModel):
    success: bool = True
    status: str = "processing"  # processing | completed | failed | cancelled
    data: dict[str, Any] | None = None
    error: str | None = None
    expires_at: str | None = None
    credits_used: int | None = None


class AgentCancelResponse(BaseModel):
    success: bool = True


class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 10
    max_depth: int = 2
    ignore_sitemap: bool = False
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    webhook: dict[str, Any] | None = None


class CrawlCreateResponse(BaseModel):
    success: bool = True
    id: str


class CrawlStatusResponse(BaseModel):
    success: bool = True
    status: str = "processing"
    completed: int = 0
    total: int = 0
    credits_used: int | None = None
    data: list[dict[str, Any]] | None = None
    error: str | None = None


class BatchScrapeRequest(BaseModel):
    urls: list[str]
    max_concurrency: int = 3
    webhook: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


class SearchResult(BaseModel):
    url: str
    title: str
    description: str = ""
    markdown: str = ""


class SearchResponse(BaseModel):
    success: bool = True
    data: dict = Field(default_factory=lambda: {"web": [], "images": [], "news": []})


class MapRequest(BaseModel):
    url: str
    limit: int = 100
    search: str | None = None


class MapResponse(BaseModel):
    success: bool = True
    links: list[str] = Field(default_factory=list)


class BrowserCreateRequest(BaseModel):
    ttl: int = Field(default=300, ge=30, le=3600, description="Session TTL in seconds")


class BrowserExecuteRequest(BaseModel):
    action: str = Field(..., description="Action: navigate, click, type, screenshot, scroll, wait, getContent, executeScript")
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    script: str | None = None
    timeout: int = 10000


class BrowserCreateResponse(BaseModel):
    success: bool = True
    id: str


class BrowserExecuteResponse(BaseModel):
    success: bool = True
    result: Any = None
    error: str | None = None


class BrowserListResponse(BaseModel):
    success: bool = True
    sessions: list[dict] = Field(default_factory=list)


class BrowserDeleteResponse(BaseModel):
    success: bool = True
    id: str


class ExtractRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, description="URLs to extract data from")
    prompt: str | None = Field(None, max_length=10000, description="Optional instruction for extraction")
    schema_: dict[str, Any] | None = Field(None, alias="schema", description="JSON Schema for structured output")
    model: str = Field(default="default", description="Model hint")
    webhook: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ExtractCreateResponse(BaseModel):
    success: bool = True
    id: str


class ExtractStatusResponse(BaseModel):
    success: bool = True
    status: str = "processing"  # processing | completed | failed
    data: dict[str, Any] | None = None
    error: str | None = None
    expires_at: str | None = None


class MonitorCreateRequest(BaseModel):
    url: str = Field(..., description="URL to monitor for changes")
    schedule: str = Field(default="0 */6 * * *", description="Cron expression for check frequency")
    webhook: str | None = Field(None, description="Webhook URL called on change")


class MonitorUpdateRequest(BaseModel):
    url: str | None = None
    schedule: str | None = None
    webhook: str | None = None


class MonitorResponse(BaseModel):
    success: bool = True
    id: str
    url: str
    schedule: str
    webhook: str | None = None
    last_checked: str | None = None
    last_result: str | None = None
    created_at: str


class MonitorListResponse(BaseModel):
    success: bool = True
    monitors: list[MonitorResponse] = Field(default_factory=list)


class MonitorDeleteResponse(BaseModel):
    success: bool = True


class ParseResponse(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class LLMsTextRequest(BaseModel):
    url: str = Field(..., description="Site URL to generate llms.txt for")
    max_pages: int = Field(default=50, ge=1, le=500, description="Max pages to scan")
    webhook: dict[str, Any] | None = None


class LLMsTextCreateResponse(BaseModel):
    success: bool = True
    id: str


class LLMsTextStatusResponse(BaseModel):
    success: bool = True
    status: str = "processing"
    data: dict[str, Any] | None = None
    error: str | None = None
    expires_at: str | None = None


class ActivityItem(BaseModel):
    """A single job entry in the activity feed."""
    id: str
    kind: str
    status: str
    url: str | None = None
    created_at: str
    completed_at: str | None = None


class ActivityResponse(BaseModel):
    """Response model for the unified activity endpoint."""
    success: bool = True
    data: list[ActivityItem] = Field(default_factory=list)
