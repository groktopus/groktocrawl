"""Parsed-file admission into existing bounded, expiring session evidence refs."""

import hashlib
import json
import re
from typing import Any

from .exceptions import InvalidRequestError, UpstreamError

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_SESSION_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_SESSION_DOCUMENTS = 20
MAX_EXTRACTION_METADATA_BYTES = 64 * 1024
MAX_EXTRACTION_ANCHORS = 1024


def safe_filename(filename: str) -> str:
    """Retain a display name, never a local path or control characters."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 240 or any(ord(char) < 32 for char in name):
        raise InvalidRequestError(detail="Invalid document filename")
    return name


def extraction_anchors(markdown: str) -> list[dict[str, Any]]:
    """Expose exact Markdown headings/page markers; never infer PDF page numbers."""
    matches = list(
        re.finditer(r"(?m)^(?:#{1,6} [^\n]+|--- Page \d+ ---)[ \t]*$", markdown)
    )
    if len(matches) > MAX_EXTRACTION_ANCHORS:
        raise InvalidRequestError(
            detail="Document exceeds 1024 extraction-anchor limit"
        )
    anchors = []
    for index, match in enumerate(matches):
        label = match.group().strip()
        anchors.append(
            {
                "kind": "page" if label.startswith("--- Page ") else "section",
                "label": label,
                "start": match.start(),
                "end": matches[index + 1].start()
                if index + 1 < len(matches)
                else len(markdown),
            }
        )
    return anchors


def document_ref(
    session_id: str,
    content: bytes,
    filename: str,
    media_type: str,
    parsed: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Admit complete parser text, preserving binary and extracted-text identities."""
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise InvalidRequestError(
            detail="Document exceeds 10 MiB admission limit or is empty"
        )
    data = parsed.get("data")
    if parsed.get("success") is not True or not isinstance(data, dict):
        raise UpstreamError(detail="Document extraction failed")
    markdown = data.get("markdown")
    metadata = data.get("metadata", {})
    if not isinstance(markdown, str) or not markdown.strip():
        raise UpstreamError(detail="Document extraction produced no usable text")
    if len(markdown.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise InvalidRequestError(
            detail="Extracted document exceeds 10 MiB admission limit"
        )
    if (
        not isinstance(metadata, dict)
        or len(json.dumps(metadata).encode()) > MAX_EXTRACTION_METADATA_BYTES
    ):
        raise UpstreamError(detail="Document extraction metadata exceeds its bound")
    name = safe_filename(filename)
    anchors = extraction_anchors(markdown)
    if len(json.dumps(anchors).encode()) > MAX_EXTRACTION_METADATA_BYTES:
        raise InvalidRequestError(
            detail="Document anchor metadata exceeds 64 KiB limit"
        )
    file_digest = hashlib.sha256(content).hexdigest()
    ref_id = "doc_" + file_digest
    content_digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    # Never retain private paths returned by a parser in the filename field.
    metadata = {**metadata, "filename": name}
    return ref_id, {
        "source": "document",
        "snapshot_id": f"doc:{file_digest}:{content_digest}",
        "url": f"/v2/session/{session_id}/documents/{ref_id}",
        "title": name,
        "filename": name,
        "media_type": media_type,
        "media_type_source": "upload-header",
        "markdown": markdown,
        "char_count": len(markdown),
        "content_digest": content_digest,
        "file_digest": file_digest,
        "extraction": metadata,
        "anchors": anchors,
        "normalization": "parser-markdown-exact/1",
    }


def check_document_quota(refs: dict[str, dict], ref_id: str, ref: dict) -> None:
    documents = {
        key: value for key, value in refs.items() if value.get("source") == "document"
    }
    documents[ref_id] = ref
    if (
        len(documents) > MAX_SESSION_DOCUMENTS
        or sum(
            len(value.get("markdown", "").encode("utf-8"))
            for value in documents.values()
        )
        > MAX_SESSION_DOCUMENT_BYTES
    ):
        raise InvalidRequestError(
            detail="Session document quota exceeded (20 documents / 32 MiB)"
        )
