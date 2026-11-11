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

from .checkpoints import CheckpointError, Checkpoints, DeadlineExceededError
from .settings import PARSER_REVISION, OcrSettings, positive_env


def native_page(source: Path, number: int, timeout: float) -> dict:
    if timeout <= 0:
        raise DeadlineExceededError("document_deadline")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "parse_svc.native_worker", str(source), str(number)],
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise DeadlineExceededError("native_extraction_timeout") from exc
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise CheckpointError("native_pdf_invalid_or_output_limit") from exc


def usable_native(text: str) -> bool:
    """A page-local heuristic, not a promise that embedded chart text was captured."""
    if not text or "\ufffd" in text:
        return False
    printable = sum(char.isprintable() or char.isspace() for char in text)
    return sum(char.isalnum() for char in text) >= 40 and printable / len(text) >= 0.98


def render_page(content: bytes, number: int, settings: OcrSettings, deadline: float):
    from PIL import Image

    remaining = min(settings.page_timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise DeadlineExceededError("document_deadline")
    # Invoke Poppler directly: pdf2image has an untimed pdfinfo preflight.
    try:
        process = subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(number),
                "-l",
                str(number),
                "-singlefile",
                "-r",
                "150",
                "-scale-to",
                str(settings.render_edge),
                "-png",
                "-",
            ],
            input=content,
            capture_output=True,
            timeout=remaining,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeadlineExceededError("render_timeout") from exc
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise CheckpointError("renderer_unavailable_or_failed") from exc
    image = Image.open(io.BytesIO(process.stdout))
    if (
        max(image.size) > settings.render_edge
        or image.width * image.height > settings.render_edge**2
    ):
        raise CheckpointError("render_dimensions_exceeded")
    image.load()
    return image


def blank_image(image: Any) -> bool:
    """Only a near-white raster proves blankness; empty model text does not."""
    return image.convert("L").getextrema()[0] >= 250


def tesseract_page(
    image: Any, timeout: float, lock_path: Path
) -> tuple[str, float | None, int]:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    # Supervisor enforces its own absolute deadline, kills/reaps Tesseract and
    # retains the global lock independently of this request process lifetime.
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "parse_svc.ocr_worker",
            str(lock_path),
            str(time.monotonic() + timeout),
        ],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    )
    text, confidence, words = json.loads(process.stdout)
    return text, confidence, words


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
    while True:
        remaining = min(settings.page_timeout, deadline - time.monotonic())
        if remaining <= 0 or cancelled.is_set():
            raise DeadlineExceededError(
                "document_deadline" if not cancelled.is_set() else "caller_disconnected"
            )
        ordinal = store.begin_attempt(execution, number, settings.attempts)
        try:
            text, confidence, words = tesseract_page(
                image,
                remaining,
                store.root / "ocr.lock"
                if os.getenv("PARSE_STATE_DIR")
                else Path(tempfile.gettempdir()) / "groktocrawl-ocr.lock",
            )
            store.finish_attempt(execution, number, ordinal, "completed")
            break
        except (subprocess.SubprocessError, OSError, ValueError):
            store.finish_attempt(execution, number, ordinal, "failed")
            if ordinal >= settings.attempts:
                raise CheckpointError("tesseract_failed") from None
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
        "page_results": [
            {
                key: record[key]
                for key in (
                    "page_number",
                    "status",
                    "engine",
                    "confidence",
                    "markdown_start",
                    "markdown_end",
                    "warnings",
                    "cached",
                    "attempts",
                    "tables_found",
                )
                if key in record
            }
            for record in records
        ],
        "extraction_version": "pagewise-v1",
        "checkpoint_persistent": persistent,
        "budget_seconds": budget,
        "force_ocr": force_ocr,
    }
    settings = OcrSettings.load()
    metadata["extraction_policy"] = {
        "parser_revision": PARSER_REVISION,
        "render_max_edge": settings.render_edge,
        "render_dpi": 150,
        "ocr_engine": "tesseract",
    }
    # Keep downstream bounded metadata usable; all receipts remain in the ledger.
    kept = []
    size = 0
    for receipt in metadata["page_results"]:
        size += len(json.dumps(receipt).encode())
        if size > 48 * 1024:
            break
        kept.append(receipt)
    metadata["page_results_omitted"] = len(records) - len(kept)
    metadata["page_results"] = kept
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
    started = time.monotonic()
    persistent_root = os.getenv("PARSE_STATE_DIR")
    source_hash = hashlib.sha256(content).hexdigest()
    scope = scope or uuid.uuid4().hex
    cancelled = cancelled or threading.Event()
    with tempfile.TemporaryDirectory(prefix="parse-") as temporary:
        source_path = Path(temporary) / "source.pdf"
        source_path.write_bytes(content)
        count = native_page(source_path, 0, min(30, settings.document_max))["pages"]
        if not 0 < count <= positive_env("PARSE_MAX_PAGES", 1024):
            raise CheckpointError("pdf_page_limit_exceeded")
        budget = min(
            settings.document_max,
            30 + count * settings.page_timeout * settings.attempts,
        )
        deadline = started + budget
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
            for index in range(count):
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
                native = ""
                try:
                    native = native_page(
                        source_path,
                        number,
                        min(settings.page_timeout, deadline - time.monotonic()),
                    )["text"]
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
                # Sparse native values remain evidence even when too short to
                # qualify as a complete page. Never replace useful OCR output.
                if (
                    record["status"] in {"failed", "truncated", "skipped"}
                    and native.strip()
                ):
                    native = native.strip()
                    if native not in record["text"]:
                        if not record["text"].strip():
                            record["engine"] = "native"
                        record["text"] += (
                            "\n\n### Partial native text\n\n" if record["text"] else ""
                        ) + native
                        record["warnings"].append("native_text_incomplete")
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
