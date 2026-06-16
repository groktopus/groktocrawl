"""FastAPI application for the scraper service.

Single endpoint: POST /scrape — takes a URL, returns clean markdown.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .cookie_store import close_client, get_client
from .exceptions import GroktoCrawlError, UpstreamError
from .fetch import smart_scrape
from .meta import fetch_meta_tags

logger = logging.getLogger(__name__)

# Cache for Playwright browser availability — probed at startup.
_browser_available: bool | None = None


async def _probe_browser() -> bool:
    """Attempt to launch a minimal Playwright browser and report success.

    Uses the same stealth launch args as Tier 3 (``create_stealth_browser``)
    so the probe accurately reflects whether the real scraping pipeline
    can start Chromium — including Docker-specific requirements like
    ``--no-sandbox`` and ``--disable-dev-shm-usage``.

    Runs once per service startup. Does NOT install system deps — that
    must happen in the Dockerfile. This detects missing shared libraries
    (libglib-2.0.so.0 etc.) or other runtime failures that prevent
    Chromium from starting.
    """
    try:
        from playwright.async_api import async_playwright

        from .stealth import STEALTH_BROWSER_ARGS

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=STEALTH_BROWSER_ARGS)
            await browser.close()
        return True
    except Exception as exc:
        logger.warning("Playwright browser probe failed: %s", exc)
        return False


@asynccontextmanager
async def lifespan(app):
    """Connect to Valkey on startup, close on shutdown.

    Also loads site adapters (YouTube, etc.) — safe to call even
    if the adapters package is not yet installed. Probes Playwright
    browser availability for health check reporting.
    """
    global _browser_available

    from .adapters.base import get_registry

    try:
        registry = get_registry()
        registry.load_all()
        logger.info("Loaded %d site adapters", len(registry._entries))
    except Exception as exc:
        logger.warning("Failed to load adapters: %s", exc)

    # Probe Playwright browser at startup (non-blocking — failure is logged, not fatal)
    try:
        _browser_available = await _probe_browser()
        logger.info("Playwright browser available: %s", _browser_available)
    except Exception as exc:
        _browser_available = False
        logger.warning("Playwright browser probe raised: %s", exc)

    await get_client()
    yield
    await close_client()


app = FastAPI(title="GroktoCrawl Scraper", version="0.1.0", lifespan=lifespan)


class ScrapeRequest(BaseModel):
    url: str


class DownloadData(BaseModel):
    """Binary content metadata for non-HTML responses."""

    filename: str
    content_type: str
    size: int
    data_url: str | None = None


class ScrapeResponse(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None


class MetaResponse(BaseModel):
    success: bool = True
    title: str | None = None
    description: str | None = None
    og_description: str | None = None
    url: str | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response body for the scraper service."""

    success: bool = False
    error: str = "An unexpected error occurred"
    error_code: str = "INTERNAL_ERROR"
    details: list | dict | None = None


@app.exception_handler(GroktoCrawlError)
async def groktocrawl_error_handler(request: Request, exc: GroktoCrawlError):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            error_code=exc.error_code,
            details=exc.details,
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    status_code = exc.status_code
    error_code_map = {
        400: "INVALID_REQUEST",
        401: "AUTH_ERROR",
        403: "AUTH_ERROR",
        404: "NOT_FOUND",
        422: "INVALID_REQUEST",
        429: "RATE_LIMITED",
        502: "UPSTREAM_ERROR",
    }
    error_code = error_code_map.get(status_code, "INTERNAL_ERROR")
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=detail, error_code=error_code).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details_list = []
    for err in exc.errors():
        loc = err.get("loc", [])
        field = ".".join(str(p) for p in loc)
        details_list.append({"field": field, "message": err.get("msg", "")})
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="Validation failed",
            error_code="INVALID_REQUEST",
            details=details_list,
        ).model_dump(),
    )


@app.get("/health")
async def health():
    checks = {
        "playwright": {
            "status": "available" if _browser_available else "unavailable",
            "available": bool(_browser_available),
        }
    }
    overall = "ok" if _browser_available is not False else "degraded"
    return {"status": overall, "checks": checks}


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):
    """Scrape a URL and return its content as clean markdown."""
    try:
        result = await smart_scrape(request.url)
        if result.get("error"):
            raise UpstreamError(detail=result["error"])
        data = {
            "markdown": result.get("markdown", ""),
            "source": result.get("source", "unknown"),
            "url": request.url,
            "quality": result.get("quality"),
            "politeness": result.get("politeness"),
            "metadata": result.get("metadata"),
        }
        if result.get("download"):
            data["download"] = result["download"]
        return ScrapeResponse(success=True, data=data)
    except Exception as e:
        logger.exception("Scrape failed for %s", request.url)
        raise UpstreamError(detail=str(e)) from e


@app.post("/scrape/meta", response_model=MetaResponse)
async def scrape_meta(request: ScrapeRequest):
    """Extract meta tags from a URL using raw HTML (cheap, one GET).

    Returns <title>, <meta name="description">, and
    <meta property="og:description"> without full page rendering.
    """
    try:
        result = await fetch_meta_tags(request.url)
        return MetaResponse(
            success=True,
            title=result.get("title"),
            description=result.get("description"),
            og_description=result.get("og_description"),
            url=request.url,
        )
    except Exception as e:
        logger.exception("Meta fetch failed for %s", request.url)
        raise UpstreamError(detail=str(e)) from e
