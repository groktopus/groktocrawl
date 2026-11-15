"""Streaming generators for the research agent.

Populated in Milestone 2 with stream_cached_artifact() and
stream_research_live() async generators extracted from api.py.
"""

import json
import logging
import time as _time
from copy import deepcopy
from typing import Any

from common.stage_metrics import StreamTiming

from ..exceptions import CitationValidationError
from ..models import CitationStyle
from ..research_cache_metrics import record_execution, record_rejection
from .citations import _apply_citation_style
from .loop import run_research_stream
from .memory import admit_research_memory

logger = logging.getLogger(__name__)


async def stream_cached_artifact(
    artifact_text: str,
    sources: list,
    memory_id: str,
    freshness: str,
    similarity: float,
    citation_style: CitationStyle,
    has_schema: bool,
    age_hours: float | None = None,
    refresh_awaitable: Any = None,
    coverage: dict | None = None,
    evidence_coverage: dict[str, Any] | None = None,
) -> Any:
    """Replay cached agent results as SSE token events.

    Preserves citation style transformation and schema-mode skip logic.
    When ``refresh_awaitable`` is supplied (stale-while-revalidate), the
    stale result is emitted first and the refreshed result follows as a
    ``refreshed`` event.
    """
    stream_start = _time.monotonic()

    # Structured JSON is opaque to the Markdown citation grammar.
    transformed_text = artifact_text
    if not has_schema:
        try:
            transformed_text, _ = _apply_citation_style(
                artifact_text, sources, citation_style
            )
        except CitationValidationError as exc:
            record_rejection("stream", "citation")
            payload = {
                "type": "error",
                "content": exc.detail,
                "error_code": exc.error_code,
                "classification": "non_retryable",
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

    # Schema mode (schema or output_schema): skip token replay,
    # emit only done event (matches non-cached schema streaming behavior)
    if not has_schema:
        # Replay artifact as token events
        chunk_size = 8
        for i in range(0, len(transformed_text), chunk_size):
            chunk = transformed_text[i : i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

    latency_ms = int((_time.monotonic() - stream_start) * 1000)
    from .evidence_recovery import mark_evidence_recovery_status

    cached_result: dict[str, Any] = {
        "coverage": deepcopy(coverage or {}),
        "evidence_coverage": deepcopy(evidence_coverage or {}),
    }
    mark_evidence_recovery_status(cached_result, "not_retained", "cached_result")
    done_payload: dict = {
        "type": "done",
        "result": transformed_text,
        "sources": [s.get("url", "") for s in sources],
        "latency_ms": latency_ms,
        "from_cache": True,
        "coverage": cached_result["coverage"]
        or {
            "scope": "cached_answer",
            "coverage_complete": False,
            "evidence": {"status": "not_retained"},
        },
        "memory_id": memory_id,
        "freshness": freshness,
        "similarity": similarity,
        "citation_style": citation_style.value,
        "evidence_coverage": cached_result["evidence_coverage"],
        "evidence_recovery": cached_result["evidence_recovery"],
    }
    if age_hours is not None:
        done_payload["age_hours"] = age_hours
        done_payload["refreshed"] = False
    if citation_style == CitationStyle.compact:
        compact_srcs = []
        for i, src in enumerate(sources, start=1):
            compact_srcs.append({"index": i, "url": src.get("url", "")})
        done_payload["sources_compact"] = compact_srcs
        done_payload["source_details"] = []
    else:
        done_payload["source_details"] = sources
    done_event = f"data: {json.dumps(done_payload)}\n\n"
    record_execution("stream", "replay", freshness, started=stream_start)
    yield done_event

    # ── Stale-while-revalidate: await the single-flight refresh ──
    if refresh_awaitable is not None:
        try:
            refreshed = await refresh_awaitable
        except Exception:
            logger.warning("Background research memory refresh failed", exc_info=True)
            refreshed = None
        if refreshed:
            refreshed_payload: dict = {
                "type": "refreshed",
                "result": refreshed.get("result", ""),
                "sources": refreshed.get("sources", []),
                "memory_id": refreshed.get("research_memory_id", ""),
                "freshness": "refreshed",
                "age_hours": 0.0,
                "evidence_coverage": refreshed.get("evidence_coverage", {}),
                "evidence_recovery": {"status": "not_retained"},
            }
            from .evidence_recovery import mark_evidence_recovery_status

            mark_evidence_recovery_status(
                refreshed_payload, "not_retained", "cached_refresh"
            )
            if citation_style == CitationStyle.compact:
                refreshed_payload["sources_compact"] = refreshed.get(
                    "sources_compact", []
                )
            yield f"data: {json.dumps(refreshed_payload)}\n\n"

    yield "data: [DONE]\n\n"


async def stream_research_live(
    prompt: str,
    urls: Any,
    schema: dict | None,
    searxng_url: str,
    scraper_url: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    requested_model: str | None,
    max_searches_per_request: int,
    include_images: bool,
    citation_style: CitationStyle,
    search_type: str = "deep",
    max_credits: int | None = None,
    include_source_content: bool = False,
    research_memory: Any = None,
    user_id: str | None = None,
    fingerprint: str | None = None,
    evidence_budget_chars: int | None = None,
    evidence_owner_scope: str = "anonymous",
    evidence_redis_url: str = "redis://localhost:6379/0",
) -> Any:
    """Orchestrate full research SSE pipeline for cache-miss or force-fresh.

    Forwards all 8 SSE event types: token, done, sources_pending,
    source_scraped, sources, error, status, research_plan, research_pass.
    """
    timing = StreamTiming("agent")
    # ``status`` / ``research_plan`` / ``research_pass`` are immediate
    # sentinels emitted before any real work; TTFB must reflect the first
    # content-bearing event, not the planning placeholder.
    _content_events = {"sources_pending", "source_scraped", "sources", "token", "done"}
    record_execution("stream", "live")
    async for event in run_research_stream(
        prompt=prompt,
        urls=urls,
        schema=schema,
        searxng_url=searxng_url,
        scraper_url=scraper_url,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        requested_model=requested_model,
        max_searches_per_request=max_searches_per_request,
        include_images=include_images,
        citation_style=citation_style,
        search_type=search_type,
        max_credits=max_credits,
        # Complete bodies are retained behind an owned paged session. The
        # source export is only included in the event when the caller asked.
        include_source_content=True,
        evidence_budget_chars=evidence_budget_chars,
    ):
        if event["type"] in _content_events:
            timing.on_first_event()
        if event["type"] == "sources_pending":
            yield f"data: {json.dumps({'type': 'sources_pending', 'sources': event['sources']})}\n\n"
        elif event["type"] == "source_scraped":
            source_event = {
                "type": "source_scraped",
                "url": event["url"],
                "source": event.get("source", ""),
                "chars": event.get("chars", 0),
            }
            if include_source_content and isinstance(event.get("markdown"), str):
                source_event["markdown"] = event["markdown"]
            yield f"data: {json.dumps(source_event)}\n\n"
        elif event["type"] == "sources":
            yield f"data: {json.dumps({'type': 'sources', 'sources': event['sources']})}\n\n"
        elif event["type"] == "token":
            timing.on_first_token()
            yield f"data: {json.dumps({'type': 'token', 'content': event['content']})}\n\n"
        elif event["type"] == "done":
            # The synthesis loop has already emitted this canonical Markdown.
            source_details = event.get("source_details", [])
            cs = citation_style

            from .evidence_recovery import retain_result_evidence

            recovery_result: dict[str, Any] = {
                "source_details": deepcopy(source_details),
                "evidence_coverage": deepcopy(event.get("evidence_coverage", {})),
                "coverage": deepcopy(event.get("coverage", {})),
            }
            client_recovery: dict[str, Any] = {
                "source_contents": event.get("source_contents", {}),
                "source_details": deepcopy(source_details),
                "evidence_coverage": deepcopy(event.get("evidence_coverage", {})),
                "coverage": deepcopy(event.get("coverage", {})),
            }
            from .evidence_recovery import mark_evidence_recovery_status

            await retain_result_evidence(
                client_recovery,
                redis_url=evidence_redis_url,
                owner_scope=evidence_owner_scope,
                include_source_content=include_source_content,
            )
            if isinstance(client_recovery.get("coverage"), dict):
                client_recovery["coverage"]["evidence"] = client_recovery.get(
                    "evidence_coverage", {}
                )
            mark_evidence_recovery_status(
                recovery_result, "not_retained", "cached_result"
            )
            memory_id = await admit_research_memory(
                research_memory,
                prompt=prompt,
                artifact=event["result"],
                source_details=recovery_result["source_details"],
                coverage=recovery_result.get("coverage"),
                model=llm_model,
                citation_style=cs.value,
                requested_model=requested_model,
                latency_ms=event["latency_ms"],
                user_id=user_id,
                fingerprint=fingerprint,
                evidence_coverage=recovery_result.get("evidence_coverage", {}),
            )
            transformed_result = event["result"]

            done_payload: dict = {
                "type": "done",
                "result": transformed_result,
                "sources": event["sources"],
                "latency_ms": event["latency_ms"],
                "evidence_coverage": event.get("evidence_coverage", {}),
                "evidence_recovery": recovery_result.get("evidence_recovery", {}),
            }
            if memory_id:
                done_payload["research_memory_id"] = memory_id
            if "evidence_session_id" in recovery_result:
                done_payload["evidence_session_id"] = recovery_result[
                    "evidence_session_id"
                ]
            # Apply citation_style transformation (VAL-CC-008, VAL-CC-009)
            done_payload["citation_style"] = cs.value
            if cs == CitationStyle.compact:
                compact_sources = []
                for i, src in enumerate(source_details, start=1):
                    compact_sources.append(
                        {
                            "index": i,
                            "url": src.get("url", ""),
                        }
                    )
                done_payload["sources_compact"] = compact_sources
                done_payload["source_details"] = []
            else:
                done_payload["source_details"] = client_recovery.get(
                    "source_details", source_details
                )
            if "coverage" in event:
                done_payload["coverage"] = client_recovery.get(
                    "coverage", event["coverage"]
                )
            done_payload["evidence_coverage"] = client_recovery.get(
                "evidence_coverage", {}
            )
            done_payload["evidence_recovery"] = client_recovery.get(
                "evidence_recovery", {}
            )
            if "evidence_session_id" in client_recovery:
                done_payload["evidence_session_id"] = client_recovery[
                    "evidence_session_id"
                ]
            if include_source_content or client_recovery.get(
                "evidence_recovery", {}
            ).get("status") == "unavailable":
                done_payload["source_contents"] = client_recovery.get(
                    "source_contents", {}
                )
            yield f"data: {json.dumps(done_payload)}\n\n"
        elif event["type"] == "error":
            payload: dict[str, Any] = {
                "type": "error",
                "content": event.get("content", "LLM provider failed"),
            }
            error_code = event.get("error_code")
            if isinstance(error_code, str):
                payload["error_code"] = error_code
            details = event.get("details")
            if isinstance(details, dict):
                payload["details"] = details
            classification = event.get("classification")
            if isinstance(classification, str):
                payload["classification"] = classification
            retry_after = event.get("retry_after_seconds")
            if isinstance(retry_after, int | float) and not isinstance(
                retry_after, bool
            ):
                payload["retry_after_seconds"] = float(retry_after)
            yield f"data: {json.dumps(payload)}\n\n"
            return
        elif event["type"] == "status":
            yield f"data: {json.dumps({'type': 'status', 'state': event['state']})}\n\n"
        elif event["type"] == "research_plan":
            yield f"data: {json.dumps({'type': 'research_plan', 'strategy': event['strategy'], 'queries': event['queries'], 'reasoning': event['reasoning']})}\n\n"
        elif event["type"] == "research_pass":
            yield f"data: {json.dumps({'type': 'research_pass', 'pass': event['pass'], 'total_passes': event['total_passes']})}\n\n"
    yield "data: [DONE]\n\n"
