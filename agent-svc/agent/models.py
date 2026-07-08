"""Pydantic models matching the Firecrawl v2 agent API contract."""

from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

# ── Valid scrape format values ──────────────────────────────────
VALID_SCRAPE_FORMATS: frozenset[str] = frozenset(
    {
        "markdown",
        "html",
        "links",
        "screenshot",
        "rawHtml",
        "screenshot@fullPage",
        "images",
    }
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


class CitationStyle(str, Enum):
    """Citation formatting styles for agent and answer responses.

    Attributes:
        inline: Bare ``[N]`` markers with a separate citations list in the
            response body (current behaviour, Firecrawl v2 default).
        compact: Self-contained ``[N](url)`` markers embedded directly in
            the markdown answer text.  No separate citations list needed.
    """

    inline = "inline"
    compact = "compact"


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


class ImageData(BaseModel):
    """Structured image metadata matching the Firecrawl v2 contract."""

    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None
    position: int = 0


class ScrapeData(BaseModel):
    markdown: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    download: DownloadData | None = None
    quality: dict[str, Any] | None = None
    images: list[ImageData] | None = None


class ScrapeResponse(BaseModel):
    success: bool
    data: ScrapeData | None = None
    error: str | None = None


class AgentRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="What the agent should research",
    )
    urls: list[str] | None = Field(
        None, description="Optional seed URLs to constrain research"
    )
    schema_: dict[str, Any] | None = Field(
        None, alias="schema", description="JSON Schema for structured output"
    )
    output_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for structured output (alias for schema)"
    )
    model: str = Field(default="default", description="Model hint")
    mode: str | None = Field(
        default=None,
        description="Agent mode: None (default agent pipeline), 'plan' (plan-only, no execution)",
    )
    max_credits: int | None = None
    webhook: dict[str, Any] | None = None
    strict_constrain_to_urls: bool = False
    stream: bool = Field(default=False, description="SSE streaming response")
    include_images: bool = Field(
        default=False, description="Collect images from scraped sources"
    )
    citation_style: CitationStyle = Field(
        default=CitationStyle.inline,
        description="Citation formatting style: inline or compact. inline uses bare [N] markers with a separate citations list; compact embeds [N](url) directly in the answer text.",
    )
    force_fresh: bool = Field(
        default=False,
        description="When True, bypass the research memory cache and run fresh research pipeline",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: Any) -> dict[str, Any] | None:
        """Reject non-dict output_schema values (e.g., arrays, strings) with 422."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"output_schema must be a JSON Schema object (dict), got {type(value).__name__}"
            )
        return value

    @field_validator("schema_")
    @classmethod
    def validate_schema_(cls, value: Any) -> dict[str, Any] | None:
        """Reject non-dict schema alias values with 422."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"schema must be a JSON Schema object (dict), got {type(value).__name__}"
            )
        return value


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


class ImageSearchResult(BaseModel):
    """Firecrawl v2 image search result shape.

    Maps to the ``data.images[]`` slot in SearchResponse.
    """

    title: str = ""
    image_url: str = ""
    image_width: int | None = None
    image_height: int | None = None
    url: str = ""
    position: int = 0


class SearchResponse(BaseModel):
    success: bool = True
    data: dict = Field(default_factory=lambda: {"web": [], "images": [], "news": []})
    output: dict[str, Any] | None = None  # Present only when output_schema provided
    query_variations: list[str] | None = None  # Present for deep search type
    warning: str | None = (
        None  # Degraded search state (e.g. all engines returned no results)
    )


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
    schema_: dict[str, Any] | None = Field(
        None,
        alias="schema",
        description="JSON Schema for structured output (alias for output_schema)",
    )
    output_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for structured output from the answer"
    )
    citation_style: CitationStyle = Field(
        default=CitationStyle.inline,
        description="Citation formatting style: inline or compact. inline uses bare [N] markers with a separate citations list; compact embeds [N](url) directly in the answer text.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: Any) -> dict[str, Any] | None:
        """Reject non-dict output_schema values (e.g., arrays, strings) with 422."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"output_schema must be a JSON Schema object (dict), got {type(value).__name__}"
            )
        return value

    @field_validator("schema_")
    @classmethod
    def validate_schema_(cls, value: Any) -> dict[str, Any] | None:
        """Reject non-dict schema alias values with 422."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"schema must be a JSON Schema object (dict), got {type(value).__name__}"
            )
        return value


