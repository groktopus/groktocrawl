"""Document parsing service — PDF and office documents to markdown.

Routes files to the right parser based on extension. Office documents,
ebooks, and CSV are converted by firecrawl-anydoc; PDFs use a text-extraction
tier with an OCR fallback for scanned or image-only files.
"""

import asyncio
import contextlib
import io
import logging
import os
import threading
from pathlib import Path
from typing import Any

import anydoc
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from common.logging import setup_logging
from common.metrics import METRICS
from common.middleware import add_request_id_middleware

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="GroktoCrawl Parse Service", version="0.1.0")

# ── Instrumentation ──────────────────────────────────────────
add_request_id_middleware(app)
METRICS.counter("parse_calls_total", "Total parse requests", ["status"])

MAX_SIZE_MB = int(os.getenv("PARSE_MAX_SIZE_MB", "50"))
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024


class ParseResponse(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None


# ---- Format detection ----


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


# Document formats handled by firecrawl-anydoc, including legacy and
# macro-enabled variants. The extension is resolved to a canonical anydoc
# format (e.g. .docm -> docx, .xlsb -> xlsx) at conversion time.
ANYDOC_FORMATS = {
    "doc": "Word document",
    "docx": "Word document",
    "docm": "Word document (macro-enabled)",
    "ppt": "PowerPoint presentation",
    "pps": "PowerPoint slideshow",
    "pot": "PowerPoint template",
    "pptx": "PowerPoint presentation",
    "pptm": "PowerPoint presentation (macro-enabled)",
    "ppsx": "PowerPoint slideshow",
    "ppsm": "PowerPoint slideshow (macro-enabled)",
    "xls": "Excel workbook",
    "xlsx": "Excel workbook",
    "xlsm": "Excel workbook (macro-enabled)",
    "xlsb": "Excel binary workbook",
    "odt": "OpenDocument text",
    "ods": "OpenDocument spreadsheet",
    "odp": "OpenDocument presentation",
    "rtf": "Rich Text Format",
    "epub": "EPUB ebook",
    "csv": "CSV data",
}

# Plain-text formats passed through as-is.
TEXT_FORMATS = {
    "md": "Markdown",
    "txt": "Plain text",
    "json": "JSON data",
    "yaml": "YAML data",
    "yml": "YAML data",
    "xml": "XML data",
    "html": "HTML document",
    "htm": "HTML document",
}

SUPPORTED_FORMATS = {
    "pdf": "PDF document",
    **ANYDOC_FORMATS,
    **TEXT_FORMATS,
}


# ---- Individual parsers ----


def _parse_pdf(content: bytes, filename: str, ocr: str = "local", **options) -> dict:
    """Pagewise local extraction; hosted conversion requires explicit opt-in."""
    if ocr != "hosted":
        from parse_svc.pagewise import parse_pdf_pages

        return parse_pdf_pages(content, filename, **options)
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    texts = [page.extract_text() or "" for page in reader.pages]
    if texts and all(text.strip() for text in texts) and len("".join(texts)) > 50:
        return {
            "markdown": "\n\n".join(texts),
            "metadata": {
                "format": "pdf",
                "filename": filename,
                "pages": len(texts),
                "extraction": "pypdf",
                "coverage": {"status": "complete"},
            },
        }
    api_url = os.getenv("PARSE_HOSTED_OCR_API_URL")
    if not api_url:
        raise HTTPException(
            422, "Hosted OCR is not configured; set PARSE_HOSTED_OCR_API_URL"
        )
    if not api_url.startswith(("http://", "https://")):
        raise HTTPException(422, "Invalid hosted OCR API URL")
    api_key = os.getenv("PARSE_HOSTED_OCR_API_KEY") or os.getenv("FIRECRAWL_API_KEY")
    try:
        markdown = anydoc.to_markdown_bytes(
            content, ocr="hosted", api_url=api_url, api_key=api_key
        ).strip()
    except anydoc.HostedError as exc:
        raise HTTPException(502, "Hosted OCR failed") from exc
    except anydoc.ConvertError as exc:
        raise HTTPException(422, "Hosted OCR failed") from exc
    return {
        "markdown": markdown,
        "metadata": {
            "format": "pdf",
            "filename": filename,
            "extraction": "anydoc_hosted_ocr",
            "degraded": not bool(markdown),
            "coverage": {"status": "complete" if markdown else "failed"},
        },
    }


def _parse_text(content: bytes, filename: str) -> dict:
    """Plain text / code / markdown files."""
    text = content.decode("utf-8", errors="replace")
    metadata = {"format": _ext(filename), "filename": filename, "chars": len(text)}
    return {"markdown": text.strip(), "metadata": metadata}


def _parse_anydoc(content: bytes, filename: str) -> dict:
    """Convert an office document / ebook / CSV to markdown via firecrawl-anydoc."""
    ext = _ext(filename)
    try:
        fmt = anydoc.format_from_extension(ext)
        markdown = anydoc.to_markdown_bytes(content, format=fmt)
    except anydoc.EncryptedError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse {filename}: document is encrypted",
        ) from e
    except anydoc.ConvertError as e:
        raise HTTPException(
            status_code=422, detail=f"Failed to parse {filename}: {e}"
        ) from e
    metadata = {
        "format": ext,
        "filename": filename,
        "extraction": "anydoc",
    }
    return {"markdown": markdown.strip(), "metadata": metadata}


