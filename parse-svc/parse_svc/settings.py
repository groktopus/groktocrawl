"""CPU PDF extraction policy and cache identity."""

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.metadata import version

from .checkpoints import CheckpointError
from .process import bounded_run

PARSER_REVISION = "cpu-pagewise-v2-poppler150-tesseract-tsv-eng"


@lru_cache(maxsize=1)
def engine_identity():
    versions = {}
    for name, args in (
        ("tesseract", ["tesseract", "--version"]),
        ("poppler", ["pdftoppm", "-v"]),
    ):
        try:
            result = bounded_run(args, timeout=2, output_limit=65536)
            versions[name] = hashlib.sha256(result.stdout + result.stderr).hexdigest()
        except (OSError, subprocess.SubprocessError):
            versions[name] = "unavailable"
    return versions


def positive_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise CheckpointError(f"invalid_{name.lower()}") from exc
    if value <= 0:
        raise CheckpointError(f"invalid_{name.lower()}")
    return value


@dataclass(frozen=True)
class OcrSettings:
    page_timeout: int
    document_max: int
    attempts: int
    render_edge: int
    min_confidence: float

    @classmethod
    def load(cls):
        try:
            retries = int(os.getenv("PARSE_PAGE_RETRIES", "1"))
        except ValueError as exc:
            raise CheckpointError("invalid_page_retries") from exc
        if not 0 <= retries <= 5:
            raise CheckpointError("invalid_page_retries")
        try:
            threshold = float(os.getenv("PARSE_OCR_MIN_CONFIDENCE", "0.60"))
        except ValueError:
            threshold = 0.60
        if not 0 <= threshold <= 1:
            threshold = 0.60
        return cls(
            positive_env("PARSE_PAGE_TIMEOUT_SECONDS", 30),
            positive_env("PARSE_DOCUMENT_MAX_SECONDS", 1800),
            retries + 1,
            positive_env("PARSE_RENDER_MAX_EDGE", 2000),
            threshold,
        )

    def fingerprint(self, force_ocr: bool):
        return json.dumps(
            {
                **asdict(self),
                "parser_revision": PARSER_REVISION,
                "pypdf": version("pypdf"),
                "engines": engine_identity(),
                "force_ocr": force_ocr,
                "tables": os.getenv("PARSE_EXTRACT_TABLES", "false").lower() == "true",
            },
            sort_keys=True,
        )
