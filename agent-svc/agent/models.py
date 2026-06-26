"""Pydantic models matching the Firecrawl v2 agent API contract."""

from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

# ── Valid scrape format values ──────────────────────────────────
VALID_SCRAPE_FORMATS: frozenset[str] = frozenset(
    {"markdown", "html", "links", "screenshot", "rawHtml", "screenshot@fullPage"}
)

# ── Valid browser action types ──────────────────────────────────
VALID_SCRAPE_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "wait",  # Wait for a specified number of milliseconds
        "click",  # Click on a CSS selector
        "screenshot",  # Take a screenshot
        "scroll",  # Scroll the page
        "write",  # Type text into a field
        "executeScript",  # Execute JavaScript
        "select",  # Select an option from a dropdown
    }
)

# ── Valid parser types ──────────────────────────────────────────
VALID_SCRAPE_PARSER_TYPES: frozenset[str] = frozenset(
    {
        "pdf",  # Extract PDF content as markdown
    }
)

# ── Valid proxy values ──────────────────────────────────────────
VALID_SCRAPE_PROXY_VALUES: frozenset[str] = frozenset(
    {
        "basic",
        "enhanced",
        "auto",
    }
)


class VerbosityLevel(str, Enum):
    compact = "compact"  # ~300 chars of body text
    standard = "standard"  # Current behavior (readability extraction)
    full = "full"  # Complete page text including structural markup


class SectionCategory(str, Enum):
    header = "header"
    navigation = "navigation"
    banner = "banner"
    body = "body"
    sidebar = "sidebar"
    footer = "footer"
    metadata = "metadata"


class ExtrasOptions(BaseModel):
    links: int | None = None  # Max external links to extract
    imageLinks: int | None = None  # Max image URLs to extract
    codeBlocks: int | None = None  # Max code blocks to extract


class ContentsOptions(BaseModel):
    text: bool | dict | None = None  # True = full text, or dict with verbosity/sections
    highlights: bool | dict | None = (
        None  # True = auto highlights, or dict with query/maxCharacters
    )
    summary: bool | dict | None = (
        None  # True = auto summary, or dict with query/maxTokens
    )
    extras: ExtrasOptions | None = None


class ErrorDetail(BaseModel):
    """A single field-level validation error."""

    field: str
    message: str


class ErrorResponse(BaseModel):
    """Standard error response body for all API endpoints."""

    success: bool = False
    error: str = "An unexpected error occurred"
    error_code: str = "INTERNAL_ERROR"
    details: list[ErrorDetail] | dict | None = None


