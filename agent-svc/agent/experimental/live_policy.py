"""Admission-versioned live execution bounds; older work keeps its deadline."""

CURRENT_LIVE_POLICY = "durable-live/5"


def execution_timeout_seconds(policy_version: str | None) -> int:
    """Select the immutable wall-clock allowance for an admitted policy."""
    return 300 if policy_version == "durable-live/5" else 180
