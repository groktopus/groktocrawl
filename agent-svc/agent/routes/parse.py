"""Parse route handlers — file upload and content extraction."""

import asyncio
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request

from ..exceptions import InvalidRequestError, NotFoundError, UpstreamError
from ..models import ParseResponse, ParseUploadUrlResponse
from ..session_scope import request_scope
from ._helpers import _get_redis_url

logger = logging.getLogger(__name__)

router = APIRouter()

PARSE_SVC_URL = "http://parse-svc:8013"
PARSE_UPLOAD_TTL = 3 * 60 * 60  # 3 hours, matches parse-svc/config.py
PARSE_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Lua script: atomically get and delete the upload data.
# Prevents race conditions where two concurrent parse requests
# with the same upload_id both retrieve and process the file.
_ATOMIC_GETDEL_SCRIPT = """
if #KEYS > 4 then
    local owner = redis.call('GET', KEYS[5]) or 'anonymous'
    if owner ~= ARGV[1] then return nil end
end
local data = redis.call('GET', KEYS[1])
if data then
    local content_type = redis.call('GET', KEYS[2])
    local filename = redis.call('GET', KEYS[3])
    redis.call('DEL', unpack(KEYS))
    return {data, content_type or false, filename or false}
end
return nil
"""


def _stage_upload(
    r: Any, upload_id: str, scope: str, body: bytes, content_type: str, filename: str
) -> bool:
    """Owner and reservation liveness are checked in the same write transaction."""
    script = """
    if not redis.call('GET', KEYS[1]) then return 0 end
    local owner = redis.call('GET', KEYS[5]) or 'anonymous'
    if owner ~= ARGV[1] then return 0 end
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[5])
    redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[5])
    redis.call('SET', KEYS[4], ARGV[4], 'EX', ARGV[5])
    redis.call('SET', KEYS[5], owner, 'EX', ARGV[5])
    redis.call('SET', KEYS[1], 'uploaded', 'EX', ARGV[5])
    return 1
    """
    prefix = f"parse:upload:{upload_id}"
    return bool(
        r.eval(
            script,
            5,
            prefix,
            prefix + ":data",
            prefix + ":content_type",
            prefix + ":filename",
            prefix + ":owner",
            scope,
            body,
            content_type,
            filename,
            PARSE_UPLOAD_TTL,
        )
    )


def _consume_upload(
    r: Any, upload_id: str, scope_id: str | None = None
) -> tuple[bytes, str, str] | None:
    """Atomically consume staged bytes and the metadata needed to parse them."""
    keys = [
        f"parse:upload:{upload_id}:data",
        f"parse:upload:{upload_id}:content_type",
        f"parse:upload:{upload_id}:filename",
        f"parse:upload:{upload_id}",
    ]
    if scope_id is not None:
        keys.append(f"parse:upload:{upload_id}:owner")
        consumed = r.register_script(_ATOMIC_GETDEL_SCRIPT)(keys=keys, args=[scope_id])
    else:
        consumed = r.register_script(_ATOMIC_GETDEL_SCRIPT)(keys=keys)
    if not consumed:
        return None

    content, content_type_raw, filename_raw = consumed
    content_type = (
        content_type_raw.decode()
        if isinstance(content_type_raw, bytes)
        else "application/octet-stream"
    )
    filename = (
        filename_raw.decode() if isinstance(filename_raw, bytes) else "uploaded_file"
    )
    return content, content_type, filename


def _parse_upstream_response(resp: httpx.Response) -> Any:
    """Return a successful Parse payload or raise a stable upstream error."""
    try:
        payload = resp.json()
    except Exception as exc:
        raise UpstreamError(
            detail="Parse service returned invalid response",
            details={"status_code": resp.status_code},
        ) from exc
    if resp.status_code >= 400:
        raise UpstreamError(
            detail=str(payload.get("detail", "Parse service request failed")),
            details={"status_code": resp.status_code},
        )
    return payload


