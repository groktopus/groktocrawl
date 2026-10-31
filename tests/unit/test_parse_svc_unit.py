"""Unit tests for parse-svc parser functions.

Tests the extension helper, text passthrough, anydoc-driven document
conversion (including legacy/macro/ODF formats), encrypted-document errors,
and the PDF OCR fallback path.
"""

import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from parse_svc.app import (
    _coerce_confidence,
    _ext,
    _ocr_min_confidence,
    _ocr_page,
    _parse_anydoc,
    _parse_pdf,
    _parse_text,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "anydoc"


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


@contextmanager
def _hosted_parse_stub(status: int, body: dict):
    """Serve one deterministic Firecrawl Parse-compatible endpoint."""
    hits: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("content-length", "0")))
            hits.append(
                {
                    "path": self.path,
                    "body": payload,
                    "authorization": self.headers.get("authorization"),
                }
            )
            reply = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, format: str, *args: object):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _tesseract_rows(pages: list[list[list[tuple[str, float]]]]) -> ModuleType:
    """Build a fake pytesseract module with one row set per page image.

    Each page is a list of lines; each line is a list of ``(word, confidence)``
    tuples using tesseract's 0-100 scale. ``convert_from_bytes`` returns page
    indexes so the fake engine can select the matching rows.
    """
    module = ModuleType("pytesseract")
    module.Output = SimpleNamespace(DICT="dict")

    def image_to_data(image, lang=None, output_type=None):
        assert output_type == "dict"
        data: dict[str, list] = {
            "level": [],
            "block_num": [],
            "line_num": [],
            "word_num": [],
            "conf": [],
            "text": [],
        }
        for line_index, line in enumerate(pages[image]):
            for word_index, (word, confidence) in enumerate(line):
                data["level"].append(5)
                data["block_num"].append(1)
                data["line_num"].append(line_index + 1)
                data["word_num"].append(word_index + 1)
                data["conf"].append(confidence)
                data["text"].append(word)
        return data

    module.image_to_data = image_to_data
    return module


def _fake_pdf2image(pages: int = 1) -> ModuleType:
    module = ModuleType("pdf2image")
    module.convert_from_bytes = lambda *args, **kwargs: list(range(pages))
    return module


def _patch_ocr(pages: list[list[list[tuple[str, float]]]]) -> object:
    return patch.dict(
        sys.modules,
        {
            "pdf2image": _fake_pdf2image(len(pages)),
            "pytesseract": _tesseract_rows(pages),
        },
    )


def _line(text: str, confidence: float) -> list[tuple[str, float]]:
    return [(word, confidence) for word in text.split()]


# ── _ext ──────────────────────────────────────────────────────────────────


class TestExt:
    """Extension extraction from various filenames."""

    def test_simple_extension(self):
        assert _ext("report.pdf") == "pdf"

    def test_multiple_dots(self):
        assert _ext("archive.tar.gz") == "gz"

    def test_no_extension(self):
        assert _ext("README") == ""

    def test_uppercase_extension(self):
        assert _ext("Document.PDF") == "pdf"

    def test_hidden_file(self):
        assert _ext(".gitignore") == ""

    def test_path_with_dirs(self):
        assert _ext("/path/to/file.txt") == "txt"

    def test_empty_string(self):
        assert _ext("") == ""


# ── _parse_text ────────────────────────────────────────────────────────────


class TestParseText:
    """Plain text / code / markdown file parsing."""

    def test_simple_text(self):
        result = _parse_text(b"Hello, world!", "test.txt")
        assert result["markdown"] == "Hello, world!"
        assert result["metadata"]["format"] == "txt"
        assert result["metadata"]["filename"] == "test.txt"

    def test_markdown_content(self):
        md = "# Title\n\nThis is **bold** text."
        result = _parse_text(md.encode(), "doc.md")
        assert "# Title" in result["markdown"]
        assert result["metadata"]["format"] == "md"

    def test_unicode_text(self):
        text = "Hello, 世界! ñoño 😀"
        result = _parse_text(text.encode("utf-8"), "unicode.txt")
        assert "世界" in result["markdown"]

    def test_large_text(self):
        text = "Line\n" * 10000
        result = _parse_text(text.encode(), "large.txt")
        assert len(result["markdown"]) > 1000
        assert result["metadata"]["chars"] == len(text)

    def test_empty_text(self):
        result = _parse_text(b"", "empty.txt")
        assert result["markdown"] == ""


