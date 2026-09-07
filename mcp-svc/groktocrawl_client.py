"""HTTP client for the GroktoCrawl agent-svc API."""

import asyncio
import ipaddress
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Bounded retry budget for HTTP 429 (rate-limit) responses. agent-svc only
# answers 429 for pre-admission rejection (never after a job is created), so
# retries cannot duplicate side effects.
_MAX_429_RETRIES = 2
# Ceiling for any single 429 backoff wait, and floor for a missing/zero
# Retry-After, so a retry can never become a hot loop.
_MAX_RETRY_WAIT_SECONDS = 10.0
_MIN_RETRY_WAIT_SECONDS = 1.0

# SSRF guard limits for server-side document downloads (parse tool).
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_MAX_REDIRECTS = 5


def _is_private_ip(ip: str) -> bool:
    """Return True for private, loopback, link-local, or reserved IPs."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _validate_download_url(url: str) -> str:
    """Reject URLs that would let the server fetch internal hosts (SSRF).

    Enforces http/https schemes, rejects private/loopback/link-local IP
    literals and local hostnames, and rejects hostnames that resolve to
    any non-public address.  Raises :class:`ValueError` with a
    human-readable reason when the URL is not safe to download.

    Note: this is a request-time guard, not a defense against active
    DNS rebinding between validation and connect; deployments with a
    hostile MCP client should additionally isolate mcp-svc with network
    policies.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"parse: only http/https URLs are allowed, got scheme {parsed.scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise ValueError("parse: URL must include a host")
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ValueError(f"parse: local host {host!r} is not allowed")

    # Fast path: IP literals are checked without DNS.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_private_ip(host):
            raise ValueError(f"parse: private IP address {host!r} is not allowed")
        return url

    # Hostname: reject if ANY resolved address is non-public.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"parse: could not resolve host {host!r}")
    for info in infos:
        addr = info[4][0]
        if _is_private_ip(addr):
            raise ValueError(
                f"parse: host {host!r} resolves to private address {addr!r}"
            )
    return url


def _retry_after_seconds(response: httpx.Response) -> float:
    """Resolve the backoff delay for a 429 response.

    Prefers the ``Retry-After`` header (whole seconds), then a
    ``retry_after_seconds`` body field (used by agent-svc's rate-limit
    contract), then a fixed fallback.  The result is clamped to
    ``[MIN_RETRY_WAIT_SECONDS, MAX_RETRY_WAIT_SECONDS]``.
    """
    header = response.headers.get("retry-after")
    if header is not None:
        try:
            return min(
                max(float(header), _MIN_RETRY_WAIT_SECONDS),
                _MAX_RETRY_WAIT_SECONDS,
            )
        except ValueError:
            pass
    try:
        body = response.json()
        if isinstance(body, dict):
            raw = body.get("retry_after_seconds")
            if isinstance(raw, (int, float)) and raw >= 0:
                return min(
                    max(float(raw), _MIN_RETRY_WAIT_SECONDS),
                    _MAX_RETRY_WAIT_SECONDS,
                )
    except (ValueError, TypeError):
        pass
    return _MIN_RETRY_WAIT_SECONDS


def _extract_response_detail(response: httpx.Response) -> str:
    """Extract a human-readable detail from an error response body.

    Tries JSON first (looking for ``detail``, ``error``, or ``message``
    keys), then falls back to the raw text (truncated).
    """
    try:
        body = response.json()
        if isinstance(body, dict):
            # FastAPI-style validation errors have a 'detail' key
            if "detail" in body:
                detail = body["detail"]
                if isinstance(detail, list):
                    # FastAPI validation errors: detail is a list of error objects
                    return "; ".join(str(d.get("msg", str(d))) for d in detail[:3])
                if isinstance(detail, str):
                    return detail[:500]
            # GroktoCrawl-style errors
            for key in ("error", "message"):
                if key in body and isinstance(body[key], str):
                    return body[key][:500]
        return response.text[:300]
    except (ValueError, TypeError):
        return response.text[:300]