@router.post("/v2/parse/upload-url", response_model=ParseUploadUrlResponse)
async def request_parse_upload_url(request: Request) -> ParseUploadUrlResponse:
    """Reserve a bounded, single-use upload slot for staged parsing."""
    from redis import Redis

    upload_id = str(uuid.uuid4())
    r = Redis.from_url(_get_redis_url(request), decode_responses=False)
    await asyncio.to_thread(
        r.set, f"parse:upload:{upload_id}", b"pending", ex=PARSE_UPLOAD_TTL
    )
    await asyncio.to_thread(
        r.set,
        f"parse:upload:{upload_id}:owner",
        request_scope(request).encode(),
        ex=PARSE_UPLOAD_TTL,
    )
    return ParseUploadUrlResponse(
        upload_id=upload_id,
        upload_url=f"{request.url.scheme}://{request.url.netloc}/v2/parse/upload/{upload_id}",
    )


@router.put("/v2/parse/upload/{upload_id}")
async def upload_parse_file(upload_id: str, request: Request) -> dict[str, Any]:
    """Upload file bytes for a previously requested upload_id.

    Stores the raw bytes, content-type, and filename in Valkey.
    The content-type is read from the ``Content-Type`` request header.
    The filename is read from the ``X-Filename`` request header.
    """
    from redis import Redis

    r = Redis.from_url(_get_redis_url(request), decode_responses=False)
    meta = await asyncio.to_thread(r.get, f"parse:upload:{upload_id}")
    owner = await asyncio.to_thread(r.get, f"parse:upload:{upload_id}:owner")
    owner_text = owner.decode() if isinstance(owner, bytes) else owner or "anonymous"
    if meta is None or owner_text != request_scope(request):
        raise NotFoundError(
            detail="Upload ID not found or expired",
            details={"upload_id": upload_id},
        )

    parts = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > PARSE_MAX_UPLOAD_BYTES:
            raise InvalidRequestError(detail="Upload exceeds 50 MiB limit")
        parts.append(chunk)
    raw_body = b"".join(parts)
    if not raw_body:
        raise InvalidRequestError(detail="Empty body — no file data received")

    content_type = request.headers.get("Content-Type", "application/octet-stream")
    filename = request.headers.get("X-Filename", "uploaded_file")

    if not await asyncio.to_thread(
        _stage_upload,
        r,
        upload_id,
        request_scope(request),
        raw_body,
        content_type,
        filename,
    ):
        raise NotFoundError(detail="Upload reservation expired or outside scope")

    return {"status": "uploaded", "upload_id": upload_id}


@router.post("/v2/parse", response_model=ParseResponse)
async def parse_file(request: Request) -> Any:
    """Upload a file and get its content as markdown.

    Supports two modes:

    - Direct: multipart form with ``file`` field (small files)
    - Two-step: form field ``upload_id`` referencing a pre-uploaded file

    ``ocr=hosted`` is an explicit opt-in and is forwarded only when the caller
    requests hosted OCR. The parse service remains local-first by default.
    """
    form = await request.form()
    form_ocr = form.get("ocr", "local")
    ocr = form_ocr if isinstance(form_ocr, str) else "local"
    if ocr not in {"local", "hosted"}:
        raise InvalidRequestError(detail="ocr must be 'local' or 'hosted'")

    # Two-step mode: retrieve pre-uploaded file from Valkey
    upload_id_raw = form.get("upload_id")
    upload_id_str = upload_id_raw if isinstance(upload_id_raw, str) else None
    if upload_id_str:
        from redis import Redis

        r = Redis.from_url(_get_redis_url(request), decode_responses=False)
        consumed = await asyncio.to_thread(
            _consume_upload, r, upload_id_str, request_scope(request)
        )
        if consumed is None:
            raise InvalidRequestError(
                detail="Upload data not found or expired",
                details={"upload_id": upload_id_str},
            )
        content, content_type, filename = consumed

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{PARSE_SVC_URL}/parse",
                files={"file": (filename, content, content_type)},
                data={"ocr": ocr},
            )
            return _parse_upstream_response(resp)

    # Direct mode: file in multipart form
    if "file" not in form:
        raise InvalidRequestError(
            detail="No file provided. Use multipart form with 'file' field."
        )

    upload = form["file"]  # type: ignore[union-attr]
    content = await upload.read()  # type: ignore[union-attr]
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{PARSE_SVC_URL}/parse",
            files={
                "file": (
                    upload.filename or "file",  # type: ignore[union-attr]
                    content,
                    upload.content_type or "application/octet-stream",  # type: ignore[union-attr]
                )
            },
            data={"ocr": ocr},
        )
        return _parse_upstream_response(resp)
