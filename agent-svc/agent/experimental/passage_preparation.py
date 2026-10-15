"""Deterministic application-owned passage preparation for ADR-0080."""

from dataclasses import dataclass

from .answer_units import SourcePassage
from .knowledge import text_digest

MAX_SOURCES = 8
MAX_PASSAGES = 32
MAX_PASSAGE_CHARACTERS = 4_000
MAX_SOURCE_BYTES = 120_000


@dataclass(frozen=True)
class RetainedSourceText:
    snapshot_id: str
    text: str


class EvidenceAdmissionLimitError(ValueError):
    """Classified construction bound with an explicit selective recovery path."""

    def __init__(self, message: str, limit: str) -> None:
        super().__init__(message)
        self.code = "EVIDENCE_ADMISSION_LIMIT"
        self.limit = limit
        self.recovery = {
            "action": "prepare_query_passages",
            "narrow_sources": True,
            "budget_chars": 32_000,
            "query_required": True,
        }


def prepare_source_passages(
    sources: tuple[RetainedSourceText, ...],
) -> tuple[SourcePassage, ...]:
    """Split exact retained text without normalization, ranking, or truncation."""

    if not 1 <= len(sources) <= MAX_SOURCES:
        raise EvidenceAdmissionLimitError(
            "invalid retained source count", "source_count"
        )
    if len({source.snapshot_id for source in sources}) != len(sources):
        raise ValueError("retained snapshot identities must be distinct")
    if any(not source.snapshot_id or not source.text for source in sources):
        raise ValueError("retained source identity and text are required")
    if sum(len(source.text.encode()) for source in sources) > MAX_SOURCE_BYTES:
        raise EvidenceAdmissionLimitError(
            "retained source byte budget exceeded", "construction_bytes"
        )

    passages: list[SourcePassage] = []
    for source_index, source in enumerate(sources, 1):
        for start in range(0, len(source.text), MAX_PASSAGE_CHARACTERS):
            end = min(start + MAX_PASSAGE_CHARACTERS, len(source.text))
            quote = source.text[start:end]
            passages.append(
                SourcePassage(
                    passage_id=f"passage-{source_index}-{len(passages) + 1}",
                    snapshot_id=source.snapshot_id,
                    start=start,
                    end=end,
                    quote=quote,
                    quote_digest=text_digest(quote),
                )
            )
            if len(passages) > MAX_PASSAGES:
                raise EvidenceAdmissionLimitError(
                    "retained sources exceed passage-count budget", "passage_count"
                )
    return tuple(passages)


def prepare_query_passages(
    sources: tuple[RetainedSourceText, ...], query: str, budget_chars: int = 32_000
) -> tuple[tuple[SourcePassage, ...], dict]:
    """Select exact passages from already authorized retained snapshots.

    This does not acquire, publish, normalize or alter retained text. Retention
    admission remains the resolver/store's responsibility; construction limits
    apply only to the selected model context.
    """
    from ..research.evidence import build_evidence

    if not 1 <= len(sources) <= MAX_SOURCES:
        raise EvidenceAdmissionLimitError(
            "invalid retained source count", "source_count"
        )
    if len({source.snapshot_id for source in sources}) != len(sources):
        raise ValueError("retained snapshot identities must be distinct")
    if not query.strip() or any(
        not source.snapshot_id or not source.text for source in sources
    ):
        raise ValueError("a query, retained identity and text are required")
    selected = build_evidence(
        [
            {"id": s.snapshot_id, "snapshot_id": s.snapshot_id, "markdown": s.text}
            for s in sources
        ],
        query,
        budget_chars,
    )
    by_id = {s.snapshot_id: s.text for s in sources}
    passages = tuple(
        SourcePassage(
            passage_id=span["passage_id"],
            snapshot_id=source["snapshot_id"],
            start=span["start"],
            end=span["end"],
            quote=by_id[source["id"]][span["start"] : span["end"]],
            quote_digest=span["quote_digest"],
        )
        for source in selected["coverage"]["sources"]
        for span in source["spans"]
    )
    if sum(len(p.quote.encode("utf-8")) for p in passages) > MAX_SOURCE_BYTES:
        raise EvidenceAdmissionLimitError(
            "selected source byte budget exceeded; lower the context budget",
            "construction_bytes",
        )
    if len(passages) > MAX_PASSAGES:
        raise EvidenceAdmissionLimitError(
            "selected sources exceed passage-count budget; narrow sources or lower the context budget",
            "passage_count",
        )
    return passages, selected["coverage"]
