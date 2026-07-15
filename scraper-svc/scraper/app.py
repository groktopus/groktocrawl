"""FastAPI application for the scraper service.

Single endpoint: POST /scrape — takes a URL, returns clean markdown.
"""

import logging
from contextlib import asynccontextmanager
from enum import StrEnum

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from common.logging import setup_logging
from common.metrics import METRICS
from common.middleware import add_request_id_middleware

from .cookie_store import close_client, get_client
from .exceptions import BrowserError, CaptchaError, GroktoCrawlError, UpstreamError
from .fetch import smart_scrape
from .meta import fetch_meta_tags

setup_logging()
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


class VerbosityLevel(StrEnum):
    compact = "compact"  # ~300 chars of body text
    standard = "standard"  # Current behavior (readability extraction)
    full = "full"  # Complete page text including structural markup


class SectionCategory(StrEnum):
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

# ── Instrumentation ──────────────────────────────────────────
add_request_id_middleware(
    app,
    record_metric=lambda labels, val: METRICS.histogram(
        "http_request_duration_seconds",
        "HTTP request latency by path and method",
        ["method", "path"],
    ).observe(labels, val),
)
METRICS.counter("scrape_calls_total", "Total scrape requests", ["status"])
METRICS.counter("meta_calls_total", "Total meta requests", ["status"])


class ScrapeRequest(BaseModel):
    url: str
    contents: ContentsOptions | None = None  # Optional content extraction options
    force_browser: bool = False
    ignore_robots_txt: bool = False
    robots_user_agent: str | None = None
    scrape_options: dict | None = None


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


@app.get("/metrics")
async def metrics():
    """Prometheus-compatible OpenMetrics endpoint."""
    return PlainTextResponse(
        METRICS.generate_openmetrics(),
        media_type="application/openmetrics-text; version=1.0.0",
    )


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):
    """Scrape a URL and return its content as clean markdown."""
    try:
        # When --format images is requested, force browser (Tier 3) so
        # raw_html is available for image extraction (Tier 1/2 don't capture it).
        scrape_opts = request.scrape_options or {}
        formats = scrape_opts.get("formats", [])
        needs_images = "images" in formats

        result = await smart_scrape(
            request.url,
            force_browser=request.force_browser or needs_images,
            ignore_robots_txt=request.ignore_robots_txt,
            robots_user_agent=request.robots_user_agent,
            scrape_options=request.scrape_options,
        )
        if result.get("error"):
            METRICS.counter(
                "scrape_calls_total", "Total scrape requests", ["status"]
            ).inc({"status": "error"})
            if result.get("error_type") == "browser_error":
                raise BrowserError(detail=result["error"])
            if result.get("error_code") == "CAPTCHA_UNRESOLVED":
                raise CaptchaError(
                    detail=result["error"], details=result.get("barrier")
                )
            raise UpstreamError(detail=result["error"])
        markdown = result.get("markdown", "")
        raw_html = result.get("raw_html_start", "")

        # ── Content extraction options (ADR pending) ─────────────
        extras_data: dict | None = None
        if request.contents:
            # Section filtering and verbosity
            if request.contents.text:
                from .extract import filter_sections

                text_opts: dict = (
                    request.contents.text
                    if isinstance(request.contents.text, dict)
                    else {}
                )
                verbosity = text_opts.get("verbosity", "standard")
                include_sections = text_opts.get("include")
                exclude_sections = text_opts.get("exclude")

                if raw_html and verbosity != "standard":
                    markdown = filter_sections(
                        raw_html,
                        include=include_sections,
                        exclude=exclude_sections,
                        verbosity=verbosity,
                    )
                elif verbosity == "compact" and markdown:
                    # Fallback: compact verbosity from markdown (no HTML needed)
                    markdown = markdown[:300]
                elif verbosity == "full" and raw_html:
                    # Full verbosity: bypass readability, render entire page
                    from markdownify import markdownify as md

                    soup = None  # type: ignore[assignment]
                    try:
                        from bs4 import BeautifulSoup

                        soup = BeautifulSoup(raw_html, "html.parser")
                    except Exception:
                        pass
                    if soup:
                        markdown = md(str(soup), heading_style="ATX", strip=[])
                    else:
                        # Fallback: strip HTML tags with regex
                        import re

                        markdown = re.sub(r"<[^>]+>", "", raw_html).strip()
                elif include_sections or exclude_sections:
                    if raw_html:
                        markdown = filter_sections(
                            raw_html,
                            include=include_sections,
                            exclude=exclude_sections,
                            verbosity=verbosity,
                        )

            # Extras extraction
            if request.contents.extras:
                from .extract import extract_extras

                if raw_html:
                    extras_data = extract_extras(raw_html, request.contents.extras)

        data = {
            "markdown": markdown,
            "source": result.get("source", "unknown"),
            "url": request.url,
            "quality": result.get("quality"),
            "politeness": result.get("politeness"),
            "metadata": result.get("metadata"),
        }
        if extras_data:
            data["extras"] = extras_data
        if result.get("download"):
            data["download"] = result["download"]

        # ── Image extraction (when requested via scrape_options.formats) ──
        scrape_opts = request.scrape_options or {}
        formats = scrape_opts.get("formats", [])
        if "images" in formats and raw_html:
            from .extract import extract_images

            remove_base64 = scrape_opts.get("remove_base64_images", False)
            images = extract_images(
                raw_html,
                base_url=request.url,
                remove_base64_images=remove_base64,
            )
            data["images"] = images
        METRICS.counter("scrape_calls_total", "Total scrape requests", ["status"]).inc(
            {"status": "success"}
        )
        return ScrapeResponse(success=True, data=data)
    except GroktoCrawlError:
        raise
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