class ScrapeOptions(BaseModel):
    """Firecrawl-compatible scrape options for controlling per-page extraction.

    All fields are optional with documented defaults. When ``None`` (or omitted),
    the scraper-svc applies its own sensible defaults for each field.

    ``model_config`` uses ``extra="allow"`` so that unrecognised fields are
    preserved and forwarded to the scraper-svc as-is (forward-compatible
    passthrough).

    Attributes:
        formats: List of output formats to return. Allowed values:
            ``markdown`` (default), ``html``, ``links``, ``screenshot``,
            ``rawHtml``, ``screenshot@fullPage``.
        only_main_content: When True, strip navigation, header, footer and
            other boilerplate from the extracted content (default: True).
        include_tags: If set, only content from these CSS/tag selectors is
            included in the output.
        exclude_tags: If set, content from these CSS/tag selectors is removed
            from the output.
        wait_for: Time in milliseconds to wait for the page to load/stabilize
            before extracting content. Useful for JS-rendered SPAs.
        mobile: When True, use a mobile user-agent and viewport dimensions
            (default: False).
        timeout: Per-page timeout in milliseconds (default: 30000, minimum: 1000).
        headers: Custom HTTP headers to forward with the request (e.g.,
            ``{"Authorization": "Bearer ..."}``).
        remove_base64_images: When True, strip ``data:image/...`` URIs from
            the extracted content (default: False).
        max_age: Maximum age of cached content in milliseconds. If the cached
            response is younger than ``max_age``, it is returned without
            re-scraping. When set to 0, caching is bypassed entirely.
        min_age: Minimum age for cache-only mode in milliseconds. When set,
            only cached content is returned. A cache miss produces a
            ``cache_miss`` error rather than fetching fresh content.
        actions: Ordered list of browser actions to execute before scraping
            each page. Each action is a dict with at minimum a ``type`` key.
            Supported types: ``wait``, ``click``, ``screenshot``, ``scroll``,
            ``write``, ``executeScript``, ``select``.
        location: Geo-location settings for the browser session. A dict with
            optional ``country`` (ISO 3166-1 alpha-2) and ``languages``
            (list of BCP 47 language tags).
        proxy: Proxy selection hint. One of ``"basic"``, ``"enhanced"``, or
            ``"auto"``. When set, influences which proxy pool is used for
            the crawl's scraping requests.
        block_ads: When True (default), ad content is stripped from scraped
            pages. When False, ads remain in the output.
        parsers: List of parser types to use for extraction. At minimum
            ``["pdf"]`` is recognised — when set, PDF files are extracted
            to markdown.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="allow",
    )

    formats: list[str] = Field(
        default=["markdown"],
        description="Output formats: markdown, html, links, etc.",
    )
    only_main_content: bool = Field(
        default=True,
        description="Strip navigation/header/footer boilerplate",
    )
    include_tags: list[str] | None = Field(
        default=None, description="Only include content from these selectors"
    )
    exclude_tags: list[str] | None = Field(
        default=None, description="Exclude content from these selectors"
    )
    wait_for: int | None = Field(
        default=None, ge=0, description="Wait time in milliseconds before extraction"
    )
    mobile: bool = Field(default=False, description="Use mobile viewport and UA")
    timeout: int = Field(
        default=30000, ge=1000, description="Per-page timeout in milliseconds"
    )
    headers: dict[str, str] | None = Field(
        default=None, description="Custom HTTP headers"
    )
    remove_base64_images: bool = Field(
        default=False, description="Strip data:image/... URIs from output"
    )
    max_age: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Maximum age of cached content in milliseconds. If the cached response"
            " is younger than ``max_age``, it is returned without re-scraping."
            " When set to 0, caching is bypassed entirely (every request is fresh)."
        ),
    )
    min_age: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Minimum age for cache-only mode in milliseconds. When set, only cached"
            " content is returned. A cache miss produces a cache_miss error for that"
            " URL rather than fetching fresh content."
        ),
    )
    actions: list[dict] | None = Field(
        default=None,
        description=(
            "Ordered list of browser actions to execute before scraping each page."
            " Each action is a dict with at minimum a ``type`` key. Supported types:"
            " ``wait``, ``click``, ``screenshot``, ``scroll``, ``write``,"
            " ``executeScript``, ``select``."
        ),
    )
    location: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Geo-location settings. Dict with optional ``country``"
            " (ISO 3166-1 alpha-2) and ``languages`` (list of BCP 47 tags)."
        ),
    )
    proxy: str | None = Field(
        default=None,
        description="Proxy selection: ``basic``, ``enhanced``, or ``auto``.",
    )
    block_ads: bool = Field(
        default=True,
        description="When True, strip ad content from scraped pages.",
    )
    parsers: list[str] | None = Field(
        default=None,
        description=('List of parser types. At minimum ``["pdf"]`` is recognised.'),
    )

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, value: list[str]) -> list[str]:
        """Validate that each format is a known/recognised value."""
        if not value:
            raise ValueError("formats must be a non-empty list")
        for fmt in value:
            if fmt not in VALID_SCRAPE_FORMATS:
                allowed = ", ".join(sorted(VALID_SCRAPE_FORMATS))
                raise ValueError(f"Invalid format '{fmt}'. Allowed values: {allowed}")
        return value

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        """Reject out-of-range timeout values."""
        if value < 1000:
            raise ValueError(f"timeout must be >= 1000ms, got {value}")
        return value

    @field_validator("wait_for")
    @classmethod
    def validate_wait_for(cls, value: int | None) -> int | None:
        """Reject negative wait_for values."""
        if value is not None and value < 0:
            raise ValueError(f"wait_for must be >= 0, got {value}")
        return value

    @field_validator("max_age")
    @classmethod
    def validate_max_age(cls, value: int | None) -> int | None:
        """Reject negative max_age values.

        VAL-SCRAPE-032: negative maxAge returns 422.
        """
        if value is not None and value < 0:
            raise ValueError(f"max_age must be >= 0, got {value}")
        return value

    @field_validator("actions")
    @classmethod
    def validate_actions(cls, value: list[dict] | None) -> list[dict] | None:
        """Validate that each action has at minimum a ``type`` key with a
        recognised value.

        VAL-SCRAPE-056 / VAL-PARITY-021: actions field accepted and validated.
        """
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("actions must be a list of action objects")
        allowed = sorted(VALID_SCRAPE_ACTION_TYPES)
        for i, action in enumerate(value):
            if not isinstance(action, dict):
                raise ValueError(
                    f"actions[{i}] must be a dict/object, got {type(action).__name__}"
                )
            action_type = action.get("type")
            if not action_type:
                raise ValueError(f"actions[{i}] is missing required 'type' field")
            if action_type not in VALID_SCRAPE_ACTION_TYPES:
                raise ValueError(
                    f"Invalid action type '{action_type}' at actions[{i}]. "
                    f"Allowed values: {', '.join(allowed)}"
                )
            # ``click`` and ``write`` actions require a ``selector`` field
            if action_type in ("click", "write", "select"):
                if "selector" not in action:
                    raise ValueError(
                        f"actions[{i}]: '{action_type}' action requires a 'selector' field"
                    )
            # ``write`` action also requires a ``value`` (text) field
            if action_type == "write":
                if "value" not in action:
                    raise ValueError(
                        f"actions[{i}]: 'write' action requires a 'value' field"
                    )
        return value

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, value: str | None) -> str | None:
        """Validate that proxy is one of the recognised values.

        VAL-PARITY-023: proxy field accepted with ``basic``/``enhanced``/``auto``.
        """
        if value is None:
            return value
        if value not in VALID_SCRAPE_PROXY_VALUES:
            allowed = ", ".join(sorted(VALID_SCRAPE_PROXY_VALUES))
            raise ValueError(f"Invalid proxy '{value}'. Allowed values: {allowed}")
        return value

    @field_validator("parsers")
    @classmethod
    def validate_parsers(cls, value: list[str] | None) -> list[str] | None:
        """Validate parser type strings.

        VAL-PARITY-025: parsers field accepted with at minimum ``pdf``.
        """
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("parsers must be a list of parser type strings")
        allowed = sorted(VALID_SCRAPE_PARSER_TYPES)
        for i, parser in enumerate(value):
            if not isinstance(parser, str):
                raise ValueError(
                    f"parsers[{i}] must be a string, got {type(parser).__name__}"
                )
            if parser not in VALID_SCRAPE_PARSER_TYPES:
                raise ValueError(
                    f"Invalid parser '{parser}' at parsers[{i}]. "
                    f"Allowed values: {', '.join(allowed)}"
                )
        return value

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate the location object structure.

        VAL-PARITY-022: location field accepted with ``country`` and
        ``languages``.
        """
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("location must be a dict/object")
        # ``country`` should be a string (ISO 3166-1 alpha-2) if present
        country = value.get("country")
        if country is not None and not isinstance(country, str):
            raise ValueError(
                f"location.country must be a string, got {type(country).__name__}"
            )
        # ``languages`` should be a list of strings if present
        languages = value.get("languages")
        if languages is not None:
            if not isinstance(languages, list):
                raise ValueError(
                    f"location.languages must be a list, got {type(languages).__name__}"
                )
            for i, lang in enumerate(languages):
                if not isinstance(lang, str):
                    raise ValueError(
                        f"location.languages[{i}] must be a string, "
                        f"got {type(lang).__name__}"
                    )
        return value

    @model_validator(mode="after")
    def validate_cache_ages(self) -> "ScrapeOptions":
        """Validate that min_age does not exceed max_age.

        VAL-SCRAPE-033: minAge greater than maxAge is rejected.
        """
        min_age = self.min_age
        max_age = self.max_age
        if min_age is not None and max_age is not None and min_age > max_age:
            raise ValueError(
                f"min_age ({min_age}ms) cannot exceed max_age ({max_age}ms)"
            )
        return self


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
    quality: dict[str, Any] | None = None


