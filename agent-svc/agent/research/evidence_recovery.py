"""Retain agent source bodies behind credential-scoped, paged session refs."""

from __future__ import annotations

import logging
from typing import Any

from ..experimental.knowledge import text_digest

logger = logging.getLogger(__name__)

_EVIDENCE_RETENTION_TTL = 86_400


def mark_evidence_recovery_status(
    result: dict[str, Any], status: str, reason: str | None = None
) -> dict[str, Any]:
    """Replace stale per-source recovery actions with an honest result status."""
    result["evidence_recovery"] = {
        "status": status,
        **({"reason": reason} if reason else {}),
    }
    coverage_objects = [result.get("evidence_coverage")]
    overall_coverage = result.get("coverage")
    if isinstance(overall_coverage, dict):
        coverage_objects.append(overall_coverage.get("evidence"))
    seen: set[int] = set()
    for coverage in coverage_objects:
        if not isinstance(coverage, dict) or id(coverage) in seen:
            continue
        seen.add(id(coverage))
        sources = coverage.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if isinstance(source, dict):
                source["recovery"] = {
                    "status": status,
                    **({"reason": reason} if reason else {}),
                }
    for key in ("sources", "source_details"):
        rows = result.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            coverage = row.get("evidence_coverage")
            if isinstance(coverage, dict):
                coverage["recovery"] = {
                    "status": status,
                    **({"reason": reason} if reason else {}),
                }
    return result


async def retain_result_evidence(
    result: dict[str, Any],
    *,
    redis_url: str,
    owner_scope: str,
    include_source_content: bool = False,
) -> dict[str, Any]:
    """Move complete source bodies to an owned session and expose page handles.

    On a storage failure, leave the source bodies inline and report the failure
    so a completed response never silently discards the only retained copy.
    """
    contents = result.get("source_contents")
    if not isinstance(contents, dict) or not contents:
        if result.get("evidence_recovery", {}).get("status") == "unavailable":
            return result
        return mark_evidence_recovery_status(result, "not_retained")

    source_rows = [
        (url, text)
        for url, text in contents.items()
        if isinstance(url, str) and isinstance(text, str) and text
    ]
    if not source_rows:
        mark_evidence_recovery_status(result, "not_retained")
        if not include_source_content:
            result.pop("source_contents", None)
        return result

    # Import lazily: SessionManager depends on the evidence module.
    from ..session import SessionManager

    manager = SessionManager(redis_url=redis_url)
    session_id: str | None = None
    try:
        session_id = await manager.create_session(
            ttl=_EVIDENCE_RETENTION_TTL, owner_scope=owner_scope
        )
        refs: dict[str, dict[str, Any]] = {}
        ref_by_url: dict[str, str] = {}
        for index, (url, text) in enumerate(source_rows, start=1):
            ref_id = f"agent_{index}"
            refs[ref_id] = {
                "url": url,
                "title": "",
                "markdown": text,
                "source": "agent",
                "char_count": len(text),
            }
            ref_by_url[url] = ref_id
        await manager._store_refs(session_id, refs)
        session = await manager.get_session(session_id)
        if session is None:
            raise RuntimeError("Evidence session disappeared during retention")

        path_prefix = f"/v2/session/{session_id}/evidence/"
        recovery = {
            "status": "retained",
            "session_id": session_id,
            "expires_at": session.get("expires_at"),
            "method": "GET",
        }
        result["evidence_session_id"] = session_id
        result["evidence_recovery"] = recovery

        coverage_objects = [result.get("evidence_coverage")]
        overall_coverage = result.get("coverage")
        if isinstance(overall_coverage, dict):
            coverage_objects.append(overall_coverage.get("evidence"))
        seen: set[int] = set()
        for coverage in coverage_objects:
            if not isinstance(coverage, dict) or id(coverage) in seen:
                continue
            seen.add(id(coverage))
            sources = coverage.get("sources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if not isinstance(source, dict):
                    continue
                source_url = source.get("url") or source.get("id")
                page_ref = (
                    ref_by_url.get(source_url)
                    if isinstance(source_url, str)
                    else None
                )
                if page_ref is None:
                    source["recovery"] = {"status": "not_retained"}
                    continue
                digest = text_digest(contents[source_url])
                source["recovery"] = {
                    "action": "read_retained_evidence",
                    "session_id": session_id,
                    "ref_id": page_ref,
                    "method": "GET",
                    "path": path_prefix + page_ref,
                    "params": {
                        "offset": 0,
                        "expected_digest": digest,
                        "budget_chars": 32_000,
                    },
                }

        # The research adapters expose evidence coverage both as an aggregate
        # and on each source detail. Keep both representations actionable.
        for row in result.get("source_details", []):
            if not isinstance(row, dict):
                continue
            source_coverage = row.get("evidence_coverage")
            if not isinstance(source_coverage, dict):
                continue
            source_url = row.get("url")
            page_ref = (
                ref_by_url.get(source_url) if isinstance(source_url, str) else None
            )
            if page_ref is None:
                source_coverage["recovery"] = {"status": "not_retained"}
                continue
            source_coverage["recovery"] = {
                "action": "read_retained_evidence",
                "session_id": session_id,
                "ref_id": page_ref,
                "method": "GET",
                "path": path_prefix + page_ref,
                "params": {
                    "offset": 0,
                    "expected_digest": text_digest(contents[source_url]),
                    "budget_chars": 32_000,
                },
            }

        if not include_source_content:
            result.pop("source_contents", None)
    except Exception:
        logger.warning("Could not retain complete agent evidence in a session", exc_info=True)
        if session_id is not None:
            try:
                await manager.delete_session(session_id)
            except Exception:
                logger.warning("Could not remove partial evidence session")
        mark_evidence_recovery_status(result, "unavailable", "retention_failed")
        # Keep source_contents inline as the explicit recovery fallback.
    return result
