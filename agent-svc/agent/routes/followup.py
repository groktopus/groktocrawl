"""Read-only explicit follow-up proposals over selected retained identities."""

import asyncio

from fastapi import APIRouter, Request

from ..exceptions import NotFoundError, UpstreamError
from ..followup import (
    FollowupExecutionRequest,
    FollowupRequest,
    FollowupResponse,
    preview,
    selected_evidence,
)
from ..session_scope import authorize_session
from ..session_store import SessionStore
from ._helpers import _get_redis_url

router = APIRouter()
_VALIDATION_TIMEOUT = 15.0


@router.post("/v2/followup/preview", response_model=FollowupResponse)
async def preview_followup(request: Request, body: FollowupRequest) -> FollowupResponse:
    """Validate explicit choices without reading accumulated histories or executing work."""
    try:
        async with asyncio.timeout(_VALIDATION_TIMEOUT):
            return await _validated_preview(request, body)
    except TimeoutError as exc:
        raise UpstreamError(detail="Selected evidence validation timed out") from exc


async def _validated_preview(
    request: Request, body: FollowupRequest
) -> FollowupResponse:
    choices = []
    store = None
    for choice in body.selected:
        observed_at = None
        if choice.kind == "session":
            if store is None:
                store = SessionStore(redis_url=_get_redis_url(request))
            # Credential ownership for new sessions; legacy IDs remain bearer capabilities.
            await authorize_session(store, choice.container_id, request)
            ref = await store.aget_ref(choice.container_id, choice.ref_id)
            if ref is None:
                raise NotFoundError(detail="Selected session evidence unavailable")
            observed_at = ref.get("scraped_at")
        else:
            from .experimental_research import get_experimental_evidence

            # Reuse feature, credential scope, tombstone and identity checks.
            evidence = await get_experimental_evidence(
                choice.container_id, choice.ref_id, request
            )
            observed_at = evidence.get("retrieved_at")
        choices.append(selected_evidence(choice, observed_at, body.max_age_seconds))
    return preview(body, choices)


@router.post("/v2/followup/execute", status_code=202)
async def execute_followup(request: Request, body: FollowupExecutionRequest) -> dict:
    """Explicitly start independent research with current, selected evidence.

    The existing preview stays read-only. This execution endpoint uses the same
    scoped idempotency and durable admission rules as ordinary research runs.
    """
    from fastapi import HTTPException

    from ..experimental.query_construction import CapturedSource
    from ..research_sources import resolve_selected_sources
    from .experimental_research import CreateResearchRunRequest, admit_research_run

    preview_body = FollowupRequest.model_validate(
        body.model_dump(exclude={"as_of", "webhook"})
    )
    proposal = await preview_followup(request, preview_body)
    if proposal.status != "ready" or proposal.proposed_query is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Clarify the follow-up before execution",
                "ambiguities": proposal.ambiguities,
            },
        )
    materials = await resolve_selected_sources(body.selected, request)
    sources = tuple(
        CapturedSource(
            url=material["url"]
            if material["url"] and material["url"].startswith(("http://", "https://"))
            else None,
            text=material["markdown"],
            retrieved_at=material["retrieved_at"],
            origin_id=f"{item.kind}:{item.container_id}:{item.ref_id}",
            lineage_id=material["content_digest"],
        )
        for item, material in zip(body.selected, materials, strict=True)
    )
    admitted = await admit_research_run(
        CreateResearchRunRequest(
            objective=proposal.proposed_query, as_of=body.as_of, webhook=body.webhook
        ),
        request,
        selected_sources=sources,
    )
    return {"success": True, **admitted}