class GroktocrawlClient:
    """Async HTTP client for all GroktoCrawl API endpoints.

    Wraps httpx.AsyncClient with typed convenience methods for each
    endpoint.  Every method returns a ``dict`` — on HTTP or transport
    errors the dict contains an ``error`` key with a human-readable
    message and (when applicable) a ``status_code`` key.

    Usage::

        async with GroktocrawlClient.from_env() as client:
            result = await client.scrape("https://example.com")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        default_timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_env(cls, default_timeout: float = 120.0) -> "GroktocrawlClient":
        """Create a client from environment variables.

        Reads ``GROKTOCRAWL_URL`` for the agent-svc base URL (falls back
        to ``GROKTOCRAWL_API_URL`` for backward compatibility, then
        ``http://localhost:8080``).  Reads ``GROKTOCRAWL_API_KEY`` for
        the optional API key.
        """
        base_url = os.environ.get(
            "GROKTOCRAWL_URL",
            os.environ.get("GROKTOCRAWL_API_URL", "http://localhost:8080"),
        )
        api_key = os.environ.get("GROKTOCRAWL_API_KEY") or None
        return cls(base_url=base_url, api_key=api_key, default_timeout=default_timeout)

    async def __aenter__(self) -> "GroktocrawlClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def _client_ctx(self) -> httpx.AsyncClient:
        """Return (and lazily initialise) the shared AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers(),
                timeout=httpx.Timeout(self._default_timeout),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── helpers ─────────────────────────────────────────────────

    def _error_result(self, msg: str, *, status_code: int | None = None) -> dict:
        result: dict[str, Any] = {"error": msg}
        if status_code is not None:
            result["status_code"] = status_code
        return result

    async def _request(
        self, method: str, path: str, json_data: dict | None = None
    ) -> dict:
        """Unified request helper with structured error handling.

        Discriminates between HTTP errors, timeouts, connection failures,
        and other transport errors — each producing a descriptive error
        dict with appropriate detail.

        HTTP 429 responses (rate limited) are retried with bounded backoff
        honoring ``Retry-After`` (header or ``retry_after_seconds`` body
        field), clamped to a maximum wait.  agent-svc only emits 429 for
        pre-admission rejection (before any job record is created), so
        retrying a 429 never duplicates side effects.
        """
        client = await self._client_ctx()
        start = time.monotonic()
        for attempt in range(_MAX_429_RETRIES + 1):
            try:
                resp = await client.request(method, path, json=json_data)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code == 429 and attempt < _MAX_429_RETRIES:
                    delay = _retry_after_seconds(exc.response)
                    logger.warning(
                        "Rate limited (429) for %s %s — retrying in %.1fs "
                        "(attempt %d/%d)",
                        method,
                        path,
                        delay,
                        attempt + 1,
                        _MAX_429_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                duration = time.monotonic() - start
                detail = _extract_response_detail(exc.response)
                if status_code == 429:
                    # Retry budget exhausted — report distinctly from a
                    # one-off 429 so callers can distinguish.
                    logger.warning(
                        "Rate limited (429) for %s %s — retry budget "
                        "exhausted after %d attempts",
                        method,
                        path,
                        _MAX_429_RETRIES + 1,
                    )
                    return self._error_result(
                        f"Rate limited after {_MAX_429_RETRIES + 1} attempts "
                        f"for {method.upper()} {path}",
                        status_code=429,
                    )
                logger.warning(
                    "HTTP %s for %s %s (%.1fs)",
                    status_code,
                    method,
                    path,
                    duration,
                )
                return self._error_result(
                    f"HTTP {status_code}: {detail}",
                    status_code=status_code,
                )
            except httpx.TimeoutException:
                duration = time.monotonic() - start
                logger.error(
                    "Timeout (%.1fs) for %s %s (threshold: %.0fs)",
                    duration,
                    method,
                    path,
                    self._default_timeout,
                )
                return self._error_result(
                    f"Request timed out after {duration:.1f}s "
                    f"(timeout: {self._default_timeout:.0f}s) for "
                    f"{method.upper()} {path}"
                )
            except httpx.ConnectError as exc:
                logger.error("Connection failed for %s %s: %s", method, path, exc)
                return self._error_result(
                    f"Connection failed: unable to reach server at "
                    f"{self._base_url} — is agent-svc running?"
                )
            except Exception as exc:
                logger.error("Request failed for %s %s: %s", method, path, exc)
                return self._error_result(str(exc))

    async def _post(self, path: str, json_data: dict | None = None) -> dict:
        return await self._request("POST", path, json_data)

    async def _patch(self, path: str, json_data: dict | None = None) -> dict:
        return await self._request("PATCH", path, json_data)

    async def _get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _delete(self, path: str) -> dict:
        return await self._request("DELETE", path)

    # ── API methods ─────────────────────────────────────────────

    async def scrape(
        self,
        url: str,
        formats: list[str] | None = None,
        only_main_content: bool = True,
    ) -> dict:
        """Scrape a URL to markdown.

        Note: ``ScrapeRequest.timeout`` exists in agent-svc but is not
        yet honored by the scrape route, so no timeout parameter is
        exposed here (see agent-svc/agent/routes/scrape.py).
        """
        body: dict[str, Any] = {"url": url}
        if formats:
            body["formats"] = formats
        if not only_main_content:
            body["only_main_content"] = only_main_content
        return await self._post("/v2/scrape", body)

    async def search(
        self,
        query: str,
        limit: int = 5,
        sources: list[str] | None = None,
        categories: list[str] | None = None,
        search_type: str | None = None,
        retrieval_mode: str | None = None,
        output_schema: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        """Web search with optional source filtering and search type."""
        body: dict[str, Any] = {"query": query, "limit": limit}
        if sources:
            body["sources"] = sources
        if categories:
            body["categories"] = categories
        if search_type:
            body["search_type"] = search_type
        if retrieval_mode:
            body["retrieval_mode"] = retrieval_mode
        if output_schema:
            body["output_schema"] = output_schema
        if system_prompt:
            body["system_prompt"] = system_prompt
        return await self._post("/v2/search", body)

    async def agent(
        self,
        prompt: str,
        model: str | None = None,
        output_schema: dict | None = None,
    ) -> dict:
        """Autonomous research agent — create job and poll until complete."""
        body: dict[str, Any] = {"prompt": prompt}
        if model and model != "default":
            body["model"] = model
        if output_schema:
            body["output_schema"] = output_schema

        create_result = await self._post("/v2/agent", body)
        if "error" in create_result:
            return create_result
        job_id = create_result.get("id")
        if not job_id:
            return self._error_result("Agent create: missing job id in response")

        # Poll for completion (max 120 seconds)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = await self._get(f"/v2/agent/{job_id}")
            if "error" in status:
                return status
            st = status.get("status", "processing")
            if st in ("completed", "failed", "cancelled"):
                return status
            await asyncio.sleep(1.0)
        return self._error_result("Agent job timed out after 120s")

    async def create_agent(
        self,
        prompt: str,
        model: str | None = None,
        urls: list[str] | None = None,
        output_schema: dict | None = None,
        citation_style: str | None = None,
        max_credits: int | None = None,
        include_images: bool = False,
        force_fresh: bool = False,
        search_type: str | None = None,
    ) -> dict:
        """Create an agent research job without polling for completion.

        Returns the agent-svc response containing the job ``id``.  Use
        :meth:`get_agent_status` to poll for results and
        :meth:`cancel_agent` to stop an in-progress job.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if model and model != "default":
            body["model"] = model
        if urls:
            body["urls"] = urls
        if output_schema:
            body["output_schema"] = output_schema
        if citation_style:
            body["citation_style"] = citation_style
        if max_credits is not None:
            body["max_credits"] = max_credits
        if include_images:
            body["include_images"] = True
        if force_fresh:
            body["force_fresh"] = True
        if search_type:
            body["search_type"] = search_type
        return await self._post("/v2/agent", body)

    async def create_research_session(self, ttl: int | None = None) -> dict:
        """Create a server-side multi-step research session."""
        body: dict[str, Any] = {}
        if ttl is not None:
            body["ttl"] = ttl
        return await self._post("/v2/session/create", body)

    async def research_session_step(
        self,
        session_id: str,
        action: str,
        params: dict[str, Any],
        parallel: bool = False,
        idempotency_key: str | None = None,
    ) -> dict:
        """Execute one typed research action within a session."""
        body: dict[str, Any] = {"action": action, "params": params}
        if parallel:
            body["parallel"] = True
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return await self._post(f"/v2/session/{session_id}/step", body)

    async def get_research_session(self, session_id: str) -> dict:
        """Get session status, history, and artifact counts."""
        return await self._get(f"/v2/session/{session_id}")

    async def export_research_session(self, session_id: str) -> dict:
        """Export the complete accumulated session artifact tree."""
        return await self._post(f"/v2/session/{session_id}/export", {})

    async def delete_research_session(self, session_id: str) -> dict:
        """Delete a research session and all associated state."""
        return await self._delete(f"/v2/session/{session_id}")

    async def resolve_research_session(
        self, session_id: str, ref_ids: list[str]
    ) -> dict:
        """Resolve session references to full source content."""
        return await self._post(
            f"/v2/session/{session_id}/resolve", {"ref_ids": ref_ids}
        )

    async def answer(
        self,
        question: str,
        num_sources: int = 5,
        model: str | None = None,
        output_schema: dict | None = None,
        citation_style: str | None = None,
        search_type: str | None = None,
        retrieval_mode: str | None = None,
    ) -> dict:
        """Grounded Q&A — synchronous."""
        body: dict[str, Any] = {"query": question, "num_sources": num_sources}
        if model and model != "default":
            body["model"] = model
        if output_schema:
            body["output_schema"] = output_schema
        if citation_style:
            body["citation_style"] = citation_style
        if search_type:
            body["search_type"] = search_type
        if retrieval_mode:
            body["retrieval_mode"] = retrieval_mode
        return await self._post("/v2/answer", body)

    # ── Non-polling job creation methods ────────────────────────

    async def create_crawl(
        self,
        url: str,
        max_pages: int | None = None,
        max_depth: int | None = None,
        limit: int | None = None,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        sitemap: str | None = None,
        ignore_robots_txt: bool = False,
        max_concurrency: int | None = None,
        delay: float | None = None,
        allow_subdomains: bool = False,
        allow_external_links: bool = False,
        prompt: str | None = None,
    ) -> dict:
        """Create a crawl job without polling for completion.

        Returns the agent-svc response containing the job ``id``.
        Use :meth:`get_crawl_status` to poll for results.
        """
        body: dict[str, Any] = {"url": url}
        if max_pages is not None:
            body["max_pages"] = max_pages
        if max_depth is not None:
            body["max_depth"] = max_depth
        if limit is not None:
            body["limit"] = limit
        if include_paths:
            body["include_paths"] = include_paths
        if exclude_paths:
            body["exclude_paths"] = exclude_paths
        if sitemap is not None:
            body["sitemap"] = sitemap
        if ignore_robots_txt:
            body["ignore_robots_txt"] = True
        if max_concurrency is not None:
            body["max_concurrency"] = max_concurrency
        if delay is not None:
            body["delay"] = delay
        if allow_subdomains:
            body["allow_subdomains"] = True
        if allow_external_links:
            body["allow_external_links"] = True
        if prompt:
            body["prompt"] = prompt
        return await self._post("/v2/crawl", body)

    async def create_extract(
        self,
        urls: list[str],
        prompt: str | None = None,
        schema: dict | None = None,
        model: str | None = None,
    ) -> dict:
        """Create an extract job without polling for completion.

        Returns the agent-svc response containing the job ``id``.
        Use :meth:`get_extract_status` to poll for results.
        """
        body: dict[str, Any] = {"urls": urls}
        if prompt:
            body["prompt"] = prompt
        if schema:
            body["schema"] = schema
        if model:
            body["model"] = model
        return await self._post("/v2/extract", body)

    async def create_batch_scrape(
        self, urls: list[str], max_concurrency: int | None = None
    ) -> dict:
        """Create a batch scrape job without polling for completion.

        Returns the agent-svc response containing the job ``id``.
        Use :meth:`get_batch_scrape_status` to poll for results.
        """
        body: dict[str, Any] = {"urls": urls}
        if max_concurrency is not None:
            body["max_concurrency"] = max_concurrency
        return await self._post("/v2/batch/scrape", body)

    async def create_llmstxt(
        self,
        url: str,
        max_pages: int | None = None,
    ) -> dict:
        """Create an llms.txt generation job without polling.

        Returns the agent-svc response containing the job ``id``.
        Use :meth:`get_llmstxt_status` to poll for results.
        """
        body: dict[str, Any] = {"url": url}
        if max_pages is not None:
            body["max_pages"] = max_pages
        return await self._post("/v2/generate-llmstxt", body)

    async def get_llmstxt_status(self, job_id: str) -> dict:
        """Get the current status of an llms.txt generation job."""
        return await self._get(f"/v2/generate-llmstxt/{job_id}")

    async def crawl(
        self,
        url: str,
        max_pages: int | None = None,
        max_depth: int | None = None,
    ) -> dict:
        """Crawl a website — create job and poll until complete."""
        body: dict[str, Any] = {"url": url}
        if max_pages is not None:
            body["max_pages"] = max_pages
        if max_depth is not None:
            body["max_depth"] = max_depth

        create_result = await self._post("/v2/crawl", body)
        if "error" in create_result:
            return create_result
        job_id = create_result.get("id")
        if not job_id:
            return self._error_result("Crawl create: missing job id in response")

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = await self._get(f"/v2/crawl/{job_id}")
            if "error" in status:
                return status
            st = status.get("status", "processing")
            if st in ("completed", "failed", "cancelled"):
                return status
            await asyncio.sleep(2.0)
        return self._error_result("Crawl job timed out after 300s")

    async def map(
        self,
        url: str,
        limit: int = 100,
        search: str | None = None,
        allow_subdomains: bool = False,
        allow_external_links: bool = False,
    ) -> dict:
        """Discover URLs on a site."""
        body: dict[str, Any] = {"url": url, "limit": limit}
        if search:
            body["search"] = search
        if allow_subdomains:
            body["allow_subdomains"] = True
        if allow_external_links:
            body["allow_external_links"] = True
        return await self._post("/v2/map", body)

    async def extract(self, url: str, schema: dict) -> dict:
        """Structured extraction from URLs."""
        body: dict[str, Any] = {"urls": [url], "schema": schema}
        create_result = await self._post("/v2/extract", body)
        if "error" in create_result:
            return create_result
        job_id = create_result.get("id")
        if not job_id:
            return self._error_result("Extract create: missing job id in response")

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = await self._get(f"/v2/extract/{job_id}")
            if "error" in status:
                return status
            st = status.get("status", "processing")
            if st in ("completed", "failed", "cancelled"):
                return status
            await asyncio.sleep(1.0)
        return self._error_result("Extract job timed out after 120s")

    async def parse(self, file_url: str) -> dict:
        """Parse a document (file at URL) to markdown.

        Downloads the file and sends it to the parse endpoint as
        multipart form data.  Uses a separate unauthenticated client
        for the download to avoid leaking the GROKTOCRAWL_API_KEY to
        third-party hosts.

        The download is SSRF-guarded: only public http/https URLs are
        accepted (private/loopback/link-local IPs and hostnames
        resolving to them are rejected, including after each redirect
        hop), redirects are bounded, and the response size is capped.
        """
        import os

        client = await self._client_ctx()

        try:
            _validate_download_url(file_url)

            # Download the file with a separate unauthenticated client
            # to avoid leaking the API key to third-party hosts.
            dl_headers: dict[str, str] = {}
            if "user-agent" in (client.headers or {}):
                dl_headers["User-Agent"] = client.headers["user-agent"]

            dl_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._default_timeout),
                headers=dl_headers,
                follow_redirects=False,
            )
            try:
                current_url = file_url
                for _ in range(_MAX_REDIRECTS + 1):
                    dl_resp = await dl_client.get(current_url)
                    if dl_resp.status_code in (301, 302, 303, 307, 308):
                        location = dl_resp.headers.get("location")
                        if not location:
                            return {"error": "parse: redirect without Location header"}
                        current_url = str(httpx.URL(current_url).join(location))
                        # Re-validate every hop so an open redirect cannot
                        # bypass the initial host check.
                        _validate_download_url(current_url)
                        continue
                    dl_resp.raise_for_status()
                    break
                else:
                    return {"error": f"parse: too many redirects ({_MAX_REDIRECTS})"}

                content_length = dl_resp.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > _MAX_DOWNLOAD_BYTES:
                            return {
                                "error": (
                                    f"parse: file exceeds "
                                    f"{_MAX_DOWNLOAD_BYTES} byte limit"
                                )
                            }
                    except ValueError:
                        pass  # Malformed header — the stream cap still applies.

                # Stream with a hard size cap so oversized bodies are
                # rejected without being fully buffered.
                chunks: list[bytes] = []
                total = 0
                async for chunk in dl_resp.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_BYTES:
                        return {
                            "error": (
                                f"parse: file exceeds {_MAX_DOWNLOAD_BYTES} byte limit"
                            )
                        }
                    chunks.append(chunk)
                file_content = b"".join(chunks)
                dl_content_type = dl_resp.headers.get(
                    "content-type", "application/octet-stream"
                )
            finally:
                await dl_client.aclose()

            filename = os.path.basename(current_url.rsplit("?", 1)[0]) or "file"

            # Upload to parse endpoint using the authenticated client
            parse_resp = await client.post(
                "/v2/parse",
                files={"file": (filename, file_content, dl_content_type)},
            )
            parse_resp.raise_for_status()
            return parse_resp.json()
        except ValueError as exc:
            logger.warning("Parse download rejected for %s: %s", file_url, exc)
            return {"error": str(exc)}
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "HTTP %s for parse of %s", exc.response.status_code, file_url
            )
            return {
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            }
        except Exception as exc:
            logger.error("Parse failed for %s: %s", file_url, exc)
            return {"error": str(exc)}

    async def batch_scrape(self, urls: list[str]) -> dict:
        """Scrape multiple URLs in batch."""
        create_result = await self._post("/v2/batch/scrape", {"urls": urls})
        if "error" in create_result:
            return create_result
        job_id = create_result.get("id")
        if not job_id:
            return self._error_result("Batch scrape: missing job id in response")

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = await self._get(f"/v2/batch/scrape/{job_id}")
            if "error" in status:
                return status
            st = status.get("status", "processing")
            if st in ("completed", "failed", "cancelled"):
                return status
            await asyncio.sleep(2.0)
        return self._error_result("Batch scrape timed out after 300s")

    async def find_similar(
        self,
        url: str,
        limit: int | None = None,
        search_mode: str | None = None,
    ) -> dict:
        """Find pages similar to a given URL."""
        body: dict[str, Any] = {"url": url}
        if limit is not None:
            body["limit"] = limit
        if search_mode:
            body["search_mode"] = search_mode
        return await self._post("/v2/find-similar", body)

    async def enrich(
        self,
        url: str | None = None,
        items: list[dict] | None = None,
        fields: dict | None = None,
        source_hint: str | None = None,
        effort: str = "low",
    ) -> dict:
        """Enrich entities with web-sourced structured data.

        ``items`` is a list of entity dicts (each with a ``url`` or entity
        name); ``fields`` maps field names to ``{"description": ...}``
        specs.  When omitted, ``url`` is treated as a single-item entity
        and a single ``summary`` field is requested.
        """
        if items is None and url is not None:
            items = [{"url": url}]
        if fields is None:
            fields = {"summary": {"description": "A concise summary"}}
        body: dict[str, Any] = {
            "items": items if items is not None else [],
            "fields": fields,
        }
        if source_hint:
            body["source_hint"] = source_hint
        if effort != "low":
            body["effort"] = effort
        return await self._post("/v2/enrich", body)

    async def generate_llmstxt(self, url: str) -> dict:
        """Generate an llms.txt file for a website."""
        create_result = await self._post("/v2/generate-llmstxt", {"url": url})
        if "error" in create_result:
            return create_result
        job_id = create_result.get("id")
        if not job_id:
            return self._error_result("Generate LLMs.txt: missing job id in response")

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = await self._get(f"/v2/generate-llmstxt/{job_id}")
            if "error" in status:
                return status
            st = status.get("status", "processing")
            if st in ("completed", "failed"):
                return status
            await asyncio.sleep(1.0)
        return self._error_result("Generate LLMs.txt timed out after 120s")

    # ── status / cancellation / activity tools ──────────────────

    async def get_crawl_status(self, job_id: str) -> dict:
        """Get the current status of a crawl job.

        Args:
            job_id: The crawl job ID returned by :meth:`crawl`.

        Returns:
            Status dict with ``status``, ``completed``, ``total``,
            ``data``, and other crawl-specific fields.
        """
        return await self._get(f"/v2/crawl/{job_id}")

    async def get_active_crawls(self) -> dict:
        """List active/processing crawl jobs.

        Returns:
            Dict with a ``data`` array of active crawl jobs, each with
            crawl-specific fields (url, max_pages, max_depth, completed,
            total).
        """
        return await self._get("/v2/crawl/active")

    async def cancel_crawl(self, job_id: str) -> dict:
        """Cancel an in-progress crawl job.

        Args:
            job_id: The crawl job ID to cancel.

        Returns:
            Confirmation dict with ``success`` and ``status`` fields.
        """
        return await self._delete(f"/v2/crawl/{job_id}")

    async def get_crawl_errors(self, job_id: str) -> dict:
        """Get per-URL errors for a crawl job.

        Args:
            job_id: The crawl job ID.

        Returns:
            Error listing with ``errors`` array and
            ``robots_blocked`` array.
        """
        return await self._get(f"/v2/crawl/{job_id}/errors")

    async def get_agent_status(self, job_id: str) -> dict:
        """Get the current status of an agent research job.

        Args:
            job_id: The agent job ID returned by :meth:`agent`.

        Returns:
            Status dict with ``status``, ``data``, ``source_details``,
            and other agent-specific fields.
        """
        return await self._get(f"/v2/agent/{job_id}")

    async def cancel_agent(self, job_id: str) -> dict:
        """Cancel an in-progress agent research job.

        Args:
            job_id: The agent job ID returned by :meth:`create_agent`.

        Returns:
            Confirmation dict with ``success`` set to True.
        """
        return await self._delete(f"/v2/agent/{job_id}")

    async def get_extract_status(self, job_id: str) -> dict:
        """Get the current status of an extract job.

        Args:
            job_id: The extract job ID returned by :meth:`extract`.

        Returns:
            Status dict with ``status``, ``data``, and extraction results.
        """
        return await self._get(f"/v2/extract/{job_id}")

    async def get_batch_scrape_status(self, job_id: str) -> dict:
        """Get the current status of a batch scrape job."""
        return await self._get(f"/v2/batch/scrape/{job_id}")

    async def cancel_batch_scrape(self, job_id: str) -> dict:
        """Cancel an in-progress batch scrape job.

        Args:
            job_id: The batch scrape job ID to cancel.

        Returns:
            Confirmation dict with ``success`` set to True.
        """
        return await self._delete(f"/v2/batch/scrape/{job_id}")

    async def get_batch_scrape_errors(self, job_id: str) -> dict:
        """Get per-URL errors for a batch scrape job.

        Args:
            job_id: The batch scrape job ID.

        Returns:
            Error listing with ``errors`` array.
        """
        return await self._get(f"/v2/batch/scrape/{job_id}/errors")

    async def get_activity(self) -> dict:
        """Get recent API activity / job queue status.

        Returns:
            Activity listing with active jobs across all job types.
        """
        return await self._get("/v2/activity")

    async def resolve_citations(
        self,
        text: str,
        sources: list[dict],
        style: str = "inline",
    ) -> dict:
        """Resolve compact citation IDs to full source cards.

        Calls POST /v2/citations/resolve on the GroktoCrawl API.

        Args:
            text: The markdown text containing citation markers (e.g. ``[1]``).
            sources: List of source dicts with ``url`` and ``title`` keys.
            style: Citation style — ``inline`` (no transformation) or
                ``compact`` (replaces ``[N]`` with ``[N](url)``).

        Returns:
            Dict with ``resolved_text``, ``citations`` array, and
            ``citation_count``.
        """
        body: dict[str, Any] = {
            "text": text,
            "sources": sources,
            "style": style,
        }
        return await self._post("/v2/citations/resolve", body)

    # ── utility tools ───────────────────────────────────────────

    async def health(self) -> dict:
        """Server health check."""
        return await self._get("/health")

    async def browser_create(self, ttl: int = 300) -> dict:
        """Create a browser session."""
        return await self._post("/v2/browser", {"ttl": ttl})

    async def browser_list(self) -> dict:
        """List active browser sessions."""
        return await self._get("/v2/browser")

    async def browser_action(self, session_id: str, action: str, **kwargs: Any) -> dict:
        """Execute an action in a browser session."""
        body: dict[str, Any] = {"action": action}
        body.update(kwargs)
        return await self._post(f"/v2/browser/{session_id}/execute", body)

    async def browser_destroy(self, session_id: str) -> dict:
        """Destroy a browser session."""
        return await self._delete(f"/v2/browser/{session_id}")

    async def monitor_create(
        self,
        url: str | None = None,
        schedule: str = "0 */6 * * *",
        webhook: str | None = None,
        monitor_type: str = "scrape",
        search_config: dict | None = None,
    ) -> dict:
        """Create a change monitor.

        ``monitor_type`` is ``"scrape"`` (watch a URL for content changes)
        or ``"search"`` (watch search results for new items; requires
        ``search_config`` with a ``query``).
        """
        body: dict[str, Any] = {
            "schedule": schedule,
            "monitor_type": monitor_type,
        }
        if url is not None:
            body["url"] = url
        if webhook is not None:
            body["webhook"] = webhook
        if search_config:
            body["search_config"] = search_config
        return await self._post("/v2/monitor", body)

    async def monitor_list(self) -> dict:
        """List all monitors."""
        return await self._get("/v2/monitor")

    async def monitor_get(self, monitor_id: str) -> dict:
        """Get a single monitor's details and last check result."""
        return await self._get(f"/v2/monitor/{monitor_id}")

    async def monitor_update(
        self,
        monitor_id: str,
        url: str | None = None,
        schedule: str | None = None,
        webhook: str | None = None,
        search_config: dict | None = None,
    ) -> dict:
        """Update a monitor's configuration."""
        body: dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if schedule is not None:
            body["schedule"] = schedule
        if webhook is not None:
            body["webhook"] = webhook
        if search_config:
            body["search_config"] = search_config
        return await self._patch(f"/v2/monitor/{monitor_id}", body)

    async def monitor_run(self, monitor_id: str) -> dict:
        """Manually trigger a monitor check immediately."""
        return await self._post(f"/v2/monitor/{monitor_id}/run", {})

    async def monitor_delete(self, monitor_id: str) -> dict:
        """Delete a monitor."""
        return await self._delete(f"/v2/monitor/{monitor_id}")