class AnswerResponse(BaseModel):
    success: bool = True
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    search_type: str = "auto"
    latency_ms: int = 0


# ── Citations Resolve ──────────────────────────────────────────


class CitationsResolveRequest(BaseModel):
    """Request to resolve inline citation markers to full URLs.

    Takes markdown text with ``[N]`` markers and a source list, and
    returns the text with resolved citations according to the requested
    style.
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=500_000,
        description="Markdown text with [N] citation markers to resolve",
    )
    sources: list[Source] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Source list where index matches [N] markers (1-based)",
    )
    style: CitationStyle = Field(
        default=CitationStyle.inline,
        description="Target citation style for the resolved output",
    )


class ResolvedCitation(BaseModel):
    """A single resolved citation with both marker and full URL."""

    index: int
    url: str
    title: str = ""
    marker_text: str = ""  # The original [N] text in the source
    resolved_text: str = ""  # The replacement text (e.g., [1](url) for compact)


class CitationsResolveResponse(BaseModel):
    """Response from the citations resolve endpoint."""

    success: bool = True
    resolved_text: str = Field(
        default="",
        description="The input text with all [N] markers resolved per the requested style",
    )
    citations: list[ResolvedCitation] = Field(
        default_factory=list,
        description="Mapping of each resolved citation marker to its URL and title",
    )
    style: CitationStyle = CitationStyle.inline
    citation_count: int = 0


# ── Session Protocol ───────────────────────────────────────────


class SessionCreateRequest(BaseModel):
    """Request to create a new research session."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    ttl: int | None = Field(
        default=None,
        ge=60,
        le=86400,
        description="Session TTL in seconds (60-86400). Default: 3600 (1 hour).",
    )


class SessionCreateResponse(BaseModel):
    """Response from session creation."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    session_id: str = ""
    expires_at: str = ""
    ttl: int = 3600


class SessionStepRequest(BaseModel):
    """Execute an action step within a research session.

    Supported actions:
        - ``search``: Search via SearXNG.  Params: ``query`` (required),
          ``limit``, ``sources``, ``categories``.
        - ``scrape``: Scrape specific URLs.  Params: ``urls`` (required,
          list of URLs), ``scrape_options`` (optional).
        - ``query``: Run LLM over accumulated session context.  Params:
          ``question`` (required), ``model`` (optional).
        - ``deepen``: Drill deeper into a cited source.  Params: ``ref_id``
          (required, citation ref from session), ``sub_topic`` (required,
          follow-up question), ``max_sources`` (optional, default 3).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    action: str = Field(
        ...,
        description="Step action: search, scrape, query, or deepen",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters",
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        allowed = {"search", "scrape", "query", "deepen"}
        if value not in allowed:
            raise ValueError(
                f"Unknown action: {value!r}. Supported: {', '.join(sorted(allowed))}"
            )
        return value

    @model_validator(mode="after")
    def validate_required_params(self) -> "SessionStepRequest":
        """Validate that required params are present for each action type.

        VAL-SES-017: search requires ``query`` in params.
        VAL-SES-018: scrape requires ``urls`` in params.
        VAL-SES-019: query requires ``question`` in params.
        VAL-SES-046: search with empty query string is rejected.
        VAL-SES-047: scrape with empty URLs list is rejected.
        """
        params = self.params or {}
        if self.action == "search":
            query = params.get("query")
            if query is None:
                raise ValueError("search action requires a 'query' parameter")
            if isinstance(query, str) and not query.strip():
                raise ValueError("search action requires a non-empty 'query' parameter")
        elif self.action == "scrape":
            urls = params.get("urls")
            if urls is None:
                raise ValueError("scrape action requires a 'urls' parameter")
            if isinstance(urls, list) and len(urls) == 0:
                raise ValueError("scrape action requires a non-empty 'urls' list")
        elif self.action == "query":
            question = params.get("question")
            if question is None:
                raise ValueError("query action requires a 'question' parameter")
            if isinstance(question, str) and not question.strip():
                raise ValueError(
                    "query action requires a non-empty 'question' parameter"
                )
        elif self.action == "deepen":
            ref_id = params.get("ref_id")
            sub_topic = params.get("sub_topic")
            if ref_id is None:
                raise ValueError("deepen action requires a 'ref_id' parameter")
            if not isinstance(ref_id, str) or not ref_id.strip():
                raise ValueError(
                    "deepen action requires a non-empty 'ref_id' parameter"
                )
            if sub_topic is None:
                raise ValueError("deepen action requires a 'sub_topic' parameter")
            if not isinstance(sub_topic, str) or not sub_topic.strip():
                raise ValueError(
                    "deepen action requires a non-empty 'sub_topic' parameter"
                )
        return self


class SessionStepResponse(BaseModel):
    """Response from a session step execution."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    step_index: int = 0
    action: str = ""
    summary: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


class SessionExportResponse(BaseModel):
    """Response from session export."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    session_id: str = ""
    artifact: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    refs: dict[str, dict[str, str]] = Field(default_factory=dict)
    artifact_length: int = 0


class SessionStatusResponse(BaseModel):
    """Response from GET /v2/session/{id} — session status and history."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    session_id: str = ""
    status: str = "active"
    created_at: str = ""
    expires_at: str = ""
    step_count: int = 0
    steps: list[dict[str, Any]] = Field(default_factory=list)
    artifact_length: int = 0


# Alias for the GET session response (used by the session endpoints feature)
SessionGetResponse = SessionStatusResponse


class SessionDeleteResponse(BaseModel):
    """Response from DELETE /v2/session/{id}."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    session_id: str = ""
    deleted: bool = False


class SessionResolveRequest(BaseModel):
    """Request to resolve reference IDs to full source content.

    Takes a list of ref IDs (e.g., ``["ref_1_1", "ref_2_3"]``) and
    returns the full content for each found ref.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    ref_ids: list[str] = Field(
        default_factory=list,
        min_length=1,
        max_length=100,
        description="Ref IDs to resolve (e.g., ['ref_1_1', 'ref_2_3'])",
    )


class SessionResolveResponse(BaseModel):
    """Response from POST /v2/session/{id}/resolve.

    Returns full source content for each requested ref ID.
    Missing refs are silently omitted from the ``refs`` dict.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = True
    session_id: str = ""
    refs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    resolved: int = 0
    missing: list[str] = Field(default_factory=list)


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


# ── Plan-Consent (Phase 3) ─────────────────────────────────────


class PlanRequest(BaseModel):
    """Request to generate a structured research plan.

    Attributes:
        prompt: The user's natural-language research question.
        model: Optional per-request LLM override.  When ``"default"``
            or omitted, the environment-configured model is used.
        urls: Optional seed URLs to scope the research plan around.
        stream: When True, stream plan generation via SSE events.
    """

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="Natural-language research question to plan for",
    )
    model: str | None = Field(
        default=None,
        description="Optional per-request LLM model override",
    )
    urls: list[str] | None = Field(
        default=None,
        description="Optional seed URLs to scope the research plan around",
    )
    stream: bool = Field(
        default=False,
        description="When True, stream plan generation via SSE events",
    )