# ── _parse_anydoc ──────────────────────────────────────────────────────────


class TestParseAnyDoc:
    """firecrawl-anydoc conversion for legacy, ODF, RTF, EPUB and macros."""

    # (fixture filename, expected metadata format)
    FORMATS = [
        ("text.doc", "doc"),
        ("text.docx", "docx"),
        ("text.docm", "docm"),
        ("text.odt", "odt"),
        ("text.rtf", "rtf"),
        ("book.epub", "epub"),
        ("sheet.ods", "ods"),
        ("sheet.xls", "xls"),
        ("sheet.xlsx", "xlsx"),
        ("sheet.xlsm", "xlsm"),
        ("sheet.xlsb", "xlsb"),
        ("pres.odp", "odp"),
        ("pres.ppt", "ppt"),
        ("pres.pptx", "pptx"),
        ("pres.pptm", "pptm"),
    ]

    @pytest.mark.parametrize("filename,ext", FORMATS)
    def test_converts_to_non_empty_markdown(self, filename, ext):
        result = _parse_anydoc(_load(filename), filename)
        assert result["markdown"].strip()
        assert result["metadata"]["format"] == ext
        assert result["metadata"]["extraction"] == "anydoc"

    def test_doc_contains_fixture_text(self):
        result = _parse_anydoc(_load("text.doc"), "text.doc")
        assert "Fixture Document" in result["markdown"]

    def test_ods_produces_table(self):
        result = _parse_anydoc(_load("sheet.ods"), "sheet.ods")
        assert "|" in result["markdown"]
        assert "Kind" in result["markdown"]

    def test_epub_contains_book_text(self):
        result = _parse_anydoc(_load("book.epub"), "book.epub")
        assert "Fixture Book" in result["markdown"]

    def test_csv_converts_to_table(self):
        result = _parse_anydoc(_load("fixture-sheet.csv"), "fixture-sheet.csv")
        assert result["markdown"].strip()
        assert result["metadata"]["format"] == "csv"

    def test_encrypted_document_raises_clear_error(self):
        with pytest.raises(HTTPException) as exc:
            _parse_anydoc(_load("encrypted--errors.odt"), "encrypted--errors.odt")
        assert "encrypted" in exc.value.detail.lower()

    def test_malformed_content_raises(self):
        with pytest.raises(HTTPException):
            _parse_anydoc(b"this is not a real office document", "bad.odt")


# ── _parse_pdf ──────────────────────────────────────────────────────────────


