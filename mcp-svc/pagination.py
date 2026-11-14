"""Server-authored continuation guidance; source text never supplies instructions."""

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit


def _offset(value: Any) -> bool:
    return type(value) is int and value >= 0


def _next_arguments(tool: str, args: dict, data: dict) -> dict | None:
    """Translate only known API coordinates, never follow an upstream URL."""
    continuation = data.get("continuation")
    if tool == "search" and isinstance(continuation, dict):
        offset = continuation.get("offset")
        if _offset(offset) and offset > args.get("offset", 0):
            if continuation.get("page", 1) == args.get("page", 1):
                return {**args, "offset": offset}
    elif tool == "select_session_evidence" and isinstance(continuation, dict):
        params = continuation.get("params")
        if not isinstance(params, dict):
            return None
        offset, digest = params.get("offset"), params.get("expected_digest")
        advances = _offset(offset) and (
            (bool(args.get("query")) and offset == 0) or offset > args.get("offset", 0)
        )
        if (
            advances
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            if digest == data.get("content_digest"):
                return {
                    **args,
                    "query": "",
                    "offset": offset,
                    "expected_digest": digest,
                }
    elif tool in {"get_crawl_status", "get_batch_scrape_status"}:
        next_url = data.get("next")
        if isinstance(next_url, str):
            try:
                offsets = parse_qs(urlsplit(next_url).query).get("offset", [])
                if len(offsets) == 1 and re.fullmatch(r"[0-9]+", offsets[0]):
                    offset = int(offsets[0])
                    if offset > args.get("offset", 0):
                        return {**args, "offset": offset}
            except ValueError:
                pass
    return None


def with_pagination(data: dict, tool: str, args: dict) -> dict:
    """Prepend guidance while retaining API fields and original call filters."""
    if data.get("error") or data.get("success") is False:
        return data
    next_args = _next_arguments(tool, args, data)
    has_hint = bool(data.get("continuation") or data.get("next"))
    state = "more_available" if next_args is not None else "unknown"
    scope = "retained_results"
    detail = "Continuation availability is unknown; do not claim complete coverage."
    if tool == "search":
        scope = "captured_upstream_page"
        coverage = data.get("coverage") or {}
        if not isinstance(coverage, dict):
            coverage = {}
        if (
            next_args is None
            and not has_hint
            and coverage.get("has_more_in_page") is False
        ):
            state = "exhausted"
        detail = (
            "This is a bounded search page. Upstream availability is unknown; "
            "continuation repeats the query and ordering may change. "
            "Exhausting this page does not mean exhaustive web coverage."
        )
    elif tool == "select_session_evidence":
        scope = "retained_source"
        if (
            next_args is None
            and not has_hint
            and data.get("continuation", False) is None
        ):
            state = "exhausted"
        detail = (
            "This is query-selected evidence, not a sequential page. "
            "Continuation starts a sequential scan at offset zero."
            if args.get("query")
            else "This is a bounded page of retained source text."
        )
        detail += " Exhausting retained text does not imply complete research coverage."
    elif data.get("status") in {"scraping", "processing", "queued", "pending"}:
        if next_args is None and not has_hint:
            state = "pending"
        detail = (
            "The job is still running; currently available results may be paginated."
        )
    elif data.get("status") == "completed" and not has_hint and "next" in data:
        state = "exhausted"
        detail = "No further retained job-result pages are available."
    if next_args is not None:
        detail += (
            " More content is available: call next_call.tool with next_call.arguments."
        )
    elif has_hint:
        detail += " Continuation cannot be safely expressed by this MCP tool; do not repeat this page."
    detail += " Read remaining relevant pages before claiming coverage, or state what you read."
    guidance = {"state": state, "scope": scope, "notice": detail, "next_call": None}
    if next_args is not None:
        guidance["next_call"] = {"tool": tool, "arguments": next_args}
    return {"pagination": guidance, **data}
