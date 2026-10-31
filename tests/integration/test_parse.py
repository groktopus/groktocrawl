"""Tests for parse-svc — document parsing endpoints.

Covers all supported formats, macro variants, OCR/encrypted error handling,
the unchanged /parse response shape, and health/metrics.
"""

import io
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from parse_svc.app import MAX_SIZE_MB, app

from tests.outcome_governance import governed_skip

client = TestClient(app)

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "anydoc"


def _require_real_ocr() -> None:
    """Skip the real-engine checks where tesseract is not installed."""
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        governed_skip(
            "real OCR dependencies (pytesseract/pdf2image) are not installed",
            owner="repository-maintainer",
            issue="#689",
            classification="retained",
            environment="parse-svc image or a host with pytesseract and pdf2image",
        )
    if shutil.which("tesseract") is None:
        governed_skip(
            "the tesseract binary is not installed",
            owner="repository-maintainer",
            issue="#689",
            classification="retained",
            environment="parse-svc image or a host with the tesseract binary",
        )


@contextmanager
def _ocr_stub(pages: list[list[list[tuple[str, float]]]]):
    """Replace tesseract with deterministic word/confidence rows."""
    pdf2image = ModuleType("pdf2image")
    pdf2image.convert_from_bytes = lambda *args, **kwargs: list(range(len(pages)))
    pytesseract = ModuleType("pytesseract")
    pytesseract.Output = SimpleNamespace(DICT="dict")

    def image_to_data(image, lang=None, output_type=None):
        assert output_type == "dict"
        rows: dict[str, list] = {
            "block_num": [],
            "line_num": [],
            "conf": [],
            "text": [],
        }
        for line_index, line in enumerate(pages[image]):
            for word, confidence in line:
                rows["block_num"].append(1)
                rows["line_num"].append(line_index + 1)
                rows["conf"].append(confidence)
                rows["text"].append(word)
        return rows

    pytesseract.image_to_data = image_to_data
    with patch.dict(sys.modules, {"pdf2image": pdf2image, "pytesseract": pytesseract}):
        yield


def _line(text: str, confidence: float) -> list[tuple[str, float]]:
    return [(word, confidence) for word in text.split()]


def _upload(name: str, content_type: str = "application/octet-stream"):
    data = (_FIXTURES / name).read_bytes()
    return client.post("/parse", files={"file": (name, io.BytesIO(data), content_type)})


# ── Health ──────────────────────────────────────────────────────────────────


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ── Metrics ─────────────────────────────────────────────────────────────────


def test_metrics():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "openmetrics-text" in resp.headers["content-type"]
    body = resp.text
    assert "# HELP" in body
    assert "# TYPE" in body
    assert "# EOF" in body.strip()


# ── Error cases ─────────────────────────────────────────────────────────────


