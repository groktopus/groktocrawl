"""Bounded synchronous PDF extraction with scope-isolated page receipts."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .checkpoints import CheckpointError, Checkpoints, DeadlineExceededError
from .vision_ocr import OcrSettings, positive_env, transcribe


def usable_native(text: str) -> bool:
    """A page-local heuristic, not a promise that embedded chart text was captured."""
    if not text or "\ufffd" in text:
        return False
    printable = sum(char.isprintable() or char.isspace() for char in text)
    return sum(char.isalnum() for char in text) >= 40 and printable / len(text) >= 0.98


def render_page(content: bytes, number: int, settings: OcrSettings, deadline: float):
    from pdf2image import convert_from_bytes

    remaining = min(settings.page_timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise DeadlineExceededError("document_deadline")
    images = convert_from_bytes(
        content,
        dpi=150,
        first_page=number,
        last_page=number,
        size=settings.render_edge,
        timeout=remaining,
        thread_count=1,
    )
    if len(images) != 1:
        raise CheckpointError("renderer_page_count_mismatch")
    image = images[0]
    if (
        max(image.size) > settings.render_edge
        or image.width * image.height > settings.render_edge**2
    ):
        raise CheckpointError("render_dimensions_exceeded")
    return image


def blank_image(image: Any) -> bool:
    """Only a near-white raster proves blankness; empty model text does not."""
    return image.convert("L").getextrema()[0] >= 250


def tesseract_page(image: Any, timeout: float) -> tuple[str, float | None, int]:
    import pytesseract

    data = pytesseract.image_to_data(
        image, lang="eng", output_type=pytesseract.Output.DICT, timeout=timeout
    )
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences = []
    for i, word in enumerate(data.get("text", [])):
        if not str(word).strip():
            continue
        key = tuple(
            (data.get(field) or [0] * len(data["text"]))[i]
            for field in ("block_num", "par_num", "line_num")
        )
        lines.setdefault(key, []).append(str(word))
        try:
            confidence = float(data["conf"][i])
            if 0 <= confidence <= 100:
                confidences.append(confidence / 100)
        except (ValueError, TypeError, KeyError, IndexError):
            pass
    text = "\n".join(" ".join(words) for words in lines.values())
    return (
        text,
        sum(confidences) / len(confidences) if confidences else None,
        len(confidences),
    )


def _failed(provenance: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        **provenance,
        "status": "failed",
        "engine": "none",
        "text": "",
        "warnings": [reason],
        "completed_at": time.time(),
        "attempts": 0,
    }


def _extract_page(
    content: bytes,
    native: str,
    number: int,
    settings: OcrSettings,
    store: Checkpoints,
    execution: str,
    deadline: float,
    provenance: dict[str, Any],
    force_ocr: bool,
    cancelled: threading.Event,
) -> dict[str, Any]:
    if not force_ocr and usable_native(native):
        return {
            **provenance,
            "status": "complete",
            "engine": "native",
            "text": native.strip(),
            "warnings": [],
            "attempts": 0,
            "completed_at": time.time(),
        }
    image = render_page(content, number, settings, deadline)
    provenance.update(
        {"render_width": image.width, "render_height": image.height, "render_dpi": 150}
    )
    if blank_image(image):
        return {
            **provenance,
            "status": "blank",
            "engine": "native",
            "text": "",
            "warnings": [],
            "attempts": 0,
            "completed_at": time.time(),
            "blank_verification": "render_all_pixels_at_least_250",
        }
    warnings: list[str] = []
    if cancelled.is_set():
        raise DeadlineExceededError("caller_disconnected")
    if settings.backend == "gemma":
        try:
            return transcribe(
                image, settings, store, execution, number, deadline, provenance
            )
        except CheckpointError as exc:
            warnings = [str(exc), "tesseract_fallback"]
    remaining = min(settings.page_timeout, deadline - time.monotonic())
    if remaining <= 0 or cancelled.is_set():
        raise DeadlineExceededError(
            "document_deadline" if not cancelled.is_set() else "caller_disconnected"
        )
    text, confidence, words = tesseract_page(image, remaining)
    quality = (
        "empty"
        if not text.strip()
        else "unassessed"
        if confidence is None
        else "low"
        if confidence < settings.min_confidence
        else "ok"
    )
    if quality != "ok":
        warnings.append(
            {
                "empty": "no_text_extracted",
                "unassessed": "ocr_confidence_unassessed",
                "low": "low_ocr_confidence",
            }[quality]
        )
    return {
        **provenance,
        "status": "complete" if quality == "ok" else "failed",
        "engine": "tesseract",
        "text": text,
        "warnings": warnings,
        "confidence": confidence,
        "words": words,
        "quality": quality,
        "attempts": len(store.attempts(execution, number)),
        "completed_at": time.time(),
    }


def _assemble(
    records: list[dict[str, Any]],
    filename: str,
    source_hash: str,
    persistent: bool,
    budget: float,
    force_ocr: bool,
) -> dict[str, Any]:
    markdown_parts = []
    offset = 0
    for record in records:
        part = f"--- Page {record['page_number']} ---\n\n{record['text']}"
        record["markdown_start"] = offset
        record["markdown_end"] = offset + len(part)
        markdown_parts.append(part)
        offset += len(part) + 2
    markdown = "\n\n".join(markdown_parts)
    engines = Counter(record["engine"] for record in records)
    statuses = Counter(record["status"] for record in records)
    completed = statuses["complete"] + statuses["blank"]
    warnings = sorted({warning for record in records for warning in record["warnings"]})
    coverage = {
        "status": "complete"
        if completed == len(records)
        else "partial"
        if completed
        else "failed",
        "total_pages": len(records),
        "completed_pages": completed,
        "failed_pages": statuses["failed"] + statuses["truncated"],
        "skipped_pages": statuses["skipped"],
        "blank_pages": statuses["blank"],
        "native_pages": engines["native"],
        "gemma_pages": engines["gemma"],
        "tesseract_pages": engines["tesseract"],
        "cached_pages": sum(bool(r.get("cached")) for r in records),
    }
    metadata: dict[str, Any] = {
        "format": "pdf",
        "filename": filename,
        "pages": len(records),
        "source_sha256": source_hash,
        "extraction_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
        "extraction": "pagewise",
        "coverage": coverage,
        "page_results": records,
        "checkpoint_persistent": persistent,
        "budget_seconds": budget,
        "force_ocr": force_ocr,
    }
    if coverage["status"] != "complete" or warnings:
        metadata["degraded"] = True
        metadata["warnings"] = warnings or ["incomplete_page_coverage"]
    return {"markdown": markdown, "metadata": metadata}


def parse_pdf_pages(
    content: bytes,
    filename: str,
    scope: str = "",
    force_ocr: bool = False,
    cancelled: threading.Event | None = None,
) -> dict[str, Any]:
    """Resume only committed compatible pages; explicit resubmission starts a window."""
    settings = OcrSettings.load()
    if settings.backend == "gemma":
        settings.validate_model()
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise CheckpointError("encrypted_pdf")
    count = len(reader.pages)
    if not count:
        raise CheckpointError("empty_pdf")
    budget = min(
        settings.document_max, 30 + count * settings.page_timeout * settings.attempts
    )
    deadline = time.monotonic() + budget
    persistent_root = os.getenv("PARSE_STATE_DIR")
    source_hash = hashlib.sha256(content).hexdigest()
    scope = scope or uuid.uuid4().hex
    cancelled = cancelled or threading.Event()
    with tempfile.TemporaryDirectory(prefix="parse-") as temporary:
        store = Checkpoints(
            Path(persistent_root or temporary),
            positive_env("PARSE_STATE_MAX_BYTES", 1073741824),
            positive_env("PARSE_STATE_TTL_SECONDS", 604800),
        )
        policy = settings.fingerprint(force_ocr)
        execution = store.execution_id(scope, source_hash, policy)
        store.cleanup()
        records = []
        with store.execution_lock(execution, deadline):
            store.admit(execution, scope, content, policy, count)
            for index, page in enumerate(reader.pages):
                number = index + 1
                provenance = {
                    "page_number": number,
                    "source_sha256": source_hash,
                    "extraction_version": "pagewise-v1",
                }
                cached = store.page(execution, number)
                if cached and cached["status"] in {"complete", "blank"}:
                    records.append({**cached, "cached": True})
                    continue
                if cancelled.is_set() or time.monotonic() >= deadline:
                    records.append(
                        {
                            **_failed(
                                provenance,
                                "caller_disconnected"
                                if cancelled.is_set()
                                else "document_deadline",
                            ),
                            "status": "skipped",
                        }
                    )
                    continue
                try:
                    native = page.extract_text() or ""
                    record = _extract_page(
                        content,
                        native,
                        number,
                        settings,
                        store,
                        execution,
                        deadline,
                        provenance,
                        force_ocr,
                        cancelled,
                    )
                except DeadlineExceededError as exc:
                    record = {**_failed(provenance, str(exc)), "status": "skipped"}
                except Exception as exc:
                    # Deliberately bounded classification, never provider bodies/secrets.
                    reason = (
                        str(exc)
                        if isinstance(exc, CheckpointError)
                        else "page_extraction_failed"
                    )
                    record = _failed(provenance, reason)
                if (
                    os.getenv("PARSE_EXTRACT_TABLES", "false").lower() == "true"
                    and record["status"] == "complete"
                ):
                    remaining = min(settings.page_timeout, deadline - time.monotonic())
                    if remaining > 0 and not cancelled.is_set():
                        try:
                            with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
                                source.write(content)
                                source.flush()
                                process = subprocess.run(
                                    [
                                        sys.executable,
                                        "-m",
                                        "parse_svc.tables",
                                        source.name,
                                        str(number),
                                    ],
                                    capture_output=True,
                                    timeout=remaining,
                                    check=True,
                                )
                            tables = json.loads(process.stdout)
                            if tables:
                                record["text"] += (
                                    "\n\n### Extracted Tables\n\n" + "\n\n".join(tables)
                                )
                                record["table_engine"] = "camelot"
                                record["tables_found"] = len(tables)
                        except (subprocess.SubprocessError, ValueError, OSError):
                            record["warnings"].append(
                                "optional_table_extraction_unavailable"
                            )
                store.save_page(execution, number, record)
                records.append(record)
        return _assemble(
            records, filename, source_hash, bool(persistent_root), budget, force_ocr
        )