class ScrapeResponse(BaseModel):
    success: bool
    data: ScrapeData | None = None
    error: str | None = None


class AgentRequest(BaseModel):
    prompt: str = Field(
        ..., max_length=100000, description="What the agent should research"
    )
    urls: list[str] | None = Field(
        None, description="Optional seed URLs to constrain research"
    )
    schema_: dict[str, Any] | None = Field(
        None, alias="schema", description="JSON Schema for structured output"
    )
    model: str = Field(default="default", description="Model hint")
    max_credits: int | None = None
    webhook: dict[str, Any] | None = None
    strict_constrain_to_urls: bool = False
    stream: bool = Field(default=False, description="SSE streaming response")

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
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    url: str
    max_pages: int = Field(
        default=0, ge=0, description="Maximum pages to scrape, 0 = unlimited"
    )
    max_depth: int = Field(
        default=2, ge=0, description="Maximum link-follow depth, must be >= 0"
    )
    limit: int | None = None
    ignore_sitemap: bool = False
    sitemap: str = Field(
        default="include",
        description="Sitemap mode: 'include' (default), 'skip', or 'only'",
    )
    ignore_query_parameters: bool = False
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    regex_on_full_url: bool = False
    verbose: bool = False
    webhook: dict[str, Any] | None = None
    crawl_entire_domain: bool = False
    allow_subdomains: bool = False
    allow_external_links: bool = False
    max_concurrency: int = Field(
        default=3,
        ge=1,
        description="Maximum concurrent page scrapes (1-50, values > 50 are capped)",
    )
    delay: float | None = Field(
        default=None,
        ge=0,
        description="Delay in seconds between scrapes. Forces concurrency to 1 when set.",
    )
    ignore_robots_txt: bool = Field(
        default=False,
        description="When True, bypass robots.txt enforcement. All discovered URLs are scraped regardless of robots.txt Disallow rules.",
    )
    robots_user_agent: str | None = Field(
        default=None,
        description="Custom User-Agent string for robots.txt evaluation. When set, robots.txt rules are evaluated against this User-Agent instead of the default bot UA.",
    )
    scrape_options: ScrapeOptions | None = Field(
        default=None,
        description="Per-page scrape options controlling output format, content filtering, viewport, and timeout behavior. Applied to every page in the crawl, including the start URL.",
    )
    prompt: str | None = Field(
        default=None,
        max_length=10000,
        description="Natural language description of what to crawl. Used to derive crawl parameters (includePaths, excludePaths, maxDepth) via LLM. Explicitly-set parameters override LLM-derived ones.",
    )
    stream: bool = Field(
        default=False,
        description="When True, the crawl response is delivered as Server-Sent Events (SSE) stream. Per-page data is streamed incrementally as pages are scraped, with a final done event on completion.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Validate that url is a well-formed HTTP/HTTPS URL."""
        parsed = urlparse(value)
        if not parsed.scheme:
            raise ValueError("URL must have a scheme (http:// or https://)")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"URL scheme must be http or https, got '{parsed.scheme}'")
        if not parsed.netloc:
            raise ValueError("URL must have a network location (host)")
        return value

    @field_validator("max_concurrency")
    @classmethod
    def validate_max_concurrency(cls, value: int) -> int:
        """Validate and cap max_concurrency values.

        Values of 0 or negative are rejected (would deadlock the semaphore).
        Values above 50 are silently capped to 50 to prevent resource exhaustion.
        """
        if value < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {value}")
        return min(value, 50)

    @field_validator("delay")
    @classmethod
    def validate_delay(cls, value: float | None) -> float | None:
        """Reject negative delay values."""
        if value is not None and value < 0:
            raise ValueError(f"delay must be >= 0, got {value}")
        return value

    @model_validator(mode="after")
    def validate_regex_patterns(self) -> "CrawlRequest":
        """When regex_on_full_url is True, validate that all path
        patterns are valid Python regular expressions."""
        if not self.regex_on_full_url:
            return self
        import re as _re

        for field_name in ("include_paths", "exclude_paths"):
            patterns = getattr(self, field_name)
            if not patterns:
                continue
            for i, pattern in enumerate(patterns):
                try:
                    _re.compile(pattern)
                except _re.error as exc:
                    raise ValueError(
                        f"Invalid regex in {field_name}[{i}]: '{pattern}' — {exc}"
                    ) from exc
        return self

    @model_validator(mode="after")
    def resolve_sitemap_mode(self) -> "CrawlRequest":
        """Backward compatibility: ignore_sitemap=true → sitemap='skip'.

        Also validates that sitemap is one of the allowed values.
        """
        # Backward compatibility: ignore_sitemap overrides sitemap field
        if self.ignore_sitemap:
            self.sitemap = "skip"

        # Validate sitemap value
        allowed = ("include", "skip", "only")
        if self.sitemap not in allowed:
            raise ValueError(
                f"Invalid sitemap mode '{self.sitemap}'. Must be one of: {', '.join(allowed)}"
            )
        return self


