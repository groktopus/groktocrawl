"""Local parsed document attachments, using existing session evidence authority."""

import asyncio
import hashlib
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request

from ..exceptions import ConflictError, InvalidRequestError, NotFoundError
from ..session_documents import (
    MAX_DOCUMENT_BYTES,
    check_document_quota,
    document_ref,
    safe_filename,
)
from ..session_scope import authorize_session, request_scope
from ..session_store import SessionStore
from ._helpers import _get_redis_url
from .parse import PARSE_SVC_URL, _consume_upload, _parse_upstream_response

router = APIRouter()
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _bounded_form_request(request: Request) -> Request:
    """Bound transfer before multipart spooling, including chunked requests."""
    received = 0

    async def receive() -> MutableMapping[str, Any]:
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_DOCUMENT_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
            raise InvalidRequestError(
                detail="Document multipart transfer limit exceeded"
            )
        return message

    return Request(request.scope, receive=receive)


@asynccontextmanager
async def _document_lock(store: SessionStore, session_id: str, request: Request):
    token = await store.acquire_lock(session_id, timeout=2, lease_ttl=30)
    if token is None:
        raise ConflictError(detail="Session is executing another operation; retry")
    context = store.set_lock_owner(token)
    try:
        await authorize_session(store, session_id, request)
        if await store.ahas_pending_steps(session_id):
            raise ConflictError(detail="Session has pending independent steps; retry")
        yield
    finally:
        store.reset_lock_owner(context)
        await store.arelease_lock(session_id, token)


def _metadata(ref_id: str, ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref_id": ref_id,
        **{key: value for key, value in ref.items() if key != "markdown"},
    }


async def _parse_document(
    content: bytes, filename: str, media_type: str
) -> dict[str, Any]:
    """Use only the established Parse service; no arbitrary acquisition URL."""
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{PARSE_SVC_URL}/parse",
            files={"file": (filename, content, media_type)},
            data={"ocr": "local"},
        )
    return _parse_upstream_response(response)


@router.post(
    "/v2/session/{session_id}/documents",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "upload_id": {"type": "string"},
                        },
                        "oneOf": [{"required": ["file"]}, {"required": ["upload_id"]}],
                        "description": "Exactly one file or scoped staged upload_id; 10 MiB maximum document bytes",
                    }
                }
            },
        }
    },
)
async def attach_session_document(session_id: str, request: Request) -> dict[str, Any]:
    """Parse a local file or consume a scoped staged upload, then attach exact text."""
    store = SessionStore(redis_url=_get_redis_url(request))
    session = await authorize_session(store, session_id, request)
    # Legacy sessions retain capability-based reads; new private attachments need
    # an owner established at creation, never opportunistically claimed by a caller.
    if session.get("owner_scope") is None:
        raise InvalidRequestError(
            detail="Create an owned session before attaching private documents"
        )
    async with _bounded_form_request(request).form(
        max_files=1, max_fields=2, max_part_size=MAX_DOCUMENT_BYTES
    ) as form:
        upload_id = form.get("upload_id")
        upload = form.get("file")
        if upload_id and upload:
            raise InvalidRequestError(detail="Provide file or upload_id, not both")
        if isinstance(upload_id, str):
            if not 0 < len(upload_id) <= 200:
                raise InvalidRequestError(detail="Invalid upload ID")
            from redis import Redis

            redis = Redis.from_url(_get_redis_url(request), decode_responses=False)
            consumed = await asyncio.to_thread(
                _consume_upload, redis, upload_id, request_scope(request)
            )
            if consumed is None:
                raise NotFoundError(
                    detail="Upload not found, expired, consumed, or outside scope"
                )
            content, media_type, filename = consumed
        elif upload is not None and not isinstance(upload, str):
            content = await upload.read(MAX_DOCUMENT_BYTES + 1)
            filename = upload.filename or "document"
            media_type = upload.content_type or "application/octet-stream"
        else:
            raise InvalidRequestError(detail="Provide multipart file or upload_id")
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise InvalidRequestError(
            detail="Document exceeds 10 MiB admission limit or is empty"
        )
    filename = safe_filename(filename)
    parsed = await _parse_document(content, filename, media_type)
    ref_id, ref = await asyncio.to_thread(
        document_ref,
        session_id,
        content,
        filename,
        media_type,
        parsed,
    )
    async with _document_lock(store, session_id, request):
        refs = await store.aget_refs(session_id)
        if ref_id in refs:
            return {
                "session_id": session_id,
                "duplicate": True,
                **_metadata(ref_id, refs[ref_id]),
            }
        await asyncio.to_thread(check_document_quota, refs, ref_id, ref)
        if not await store.aadd_ref(session_id, ref_id, ref):
            raise NotFoundError(
                detail="Session expired or was deleted before admission"
            )
    return {"session_id": session_id, "duplicate": False, **_metadata(ref_id, ref)}


@router.get("/v2/session/{session_id}/documents")
async def list_session_documents(session_id: str, request: Request) -> dict[str, Any]:
    store = SessionStore(redis_url=_get_redis_url(request))
    await authorize_session(store, session_id, request)
    refs = await store.aget_refs(session_id)
    return {
        "session_id": session_id,
        "documents": [
            _metadata(key, ref)
            for key, ref in refs.items()
            if ref.get("source") == "document"
        ],
    }


@router.get("/v2/session/{session_id}/documents/{ref_id}")
async def read_session_document(
    session_id: str,
    ref_id: str,
    request: Request,
    start: int | None = Query(default=None, ge=0),
    end: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Resolve retained text or a citation's exact Unicode character span."""
    store = SessionStore(redis_url=_get_redis_url(request))
    await authorize_session(store, session_id, request)
    ref = await store.aget_ref(session_id, ref_id)
    if ref is None or ref.get("source") != "document":
        raise NotFoundError(detail="Document not attached or expired")
    text = ref["markdown"]
    if hashlib.sha256(text.encode()).hexdigest() != ref["content_digest"]:
        raise InvalidRequestError(detail="Document evidence integrity mismatch")
    begin, stop = (
        start if start is not None else 0,
        end if end is not None else len(text),
    )
    if not 0 <= begin < stop <= len(text):
        raise InvalidRequestError(detail="Invalid document character span")
    quote = text[begin:stop]
    return {
        **_metadata(ref_id, ref),
        "markdown": quote,
        "start": begin,
        "end": stop,
        "quote_digest": hashlib.sha256(quote.encode()).hexdigest(),
    }


@router.delete("/v2/session/{session_id}/documents/{ref_id}")
async def detach_session_document(
    session_id: str, ref_id: str, request: Request
) -> dict[str, Any]:
    store = SessionStore(redis_url=_get_redis_url(request))
    async with _document_lock(store, session_id, request):
        removed = await store.aremove_document(session_id, ref_id)
        if not removed:
            raise NotFoundError(detail="Document not attached or expired")
    return {"session_id": session_id, "ref_id": ref_id, "detached": True}
