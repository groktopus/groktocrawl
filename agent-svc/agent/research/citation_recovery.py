"""Bounded citation correction for completed legacy Markdown synthesis.

Candidate text is never published before completion, token parity and citation
validation. Correction reuses the original evidence; it is not a job retry.
"""

from collections.abc import AsyncGenerator
from typing import Any

from ..cancel import raise_if_cancelled
from ..exceptions import CitationValidationError
from ..llm import LLMClient
from ..metrics import METRICS
from .citations import _apply_citation_style


def initialize_citation_metrics() -> None:
    """Expose bounded zero samples before traffic, without resetting counters."""
    validation = METRICS.counter(
        "groktocrawl_citation_validation_total",
        "Citation validation outcomes by requested style",
        ["style", "outcome"],
    )
    recovery = METRICS.counter(
        "groktocrawl_citation_recovery_total",
        "Bounded citation correction outcomes by synthesis stage",
        ["stage", "outcome"],
    )
    for style in ("inline", "compact"):
        for outcome in ("valid", "normalized", "invalid"):
            validation.inc({"style": style, "outcome": outcome}, 0)
    for stage in ("answer", "synthesis"):
        for outcome in ("attempted", "succeeded", "exhausted"):
            recovery.inc({"stage": stage, "outcome": outcome}, 0)


def _record_recovery(stage: str, outcome: str) -> None:
    METRICS.counter(
        "groktocrawl_citation_recovery_total",
        "Bounded citation correction outcomes by synthesis stage",
        ["stage", "outcome"],
    ).inc({"stage": stage, "outcome": outcome})


def _correction_prompt(original: str, source_count: int) -> str:
    # Do not copy the rejected output, URLs, or raw diagnostics into instructions.
    return (
        original
        + "\n\nYour previous completed answer had invalid citation formatting. "
        "Write a replacement answer using the SAME supplied evidence and question. "
        f"Only source numbers 1 through {source_count} exist. "
        "Use separate canonical markers [1], [2], etc.; do not use ranges, "
        "leading zeroes, or invented source numbers. Cite a source only when it "
        "supports the claim. Do not invent or remap sources. If the evidence "
        "does not support an answer, say so. Return only the replacement answer."
    )


async def generate_cited_answer(
    llm: LLMClient,
    *,
    system_prompt: str,
    user_prompt: str,
    context: str,
    source_map: list[dict[str, Any]],
    citation_style: Any,
    stage: str,
) -> tuple[str, list[dict]]:
    """Generate once, correcting one completed citation-invalid answer at most."""
    sources = [dict(source) for source in source_map]
    original_prompt = user_prompt
    for attempt in range(2):
        raise_if_cancelled()
        answer = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context=context,
            stage=stage,
        )
        try:
            result = _apply_citation_style(answer, sources, citation_style)
        except CitationValidationError:
            _record_recovery(stage, "exhausted" if attempt else "attempted")
            if attempt:
                raise
            user_prompt = _correction_prompt(original_prompt, len(sources))
        else:
            if attempt:
                _record_recovery(stage, "succeeded")
            return result
    raise AssertionError("bounded citation correction did not terminate")


async def stream_cited_answer(
    llm: LLMClient,
    *,
    system_prompt: str,
    user_prompt: str,
    context: str,
    source_map: list[dict[str, Any]],
    citation_style: Any,
    stage: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Publish only a validated attempt, preserving provider error metadata."""
    sources = [dict(source) for source in source_map]
    original_prompt = user_prompt
    for attempt in range(2):
        raise_if_cancelled()
        raw_parts: list[str] = []
        full_content = None
        completed = False
        async for event in llm.generate_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context=context,
            stage=stage,
        ):
            if event["type"] == "error":
                yield event
                return
            if completed:
                raise CitationValidationError("LLM stream continued after completion")
            if event["type"] == "token":
                content = event.get("content")
                if not isinstance(content, str):
                    raise CitationValidationError("LLM token stream was invalid")
                raw_parts.append(content)
            elif event["type"] == "done":
                full_content = event.get("full_content")
                completed = True
        # Stream-integrity failures never qualify for correction, even if an
        # earlier token contained an invalid marker. The paid outcome is unclear.
        if not completed or not isinstance(full_content, str):
            raise CitationValidationError("LLM stream ended without a final completion")
        if full_content != "".join(raw_parts):
            raise CitationValidationError(
                "LLM token stream did not match its final completion"
            )
        try:
            answer, citations = _apply_citation_style(
                full_content, sources, citation_style
            )
        except CitationValidationError:
            _record_recovery(stage, "exhausted" if attempt else "attempted")
            if attempt:
                raise
            user_prompt = _correction_prompt(original_prompt, len(sources))
        else:
            if attempt:
                _record_recovery(stage, "succeeded")
            raise_if_cancelled()
            if answer:
                yield {"type": "token", "content": answer}
            yield {"type": "done", "full_content": answer, "citations": citations}
            return