class CrawlCreateResponse(BaseModel):
    success: bool = True
    id: str


class BatchScrapeStatusResponse(BaseModel):
    """Status response for GET /v2/batch/scrape/{id}.

    Mirrors CrawlStatusResponse but without crawl-specific fields
    (errors, robots_blocked, filtered_out are separate endpoints).
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    success: bool = True
    status: str = "processing"
    completed: int = 0
    total: int = 0
    credits_used: int | None = None
    data: list[dict[str, Any]] | None = None
    error: str | None = None
    next: str | None = Field(
        default=None,
        description="URL for the next chunk of paginated results.",
    )
    created_at: str | None = None
    completed_at: str | None = None
    expires_at: str | None = None
    duration: int | None = None


class CrawlStatusResponse(BaseModel):
    """Firecrawl v2-compatible crawl status response.

    Attributes:
        success: Whether the API call succeeded.
        status: Job status: ``processing``, ``completed``, ``failed``,
            or ``cancelled``.
        completed: Number of pages successfully scraped.
        total: Total number of pages discovered (scraped + queued).
        credits_used: Number of credits consumed (1 per page scraped).
        data: List of page documents, each containing ``url``, ``markdown``,
            ``metadata`` (with ``title``, ``description``, ``language``,
            ``sourceURL``, ``statusCode``), and other optional fields.
        error: Error message if the job failed.
        next: URL for the next chunk of paginated results. ``null`` when
            all data fits in a single response (under ~10MB).
        created_at: ISO 8601 timestamp when the crawl was created.
        completed_at: ISO 8601 timestamp when the crawl finished
            (``null`` while processing).
        expires_at: ISO 8601 timestamp when results expire
            (24h after creation).
        duration: Elapsed milliseconds between ``created_at`` and
            ``completed_at`` (``null`` while processing).
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    success: bool = True
    status: str = "processing"
    completed: int = 0
    total: int = 0
    credits_used: int | None = None
    data: list[dict[str, Any]] | None = None
    error: str | None = None
    next: str | None = Field(
        default=None,
        description=(
            "URL for the next chunk of paginated results. Present when the response"
            " data exceeds ~10MB. Null when all data fits in a single response."
        ),
    )
    created_at: str | None = None
    completed_at: str | None = None
    expires_at: str | None = None
    duration: int | None = None


