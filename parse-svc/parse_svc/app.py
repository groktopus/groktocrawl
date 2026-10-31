"""Document parsing service — PDF and office documents to markdown.

Routes files to the right parser based on extension. Office documents,
ebooks, and CSV are converted by firecrawl-anydoc; PDFs use a text-extraction
tier with an OCR fallback for scanned or image-only files.
"""

import io
import logging
import os
from pathlib import Path
from typing import Any

import anydoc
from fastapi import FastAPI, HTTPException, UploadFile
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


# ---- OCR quality ----

DEFAULT_OCR_MIN_CONFIDENCE = 0.60
OCR_ENGINE = "tesseract"


def _ocr_min_confidence() -> float:
    """Return the effective low-confidence threshold on the 0.0-1.0 scale.

    Invalid configuration falls back to the documented default instead of
    failing a parse; the misconfiguration is logged for operators.
    """
    raw = os.getenv("PARSE_OCR_MIN_CONFIDENCE", "").strip()
    if not raw:
        return DEFAULT_OCR_MIN_CONFIDENCE
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid PARSE_OCR_MIN_CONFIDENCE=%r; using default", raw)
        return DEFAULT_OCR_MIN_CONFIDENCE
    if not 0.0 <= value <= 1.0:
        logger.warning(
            "PARSE_OCR_MIN_CONFIDENCE=%r outside 0.0-1.0; using default", raw
        )
        return DEFAULT_OCR_MIN_CONFIDENCE
    return value


