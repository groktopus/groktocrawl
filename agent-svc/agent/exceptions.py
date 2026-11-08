"""Custom exception hierarchy for GroktoCrawl API endpoints.

All exceptions carry a status_code, error_code, and detail string that
are rendered by FastAPI exception handlers into a consistent JSON
response shape.
"""


class GroktoCrawlError(Exception):
    """Base exception for all GroktoCrawl errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    detail: str = "An unexpected error occurred"
    details: dict | None = None

    def __init__(self, detail: str | None = None, details: dict | None = None):
        if detail is not None:
            self.detail = detail
        if details is not None:
            self.details = details
        super().__init__(self.detail)


class NotFoundError(GroktoCrawlError):
    status_code = 404
    error_code = "NOT_FOUND"
    detail = "Resource not found"


class InvalidRequestError(GroktoCrawlError):
    status_code = 400
    error_code = "INVALID_REQUEST"
    detail = "Invalid request"


class ScrapeError(GroktoCrawlError):
    status_code = 502
    error_code = "SCRAPE_FAILED"
    detail = "Scrape failed"


class BarrierDetectedError(ScrapeError):
    """The scraped page is barrier/challenge content (#586, ADR-0015).

    Subclass of :class:`ScrapeError` so existing ``except ScrapeError``
    handlers keep working; the dedicated class-level ``error_code`` keeps
    the rendered top-level body field consistent with
    ``details.error_code`` instead of relying on the handler to dig the
    specific code out of ``details``.
    """

    error_code = "BARRIER_DETECTED"
    detail = "Barrier or challenge content detected"


class CaptchaError(GroktoCrawlError):
    status_code = 502
    error_code = "CAPTCHA_UNRESOLVED"
    detail = "CAPTCHA challenge could not be resolved"


class BrowserError(GroktoCrawlError):
    status_code = 502
    error_code = "BROWSER_ERROR"
    detail = "Browser service error"


class UpstreamError(GroktoCrawlError):
    status_code = 502
    error_code = "UPSTREAM_ERROR"
    detail = "Upstream service error"


class StructuredOutputError(UpstreamError):
    """The LLM returned output that does not satisfy the requested schema."""

    detail = "LLM structured output was invalid"


class CitationValidationError(UpstreamError):
    """The LLM produced citation syntax that cannot be safely resolved."""

    error_code = "CITATION_INVALID"
    detail = "LLM output contained invalid citations"


class ProviderOutputError(UpstreamError):
    """The provider returned an invalid or unusable completion envelope."""

    detail = "LLM provider output was invalid"


class SearchError(GroktoCrawlError):
    status_code = 502
    error_code = "SEARCH_ERROR"
    detail = "Search failed"


class SemanticError(GroktoCrawlError):
    """The semantic-svc vector backend failed or is unreachable.

    Raised when a dependent call into semantic-svc (e.g. the find-similar
    vector search) fails at the transport or HTTP level, so the failure
    surfaces as a structured 502 instead of being masked as an empty
    success result (issue #588).
    """

    status_code = 502
    error_code = "SEMANTIC_SERVICE_ERROR"
    detail = "Semantic service error"


class ConflictError(GroktoCrawlError):
    status_code = 409
    error_code = "CONFLICT"
    detail = "Resource conflict"


class RateLimitedError(GroktoCrawlError):
    """Per-client admission rejection with retry metadata.

    Raised by route handlers when the per-client budget is exhausted
    BEFORE a job is created, so a rejected request never leaves a job
    record. Carries retry metadata that the FastAPI exception handler
    renders into ``Retry-After`` / ``RateLimit-*`` headers and the
    ``retryable`` / ``retry_after_seconds`` body fields.

    ``bucket`` must be a stable non-secret identifier (``search`` or
    ``crawl``), never the client IP.
    """

    status_code = 429
    error_code = "RATE_LIMITED"
    detail = "Rate limit exceeded"

    def __init__(
        self,
        detail: str | None = None,
        details: dict | None = None,
        *,
        retry_after_seconds: float | None = None,
        bucket: str | None = None,
        limit: int | None = None,
        remaining: int | None = None,
        reset_at: str | None = None,
    ):
        super().__init__(detail, details)
        self.retry_after_seconds = retry_after_seconds
        self.bucket = bucket
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        if (bucket or limit is not None or remaining is not None or reset_at) and (
            not self.details or not isinstance(self.details, dict)
        ):
            self.details = {}
        if isinstance(self.details, dict):
            if bucket is not None:
                self.details["bucket"] = bucket
            if limit is not None:
                self.details["limit"] = limit
            if remaining is not None:
                self.details["remaining"] = remaining
            if reset_at is not None:
                self.details["reset_at"] = reset_at


class RetryableRateLimitError(RateLimitedError):
    """Downstream rate-limit condition that may be retried at the job level.

    Raised by internal clients (e.g. ``SearXNGClient``) when an upstream
    component definitively answers HTTP 429 ``RATE_LIMITED``. Sync routes
    render it as a retryable 429 to the caller; the background worker
    catches it to schedule a bounded, cancellable retry of the job
    instead of failing terminally.

    Only explicit upstream 429 ``RATE_LIMITED`` responses are classified
    this way — never ambiguous outcomes or local budget exhaustion.
    """

    retryable = True