class ParamsPreviewRequest(BaseModel):
    """Request model for POST /v2/crawl/params-preview.

    Attributes:
        url: The target URL to crawl (used for context).
        prompt: Natural language description of what to crawl.
            Required — the endpoint derives crawl parameters from this.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    url: str
    prompt: str = Field(
        ...,
        max_length=10000,
        description="Natural language description of what to crawl",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Validate that url is a well-formed HTTP/HTTPS URL."""
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if not parsed.scheme:
            raise ValueError("URL must have a scheme (http:// or https://)")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"URL scheme must be http or https, got '{parsed.scheme}'")
        if not parsed.netloc:
            raise ValueError("URL must have a network location (host)")
        return value


class ParamsPreviewResponse(BaseModel):
    """Response model for POST /v2/crawl/params-preview.

    Returns the derived crawl parameters WITHOUT starting a crawl job.
    Explicitly-set fields (when provided alongside ``prompt``) override
    LLM-derived equivalents — this preview shows the final merged result.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    success: bool = True
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    max_depth: int | None = None
    limit: int | None = None
    ignore_robots_txt: bool | None = None
    robots_user_agent: str | None = None
    deduplicate_similar_urls: bool | None = None
    error: str | None = None


class BatchScrapeRequest(BaseModel):
    urls: list[str]
    max_concurrency: int = 3
    webhook: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 5
    search_type: str = "fast"  # "fast" | "rich" | "deep"
    retrieval_mode: str = (
        "keyword"  # "keyword" | "semantic" | "hybrid" | "vector" | "hybrid_vector"
    )
    categories: list[str] | None = None
    sources: list[str] | None = None
    output_schema: dict[str, Any] | None = None  # JSON Schema for structured extraction
    system_prompt: str | None = None  # Guidance for synthesis
    contents: ContentsOptions | None = None  # Content extraction options
    stream: bool = Field(default=False, description="SSE streaming response")


class SearchResult(BaseModel):
    url: str
    title: str
    description: str = ""
    # ── Content extraction fields (populated when contents in SearchRequest) ──
    highlights: str | None = None  # LLM-extracted relevant passages
    summary: str | None = None  # LLM-generated summary
    extras: dict | None = None  # Links, images, code blocks from scraper
    markdown: str | None = None  # Full scraped markdown when contents requested


class SearchResponse(BaseModel):
    success: bool = True
    data: dict = Field(default_factory=lambda: {"web": [], "images": [], "news": []})
    output: dict[str, Any] | None = None  # Present only when output_schema provided
    query_variations: list[str] | None = None  # Present for deep search type


class FindSimilarRequest(BaseModel):
    url: str
    limit: int = 10
    search_mode: str = "qdrant"  # "qdrant" | "web"
    contents: ContentsOptions | None = None


class FindSimilarResponse(BaseModel):
    success: bool = True
    data: list[dict]
    query_url: str
    search_mode: str
    latency_ms: float


class MapRequest(BaseModel):
    url: str
    limit: int = 100
    search: str | None = None
    allow_subdomains: bool = False
    allow_external_links: bool = False


class MapResponse(BaseModel):
    success: bool = True
    links: list[str] = Field(default_factory=list)


class BrowserCreateRequest(BaseModel):
    ttl: int = Field(default=300, ge=30, le=3600, description="Session TTL in seconds")


class BrowserExecuteRequest(BaseModel):
    action: str = Field(
        ...,
        description="Action: navigate, click, type, screenshot, scroll, wait, getContent, executeScript",
    )
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
    prompt: str | None = Field(
        None, max_length=10000, description="Optional instruction for extraction"
    )
    schema_: dict[str, Any] | None = Field(
        None, alias="schema", description="JSON Schema for structured output"
    )
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


class SearchMonitorConfig(BaseModel):
    """Configuration for search-based monitors."""

    query: str = Field(..., description="Search query to monitor")
    sources: list[str] | None = Field(
        None, description="Source types (web, news, images, video, social)"
    )
    categories: list[str] | None = Field(
        None,
        description="Content categories (research, github, pdf, news, science, it)",
    )
    numResults: int = Field(
        default=10, ge=1, le=50, description="Max results per check"
    )


class MonitorCreateRequest(BaseModel):
    url: str | None = Field(None, description="URL to monitor (scrape type)")
    schedule: str = Field(
        default="0 */6 * * *", description="Cron expression for check frequency"
    )
    webhook: str | None = Field(None, description="Webhook URL called on change")
    monitor_type: str = Field(
        default="scrape", description="Monitor type: 'scrape' or 'search'"
    )
    search_config: SearchMonitorConfig | None = Field(
        None, description="Search monitor configuration (required for search type)"
    )

    @model_validator(mode="after")
    def validate_monitor_type_fields(self):
        if self.monitor_type == "search":
            if self.search_config is None or not self.search_config.query:
                raise ValueError(
                    "search_config with a non-empty query is required for monitor_type='search'"
                )
        elif self.monitor_type == "scrape":
            if not self.url:
                raise ValueError("url is required for monitor_type='scrape'")
        else:
            raise ValueError(
                f"Unknown monitor_type: '{self.monitor_type}'. Must be 'scrape' or 'search'"
            )
        return self


class MonitorUpdateRequest(BaseModel):
    url: str | None = None
    schedule: str | None = None
    webhook: str | None = None
    search_config: SearchMonitorConfig | None = None


class MonitorResponse(BaseModel):
    success: bool = True
    id: str
    monitor_type: str = "scrape"
    url: str | None = None
    search_config: dict | None = None
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


class MonitorCheckItem(BaseModel):
    """A single monitor check result entry."""

    monitor_id: str = ""
    monitor_type: str = "scrape"
    url: str | None = None
    query: str | None = None
    checked_at: str = ""
    changed: bool = False
    diff: str | None = None
    previous_length: int | None = None
    current_length: int | None = None
    new_results: list[dict[str, Any]] | None = None
    new_count: int | None = None
    total_results: int | None = None
    error: str | None = None


class MonitorCheckListResponse(BaseModel):
    """Response for GET /v2/monitor/{id}/checks."""

    success: bool = True
    data: list[MonitorCheckItem] = Field(default_factory=list)
    total: int = 0


class ParseResponse(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class ParseUploadUrlResponse(BaseModel):
    """Response for POST /v2/parse/upload-url."""

    success: bool = True
    upload_id: str
    upload_url: str


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


class Source(BaseModel):
    """A source used to ground an answer."""

    url: str
    title: str = ""
    relevance: str = ""


class Citation(BaseModel):
    """An inline citation mapping [N] to a URL."""

    index: int
    url: str


class AnswerRequest(BaseModel):
    query: str = Field(..., max_length=10000, description="Natural language question")
    search_type: str = Field(default="auto", description="Hint for search depth")
    retrieval_mode: str = Field(
        default="keyword",
        description="keyword | semantic | hybrid | vector | hybrid_vector",
    )
    num_sources: int = Field(
        default=5, ge=1, le=20, description="How many sources to ground the answer"
    )
    model: str = Field(default="default", description="Per-request LLM override")
    stream: bool = Field(default=False, description="SSE streaming response")


class AnswerResponse(BaseModel):
    success: bool = True
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    search_type: str = "auto"
    latency_ms: int = 0


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


class ConcurrencyCheckResponse(BaseModel):
    """Response for GET /v2/concurrency-check."""

    success: bool = True
    max_concurrency: int = 50
    current: int = 0


class EnrichmentField(BaseModel):
    """A field to extract for each enrichment item."""

    description: str


class EnrichRequest(BaseModel):
    """Request to enrich a list of entities with web-sourced structured data."""

    items: list[dict[str, Any]]
    fields: dict[str, EnrichmentField]
    source_hint: str | None = None  # company, person, url, product
    effort: str = "low"  # low | medium | high


class EnrichmentValue(BaseModel):
    """The extracted value for a single field, with source attribution."""

    value: str | None = None
    source: str | None = None  # URL where this value was found


class EnrichResponse(BaseModel):
    """Response for the enrichment endpoint."""

    success: bool = True
    data: list[dict]
    latency_ms: float = 0
    items_enriched: int = 0
    fields_per_item: int = 0


class CrawlActiveItem(BaseModel):
    """A single active crawl job entry for ``GET /v2/crawl/active``.

    Includes crawl-specific fields (``url``, ``max_pages``, ``max_depth``,
    ``completed``, ``total``) that distinguish it from the generic
    ``ActivityItem`` used by the unified ``/v2/activity`` endpoint.
    """

    id: str
    url: str | None = None
    status: str = "processing"
    created_at: str
    completed: int = 0
    total: int = 0
    max_pages: int | None = None
    max_depth: int | None = None


class CrawlActiveResponse(BaseModel):
    """Response model for ``GET /v2/crawl/active``.

    Returns only jobs with ``kind: "crawl"``. Excludes completed, failed,
    and cancelled crawls by default (filterable via ``status`` query param).
    """

    success: bool = True
    data: list[CrawlActiveItem] = Field(default_factory=list)


class CrawlErrorItem(BaseModel):
    """A single error entry for the GET /v2/crawl/{id}/errors endpoint.

    Attributes:
        url: The URL that failed.
        error: Human-readable error description (maps to ``message`` in
            the validation contract).
        error_type: Machine-readable error category (e.g., ``timeout``,
            ``robots_blocked``, ``dns_error``, ``http_error``,
            ``scrape_error``, ``cache_miss``, ``duplicate_canonical``,
            ``duplicate_content``).
        error_code: Machine-readable error code string (e.g., ``TIMEOUT``,
            ``ROBOTS_BLOCKED``, ``SCRAPE_ERROR``).
        timestamp: ISO 8601 timestamp of when the error occurred.
        timeout_ms: For timeout errors, the configured per-scrape timeout
            in milliseconds.
        elapsed_ms: For timeout errors, the actual elapsed time before
            the timeout fired, in milliseconds.
        http_status: For HTTP errors, the HTTP status code (e.g., 404,
            403, 500).
    """

    url: str
    error: str = ""
    error_type: str = "scrape_error"
    error_code: str = ""
    timestamp: str = ""
    timeout_ms: int | None = None
    elapsed_ms: int | None = None
    http_status: int | None = None


class CrawlErrorsResponse(BaseModel):
    """Response model for GET /v2/crawl/{id}/errors.

    Attributes:
        success: Always ``True`` for a successful response.
        errors: List of error objects. Includes all scrape failures,
            cache misses, and duplicate-detection errors. Politeness-
            blocked URLs are also included here (with ``error_type:
            "robots_blocked"``) and in ``robots_blocked``.
        robots_blocked: Subset of ``errors`` containing only URLs that
            were blocked by robots.txt or politeness rate limiting.
    """

    success: bool = True
    errors: list[CrawlErrorItem] = Field(default_factory=list)
    robots_blocked: list[CrawlErrorItem] = Field(default_factory=list)
    error: str | None = None


class BatchScrapeErrorsResponse(BaseModel):
    """Response model for GET /v2/batch/scrape/{id}/errors."""

    success: bool = True
    errors: list[CrawlErrorItem] = Field(default_factory=list)
