"""Shared policy and receipts for bounded barrier recovery.

The scraper service owns this contract so every caller gets the same recovery
behavior without having to know which countermeasure applies to a challenge.
"""

from __future__ import annotations

from copy import deepcopy


def _barrier_details(result: dict | None) -> dict:
    barrier = (result or {}).get("barrier") or {}
    return barrier if isinstance(barrier, dict) else {}


def new_recovery_receipt(
    result: dict | None, *, initial_strategy: str = "playwright"
) -> dict:
    """Create a sanitized receipt from the first browser result."""
    barrier = _barrier_details(result)
    first_attempt: dict = {
        "strategy": initial_strategy,
        "outcome": "barrier" if barrier else "no_content",
    }
    captcha_attempts = barrier.get("attempted_strategies")
    if isinstance(captcha_attempts, list) and captcha_attempts:
        first_attempt["sub_attempts"] = list(captcha_attempts)
    return {
        "trigger": "barrier",
        "barrier_type": barrier.get("type") or "unknown",
        "provider": barrier.get("provider"),
        "attempts": [first_attempt],
        "outcome": "in_progress",
        "exhausted": False,
    }


def record_attempt(
    receipt: dict, strategy: str, outcome: str, reason: str | None = None
) -> None:
    attempt = {"strategy": strategy, "outcome": outcome}
    if reason:
        attempt["reason"] = reason
    receipt["attempts"].append(attempt)


def flaresolverr_applicable(receipt: dict) -> bool:
    """Use FlareSolverr for Cloudflare/Turnstile or an unclassified miss."""
    barrier_type = receipt.get("barrier_type")
    provider = receipt.get("provider")
    return (
        barrier_type == "cloudflare"
        or provider == "turnstile"
        or (barrier_type == "unknown" and provider is None)
    )


def browser_service_applicable(receipt: dict) -> bool:
    """A fresh browser session can help interactive challenges, not throttling."""
    return receipt.get("barrier_type") not in {"rate-limit", "empty"}


def attach_recovery(result: dict, receipt: dict, *, recovered: bool) -> dict:
    """Attach a copied receipt without mutating a shared result fixture."""
    enriched = dict(result)
    finalized = deepcopy(receipt)
    finalized["outcome"] = "recovered" if recovered else "unresolved"
    finalized["exhausted"] = not recovered
    enriched["recovery"] = finalized
    return enriched
