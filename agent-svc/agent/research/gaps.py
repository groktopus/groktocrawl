"""Gap detection for research coverage analysis."""

import json
import time

from common.stage_metrics import observe_elapsed

from ..llm import LLMClient, ProviderOutputError
from .evidence import build_evidence_async


class GapDetectionError(ProviderOutputError):
    """The gap analyzer did not produce a valid coverage decision."""


_GAP_SECONDS = "groktocrawl_research_gap_detection_seconds"
_GAP_SECONDS_HELP = "Research gap-detection LLM call latency"


async def _detect_gaps(
    combined_context: str,
    llm: LLMClient,
    original_query: str = "",
) -> list[str]:
    """Check if the research context has coverage gaps.

    Uses an LLM call to analyze the scraped context for topic areas
    that are not adequately covered relative to the original research query.

    Returns a list of topic strings (max 5) for missing areas, or an
    empty list if coverage is adequate.
    """
    started = time.monotonic()
    try:
        if not combined_context:
            raise GapDetectionError("Gap detection requires nonempty evidence")

        query_context = (
            f'Original research query: "{original_query}"\n\n' if original_query else ""
        )
        selected = await build_evidence_async(
            [{"id": "research-context", "markdown": combined_context}],
            original_query,
            12000,
        )
        gap_check_prompt = (
            f"{query_context}Analyze the following research context and identify specific topics, "
            "angles, or aspects of the original query that are NOT adequately covered "
            "by the gathered sources. Focus on what's missing or thin, not what's present. "
            "Return a JSON array of topic strings (max 5) that would make good follow-up search queries. "
            "Return [] if you're satisfied with coverage.\n\n"
            f"Context:\n{selected['context']}"
        )
        # Provider errors (including rate limits and cancellation) retain their
        # existing contracts. They must never become an adequate-coverage vote.
        result = await llm.generate(
            system_prompt="You are a research gap analyzer.",
            user_prompt=gap_check_prompt,
            stage="gap_detection",
        )
        try:
            cleaned = result.strip().removeprefix("```json").removesuffix("```").strip()
            gaps = json.loads(cleaned)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise GapDetectionError("Gap detection returned invalid JSON") from exc
        if (
            not isinstance(gaps, list)
            or len(gaps) > 5
            or any(not isinstance(gap, str) or not gap.strip() for gap in gaps)
        ):
            raise GapDetectionError("Gap detection returned an invalid topic list")
        return [gap.strip() for gap in gaps]

    finally:
        observe_elapsed(_GAP_SECONDS, _GAP_SECONDS_HELP, {}, started)
