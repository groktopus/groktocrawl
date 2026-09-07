"""Session route handlers — multi-step research sessions."""

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..exceptions import ConflictError, InvalidRequestError, NotFoundError
from ..models import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDeleteResponse,
    SessionExportResponse,
    SessionResolveRequest,
    SessionResolveResponse,
    SessionStatusResponse,
    SessionStepRequest,
    SessionStepResponse,
)
from ._helpers import _get_redis_url

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v2/session/create", response_model=SessionCreateResponse)
async def create_session(request: Request, body: SessionCreateRequest) -> Any:
    """Create a new research session.

    Sessions accumulate search results, scraped content, and LLM answers
    server-side so agents can steer multi-step research without carrying
    full page content in their context window.
    """
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    session_id = await mgr.create_session(ttl=body.ttl)
    session = await mgr.get_session(session_id)
    if session is None:
        raise NotFoundError(detail="Failed to create session")
    return SessionCreateResponse(
        session_id=session["id"],
        expires_at=session["expires_at"],
        ttl=session.get("ttl", 3600),
    )


@router.post("/v2/session/{session_id}/step", response_model=SessionStepResponse)
async def session_step(
    request: Request, session_id: str, body: SessionStepRequest
) -> Any:
    """Execute an action step within a research session.

    Supported actions:
        - ``search``: SearXNG search, stores results as refs. Params: query, limit.
        - ``scrape``: Scrape URLs, stores content as refs. Params: urls[].
        - ``query``: LLM over accumulated context. Params: question.
    """
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    try:
        result = await mgr.step(
            session_id=session_id,
            action=body.action,
            params=body.params,
            searxng_url=request.app.state.searxng_url,
            scraper_url=request.app.state.scraper_url,
            llm_base_url=request.app.state.llm_base_url,
            llm_api_key=request.app.state.llm_api_key,
            llm_model=request.app.state.llm_model,
            parallel=body.parallel,
            idempotency_key=body.idempotency_key,
        )
        return SessionStepResponse(
            step_index=result["step_index"],
            action=result["action"],
            summary=result["summary"],
            result=result,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise NotFoundError(detail=msg)
        if "currently executing" in msg.lower() or "pending independent" in msg.lower():
            raise ConflictError(detail=msg)
        if "idempotency key conflicts" in msg.lower():
            raise ConflictError(detail=msg)
        raise InvalidRequestError(detail=msg)


@router.get("/v2/session/{session_id}", response_model=SessionStatusResponse)
async def get_session(request: Request, session_id: str) -> Any:
    """Get session status, step history, and artifact length."""
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    session = await mgr.get_session(session_id)
    if session is None:
        raise NotFoundError(detail=f"Session not found: {session_id}")
    return SessionStatusResponse(
        session_id=session["id"],
        status=session.get("status", "active"),
        created_at=session.get("created_at", ""),
        expires_at=session.get("expires_at", ""),
        step_count=session.get("step_count", 0),
        steps=session.get("steps", []),
        artifact_length=session.get("artifact_length", 0),
    )


@router.post("/v2/session/{session_id}/export", response_model=SessionExportResponse)
async def export_session(request: Request, session_id: str) -> Any:
    """Export the accumulated session artifact as markdown."""
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    try:
        export = await mgr.export_session(session_id)
        return SessionExportResponse(**export)
    except ValueError as e:
        raise NotFoundError(detail=str(e))


@router.delete("/v2/session/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(request: Request, session_id: str) -> Any:
    """Delete a session and all associated data."""
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    deleted = await mgr.delete_session(session_id)
    return SessionDeleteResponse(session_id=session_id, deleted=deleted)


@router.post(
    "/v2/session/{session_id}/resolve",
    response_model=SessionResolveResponse,
)
async def resolve_session_refs(
    request: Request, session_id: str, body: SessionResolveRequest
) -> Any:
    """Resolve reference IDs to full source content.

    Returns full markdown, URL, title, source, char_count, and
    scraped_at for each requested ref.  Missing refs are silently
    omitted — compare ``resolved`` count against ``len(ref_ids)``
    to detect gaps.

    Args:
        session_id: The session to query.
        body: Contains ``ref_ids`` (list of ref ID strings).

    Returns:
        ``SessionResolveResponse`` with resolved refs, resolved count,
        and list of missing ref IDs.

    Raises:
        ``NotFoundError`` (404) if the session does not exist.
    """
    from ..session import SessionManager

    mgr = SessionManager(redis_url=_get_redis_url(request))
    session = await mgr.get_session(session_id)
    if session is None:
        raise NotFoundError(
            detail=f"Session not found: {session_id}",
            details={"session_id": session_id},
        )

    resolved_refs = await mgr.resolve_refs(session_id, body.ref_ids)

    # Build compact ref view (full markdown included for resolve)
    compact: dict[str, dict[str, Any]] = {}
    for ref_id in body.ref_ids:
        ref_data = resolved_refs.get(ref_id)
        if ref_data is not None:
            compact[ref_id] = {
                "url": ref_data.get("url", ""),
                "title": ref_data.get("title", ""),
                "markdown": ref_data.get("markdown", ""),
                "source": ref_data.get("source", "unknown"),
                "char_count": ref_data.get("char_count", 0),
                "scraped_at": ref_data.get("scraped_at", ""),
            }

    missing = [rid for rid in body.ref_ids if rid not in resolved_refs]

    return SessionResolveResponse(
        session_id=session_id,
        refs=compact,
        resolved=len(compact),
        missing=missing,
    )
