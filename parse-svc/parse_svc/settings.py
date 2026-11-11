"""CPU PDF extraction policy and cache identity."""

import json
import os
from dataclasses import asdict, dataclass
from importlib.metadata import version

from .checkpoints import CheckpointError

PARSER_REVISION = "cpu-pagewise-v2-poppler150-tesseract-tsv-eng"


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
            positive_env("PARSE_RENDER_MAX_EDGE", 2400),
            threshold,
        )

    def fingerprint(self, force_ocr: bool):
        return json.dumps(
            {
                **asdict(self),
                "parser_revision": PARSER_REVISION,
                "pypdf": version("pypdf"),
                "force_ocr": force_ocr,
                "tables": os.getenv("PARSE_EXTRACT_TABLES", "false").lower() == "true",
            },
            sort_keys=True,
        )