class PlanResponse(BaseModel):
    """Response from POST /v2/agent/plan or POST /v2/agent {mode: plan}.

    Returns the generated plan ID, full plan object, and metadata so the
    client can display it for review and modification before executing.
    """

    success: bool = True
    plan_id: str = ""
    plan: dict = Field(default_factory=dict)
    created_at: str = ""
    expires_at: str = ""


class PlanModification(BaseModel):
    """A single modification to apply to a plan before execution.

    Supported types:
        - ``narrow``: Reduce scope (fewer sources, narrower focus).
          Params: ``focus`` (str, required).
        - ``add_dimension``: Add a comparison dimension.
          Params: ``dimension`` (str, required).
        - ``modify_query``: Change a search query in the plan.
          Params: ``phase_index`` (int, 0-based), ``new_query`` (str, required).
    """

    type: str = Field(
        ...,
        description="Modification type: narrow, add_dimension, or modify_query",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        allowed = frozenset({"narrow", "add_dimension", "modify_query"})
        if value not in allowed:
            raise ValueError(
                f"Invalid modification type '{value}'. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )
        return value

    @model_validator(mode="after")
    def validate_required_params(self) -> "PlanModification":
        """Validate that required params are present for each modification type."""
        params = self.params or {}
        if self.type == "narrow":
            if not params.get("focus"):
                raise ValueError("narrow modification requires 'focus' in params")
        elif self.type == "add_dimension":
            if not params.get("dimension"):
                raise ValueError(
                    "add_dimension modification requires 'dimension' in params"
                )
        elif self.type == "modify_query":
            if "phase_index" not in params:
                raise ValueError(
                    "modify_query modification requires 'phase_index' in params"
                )
            if not params.get("new_query"):
                raise ValueError(
                    "modify_query modification requires 'new_query' in params"
                )
        return self


class PlanModifications(BaseModel):
    """Optional modifications to apply to a plan before execution (dict form).

    This is the legacy dict form — use ``PlanModification`` list form for
    new code.  Both are accepted by the execute endpoint.

    Attributes:
        narrow: Optional focus string to narrow the research scope.
            Injected into the first search phase as additional context.
        add_dimension: Additional analysis dimensions to append.
        remove_dimension: Dimensions to exclude from the analysis.
    """

    narrow: str | None = Field(
        default=None,
        max_length=5000,
        description="Narrow the research focus with this additional context",
    )
    add_dimension: list[str] | None = Field(
        default=None,
        description="Additional analysis dimensions to include",
    )
    remove_dimension: list[str] | None = Field(
        default=None,
        description="Dimensions to exclude from the analysis",
    )


class ExecutePlanRequest(BaseModel):
    """Request to execute a previously-generated research plan.

    Loads the plan from Valkey, applies any modifications, and creates
    a job for the research pipeline.  Plans are one-shot: consumed on
    first successful execution.

    Supports two modification formats:
        - List form (preferred): ``[{type: "narrow"/"add_dimension"/"modify_query",
          params: {...}}]``
        - Dict form (legacy): ``{narrow: "...", add_dimension: [...], ...}``

    Attributes:
        plan_id: The plan ID returned by POST /v2/agent/plan or
            POST /v2/agent with mode:plan.
        modifications: Optional adjustments to narrow scope or change
            analysis dimensions before execution.
    """

    plan_id: str = Field(..., description="Plan ID from plan generation")
    modifications: Any = Field(
        default=None,
        description="Optional plan modifications before execution",
    )
    stream: bool = Field(
        default=False,
        description="When True, stream execution results via SSE events",
    )
    webhook: dict[str, Any] | None = Field(
        default=None,
        description="Optional webhook URL and configuration for completion/failure notifications",
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def normalize_modifications(self) -> "ExecutePlanRequest":
        """Normalize modifications into a validated internal form.

        - ``None`` → ``None`` (no modifications)
        - Dict form (``{narrow: ..., add_dimension: ...}``) → validated
          ``PlanModifications``
        - List form (``[{type: ..., params: ...}, ...]``) → validated
          list of ``PlanModification``
        - Empty list ``[]`` → ``None``
        """
        raw = self.modifications
        if raw is None:
            return self

        if isinstance(raw, list):
            if len(raw) == 0:
                # Empty list → treat as no modifications
                self.modifications = None
                return self
            # Validate each item as a PlanModification
            validated: list[PlanModification] = []
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Each modification must be an object, got {type(item).__name__}"
                    )
                validated.append(PlanModification(**item))
            self.modifications = validated
            return self

        if isinstance(raw, dict):
            # Try to interpret as dict form (PlanModifications)
            if "type" in raw:
                # Looks like a single PlanModification in dict form
                mod = PlanModification(**raw)
                self.modifications = [mod]
                return self
            # Otherwise treat as legacy PlanModifications form
            mods = PlanModifications(**raw)
            self.modifications = mods
            return self

        raise ValueError(
            f"modifications must be a dict, list, or null, got {type(raw).__name__}"
        )


# ── Depth Injection (Phase 3) ──────────────────────────────────


class DeepenRequest(BaseModel):
    """Request to drill deeper into a cited source within a research session.

    Used as a standalone request to the deepen endpoint or embedded in
    ``SessionStepRequest.params`` for the ``deepen`` session action.

    Attributes:
        ref_id: Citation reference from the session (e.g., ``ref_2_3``).
        sub_topic: Follow-up question or investigation angle.
        max_sources: Maximum new sources to discover (default 3).
    """

    ref_id: str = Field(
        ...,
        description="Citation reference from the session (e.g., ref_2_3)",
    )
    sub_topic: str = Field(
        ...,
        max_length=10000,
        description="Follow-up question or investigation angle",
    )
    max_sources: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Maximum new sources to discover (default 3)",
    )


class DeepenResponse(BaseModel):
    """Response from a deepen action.

    Attributes:
        new_findings: LLM-synthesised findings from the deep-dive.
        new_sources: List of newly discovered source dicts with
            ``url`` and ``title``.
        inserted_at: Citation reference where findings were inserted
            into the session artifact.
    """

    success: bool = True
    new_findings: str = ""
    new_sources: list[dict] = Field(default_factory=list)
    inserted_at: str = ""


# ── Research Memory (Phase 4) ──────────────────────────────────


class ResearchMemoryQueryRequest(BaseModel):
    """Request to search the research memory for a semantically similar
    cached artifact.

    Attributes:
        question: The research question to search for.  Embedded via
            BAAI/bge-m3 and matched against stored artifacts by cosine
            similarity.
        max_age_hours: Maximum age in hours of artifacts to consider.
            Default 72 (3 days).  Older artifacts are skipped.
    """

    question: str = Field(
        ...,
        max_length=100000,
        description="Research question to search for in memory",
    )
    max_age_hours: int | None = Field(
        default=None,
        ge=1,
        le=720,
        description="Maximum age in hours of artifacts to consider (default 72)",
    )


class ResearchMemoryQueryResponse(BaseModel):
    """Response from a research memory query.

    Attributes:
        hit: Whether a semantically similar artifact was found.
        artifact: The full artifact dict if ``hit`` is True, with keys
            ``query``, ``artifact``, ``sources``, ``model``,
            ``created_at``, ``expires_at``, and ``user_id``.
        similarity: Cosine similarity score of the best match
            (``None`` on miss).
        freshness: Freshness classification: ``"fresh"``, ``"aging"``,
            or ``"stale"`` (``None`` on miss).
        memory_id: The UUID of the matched entry (``None`` on miss).
    """

    hit: bool = False
    artifact: dict | None = None
    similarity: float | None = None
    freshness: str | None = None
    memory_id: str | None = None


class ResearchMemoryStoreRequest(BaseModel):
    """Request to store a research artifact in memory.

    Attributes:
        question: The original research question (used for semantic
            indexing).
        answer: The LLM-synthesised answer (markdown).
        sources: List of source dicts, each with at minimum ``url``
            and ``title``.
        metadata: Optional dict with extra context (model used,
            user context, etc.).
    """

    question: str = Field(
        ...,
        max_length=100000,
        description="Original research question",
    )
    answer: str = Field(
        ...,
        max_length=500000,
        description="LLM-synthesised answer",
    )
    sources: list[dict] = Field(
        ...,
        description="Source documents with url and title",
    )
    metadata: dict | None = Field(
        default=None,
        description="Optional extra context",
    )


class ResearchMemoryStoreResponse(BaseModel):
    """Response from storing a research artifact in memory.

    Attributes:
        artifact_id: UUID v4 identifier for the stored artifact.
    """

    artifact_id: str = ""


# ── Research Memory Batch Operations ────────────────────────────


class MemoryBatchQueryRequest(BaseModel):
    """Batch query request: look up multiple queries against memory.

    Each query is independently embedded and searched against Qdrant.
    Results are returned in the same order as queries.
    """

    queries: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="List of query strings to look up",
    )