def _coerce_confidence(value: Any) -> float | None:
    """Normalize one tesseract confidence to 0.0-1.0, or None if unusable."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 100.0:
        return None
    return confidence / 100.0


def _ocr_page(image: Any) -> tuple[str, float | None, int]:
    """Run one tesseract pass and return (text, mean confidence, word count).

    ``image_to_data`` yields word text and confidence from the same tesseract
    invocation; reconstructing lines avoids a second OCR pass purely to obtain
    confidence. Rows with no usable confidence contribute text but no sample.
    """
    import pytesseract

    data = pytesseract.image_to_data(
        image, lang="eng", output_type=pytesseract.Output.DICT
    )
    if not isinstance(data, dict):
        return "", None, 0
    texts = data.get("text", [])
    confidences = data.get("conf", [])
    line_numbers = data.get("line_num", [])
    block_numbers = data.get("block_num", [])

    lines: list[str] = []
    current: list[str] = []
    current_key: tuple[Any, Any] | None = None
    measured: list[float] = []
    for index, raw_text in enumerate(texts):
        word = str(raw_text).strip()
        if not word:
            continue
        key = (
            block_numbers[index] if index < len(block_numbers) else 0,
            line_numbers[index] if index < len(line_numbers) else 0,
        )
        if current_key is not None and key != current_key:
            lines.append(" ".join(current))
            current = []
        current.append(word)
        current_key = key
        confidence = _coerce_confidence(
            confidences[index] if index < len(confidences) else None
        )
        if confidence is not None:
            measured.append(confidence)
    if current:
        lines.append(" ".join(current))

    mean = sum(measured) / len(measured) if measured else None
    return "\n".join(lines), mean, len(measured)


# ---- Individual parsers ----


def _parse_pdf(content: bytes, filename: str, ocr: str = "local") -> dict:
    """Parse PDF locally first, with optional explicit hosted OCR."""
    markdown_parts = []
    metadata: dict[str, Any] = {"format": "pdf", "filename": filename}
    text_extracted = False

    # Tier 1: pypdf text extraction
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        metadata["pages"] = len(reader.pages)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
        full_text = "\n\n".join(pages_text).strip()
        if len(full_text) > 50:
            markdown_parts.append(full_text)
            text_extracted = True
            metadata["extraction"] = "pypdf"
    except Exception as e:
        logger.warning("pypdf failed: %s", e)

    # Tier 2: local OCR is the default; hosted mode is deliberately separate.
    ocr_info: dict[str, Any] | None = None
    if ocr != "hosted" and (not text_extracted or len("".join(markdown_parts)) < 100):
        threshold = _ocr_min_confidence()
        try:
            from pdf2image import convert_from_bytes

            images = convert_from_bytes(
                content,
                dpi=300,
                first_page=1,
                last_page=min(10, metadata.get("pages", 10)),
            )
            ocr_parts = []
            page_confidences: list[float | None] = []
            word_count = 0
            confidence_total = 0.0
            confidence_samples = 0
            for i, img in enumerate(images):
                text, mean_confidence, words = _ocr_page(img)
                ocr_parts.append(f"--- Page {i + 1} ---\n\n{text}")
                page_confidences.append(mean_confidence)
                word_count += words
                if mean_confidence is not None and words:
                    confidence_total += mean_confidence * words
                    confidence_samples += words
            ocr_text = "\n\n".join(ocr_parts).strip()
            measured = [value for value in page_confidences if value is not None]
            mean_confidence = (
                round(confidence_total / confidence_samples, 4)
                if confidence_samples
                else None
            )
            low_pages = [
                index + 1
                for index, value in enumerate(page_confidences)
                if value is not None and value < threshold
            ]
            if mean_confidence is None:
                quality = "unassessed"
            elif mean_confidence < threshold:
                quality = "low"
            else:
                quality = "ok"
            ocr_info = {
                "engine": OCR_ENGINE,
                "pages_processed": len(images),
                "words": word_count,
                "mean_confidence": mean_confidence,
                "min_page_confidence": round(min(measured), 4) if measured else None,
                "low_confidence_pages": low_pages,
                "threshold": threshold,
                "quality": quality,
            }
            if len(ocr_text) > 50:
                markdown_parts = [ocr_text]
                metadata["extraction"] = "local_ocr"
                text_extracted = True
                if quality == "low":
                    metadata["degraded"] = True
                    metadata.setdefault("warnings", []).append("low_ocr_confidence")
                elif quality == "unassessed":
                    metadata["degraded"] = True
                    metadata.setdefault("warnings", []).append(
                        "ocr_confidence_unassessed"
                    )
            else:
                ocr_info["quality"] = "empty"
        except ImportError:
            logger.warning("pytesseract/pdf2image not available, skipping OCR")
            ocr_info = {
                "engine": OCR_ENGINE,
                "threshold": threshold,
                "quality": "unavailable",
            }
        except Exception as e:
            logger.warning("OCR failed: %s", e)
            ocr_info = {
                "engine": OCR_ENGINE,
                "threshold": threshold,
                "quality": "unavailable",
            }

    # Tier 3: Table extraction for PDFs with tables
    try:
        import camelot

        tables = camelot.read_pdf(io.BytesIO(content), pages="all", flavor="lattice")
        if len(tables) > 0:
            from tabulate import tabulate

            table_md_parts = []
            for _i, table in enumerate(tables):
                md = tabulate(table.df, headers="keys", tablefmt="github")
                table_md_parts.append(md)
            if table_md_parts:
                markdown_parts.append(
                    "\n\n### Extracted Tables\n\n" + "\n\n".join(table_md_parts)
                )
                metadata["tables_found"] = len(tables)
    except ImportError:
        logger.debug("camelot not available, skipping table extraction")
    except Exception as e:
        logger.debug("Table extraction failed: %s", e)

    markdown = "\n\n".join(markdown_parts).strip()
    if ocr_info is not None:
        metadata["ocr"] = ocr_info
    if ocr == "hosted" and not text_extracted:
        api_url = os.getenv("PARSE_HOSTED_OCR_API_URL")
        if not api_url:
            raise HTTPException(
                status_code=422,
                detail="Hosted OCR is not configured; set PARSE_HOSTED_OCR_API_URL",
            )
        if not api_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="Invalid hosted OCR API URL")
        try:
            api_key = os.getenv("PARSE_HOSTED_OCR_API_KEY") or os.getenv(
                "FIRECRAWL_API_KEY"
            )
            kwargs = {"ocr": "hosted", "api_url": api_url}
            if api_key:
                kwargs["api_key"] = api_key
            markdown = anydoc.to_markdown_bytes(content, **kwargs).strip()
            metadata["extraction"] = "anydoc_hosted_ocr"
        except anydoc.HostedError as e:
            raise HTTPException(status_code=502, detail="Hosted OCR failed") from e
        except anydoc.ConvertError as e:
            raise HTTPException(
                status_code=422, detail=f"Hosted OCR failed: {e}"
            ) from e
    if not markdown:
        # The caller must not infer a clean parse from an empty document.
        metadata["degraded"] = True
        warnings = metadata.setdefault("warnings", [])
        if "no_text_extracted" not in warnings:
            warnings.append("no_text_extracted")
    return {"markdown": markdown, "metadata": metadata}


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
async def parse_file(file: UploadFile, ocr: str = "local"):
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

    if ocr not in {"local", "hosted"}:
        raise HTTPException(status_code=422, detail="ocr must be 'local' or 'hosted'")

    parser = PARSERS[ext]
    try:
        if ext == "pdf":
            result = _parse_pdf(content, file.filename, ocr=ocr)
        else:
            result = parser(content, file.filename)
        md = result.get("markdown", "")
        meta = result.get("metadata", {})
        meta["size_bytes"] = len(content)
        return ParseResponse(success=True, data={"markdown": md, "metadata": meta})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Parse failed for %s", file.filename)
        return ParseResponse(success=False, error=str(e))
