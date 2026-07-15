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


class SearchError(GroktoCrawlError):
    status_code = 502
    error_code = "SEARCH_ERROR"
    detail = "Search failed"


class ConflictError(GroktoCrawlError):
    status_code = 409
    error_code = "CONFLICT"
    detail = "Resource conflict"


class RateLimitedError(GroktoCrawlError):
    status_code = 429
    error_code = "RATE_LIMITED"
    detail = "Rate limit exceeded"