class TestParsePdf:
    """PDF parsing with local-first and explicit hosted OCR behavior."""

    def _make_minimal_pdf(self):
        """Return a minimal valid PDF (no extractable text)."""
        return (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\n"
            b"startxref\n190\n%%EOF"
        )

    def test_pypdf_extraction_path(self):
        content = self._make_minimal_pdf()
        result = _parse_pdf(content, "test.pdf")
        assert result["metadata"]["format"] == "pdf"
        assert result["metadata"]["filename"] == "test.pdf"
        assert "pages" in result["metadata"]

    def test_text_pdf_extracts_content(self):
        result = _parse_pdf(_load("fixture-text.pdf"), "fixture-text.pdf")
        assert result["metadata"]["format"] == "pdf"
        assert "Fixture Document" in result["markdown"]

    def test_ocr_import_failure_handled_gracefully(self):
        # pytesseract/pdf2image may be absent; OCR must degrade without crashing.
        content = self._make_minimal_pdf()
        result = _parse_pdf(content, "test.pdf")
        assert "markdown" in result
        assert "metadata" in result
        assert result["metadata"]["format"] == "pdf"

    def test_scanned_pdf_falls_through_to_ocr(self):
        # Simulate a scanned/image-only PDF: pypdf extracts no text, so the
        # OCR tier must run (tesseract/pdf2image patched in-process).
        text = "This is OCR-extracted text from a scanned page. " * 3
        pages = [[_line(text, 92.5)]]
        content = _load("scanned-image-only.pdf")
        with _patch_ocr(pages):
            result = _parse_pdf(content, "scan.pdf")
        assert result["metadata"].get("extraction") == "local_ocr"
        assert "OCR-extracted" in result["markdown"]
        assert result["metadata"]["ocr"]["quality"] == "ok"
        assert result["metadata"]["ocr"]["mean_confidence"] == 0.925
        assert result["metadata"]["ocr"]["words"] == 24
        assert result["metadata"].get("degraded") is None
        assert "warnings" not in result["metadata"]

    def test_low_confidence_ocr_is_flagged_but_text_is_preserved(self):
        pages = [
            [
                _line("ASCIT formnat Sor Network Interchange", 31.0),
                _line("garbled scan output remains", 34.0),
            ]
        ]
        with _patch_ocr(pages):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        assert "ASCIT formnat" in result["markdown"]
        assert result["metadata"]["extraction"] == "local_ocr"
        assert result["metadata"]["ocr"]["quality"] == "low"
        assert result["metadata"]["ocr"]["threshold"] == 0.60
        assert result["metadata"]["ocr"]["low_confidence_pages"] == [1]
        assert result["metadata"]["degraded"] is True
        assert result["metadata"]["warnings"] == ["low_ocr_confidence"]

    def test_low_confidence_page_is_reported_by_number(self):
        pages = [
            [_line("clearly readable first page text", 95.0)],
            [_line("badly degraded second page text", 25.0)],
        ]
        with _patch_ocr(pages):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        ocr = result["metadata"]["ocr"]
        assert ocr["pages_processed"] == 2
        assert ocr["low_confidence_pages"] == [2]
        assert ocr["min_page_confidence"] == 0.25
        assert ocr["mean_confidence"] == pytest.approx(0.6, abs=0.001)
        assert result["metadata"].get("degraded") is None

    def test_confidence_threshold_env_override(self, monkeypatch):
        monkeypatch.setenv("PARSE_OCR_MIN_CONFIDENCE", "0.95")
        pages = [[_line("usually acceptable scan output text", 90.0)]]
        with _patch_ocr(pages):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        assert result["metadata"]["ocr"]["threshold"] == 0.95
        assert result["metadata"]["ocr"]["quality"] == "low"
        assert result["metadata"]["degraded"] is True

    def test_invalid_confidence_threshold_falls_back_to_default(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("PARSE_OCR_MIN_CONFIDENCE", "not-a-number")
        assert _ocr_min_confidence() == 0.60
        monkeypatch.setenv("PARSE_OCR_MIN_CONFIDENCE", "2.0")
        assert _ocr_min_confidence() == 0.60
        assert "PARSE_OCR_MIN_CONFIDENCE" in caplog.text

    def test_ocr_without_confidence_samples_is_not_called_clean(self):
        pages = [[[("x" * 60, -1.0)]]]
        with _patch_ocr(pages):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        assert result["metadata"]["ocr"]["quality"] == "unassessed"
        assert result["metadata"]["degraded"] is True
        assert result["metadata"]["warnings"] == ["ocr_confidence_unassessed"]

    def test_empty_ocr_output_is_marked_degraded(self):
        with _patch_ocr([[]]):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        assert result["metadata"]["ocr"]["quality"] == "empty"
        assert result["metadata"]["degraded"] is True
        assert "no_text_extracted" in result["metadata"]["warnings"]
        assert result["metadata"].get("extraction") != "local_ocr"

    def test_missing_ocr_engine_is_reported_unavailable(self):
        with patch.dict(
            sys.modules,
            {"pdf2image": _fake_pdf2image(1), "pytesseract": None},
        ):
            result = _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf")

        assert result["metadata"]["ocr"]["quality"] == "unavailable"
        assert result["metadata"]["degraded"] is True
        assert "no_text_extracted" in result["metadata"]["warnings"]

    def test_ocr_page_reconstructs_lines_and_ignores_unscored_rows(self):
        fake = _tesseract_rows(
            [[[("hello", 80.0), ("world", 60.0)], [("", -1.0)], [("second", 100.0)]]]
        )
        with patch.dict(sys.modules, {"pytesseract": fake}):
            text, mean, words = _ocr_page(0)

        assert text == "hello world\nsecond"
        assert mean == pytest.approx(0.8)
        assert words == 3

    def test_coerce_confidence_rejects_out_of_range_values(self):
        assert _coerce_confidence(95) == 0.95
        assert _coerce_confidence("62.5") == 0.625
        assert _coerce_confidence(-1) is None
        assert _coerce_confidence(101) is None
        assert _coerce_confidence("n/a") is None

    def test_hosted_ocr_uses_real_anydoc_binding_and_sends_whole_pdf(self, monkeypatch):
        content = _load("scanned-image-only.pdf")
        reply = {"success": True, "data": {"markdown": "# Hosted scan\n"}}
        with _hosted_parse_stub(200, reply) as (api_url, hits):
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_URL", api_url)
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_KEY", "test-only-api-key")

            result = _parse_pdf(content, "scan.pdf", ocr="hosted")

        assert result["markdown"] == "# Hosted scan"
        assert result["metadata"]["extraction"] == "anydoc_hosted_ocr"
        assert result["metadata"].get("degraded") is None
        assert "warnings" not in result["metadata"]
        assert len(hits) == 1
        assert hits[0]["path"] == "/v2/parse"
        assert hits[0]["authorization"] == "Bearer test-only-api-key"
        payload = hits[0]["body"]
        assert isinstance(payload, bytes)
        assert content in payload

    def test_hosted_request_keeps_text_pdf_local(self, monkeypatch):
        reply = {"success": True, "data": {"markdown": "must not be used"}}
        with _hosted_parse_stub(200, reply) as (api_url, hits):
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_URL", api_url)

            result = _parse_pdf(
                _load("fixture-text.pdf"), "fixture-text.pdf", ocr="hosted"
            )

        assert "Fixture Document" in result["markdown"]
        assert result["metadata"]["extraction"] == "pypdf"
        assert hits == []

    def test_hosted_ocr_requires_explicit_endpoint(self, monkeypatch):
        monkeypatch.delenv("PARSE_HOSTED_OCR_API_URL", raising=False)
        monkeypatch.delenv("PARSE_HOSTED_OCR_API_URL", raising=False)
        with pytest.raises(HTTPException) as exc:
            _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf", ocr="hosted")
        assert exc.value.status_code == 422
        assert "not configured" in exc.value.detail.lower()

    def test_hosted_ocr_rejects_invalid_endpoint(self, monkeypatch):
        monkeypatch.setenv("PARSE_HOSTED_OCR_API_URL", "file:///tmp/not-http")
        with pytest.raises(HTTPException) as exc:
            _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf", ocr="hosted")
        assert exc.value.status_code == 422
        assert "invalid" in exc.value.detail.lower()

    def test_hosted_failure_redacts_credentials(self, monkeypatch, caplog):
        credential = "test-only-api-key"
        reply = {
            "success": False,
            "error": f"provider rejected {credential}",
        }
        with _hosted_parse_stub(401, reply) as (api_url, _hits):
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_URL", api_url)
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_KEY", credential)

            with pytest.raises(HTTPException) as exc:
                _parse_pdf(_load("scanned-image-only.pdf"), "scan.pdf", ocr="hosted")

        assert exc.value.status_code == 502
        assert credential not in exc.value.detail
        assert credential not in caplog.text

    def test_default_local_mode_never_hits_hosted_endpoint(self, monkeypatch):
        content = _load("scanned-image-only.pdf")
        pages = [[_line("local text " * 20, 28.0)]]
        reply = {"success": True, "data": {"markdown": "must not be used"}}

        with _hosted_parse_stub(200, reply) as (api_url, hits):
            monkeypatch.setenv("PARSE_HOSTED_OCR_API_URL", api_url)
            with _patch_ocr(pages):
                result = _parse_pdf(content, "scan.pdf")

        assert result["metadata"]["extraction"] == "local_ocr"
        assert result["metadata"]["degraded"] is True
        assert hits == []

    def test_corrupt_pdf_graceful(self):
        result = _parse_pdf(b"not a pdf at all", "bad.pdf")
        assert "markdown" in result
        assert "metadata" in result
