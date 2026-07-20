"""Best-effort admission of completed research into Research Memory."""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def admit_research_memory(
    research_memory: Any,
    *,
    prompt: str,
    artifact: str,
    source_details: list[dict[str, Any]] | list[str],
    model: str,
    citation_style: str,
    requested_model: str | None = None,
    latency_ms: int = 0,
    user_id: str | None = None,
) -> str | None:
    """Store a valid final artifact, treating unavailable memory as non-fatal."""
    if (
        research_memory is None
        or not artifact
        or artifact.startswith("Error:")
        or not source_details
    ):
        return None

    memory_scope = os.environ.get("RESEARCH_MEMORY_SCOPE", "global")
    if memory_scope == "per_user" and user_id is None:
        user_id = "anonymous"
    metadata: dict[str, Any] = {
        "model": model,
        "citation_style": citation_style,
        "latency_ms": latency_ms,
    }
    if requested_model and requested_model != "default":
        metadata["requested_model"] = requested_model

    try:
        artifact_id = await research_memory.store(
            prompt=prompt,
            artifact=artifact,
            sources=source_details,
            model=model,
            user_id=user_id if memory_scope == "per_user" else None,
            metadata=metadata,
        )
        logger.info(
            "Stored research memory artifact %s (scope=%s)", artifact_id, memory_scope
        )
        return artifact_id
    except Exception:
        logger.warning(
            "Failed to store research memory (service may be down)", exc_info=True
        )
        return None
