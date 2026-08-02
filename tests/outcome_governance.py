"""Shared governance primitives for pytest skips and expected failures."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

CLASSIFICATIONS = {"retained", "fixed/re-enabled", "quarantined", "deleted"}


@dataclass(frozen=True)
class GovernanceMetadata:
    owner: str
    issue: str
    classification: str
    review_date: str | None = None
    environment: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "owner": self.owner,
            "issue": self.issue,
            "classification": self.classification,
        }
        if self.review_date:
            result["review_date"] = self.review_date
        if self.environment:
            result["environment"] = self.environment
        return result


def validate_metadata(
    *,
    reason: str,
    owner: str,
    issue: str,
    classification: str,
    review_date: str | None = None,
    environment: str | None = None,
) -> GovernanceMetadata:
    """Validate the required metadata shared by runtime and marker APIs."""
    if not reason.strip():
        raise ValueError("governance reason must be concise and non-empty")
    if not owner.strip() or not issue.strip():
        raise ValueError("governance owner and issue are required")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"unknown governance classification: {classification}")
    if not review_date and not environment:
        raise ValueError("governance metadata needs review_date or environment")
    return GovernanceMetadata(owner, issue, classification, review_date, environment)


def format_reason(reason: str, metadata: GovernanceMetadata) -> str:
    fields = "; ".join(f"{key}={value}" for key, value in metadata.as_dict().items())
    return f"{reason.strip()} [governance: {fields}]"


def governed_skip(
    reason: str,
    *,
    owner: str,
    issue: str,
    classification: str,
    review_date: str | None = None,
    environment: str | None = None,
    allow_module_level: bool = False,
) -> None:
    """Skip at runtime only after validating reviewable governance metadata."""
    metadata = validate_metadata(
        reason=reason,
        owner=owner,
        issue=issue,
        classification=classification,
        review_date=review_date,
        environment=environment,
    )
    pytest.skip(format_reason(reason, metadata), allow_module_level=allow_module_level)


def metadata_from_marker(marker) -> dict[str, str]:
    """Return governance fields from a skip/xfail marker."""
    return {
        key: str(marker.kwargs[key])
        for key in ("owner", "issue", "classification", "review_date", "environment")
        if key in marker.kwargs
    }


def metadata_from_reason(reason: str | None) -> dict[str, str]:
    """Parse the stable metadata suffix emitted by ``governed_skip``."""
    if not reason or "[governance: " not in reason:
        return {}
    fields = reason.rsplit("[governance: ", 1)[1].removesuffix("]")
    return dict(part.split("=", 1) for part in fields.split("; ") if "=" in part)
