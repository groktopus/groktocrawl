"""Gap detection for research coverage analysis."""

import asyncio
import json
import logging
import time

from common.stage_metrics import observe_elapsed

from ..llm import LLMClient
from .evidence import build_evidence

logger = logging.getLogger(__name__)

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
            return []

        query_context = (
            f'Original research query: "{original_query}"\n\n' if original_query else ""
        )
        selected = await asyncio.to_thread(
            build_evidence,
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
        try:
            result = await llm.generate(
                system_prompt="You are a research gap analyzer.",
                user_prompt=gap_check_prompt,
                stage="gap_detection",
            )
            cleaned = result.strip().removeprefix("```json").removesuffix("```").strip()
            gaps = json.loads(cleaned)
            if isinstance(gaps, list) and len(gaps) <= 5:
                return [g for g in gaps if isinstance(g, str)]
            return []
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Gap detection failed: %s", e)
            return []
    finally:
        observe_elapsed(_GAP_SECONDS, _GAP_SECONDS_HELP, {}, started)
