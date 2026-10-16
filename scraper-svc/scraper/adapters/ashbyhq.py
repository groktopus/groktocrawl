"""
AshbyHQ ATS adapter — extracts job postings from AshbyHQ-powered career pages.

Fallback chain (top → bottom):
  1. Public posting-api ``/posting-api/job-board/{board}`` (keyless, at the TOP)
  2. Authenticated ``jobPosting.list`` RPC tier (gated behind ``ASHBY_API_KEY``)
  3. ``window.__appData`` JSON extraction from SSR HTML
  4. Page scrape via readability-lxml
  5. ``AdapterError`` — falls through to the generic scrape pipeline

The public posting-api returns the full set of published postings as structured
JSON (including compensation when requested), so it is preferred whenever the
board name can be derived from the URL.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_ASHBYHQ_URL_PATTERNS = [
    # jobs.ashbyhq.com/{company}  (listing page)
    re.compile(
        r"^https?://jobs\.ashbyhq\.com/"
        r"(?P<company>[^/]+)/?$"
    ),
    # jobs.ashbyhq.com/{company}/{uuid}  (individual job)
    re.compile(
        r"^https?://jobs\.ashbyhq\.com/"
        r"(?P<company>[^/]+)/(?P<uuid>[a-f0-9\-]+)"
    ),
]

# Direct match for the public posting-api board feed.
_ASHBY_API_PATTERNS = [
    re.compile(
        r"^https?://api\.ashbyhq\.com/"
        r"posting-api/job-board/(?P<board>[^/]+)"
    ),
]


def _extract_company(url: str) -> str | None:
    """Extract the company slug from an AshbyHQ careers URL."""
    for pattern in _ASHBYHQ_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group("company")
    return None


def _extract_board(url: str) -> str | None:
    """Derive the Ashby board name from a URL.

    Per Ashby docs the board name is the final URL path segment of the jobs
    page (``jobs.ashbyhq.com/linear`` → ``linear``) and it also appears in the
    direct posting-api URL (``/posting-api/job-board/{board}``).
    """
    for pattern in _ASHBY_API_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group("board")
    return _extract_company(url)


def _extract_uuid(url: str) -> str | None:
    """Extract the job UUID from an AshbyHQ job URL.

    Returns ``None`` for listing pages (no UUID).
    """
    m = _ASHBYHQ_URL_PATTERNS[1].search(url)
    if m:
        return m.group("uuid")
    return None


# ── Data extraction ──────────────────────────────────────────────


async def _fetch_html(url: str) -> str | None:
    """Fetch HTML content from an AshbyHQ URL.

    Uses a browser-like User-Agent to ensure SSR content is returned.
    """
    try:
        async with source_httpx_client(
            timeout=15,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; GroktoCrawl/0.7.0; AshbyHQ adapter)"
                    ),
                },
            )
            if resp.status_code == 200:
                return resp.text
            logger.debug("AshbyHQ fetch returned %d for %s", resp.status_code, url)
            return None
    except httpx.TimeoutException:
        logger.debug("AshbyHQ fetch timed out for %s", url)
        return None
    except httpx.RequestError as exc:
        logger.debug("AshbyHQ fetch failed for %s: %s", url, exc)
        return None


def _extract_appdata_json(html: str) -> dict | None:
    """Extract ``window.__appData`` JSON from AshbyHQ SSR HTML.

    Uses ``json.JSONDecoder.raw_decode`` for robust handling of nested JSON.
    """
    marker = "window.__appData"
    idx = html.find(marker)
    if idx == -1:
        return None

    # Find the opening brace after the marker
    start = html.find("{", idx)
    if start == -1:
        return None

    try:
        decoder = json.JSONDecoder()
        obj, _end = decoder.raw_decode(html, start)
        return obj
    except json.JSONDecodeError:
        logger.debug("Failed to parse __appData JSON (raw_decode)")
        return None


async def _fetch_and_parse_appdata(url: str) -> dict | None:
    """Fetch an AshbyHQ URL and extract ``window.__appData`` JSON.

    Returns the parsed JSON dict, or ``None`` on failure.
    """
    html = await _fetch_html(url)
    if not html:
        return None
    return _extract_appdata_json(html)


# ── Ashby API tiers ──────────────────────────────────────────────

_PUBLIC_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
_RPC_URL = "https://api.ashbyhq.com/jobPosting.list"

_USER_AGENT = "Mozilla/5.0 (compatible; GroktoCrawl/0.7.0; AshbyHQ adapter)"


async def _fetch_board(board: str, include_compensation: bool = True) -> dict | None:
    """Fetch the public posting-api board feed (keyless).

    Returns the parsed ``{jobs, apiVersion}`` dict, or ``None`` on any
    failure (including an unknown board → 404).
    """
    url = f"{_PUBLIC_API_BASE}/{board}"
    params = {"includeCompensation": "true" if include_compensation else "false"}
    try:
        async with source_httpx_client(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.debug(
                "AshbyHQ posting-api returned %d for board %s",
                resp.status_code,
                board,
            )
            return None
    except httpx.TimeoutException:
        logger.debug("AshbyHQ posting-api timed out for board %s", board)
        return None
    except httpx.RequestError as exc:
        logger.debug("AshbyHQ posting-api failed for board %s: %s", board, exc)
        return None
    except ValueError:
        logger.debug("AshbyHQ posting-api returned invalid JSON for %s", board)
        return None


async def _fetch_rpc_jobs(api_key: str) -> list | None:
    """Call the authenticated ``jobPosting.list`` RPC tier.

    Uses HTTP Basic auth with the API key as the username and a blank
    password. Requires the ``jobsRead`` permission. Returns the list of
    results, or ``None`` on failure.
    """
    body = {"listedOnly": True}
    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.post(_RPC_URL, json=body, auth=(api_key, ""))
            if resp.status_code == 200:
                return _rpc_results(resp.json())
            logger.debug("AshbyHQ jobPosting.list returned %d", resp.status_code)
            return None
    except httpx.TimeoutException:
        logger.debug("AshbyHQ jobPosting.list timed out")
        return None
    except httpx.RequestError as exc:
        logger.debug("AshbyHQ jobPosting.list failed: %s", exc)
        return None
    except ValueError:
        logger.debug("AshbyHQ jobPosting.list returned invalid JSON")
        return None


def _rpc_results(data: dict) -> list | None:
    """Extract the ``results`` list from a ``jobPosting.list`` response.

    Defensively handles the response shape variations of the RPC surface.
    """
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("results"), list):
        return data["results"]
    nested = data.get("data")
    if isinstance(nested, dict):
        if isinstance(nested.get("results"), list):
            return nested["results"]
        listing = nested.get("jobPosting", {}).get("list", {})
        if isinstance(listing.get("results"), list):
            return listing["results"]
    return None


def _rpc_result_to_job(result: dict) -> dict:
    """Normalize a ``jobPosting.list`` result into the posting-api job shape."""
    if not isinstance(result, dict):
        return {}
    job_url = result.get("externalLink") or result.get("applyLink") or ""
    return {
        "id": result.get("id", ""),
        "title": result.get("title", ""),
        "department": result.get("departmentName", ""),
        "team": result.get("teamName", ""),
        "location": result.get("locationName", ""),
        "secondaryLocations": [],
        "employmentType": result.get("employmentType", ""),
        "workplaceType": result.get("workplaceType", ""),
        "isRemote": result.get("isRemote", False),
        "isListed": result.get("isListed", False),
        "publishedAt": result.get("publishedDate", ""),
        "jobUrl": job_url,
        "applyUrl": result.get("applyLink", "") or job_url,
        "descriptionHtml": result.get("descriptionHtml", ""),
        "descriptionPlain": result.get("descriptionPlain", ""),
        "compensation": {
            "compensationTierSummary": result.get("compensationTierSummary"),
            "compensationTiers": result.get("compensationTiers", []),
            "summaryComponents": [],
        },
    }


def _find_job(jobs: list, uuid: str) -> dict | None:
    """Find a job by posting UUID within a ``jobs[]`` list."""
    for job in jobs:
        if job.get("id") == uuid:
            return job
    return None


# ── HTML → markdown conversion ───────────────────────────────────


def _html_to_markdown(html: str) -> str:
    """Convert HTML job description to clean markdown.

    Uses readability-lxml + markdownify (standard deps of scraper-svc).
    Falls back to BeautifulSoup text extraction.
    """
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(html)
        summary = doc.summary()
        markdown = md(summary, heading_style="ATX", strip=["script", "style"])
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("readability-lxml failed: %s", exc)

    # Fallback: BeautifulSoup text extraction
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:20000]
    except Exception as exc:
        logger.debug("BeautifulSoup fallback failed: %s", exc)

    return html[:10000]


# ── Response formatting ──────────────────────────────────────────


def _format_listing(postings: list, company: str) -> tuple[str, dict]:
    """Convert a list of AshbyHQ job postings to a markdown table + metadata.

    Returns ``(markdown, metadata)``.
    """
    parts: list[str] = []
    parts.append(f"# {company} — Job Openings")
    parts.append("")

    if not postings:
        parts.extend(["*No job postings available at this time.*"])
        return "\n".join(parts), {
            "source": "ashbyhq-listing",
            "company": company,
            "total_openings": 0,
        }

    headers = [
        "Title",
        "Department",
        "Location",
        "Workplace Type",
        "Employment Type",
        "Posted",
    ]
    parts.append("| " + " | ".join(headers) + " |")
    parts.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for p in postings:
        row = [
            p.get("title", ""),
            p.get("departmentName", "") or "",
            p.get("locationName", "") or "",
            p.get("workplaceType", "") or "",
            p.get("employmentType", "") or "",
            (p.get("publishedDate") or "")[:10],
        ]
        parts.append("| " + " | ".join(row) + " |")

    metadata: dict = {
        "source": "ashbyhq-listing",
        "company": company,
        "total_openings": len(postings),
    }

    return "\n".join(parts), metadata


def _format_job(data: dict, company: str, uuid: str) -> tuple[str, dict]:
    """Convert an AshbyHQ job posting response to markdown + metadata.

    Returns ``(markdown, metadata)``.
    """
    posting = data.get("posting", {})
    if not posting:
        raise AdapterError(
            f"No posting data found in AshbyHQ response for {company}/{uuid}"
        )

    title = posting.get("title", "")
    url = f"https://jobs.ashbyhq.com/{company}/{uuid}"

    # Build metadata
    metadata: dict = {
        "id": posting.get("id", ""),
        "title": title,
        "departmentName": posting.get("departmentName", ""),
        "teamName": posting.get("teamName", ""),
        "locationName": posting.get("locationName", ""),
        "workplaceType": posting.get("workplaceType", ""),
        "employmentType": posting.get("employmentType", ""),
        "publishedDate": (posting.get("publishedDate") or "")[:10],
        "updatedAt": (posting.get("updatedAt") or "")[:10],
        "jobRequisitionId": posting.get("jobRequisitionId", ""),
        "isRemote": posting.get("isRemote", False),
        "compensationTierSummary": posting.get("compensationTierSummary", ""),
        "shouldDisplayCompensation": posting.get("shouldDisplayCompensation", False),
        "source": "ashbyhq",
        "url": url,
    }

    # Build markdown
    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")

    # Key details table
    detail_items: list[tuple[str, str]] = [
        ("Department", posting.get("departmentName", "")),
        ("Team", posting.get("teamName", "")),
        ("Location", posting.get("locationName", "")),
        ("Workplace Type", posting.get("workplaceType", "")),
        ("Employment Type", posting.get("employmentType", "")),
        ("Published", (posting.get("publishedDate") or "")[:10]),
        ("Updated", (posting.get("updatedAt") or "")[:10]),
        ("Job Requisition ID", posting.get("jobRequisitionId", "")),
        ("Remote", "Yes" if posting.get("isRemote") else "No"),
    ]

    # Add compensation if available
    compensation_summary = posting.get("compensationTierSummary", "")
    if compensation_summary:
        detail_items.append(("Compensation Summary", compensation_summary))

    parts.append("| Field | Value |")
    parts.append("|-------|-------|")
    for label, val in detail_items:
        if val:
            parts.append(f"| **{label}** | {val} |")
    parts.append("")

    # Description
    description_html = posting.get("descriptionHtml", "")
    if description_html:
        desc_md = _html_to_markdown(description_html)
        if desc_md:
            parts.append("## Description")
            parts.append("")
            parts.append(desc_md)
        else:
            parts.append("*No description available*")
    elif posting.get("descriptionPlainText"):
        parts.append("## Description")
        parts.append("")
        parts.append(posting["descriptionPlainText"])
    else:
        parts.append("*No description available*")

    parts.append("")
    parts.append(f"*Source: [AshbyHQ]({url})*")

    markdown = "\n".join(parts).strip()
    return markdown, metadata


# ── API-tier formatting ──────────────────────────────────────────


def _escape_cell(value) -> str:
    """Escape a value for use inside a single markdown table cell.

    Replaces ``|`` with ``\\|`` and collapses newlines to ``<br>`` so a field
    containing a pipe or line break renders as one cell instead of breaking
    the table layout.
    """
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|")
    return text.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def _location_string(job: dict) -> str:
    """Combine ``location`` and ``secondaryLocations`` into one string."""
    primary = job.get("location")
    if isinstance(primary, dict):
        primary = primary.get("locationName") or primary.get("name") or ""
    secondary = job.get("secondaryLocations") or []
    locs: list[str] = []
    if primary:
        locs.append(str(primary))
    for loc in secondary:
        if isinstance(loc, dict):
            loc = loc.get("locationName") or loc.get("name") or ""
        if loc:
            locs.append(str(loc))
    return ", ".join(locs)


def _format_compensation(compensation) -> str:
    """Render a compensation dict into a human-readable string.

    Handles ``compensationTierSummary``, ``compensationTiers[]`` and
    ``summaryComponents[]``. Returns ``""`` when nothing is disclosed.
    """
    if not isinstance(compensation, dict):
        return ""
    parts: list[str] = []
    tier_summary = compensation.get("compensationTierSummary")
    if tier_summary:
        parts.append(str(tier_summary))
    for tier in compensation.get("compensationTiers") or []:
        if not isinstance(tier, dict):
            continue
        label = tier.get("title") or tier.get("name") or ""
        summary = tier.get("summary") or tier.get("compensationTierSummary") or ""
        if label and summary:
            parts.append(f"{label}: {summary}")
        elif label:
            parts.append(label)
        elif summary:
            parts.append(summary)
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        for key in ("summary", "summaryText", "displayValue", "value"):
            val = component.get(key)
            if val:
                parts.append(str(val))
                break
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "; ".join(seen)


def _format_api_listing(jobs: list, board: str) -> tuple[str, dict]:
    """Convert the public posting-api ``jobs[]`` to a markdown table."""
    parts: list[str] = []
    parts.append(f"# {board} — Job Openings")
    parts.append("")

    if not jobs:
        parts.append("*No job postings available at this time.*")
        return "\n".join(parts), {
            "source": "ashbyhq-api",
            "board": board,
            "total_openings": 0,
        }

    headers = [
        "Title",
        "Department",
        "Team",
        "Location",
        "Workplace Type",
        "Employment Type",
        "Compensation",
    ]
    parts.append("| " + " | ".join(headers) + " |")
    parts.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for job in jobs:
        row = [
            _escape_cell(job.get("title", "")),
            _escape_cell(job.get("department", "") or ""),
            _escape_cell(job.get("team", "") or ""),
            _escape_cell(_location_string(job)),
            _escape_cell(job.get("workplaceType", "") or ""),
            _escape_cell(job.get("employmentType", "") or ""),
            _escape_cell(_format_compensation(job.get("compensation"))),
        ]
        parts.append("| " + " | ".join(row) + " |")

    metadata: dict = {
        "source": "ashbyhq-api",
        "board": board,
        "total_openings": len(jobs),
    }
    return "\n".join(parts), metadata


def _format_api_job(job: dict, board: str, uuid: str) -> tuple[str, dict]:
    """Convert a single posting-api job into markdown + metadata."""
    title = job.get("title", "")
    url = job.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{uuid}"
    location = _location_string(job)
    compensation = _format_compensation(job.get("compensation"))

    metadata: dict = {
        "id": job.get("id", uuid),
        "title": title,
        "department": job.get("department", ""),
        "team": job.get("team", ""),
        "location": location,
        "workplaceType": job.get("workplaceType", ""),
        "employmentType": job.get("employmentType", ""),
        "isRemote": job.get("isRemote", False),
        "isListed": job.get("isListed", False),
        "publishedAt": (job.get("publishedAt") or "")[:10],
        "source": "ashbyhq-api",
        "url": url,
    }
    if compensation:
        metadata["compensation"] = compensation

    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")

    detail_items: list[tuple[str, str]] = [
        ("Department", job.get("department", "")),
        ("Team", job.get("team", "")),
        ("Location", location),
        ("Workplace Type", job.get("workplaceType", "")),
        ("Employment Type", job.get("employmentType", "")),
        ("Remote", "Yes" if job.get("isRemote") else "No"),
        ("Published", (job.get("publishedAt") or "")[:10]),
    ]
    if compensation:
        detail_items.append(("Compensation", compensation))

    parts.append("| Field | Value |")
    parts.append("|-------|-------|")
    for label, val in detail_items:
        if val:
            parts.append(f"| **{label}** | {_escape_cell(val)} |")
    parts.append("")

    description_html = job.get("descriptionHtml", "")
    if description_html:
        desc_md = _html_to_markdown(description_html)
        if desc_md:
            parts.append("## Description")
            parts.append("")
            parts.append(desc_md)
        else:
            parts.append("*No description available*")
    elif job.get("descriptionPlain"):
        parts.append("## Description")
        parts.append("")
        parts.append(job["descriptionPlain"])
    else:
        parts.append("*No description available*")

    parts.append("")
    parts.append(f"*Source: [AshbyHQ]({url})*")

    return "\n".join(parts).strip(), metadata


# ── Adapter class ────────────────────────────────────────────────


@adapter
class AshbyHQAdapter(SiteAdapter):
    """Extract job postings from AshbyHQ-powered career pages."""

    name = "ashbyhq"

    patterns = _ASHBYHQ_URL_PATTERNS + _ASHBY_API_PATTERNS

    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        board = _extract_board(url)
        if not board:
            raise AdapterError(f"Could not extract board from AshbyHQ URL: {url}")
        company = _extract_company(url) or board
        uuid = _extract_uuid(url)

        def _result_from_jobs(
            jobs: list, board: str, uuid: str | None
        ) -> AdapterResult | None:
            """Build a result from a ``jobs[]`` list, or ``None`` if unmatched."""
            if uuid:
                job = _find_job(jobs, uuid)
                if not job:
                    return None
                markdown, metadata = _format_api_job(job, board, uuid)
                logger.info(
                    "AshbyHQ adapter: API job %s (%d chars)",
                    uuid,
                    len(markdown),
                )
                return AdapterResult(
                    success=True,
                    markdown=markdown,
                    metadata=metadata,
                    source="ashbyhq-api",
                    url=url,
                )
            markdown, metadata = _format_api_listing(jobs, board)
            logger.info("AshbyHQ adapter: API listing with %d jobs", len(jobs))
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="ashbyhq-api",
                url=url,
            )

        # Tier 1: public posting-api (keyless), at the TOP of the chain
        logger.info("AshbyHQ adapter: trying public posting-api for %s", url)
        board_data = await ctx.with_timeout(_fetch_board(board), timeout=15)
        if board_data is not None and isinstance(board_data.get("jobs"), list):
            result = _result_from_jobs(board_data["jobs"], board, uuid)
            if result:
                return result
            # uuid not among published jobs → fall through to SSR

        # Tier 2: authenticated jobPosting.list RPC (gated by ASHBY_API_KEY)
        #
        # Only engaged for individual job URLs: jobPosting.list is scoped to
        # the company that owns the key, so its results cannot be trusted to
        # represent an arbitrary listing board. Individual postings are safe
        # because posting UUIDs are globally unique.
        api_key = ctx.config.get("ADAPTER_ASHBY_API_KEY") or ctx.config.get(
            "ASHBY_API_KEY"
        )
        if api_key and uuid:
            logger.info("AshbyHQ adapter: trying authenticated RPC for %s", url)
            rpc_results = await ctx.with_timeout(_fetch_rpc_jobs(api_key), timeout=15)
            if rpc_results:
                jobs = [_rpc_result_to_job(r) for r in rpc_results]
                result = _result_from_jobs(jobs, board, uuid)
                if result:
                    return result

        # Tier 3: window.__appData JSON extraction from SSR HTML
        logger.info("AshbyHQ adapter: trying __appData extraction for %s", url)
        data = await ctx.with_timeout(_fetch_and_parse_appdata(url), timeout=15)

        if data:
            if uuid:
                # Individual job page
                posting = data.get("posting")
                if not posting:
                    raise AdapterError(
                        f"No posting data in AshbyHQ response for {company}/{uuid}"
                    )
                markdown, metadata = _format_job(data, company, uuid)
                logger.info(
                    "AshbyHQ adapter: extracted job %s (%d chars)",
                    uuid,
                    len(markdown),
                )
                return AdapterResult(
                    success=True,
                    markdown=markdown,
                    metadata=metadata,
                    source="ashbyhq",
                    url=url,
                )

            # Listing page
            postings = data.get("jobBoard", {}).get("jobPostings", [])
            if not postings:
                raise AdapterError(
                    f"No job postings found for AshbyHQ board: {company}"
                )
            markdown, metadata = _format_listing(postings, company)
            logger.info(
                "AshbyHQ adapter: extracted listing with %d jobs",
                len(postings),
            )
            return AdapterResult(
                success=True,
                markdown=markdown,
                metadata=metadata,
                source="ashbyhq-listing",
                url=url,
            )

        # Tier 4: readability page scrape
        logger.info("AshbyHQ adapter: trying readability fallback for %s", url)
        readable = await scrape_page(url)
        if readable:
            return AdapterResult(
                success=True,
                markdown=readable,
                metadata={"source": "ashbyhq-readability"},
                url=url,
            )

        raise AdapterError(f"Could not extract content from AshbyHQ URL: {url}")