def test_unsupported_format():
    resp = client.post(
        "/parse",
        files={"file": ("test.xyz", io.BytesIO(b"hello"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Unsupported format" in detail


def test_file_too_large():
    large_content = b"x" * (MAX_SIZE_MB * 1024 * 1024 + 1)
    resp = client.post(
        "/parse",
        files={"file": ("large.pdf", io.BytesIO(large_content), "application/pdf")},
    )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


def test_no_filename():
    resp = client.post(
        "/parse",
        files={"file": ("", io.BytesIO(b"content"), "text/plain")},
    )
    assert resp.status_code in (400, 422)


def test_no_extension():
    resp = client.post(
        "/parse",
        files={"file": ("README", io.BytesIO(b"content"), "text/plain")},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "extension" in detail.lower()


# ── Response shape ──────────────────────────────────────────────────────────


def test_response_shape():
    """The success response keeps the {success, data:{markdown, metadata}, error} shape."""
    resp = client.post(
        "/parse",
        files={"file": ("hello.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"success", "data", "error"}
    assert data["success"] is True
    assert data["error"] is None
    assert set(data["data"].keys()) == {"markdown", "metadata"}
    assert isinstance(data["data"]["markdown"], str)
    assert isinstance(data["data"]["metadata"], dict)


# ── anydoc document formats ─────────────────────────────────────────────────


# (fixture filename, expected metadata format)
_ANYDOC_FORMATS = [
    ("text.doc", "doc"),
    ("text.docx", "docx"),
    ("text.odt", "odt"),
    ("sheet.ods", "ods"),
    ("pres.odp", "odp"),
    ("text.rtf", "rtf"),
    ("book.epub", "epub"),
    ("sheet.xls", "xls"),
    ("sheet.xlsx", "xlsx"),
    ("pres.ppt", "ppt"),
    ("pres.pptx", "pptx"),
]


@pytest.mark.parametrize("filename,ext", _ANYDOC_FORMATS)
def test_legacy_and_odf_formats(filename, ext):
    resp = _upload(filename)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["markdown"].strip()
    assert data["data"]["metadata"]["format"] == ext


@pytest.mark.parametrize(
    "filename,ext",
    [
        ("text.docm", "docm"),
        ("pres.pptm", "pptm"),
        ("sheet.xlsm", "xlsm"),
        ("sheet.xlsb", "xlsb"),
    ],
)
def test_macro_variants(filename, ext):
    """Macro-enabled variants are accepted (200) and convert to markdown.

    Genuine macro-enabled binary files (.xlsb/.docm/.pptm) are not available
    offline, so these fixtures are real base-format files renamed to the macro
    extension. They verify the /parse allow-list accepts the extension and that
    it dispatches to a working converter (anydoc canonicalizes .docm->docx,
    .xlsm/.xlsb->xlsx, .pptm->pptx) without returning a 400.
    """
    resp = _upload(filename)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["markdown"].strip()
    assert data["data"]["metadata"]["format"] == ext


def test_encrypted_document_returns_clear_error():
    resp = _upload("encrypted--errors.odt")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "encrypted" in detail.lower()


# ── PDF ─────────────────────────────────────────────────────────────────────


def test_pdf_parsing():
    resp = _upload("fixture-text.pdf", "application/pdf")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["metadata"]["format"] == "pdf"
    assert "Fixture Document" in data["data"]["markdown"]


def test_scanned_pdf_not_raw_noise():
    """Scanned/image-only PDF does not return a hard unsupported error."""
    resp = _upload("scanned-image-only.pdf", "application/pdf")
    # OCR is optional in the local test env; the endpoint must succeed without
    # returning raw binary/noise. A missing OCR tier is itself a degraded signal.
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["metadata"]["format"] == "pdf"
    if data["data"]["metadata"].get("ocr", {}).get("quality") == "unavailable":
        assert data["data"]["metadata"]["degraded"] is True


def test_low_confidence_ocr_response_keeps_envelope_and_flags_degraded():
    """The HTTP contract marks low-confidence OCR without changing its shape."""
    pages = [[_line("ASCIT formnat Sor Network Interchange", 31.0)]]
    with _ocr_stub(pages):
        resp = _upload("scanned-image-only.pdf", "application/pdf")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert set(body["data"].keys()) == {"markdown", "metadata"}
    assert body["success"] is True
    assert "ASCIT formnat" in body["data"]["markdown"]
    metadata = body["data"]["metadata"]
    assert metadata["extraction"] == "local_ocr"
    assert metadata["ocr"]["quality"] == "low"
    assert metadata["degraded"] is True
    assert metadata["warnings"] == ["low_ocr_confidence"]


def test_scanned_readable_fixture_has_expected_ocr_text():
    """The committed readable scan yields the expected text under real OCR."""
    _require_real_ocr()
    resp = _upload("scanned-readable.pdf", "application/pdf")
    assert resp.status_code == 200
    metadata = resp.json()["data"]["metadata"]
    assert metadata["extraction"] == "local_ocr"
    assert "ASCII format for Network Interchange" in resp.json()["data"]["markdown"]
    assert metadata["ocr"]["quality"] == "ok"
    assert metadata.get("degraded") is None


def test_scanned_degraded_fixture_is_flagged():
    """The deliberately degraded scan must not present as a clean parse."""
    _require_real_ocr()
    resp = _upload("scanned-degraded.pdf", "application/pdf")
    assert resp.status_code == 200
    metadata = resp.json()["data"]["metadata"]
    assert metadata["ocr"]["quality"] != "ok"
    assert metadata["degraded"] is True


# ── CSV ─────────────────────────────────────────────────────────────────────


def test_csv_parsing():
    resp = _upload("fixture-sheet.csv", "text/csv")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["metadata"]["format"] == "csv"
    assert data["data"]["markdown"].strip()


# ── Text formats ────────────────────────────────────────────────────────────


def test_txt_parsing():
    resp = client.post(
        "/parse",
        files={"file": ("test.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "hello world" in data["data"]["markdown"]


def test_markdown_parsing():
    md_content = b"# Title\n\nThis is **bold** and *italic*."
    resp = client.post(
        "/parse",
        files={"file": ("doc.md", io.BytesIO(md_content), "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["metadata"]["format"] == "md"
    assert "# Title" in data["data"]["markdown"]


@pytest.mark.parametrize(
    "filename,content",
    [
        ("data.json", b'{"name": "test", "value": 42}'),
        ("config.yaml", b"name: test\nversion: 1.0\n"),
        ("data.xml", b"<root><item>value</item></root>"),
        ("page.html", b"<html><body><h1>Title</h1><p>Content</p></body></html>"),
    ],
)
def test_other_text_formats(filename, content):
    resp = client.post(
        "/parse",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["markdown"].strip()
