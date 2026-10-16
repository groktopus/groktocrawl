"""Resolve explicitly selected retained evidence without trusting caller text."""

import hashlib
from typing import Any

from fastapi import HTTPException, Request

from .followup import EvidenceChoice
from .session_scope import authorize_session
from .session_store import SessionStore

MAX_SELECTED_SOURCE_BYTES = 256_000


async def resolve_selected_sources(
    selected: list[EvidenceChoice], request: Request
) -> list[dict[str, Any]]:
    """Read current scoped bytes; preserve identities for later liveness checks.

    No source URL is fetched here. A caller's subject is only a display label,
    never a substitute for retained evidence or an instruction to a model.
    """
    from .routes._helpers import _get_redis_url
    from .routes.experimental_research import get_experimental_evidence

    identities = [(item.kind, item.container_id, item.ref_id) for item in selected]
    if len(selected) > 8 or len(set(identities)) != len(identities):
        raise HTTPException(422, "Select at most eight distinct evidence references")
    store = SessionStore(redis_url=_get_redis_url(request))
    sources = []
    total_bytes = 0
    for item in selected:
        if item.kind == "session":
            await authorize_session(store, item.container_id, request)
            ref = await store.aget_ref(item.container_id, item.ref_id)
            if ref is not None and ref.get("research_source"):
                ref = await resolve_attached_reference(ref, request)
            if ref is None or not ref.get("markdown"):
                raise HTTPException(404, "Selected session evidence unavailable")
            body = ref["markdown"]
            digest = hashlib.sha256(body.encode()).hexdigest()
            if ref.get("content_digest", digest) != digest:
                raise HTTPException(503, "Selected evidence integrity unavailable")
            url = ref.get("url", "")
            observed = ref.get("scraped_at")
        else:
            evidence = await get_experimental_evidence(
                item.container_id, item.ref_id, request
            )
            body = evidence["body"]
            digest = hashlib.sha256(body.encode()).hexdigest()
            if evidence["content_digest"] != digest:
                raise HTTPException(503, "Selected evidence integrity unavailable")
            url = evidence.get("url")
            observed = evidence.get("retrieved_at")
        if not body.strip():
            raise HTTPException(422, "Selected evidence contains no text")
        total_bytes += len(body.encode())
        if total_bytes > MAX_SELECTED_SOURCE_BYTES:
            raise HTTPException(422, "Selected evidence exceeds 256000 bytes")
        sources.append(
            {
                "selection": item.model_dump(),
                "markdown": body,
                "content_digest": digest,
                "url": url,
                "retrieved_at": observed,
            }
        )
    return sources


async def resolve_attached_reference(
    ref: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Recheck a linked root on every use; never cache its body in the session."""
    pointer = ref.get("research_source")
    if pointer is None:
        return ref
    selection = EvidenceChoice.model_validate(pointer)
    if selection.kind != "root":
        raise HTTPException(503, "Invalid retained research attachment")
    material = (await resolve_selected_sources([selection], request))[0]
    if material["content_digest"] != ref.get("content_digest"):
        raise HTTPException(409, "Attached research evidence changed")
    return {
        **ref,
        "markdown": material["markdown"],
        "char_count": len(material["markdown"]),
        "url": material["url"],
        "scraped_at": material["retrieved_at"],
    }
