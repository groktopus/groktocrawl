"""Read-only explicit follow-up proposals over selected retained identities."""

import asyncio

from fastapi import APIRouter, Request

from ..exceptions import NotFoundError, UpstreamError
from ..followup import FollowupRequest, FollowupResponse, preview, selected_evidence
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
            await get_experimental_evidence(choice.container_id, choice.ref_id, request)
        choices.append(selected_evidence(choice, observed_at, body.max_age_seconds))
    return preview(body, choices)