class MemoryBatchQueryEntry(BaseModel):
    """A single batch query result entry."""

    hit: bool = False
    similarity: float | None = None
    freshness: str | None = None
    memory_id: str | None = None
    query: str | None = None
    artifact: str | None = None
    sources: list[dict] | None = None
    error: str | None = None


class MemoryBatchQueryResponse(BaseModel):
    """Batch query response containing per-query results."""

    success: bool = True
    results: list[MemoryBatchQueryEntry] = Field(default_factory=list)


class MemoryBatchStoreEntry(BaseModel):
    """A single entry in a batch store request."""

    query: str = Field(..., min_length=1, description="Research question")
    artifact: str = Field(..., min_length=1, description="LLM-synthesised answer")
    sources: list[dict] = Field(
        ...,
        min_length=1,
        description="Source documents with url and title",
    )
    model: str = Field(default="", description="LLM model name")


class MemoryBatchStoreRequest(BaseModel):
    """Batch store request: store multiple artifacts independently.

    Each entry is stored independently.  Partial success is allowed —
    if one entry fails (e.g. embedding failure), the others still
    succeed with per-entry status.
    """

    entries: list[MemoryBatchStoreEntry] = Field(
        default_factory=list,
        max_length=100,
        description="List of artifacts to store",
    )


class MemoryBatchStoreResult(BaseModel):
    """Per-entry result from a batch store operation."""

    success: bool = False
    memory_id: str | None = None
    error: str | None = None


class MemoryBatchStoreResponse(BaseModel):
    """Batch store response with per-entry status."""

    success: bool = True
    stored_count: int = 0
    failed_count: int = 0
    results: list[MemoryBatchStoreResult] = Field(default_factory=list)