# ---- Router ----

PARSERS = {
    "pdf": _parse_pdf,
    **dict.fromkeys(ANYDOC_FORMATS, _parse_anydoc),
    **dict.fromkeys(TEXT_FORMATS, _parse_text),
}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    """Prometheus-compatible OpenMetrics endpoint."""
    return PlainTextResponse(
        METRICS.generate_openmetrics(),
        media_type="application/openmetrics-text; version=1.0.0",
    )


@app.post("/parse", response_model=ParseResponse)
async def parse_file(request: Request, file: UploadFile):
    """Upload a file and get its content as markdown."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = _ext(file.filename)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail=f"Could not determine file extension: {file.filename}",
        )

    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: .{ext}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        )

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File too large. Max {MAX_SIZE_MB}MB."
        )

    logger.info("Parsing %s (%s, %d bytes)", file.filename, ext, len(content))

    form = await request.form()
    query_ocr, form_ocr = request.query_params.get("ocr"), form.get("ocr")
    if query_ocr and form_ocr and query_ocr != form_ocr:
        raise HTTPException(422, "Conflicting OCR selectors")
    ocr = query_ocr or form_ocr or "local"
    force_ocr = (
        str(
            form.get("force_ocr", request.query_params.get("force_ocr", "false"))
        ).lower()
        == "true"
    )
    scope = request.headers.get("X-Parse-Scope", "")
    if scope and (len(scope) != 64 or any(c not in "0123456789abcdef" for c in scope)):
        raise HTTPException(422, "Invalid parse scope")
    if ocr not in {"local", "hosted"}:
        raise HTTPException(status_code=422, detail="ocr must be 'local' or 'hosted'")

    parser = PARSERS[ext]
    cancelled = threading.Event()

    async def watch_disconnect():
        while not cancelled.is_set():
            await asyncio.sleep(0.25)
            if await request.is_disconnected():
                cancelled.set()

    watcher = asyncio.create_task(watch_disconnect())
    try:
        if ext == "pdf":
            result = await asyncio.to_thread(
                _parse_pdf,
                content,
                file.filename,
                ocr=ocr,
                scope=scope,
                force_ocr=force_ocr,
                cancelled=cancelled,
            )
        else:
            result = await asyncio.to_thread(parser, content, file.filename)
        md = result.get("markdown", "")
        meta = result.get("metadata", {})
        meta["size_bytes"] = len(content)
        return ParseResponse(success=True, data={"markdown": md, "metadata": meta})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Parse failed for %s", file.filename)
        return ParseResponse(success=False, error=str(e))
    finally:
        cancelled.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
