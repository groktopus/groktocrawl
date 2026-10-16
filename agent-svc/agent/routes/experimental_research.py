"""Opt-in experimental research protocol adapters.

New runs use fictional sources and authored judgments and require an explicit
development fixture opt-in. Existing process-local, Valkey, and PostgreSQL
retained reads and recovery keep their separate gates; no live research adapter
is selected by this route.
"""

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.features import is_enabled

from ..experimental.artifact_authority import (
    ArtifactAuthority,
    RetainedArtifactSet,
)
from ..experimental.client_protocol import ProtocolEvent, ProtocolState, replay_after
from ..experimental.consolidated_example import example_journey
from ..experimental.durable_research import (
    DurableConflictError,
    DurableResearchError,
    DurableResearchLedger,
    DurableRun,
)
from ..experimental.source_store import StorageConflictError

router = APIRouter()

_ROUTE_PREFIX = "/experimental/research/v1"
_MAX_DURABLE_TERMINAL_PAYLOAD_BYTES = 1_048_576


class CreateResearchRunRequest(BaseModel):
    """Bounded fixture-run admission request."""

    protocol_version: Literal["research/1"] = "research/1"
    objective: str = Field(min_length=1, max_length=10_000)
    as_of: datetime | None = None
    webhook: str | None = None


class AttachResearchSessionRequest(BaseModel):
    """Optimistic attachment of a completed research root to a session."""

    run_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(strict=True, ge=0)


@dataclass
class _RunRecord:
    run_id: str
    research_id: str
    scope_id: str
    objective: str
    request_digest: str
    events: list[ProtocolEvent] = field(default_factory=list)
    state: Literal[
        "accepted", "running", "cancel_requested", "completed", "failed", "cancelled"
    ] = "accepted"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    journey: Any = None
    task: asyncio.Task[None] | None = None
    deleted: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    durable_ledger: DurableResearchLedger | None = None
    durable_owner_id: str | None = None
    durable_generation: int | None = None
    durable_manifest: bytes | None = None
    durable_artifacts: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    durable_knowledge: bytes | None = None
    durable_evidence: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    artifact_authority: ArtifactAuthority | None = None
    authority_set_digest: str | None = None


_RUNS: dict[str, _RunRecord] = {}
_IDEMPOTENCY: dict[tuple[str, str], tuple[str, str]] = {}
_SESSION_ATTACHMENTS: dict[tuple[str, str], tuple[int, str]] = {}
_DURABLE_LEDGERS: dict[str, DurableResearchLedger] = {}
_ARTIFACT_AUTHORITIES: dict[str, ArtifactAuthority] = {}


def _scope_id(request: Request) -> str:
    """Derive a stable process-local scope without trusting request payload."""
    credential = request.headers.get("Authorization") or request.headers.get(
        "X-API-Key"
    )
    if not credential:
        return "anonymous"
    return "key:" + hashlib.sha256(credential.encode()).hexdigest()[:32]


def _require_feature(feature: str = "experimental_research") -> None:
    if not is_enabled(feature):
        raise HTTPException(status_code=404, detail="Experimental research disabled")


def _require_runs() -> None:
    _require_feature()
    if not is_enabled("experimental_research_runs"):
        raise HTTPException(
            status_code=404, detail="Experimental research runs disabled"
        )


def _durable_enabled() -> bool:
    return is_enabled("experimental_research_durable")


def _artifact_authority_enabled() -> bool:
    return _durable_enabled() and is_enabled("experimental_research_postgres_artifacts")


def _scope_uuid(scope_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"groktocrawl-x:research-scope:{scope_id}")


def _artifact_authority(request: Request) -> ArtifactAuthority:
    dsn = getattr(request.app.state, "research_postgres_dsn", None) or os.environ.get(
        "DURABLE_RESEARCH_POSTGRES_DSN"
    )
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL experimental artifact authority unavailable",
        )
    authority = _ARTIFACT_AUTHORITIES.get(dsn)
    if authority is None:
        authority = ArtifactAuthority(dsn)
        _ARTIFACT_AUTHORITIES[dsn] = authority
    return authority


def _durable_ledger(request: Request) -> DurableResearchLedger:
    url = getattr(request.app.state, "valkey_url", None) or os.environ.get(
        "DURABLE_RESEARCH_REDIS_URL"
    )
    if not url:
        raise HTTPException(
            status_code=503, detail="Durable experimental research storage unavailable"
        )
    ledger = _DURABLE_LEDGERS.get(url)
    if ledger is None:
        ledger = DurableResearchLedger(url)
        _DURABLE_LEDGERS[url] = ledger
    return ledger


def _build_event(
    record: _RunRecord,
    event: Literal["accepted", "progress", "done", "error", "cancelled"],
    state: ProtocolState,
    **kwargs: Any,
) -> ProtocolEvent:
    return ProtocolEvent(
        protocol_version="research/1",
        run_id=record.run_id,
        sequence=len(record.events) + 1,
        event=event,
        state=state,
        **kwargs,
    )


def _event(
    record: _RunRecord,
    event: Literal["accepted", "progress", "done", "error", "cancelled"],
    state: ProtocolState,
    **kwargs: Any,
) -> ProtocolEvent:
    event_record = _build_event(record, event, state, **kwargs)
    record.events.append(event_record)
    return event_record


def _decode_b64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise DurableResearchError(f"durable {label} payload is invalid")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise DurableResearchError(f"durable {label} payload is invalid") from exc


def _durable_artifact_payload(record: _RunRecord) -> dict[str, Any]:
    if record.journey is None:
        raise DurableResearchError("completed run has no fixture material")
    manifest = record.journey.manifest_bytes
    artifacts: dict[str, dict[str, str]] = {}
    for report in record.journey.reports:
        artifacts[report.artifact.artifact_id] = {
            "body_b64": base64.b64encode(report.body).decode("ascii"),
            "content_digest": report.artifact.content_digest,
        }
    payload = {
        "manifest": {
            "body_b64": base64.b64encode(manifest).decode("ascii"),
            "content_digest": hashlib.sha256(manifest).hexdigest(),
        },
        "artifacts": artifacts,
        "knowledge_digest": hashlib.sha256(record.journey.knowledge_bytes).hexdigest(),
        "knowledge_b64": base64.b64encode(record.journey.knowledge_bytes).decode(
            "ascii"
        ),
        "evidence": {
            source.reference.snapshot_id: {
                "body_b64": base64.b64encode(source.body).decode("ascii"),
                "media_type": source.media_type,
                "content_digest": hashlib.sha256(source.body).hexdigest(),
            }
            for source in record.journey.sources
        },
    }
    encoded_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if encoded_size > _MAX_DURABLE_TERMINAL_PAYLOAD_BYTES:
        raise DurableResearchError("durable fixture payload exceeds its size bound")
    return payload


def _decode_durable_artifacts(
    terminal: dict[str, Any],
) -> tuple[bytes | None, dict[str, tuple[bytes, str]]]:
    material = terminal.get("artifacts")
    manifest_payload = terminal.get("manifest")
    if material is None and manifest_payload is None:
        return None, {}
    if not isinstance(manifest_payload, dict) or not isinstance(material, dict):
        raise DurableResearchError("durable artifact payload is invalid")
    manifest = _decode_b64(manifest_payload.get("body_b64"), "manifest")
    if hashlib.sha256(manifest).hexdigest() != manifest_payload.get("content_digest"):
        raise DurableResearchError("durable manifest digest mismatch")
    artifacts: dict[str, tuple[bytes, str]] = {}
    for artifact_id, item in material.items():
        if not isinstance(artifact_id, str) or not isinstance(item, dict):
            raise DurableResearchError("durable artifact payload is invalid")
        body = _decode_b64(item.get("body_b64"), "artifact")
        digest = item.get("content_digest")
        if not isinstance(digest, str) or hashlib.sha256(body).hexdigest() != digest:
            raise DurableResearchError("durable artifact digest mismatch")
        artifacts[artifact_id] = (body, digest)
    return manifest, artifacts


def _restore_events(durable: DurableRun) -> list[ProtocolEvent]:
    terminal = durable.terminal_payload or {}
    raw_events = terminal.get("events") or durable.payload.get("events") or []
    if not isinstance(raw_events, list):
        raise DurableResearchError("durable event history is invalid")
    try:
        return [ProtocolEvent.model_validate(item) for item in raw_events]
    except (TypeError, ValueError) as exc:
        raise DurableResearchError("durable event history is invalid") from exc


def _durable_terminal_payload(
    record: _RunRecord,
    terminal_event: ProtocolEvent,
    retained: RetainedArtifactSet | None = None,
) -> dict[str, Any]:
    projected_result = dict(record.result or {})
    if retained is not None:
        projected_result.pop("summary", None)
    projected_events = []
    for event in (*record.events, terminal_event):
        value = event.model_dump(mode="json")
        if retained is not None and event.event == "done":
            value["result"] = projected_result
        projected_events.append(value)
    payload = {
        "research_id": record.research_id,
        "result": projected_result,
        "events": projected_events,
    }
    if retained is None:
        terminal = {**payload, **_durable_artifact_payload(record)}
    else:
        terminal = {
            **payload,
            "artifact_authority": {
                "schema_version": "postgres-artifact-authority/1",
                "scope_id": str(retained.scope_id),
                "research_id": str(retained.research_id),
                "run_id": str(retained.run_id),
                "artifact_set_id": str(retained.artifact_set_id),
                "manifest_digest": retained.manifest_digest,
                "set_digest": retained.set_digest,
                "artifacts": {
                    item.layer: {
                        "artifact_id": item.artifact_id,
                        "content_digest": item.content_digest,
                    }
                    for item in retained.artifacts
                },
            },
        }
    if (
        len(json.dumps(terminal, separators=(",", ":")).encode("utf-8"))
        > _MAX_DURABLE_TERMINAL_PAYLOAD_BYTES
    ):
        raise DurableResearchError("durable terminal projection exceeds its size bound")
    return terminal


async def _hydrate_authority_record(record: _RunRecord) -> None:
    """Restore the public summary from PostgreSQL without copying it into Valkey."""
    if (
        record.state != "completed"
        or record.result is None
        or "summary" in record.result
        or record.artifact_authority is None
        or record.authority_set_digest is None
    ):
        return
    try:
        retained = await record.artifact_authority.read(
            _scope_uuid(record.scope_id), UUID(record.research_id)
        )
    except StorageConflictError as exc:
        raise HTTPException(
            status_code=getattr(exc, "status_code", 503),
            detail="Artifact authority unavailable",
        ) from exc
    if retained.set_digest != record.authority_set_digest:
        raise HTTPException(status_code=503, detail="Artifact integrity unavailable")
    summary = next(item for item in retained.artifacts if item.layer == "summary")
    record.result["summary"] = summary.body.decode("utf-8")
    if record.events and record.events[-1].event == "done":
        record.events[-1] = record.events[-1].model_copy(
            update={"result": record.result}
        )


def _record_projection(record: _RunRecord) -> dict[str, Any]:
    terminal = (
        record.events[-1] if record.events and record.events[-1].terminal else None
    )
    return {
        "protocol_version": "research/1",
        "run_id": record.run_id,
        "research_id": record.research_id,
        "state": record.state,
        "execution_outcome": (
            "completed"
            if record.state == "completed"
            else record.state
            if record.state in {"failed", "cancelled"}
            else None
        ),
        "result": record.result
        if record.state == "completed" and record.result is not None
        else None,
        "error": record.error if terminal and terminal.event == "error" else None,
        "status_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}",
        "events_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}/events",
    }


def _check_retained_ledger(record: _RunRecord) -> None:
    if record.durable_ledger is not None:
        current = record.durable_ledger.get(record.run_id)
        if current is None:
            if (
                record.artifact_authority is not None
                and record.state == "completed"
                and record.authority_set_digest is not None
            ):
                record.durable_ledger = (
                    None  # completed PostgreSQL roots outlive the execution ledger
                )
                return
            raise HTTPException(status_code=410, detail="Research run expired")
        if current.state == "deleted":
            record.deleted = True
    if record.deleted:
        raise HTTPException(status_code=410, detail="Research run deleted")


def _find_run(run_id: str, request: Request) -> _RunRecord:
    record = _RUNS.get(run_id)
    if record is None and _durable_enabled():
        durable = _durable_ledger(request).get(run_id)
        if durable is not None:
            try:
                record = _restore_durable_record(durable)
            except DurableResearchError as exc:
                raise HTTPException(
                    status_code=503, detail="Durable research projection unavailable"
                ) from exc
            record.durable_ledger = _durable_ledger(request)
            if _artifact_authority_enabled():
                record.artifact_authority = _artifact_authority(request)
            if record.scope_id != _scope_id(request):
                raise HTTPException(status_code=404, detail="Research run not found")
            _RUNS[run_id] = record
            if (
                durable.state in {"admitted", "running"}
                and record.artifact_authority is None
            ):
                record.task = asyncio.create_task(_execute_run(record))
    if record is None or record.scope_id != _scope_id(request):
        raise HTTPException(status_code=404, detail="Research run not found")
    _check_retained_ledger(record)
    return record


def _decode_retained_evidence(terminal: dict[str, Any]) -> dict[str, tuple[bytes, str]]:
    material = terminal.get("evidence", {})
    if not isinstance(material, dict) or len(material) > 1000:
        raise DurableResearchError("invalid durable evidence")
    selected = {}
    total = 0
    for snapshot, item in material.items():
        if not isinstance(snapshot, str) or not isinstance(item, dict):
            raise DurableResearchError("invalid durable evidence")
        body = _decode_b64(item.get("body_b64"), "evidence")
        total += len(body)
        if (
            total > _MAX_DURABLE_TERMINAL_PAYLOAD_BYTES
            or hashlib.sha256(body).hexdigest() != item.get("content_digest")
            or item.get("media_type") not in {"text/plain", "text/markdown"}
        ):
            raise DurableResearchError("invalid durable evidence")
        selected[snapshot] = (body, item["media_type"])
    return selected


def _restore_durable_record(durable: DurableRun) -> _RunRecord:
    payload = durable.payload
    terminal = durable.terminal_payload or {}
    state = durable.state
    deleted = state == "deleted"
    if state not in {
        "accepted",
        "running",
        "cancel_requested",
        "completed",
        "failed",
        "cancelled",
        "deleted",
    }:
        state = "accepted"
    if deleted:
        # Deletion is a durable tombstone, not a public protocol state.
        state = "cancelled"
    typed_state = cast(
        Literal[
            "accepted",
            "running",
            "cancel_requested",
            "completed",
            "failed",
            "cancelled",
        ],
        state,
    )
    manifest, artifacts = _decode_durable_artifacts(terminal)
    if "knowledge_b64" in terminal:
        knowledge = _decode_b64(terminal["knowledge_b64"], "knowledge")
        if len(knowledge) > _MAX_DURABLE_TERMINAL_PAYLOAD_BYTES or hashlib.sha256(
            knowledge
        ).hexdigest() != terminal.get("knowledge_digest"):
            raise DurableResearchError("durable knowledge digest mismatch")
    authority_pointer = terminal.get("artifact_authority")
    authority_set_digest = (
        authority_pointer.get("set_digest")
        if isinstance(authority_pointer, dict)
        and isinstance(authority_pointer.get("set_digest"), str)
        else None
    )
    return _RunRecord(
        run_id=durable.run_id,
        research_id=str(payload.get("research_id", durable.run_id)),
        scope_id=durable.scope_id,
        objective=str(payload.get("objective", "Recovered experimental research run")),
        request_digest=durable.request_digest,
        state=typed_state,
        result=terminal.get("result") if typed_state == "completed" else None,
        events=_restore_events(durable),
        durable_knowledge=_decode_b64(terminal["knowledge_b64"], "knowledge")
        if "knowledge_b64" in terminal
        else None,
        durable_evidence=_decode_retained_evidence(terminal),
        durable_manifest=manifest,
        durable_artifacts=artifacts,
        authority_set_digest=authority_set_digest,
        durable_ledger=None,
        deleted=deleted,
    )


def _artifact_result(record: _RunRecord) -> dict[str, Any]:
    journey = record.journey
    manifest = journey.candidate.admitted.manifest
    summary = next(
        report for report in journey.reports if report.artifact.layer == "summary"
    )
    return {
        "research_id": record.research_id,
        "ir_revision_id": manifest.revision_id,
        "artifact_set_id": manifest.artifact_set_id,
        "summary": summary.body.decode("utf-8"),
        "coverage": manifest.coverage,
        "manifest_url": f"{_ROUTE_PREFIX}/artifact-sets/{manifest.artifact_set_id}",
        "artifacts": {
            report.artifact.layer: f"{_ROUTE_PREFIX}/artifacts/{report.artifact.artifact_id}"
            for report in journey.reports
        },
    }


def _retained_artifact_result(retained: RetainedArtifactSet) -> dict[str, Any]:
    manifest = json.loads(retained.manifest)
    summary = next(item for item in retained.artifacts if item.layer == "summary")
    return {
        "research_id": str(retained.research_id),
        "ir_revision_id": manifest["revision_id"],
        "artifact_set_id": str(retained.artifact_set_id),
        "summary": summary.body.decode("utf-8"),
        "coverage": manifest["coverage"],
        "manifest_url": f"{_ROUTE_PREFIX}/artifact-sets/{retained.artifact_set_id}",
        "artifacts": {
            item.layer: f"{_ROUTE_PREFIX}/artifacts/{item.artifact_id}"
            for item in retained.artifacts
        },
    }


async def _reconcile_authority(record: _RunRecord) -> None:
    """Project an already committed PostgreSQL result into Valkey after loss."""
    if (
        record.state not in {"accepted", "running"}
        or record.artifact_authority is None
        or record.durable_ledger is None
    ):
        return
    retained = await record.artifact_authority.find_run(
        _scope_uuid(record.scope_id), UUID(record.run_id)
    )
    if retained is None:
        if record.task is None:
            record.task = asyncio.create_task(_execute_run(record))
        return
    try:
        claimed = record.durable_ledger.claim(
            record.run_id,
            owner_id=f"reconciler:{uuid4()}",
            attempt_id=f"reconcile:{uuid4()}",
        )
    except DurableConflictError:
        # A live owner still holds the lease. Leave the committed PostgreSQL
        # result in place and let a later status/SSE request reconcile it once
        # that lease has expired.
        return
    if claimed.owner_id is None:
        raise DurableResearchError("reconciliation owner unavailable")
    record.durable_owner_id = claimed.owner_id
    record.durable_generation = claimed.owner_generation
    record.result = _retained_artifact_result(retained)
    record.authority_set_digest = retained.set_digest
    terminal_event = _build_event(record, "done", "completed", result=record.result)
    terminal_payload = _durable_terminal_payload(record, terminal_event, retained)
    checkpoint_digest = hashlib.sha256(
        json.dumps(terminal_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    result_digest = hashlib.sha256(
        json.dumps(record.result, sort_keys=True).encode("utf-8")
    ).hexdigest()
    record.durable_ledger.checkpoint(
        record.run_id,
        claimed.owner_id,
        claimed.owner_generation,
        "terminal_projection",
        checkpoint_digest,
    )
    record.durable_ledger.commit_result(
        record.run_id,
        claimed.owner_id,
        claimed.owner_generation,
        result_digest,
        terminal_payload=terminal_payload,
    )
    record.state = "completed"
    record.events.append(terminal_event)


async def _execute_run(record: _RunRecord) -> None:
    async with record.lock:
        if record.deleted:
            record.state = "cancelled"
            return
        if record.state not in {"accepted", "running"}:
            return
        if record.durable_ledger is not None:
            try:
                claimed = record.durable_ledger.claim(
                    record.run_id,
                    owner_id=f"worker:{uuid4()}",
                    attempt_id=f"attempt:{uuid4()}",
                )
            except Exception:
                return
            record.durable_owner_id = claimed.owner_id
            record.durable_generation = claimed.owner_generation
        record.state = "running"
        _event(record, "progress", "running", stage="acquisition")
    try:
        journey = example_journey(
            scope_id=record.scope_id,
            research_id=record.research_id,
            objective=record.objective,
            artifact_set_id=str(
                uuid5(NAMESPACE_URL, f"groktocrawl-x:artifact-set:{record.run_id}")
            ),
        )
        result = await journey.run()
    except asyncio.CancelledError:
        async with record.lock:
            if record.state not in {"completed", "failed", "cancelled"}:
                record.state = "cancelled"
                _event(record, "cancelled", "cancelled")
        raise
    except Exception:  # pragma: no cover - defensive terminal contract
        async with record.lock:
            if record.state == "cancel_requested":
                record.state = "cancelled"
                _event(record, "cancelled", "cancelled")
            elif record.state not in {"completed", "cancelled"}:
                record.state = "failed"
                record.error = {"code": "fixture_run_failed", "retryable": False}
                _event(record, "error", "failed", error=record.error)
        return
    async with record.lock:
        if record.deleted or record.state == "cancel_requested":
            record.state = "cancelled"
            _event(record, "cancelled", "cancelled")
            return
        record.journey = result
        record.result = _artifact_result(record)
        terminal_event: ProtocolEvent | None = None
        if (
            record.durable_ledger is not None
            and record.durable_owner_id is not None
            and record.durable_generation is not None
        ):
            terminal_event = _build_event(
                record, "done", "completed", result=record.result
            )
            retained = None
            if record.artifact_authority is not None:
                manifest = result.candidate.admitted.manifest
                retained = await record.artifact_authority.commit(
                    _scope_uuid(record.scope_id),
                    UUID(record.research_id),
                    UUID(record.run_id),
                    UUID(manifest.artifact_set_id),
                    result.manifest_bytes,
                    {
                        report.artifact.layer: (
                            report.artifact.artifact_id,
                            report.body,
                        )
                        for report in result.reports
                    },
                    knowledge=result.knowledge_bytes,
                    evidence={
                        source.reference.snapshot_id: (source.body, source.media_type)
                        for source in result.sources
                    },
                )
                record.authority_set_digest = retained.set_digest
            terminal_payload = _durable_terminal_payload(
                record, terminal_event, retained
            )
            checkpoint_digest = hashlib.sha256(
                json.dumps(terminal_payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
            result_digest = hashlib.sha256(
                json.dumps(record.result, sort_keys=True).encode("utf-8")
            ).hexdigest()
            record.durable_ledger.checkpoint(
                record.run_id,
                record.durable_owner_id,
                record.durable_generation,
                "terminal_projection",
                checkpoint_digest,
            )
            record.durable_ledger.commit_result(
                record.run_id,
                record.durable_owner_id,
                record.durable_generation,
                result_digest,
                terminal_payload=terminal_payload,
            )
        record.state = "completed"
        if terminal_event is not None:
            record.events.append(terminal_event)
        else:
            _event(record, "done", "completed", result=record.result)


def capability_document() -> dict[str, Any]:
    """Return the honest capability boundary for the current W6 slice."""
    runs_available = is_enabled("experimental_research_runs")
    durable = runs_available and _durable_enabled()
    fixture_creation = runs_available and is_enabled(
        "experimental_research_fixture_runs"
    )
    return {
        "protocol_version": "research/1",
        "route_prefix": _ROUTE_PREFIX,
        "live_research_available": False,
        "implementation_stage": "postgres_artifact_authority_fixture_adapter"
        if durable and _artifact_authority_enabled()
        else "durable_fixture_run_adapter"
        if durable
        else "fixture_run_adapter"
        if runs_available
        else "contract_and_golden_traces",
        "recovery_mode": "postgres_artifacts_valkey_fenced"
        if durable and _artifact_authority_enabled()
        else "valkey_fenced"
        if durable
        else "process_local"
        if runs_available
        else "not_advertised",
        "replay": {
            "mode": "contract_only",
            "window_events": 0,
        },
        "operations": {
            "workspace": {
                "available": runs_available,
                "scope": "independent_roots",
                "source": "existing_artifact_authority",
                "postgres_schema_required": 16
                if _artifact_authority_enabled()
                else None,
            },
            "capabilities": {"available": True},
            "runs": {
                "available": runs_available,
                "reason": None if runs_available else "public_adapters_pending",
                "create_available": fixture_creation,
                "creation_reason": None
                if fixture_creation
                else "development_fixture_opt_in_required",
                "execution_mode": "fixture_only",
            },
            "artifacts": {
                "available": runs_available,
                "reason": None if runs_available else "public_adapters_pending",
            },
            "evidence": {
                "available": runs_available,
                "reason": None if runs_available else "public_adapters_pending",
            },
            "sessions": {
                "available": runs_available,
                "reason": None if runs_available else "public_adapters_pending",
                "mode": "attachment_only" if runs_available else None,
            },
        },
    }


@router.get(f"{_ROUTE_PREFIX}/capabilities")
async def get_experimental_research_capabilities() -> dict[str, Any]:
    """Advertise the opt-in protocol without implying unavailable operations."""
    if not is_enabled("experimental_research"):
        raise HTTPException(status_code=404, detail="Experimental research disabled")
    return capability_document()


@router.post(f"{_ROUTE_PREFIX}/runs", status_code=202)
async def create_experimental_research_run(
    payload: CreateResearchRunRequest, request: Request
) -> dict[str, Any]:
    """Admit one bounded fixture run with scoped idempotency."""
    _require_runs()
    if not is_enabled("experimental_research_fixture_runs"):
        raise HTTPException(
            status_code=404,
            detail="Fixture research creation disabled; no live research adapter is available",
        )
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    scope = _scope_id(request)
    durable = _durable_ledger(request) if _durable_enabled() else None
    authority = _artifact_authority(request) if _artifact_authority_enabled() else None
    if authority is not None:
        try:
            await authority.ensure_evidence_schema()
        except StorageConflictError as exc:
            raise HTTPException(
                status_code=503,
                detail="Artifact evidence schema 16 required before admission",
            ) from exc
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    existing = _IDEMPOTENCY.get((scope, key)) if durable is None else None
    if durable is not None:
        selected_run_id = str(uuid4())
        accepted_event = ProtocolEvent(
            protocol_version="research/1",
            run_id=selected_run_id,
            sequence=1,
            event="accepted",
            state="accepted",
        )
        admitted = durable.admit(
            scope,
            key,
            digest,
            run_id=selected_run_id,
            payload={
                "objective": payload.objective,
                "research_id": str(uuid4()),
                "events": [accepted_event.model_dump(mode="json")],
            },
        )
        research_id = str(admitted.payload.get("research_id", str(uuid4())))
        record = _RUNS.get(admitted.run_id)
        if record is None:
            record = _restore_durable_record(admitted)
            record.research_id = research_id
            record.durable_ledger = durable
            _RUNS[admitted.run_id] = record
        record.artifact_authority = authority
        if authority is not None:
            await authority.ensure_scope(_scope_uuid(scope))
        if record.task is None:
            record.task = asyncio.create_task(_execute_run(record))
        return {
            "protocol_version": "research/1",
            "run_id": record.run_id,
            "research_id": record.research_id,
            "state": record.state,
            "status_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}",
            "events_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}/events",
        }
    if existing is not None:
        run_id, prior_digest = existing
        if prior_digest != digest:
            raise HTTPException(status_code=409, detail="Idempotency key conflict")
        record = _RUNS[run_id]
        return {
            "protocol_version": "research/1",
            "run_id": record.run_id,
            "research_id": record.research_id,
            "state": record.state,
            "status_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}",
            "events_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}/events",
        }
    run_id = str(uuid4())
    record = _RunRecord(
        run_id=run_id,
        research_id=str(uuid4()),
        scope_id=scope,
        objective=payload.objective,
        request_digest=digest,
    )
    _event(record, "accepted", "accepted")
    _RUNS[run_id] = record
    _IDEMPOTENCY[(scope, key)] = (run_id, digest)
    record.task = asyncio.create_task(_execute_run(record))
    return {
        "protocol_version": "research/1",
        "run_id": record.run_id,
        "research_id": record.research_id,
        "state": record.state,
        "status_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}",
        "events_url": f"{_ROUTE_PREFIX}/runs/{record.run_id}/events",
    }


@router.get(f"{_ROUTE_PREFIX}/runs/{{run_id}}")
async def get_experimental_research_run(
    run_id: str, request: Request
) -> dict[str, Any]:
    """Return the authoritative process-local run projection."""
    _require_runs()
    record = _find_run(run_id, request)
    await _reconcile_authority(record)
    await _hydrate_authority_record(record)
    return _record_projection(record)


@router.get(f"{_ROUTE_PREFIX}/runs/{{run_id}}/events")
async def stream_experimental_research_events(
    run_id: str, request: Request
) -> StreamingResponse:
    """Replay retained process-local events as SSE; no restart recovery is claimed."""
    _require_runs()
    record = _find_run(run_id, request)
    await _reconcile_authority(record)
    await _hydrate_authority_record(record)
    try:
        events = replay_after(record.events, request.headers.get("Last-Event-ID"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def body():
        for event in events:
            yield f"id: {event.event_id}\nevent: {event.event}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")


@router.post(f"{_ROUTE_PREFIX}/runs/{{run_id}}/cancel", status_code=202)
async def cancel_experimental_research_run(
    run_id: str, request: Request
) -> dict[str, Any]:
    """Request cancellation and return the current authoritative projection."""
    _require_runs()
    record = _find_run(run_id, request)
    async with record.lock:
        if record.state in {"completed", "failed", "cancelled"}:
            return _record_projection(record)
        if record.durable_ledger is not None:
            terminal_event = _build_event(record, "cancelled", "cancelled")
            terminal_payload = {
                "research_id": record.research_id,
                "events": [
                    event.model_dump(mode="json")
                    for event in (*record.events, terminal_event)
                ],
            }
            durable = record.durable_ledger.cancel(
                record.run_id, terminal_payload=terminal_payload
            )
            if durable.state == "completed":
                recovered = _restore_durable_record(durable)
                record.state = recovered.state
                record.result = recovered.result
                record.events = recovered.events
                record.durable_manifest = recovered.durable_manifest
                record.durable_artifacts = recovered.durable_artifacts
                return _record_projection(record)
            record.state = "cancelled"
            record.events.append(terminal_event)
            if record.task is not None and not record.task.done():
                record.task.cancel()
            return _record_projection(record)
        if record.state == "accepted":
            record.state = "cancelled"
            _event(record, "cancelled", "cancelled")
            if record.task is not None and not record.task.done():
                record.task.cancel()
            return _record_projection(record)
        record.state = "cancel_requested"
        _event(record, "progress", "cancel_requested", stage="cancellation")
        if record.task is not None and not record.task.done():
            record.task.cancel()
    return _record_projection(record)


def _durable_read_records(request: Request) -> list[_RunRecord]:
    """Hydrate retained durable records for opaque artifact URLs."""
    if not _durable_enabled():
        return []
    ledger = _durable_ledger(request)
    scope = _scope_id(request)
    records: list[_RunRecord] = []
    for durable in ledger.retained():
        if durable.scope_id != scope:
            continue
        try:
            record = _restore_durable_record(durable)
        except DurableResearchError as exc:
            raise HTTPException(
                status_code=503, detail="Durable research projection unavailable"
            ) from exc
        record.durable_ledger = ledger
        if _artifact_authority_enabled():
            record.artifact_authority = _artifact_authority(request)
        _RUNS.setdefault(record.run_id, record)
        records.append(_RUNS[record.run_id])
    return records


@router.get(f"{_ROUTE_PREFIX}/artifact-sets/{{artifact_set_id}}")
async def get_experimental_artifact_set(
    artifact_set_id: str, request: Request
) -> dict[str, Any]:
    """Return the exact audited manifest for a completed fixture run."""
    _require_runs()
    scope = _scope_id(request)
    records = list(_RUNS.values())
    records.extend(_durable_read_records(request))
    for record in records:
        if record.scope_id != scope or (
            record.journey is None
            and record.durable_manifest is None
            and record.authority_set_digest is None
        ):
            continue
        expected_set = (record.result or {}).get("artifact_set_id") or str(
            uuid5(NAMESPACE_URL, f"groktocrawl-x:artifact-set:{record.run_id}")
        )
        if expected_set != artifact_set_id:
            continue
        _check_retained_ledger(record)
        if record.deleted:
            raise HTTPException(status_code=410, detail="Artifact set deleted")
        if (
            record.artifact_authority is not None
            and record.authority_set_digest is not None
        ):
            try:
                retained = await record.artifact_authority.read(
                    _scope_uuid(record.scope_id), UUID(record.research_id)
                )
            except StorageConflictError as exc:
                raise HTTPException(
                    status_code=getattr(exc, "status_code", 503),
                    detail="Artifact authority unavailable",
                ) from exc
            if retained.set_digest != record.authority_set_digest:
                raise HTTPException(
                    status_code=503, detail="Artifact integrity unavailable"
                )
            if str(retained.artifact_set_id) != artifact_set_id:
                continue
            manifest_bytes = retained.manifest
        elif record.journey is not None:
            manifest_bytes = record.journey.manifest_bytes
        else:
            if (
                record.result is None
                or record.result.get("artifact_set_id") != artifact_set_id
            ):
                continue
            durable_manifest = record.durable_manifest
            if durable_manifest is None:
                continue
            manifest_bytes = durable_manifest
        manifest_payload = json.loads(manifest_bytes)
        if manifest_payload.get("artifact_set_id") != artifact_set_id:
            continue
        return manifest_payload
    if _artifact_authority_enabled():
        authority = _artifact_authority(request)
        try:
            retained = await authority.lookup(
                _scope_uuid(scope), artifact_set=UUID(artifact_set_id)
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=getattr(exc, "status_code", 404),
                detail="Retained artifact set unavailable",
            ) from exc
        _catalog_record(retained, scope, authority)
        return json.loads(retained.manifest)
    raise HTTPException(status_code=404, detail="Artifact set not found")


@router.get(f"{_ROUTE_PREFIX}/artifacts/{{artifact_id}}")
async def get_experimental_artifact(artifact_id: str, request: Request) -> Response:
    """Return exact retained fixture bytes after scope and digest checks."""
    _require_runs()
    scope = _scope_id(request)
    records = list(_RUNS.values())
    records.extend(_durable_read_records(request))
    for record in records:
        if record.scope_id != scope:
            continue
        expected_set = (record.result or {}).get("artifact_set_id") or str(
            uuid5(NAMESPACE_URL, f"groktocrawl-x:artifact-set:{record.run_id}")
        )
        known_ids = {
            url.rsplit("/", 1)[-1]
            for url in (record.result or {}).get("artifacts", {}).values()
        }
        if record.journey is not None:
            known_ids.update(
                report.artifact.artifact_id for report in record.journey.reports
            )
        known_ids.update(record.durable_artifacts)
        known_ids.update(
            f"{expected_set}-{layer}" for layer in ("summary", "analysis", "dossier")
        )
        if artifact_id not in known_ids:
            continue
        _check_retained_ledger(record)
        if record.deleted:
            raise HTTPException(status_code=410, detail="Artifact deleted")
        if (
            record.artifact_authority is not None
            and record.authority_set_digest is not None
        ):
            try:
                retained = await record.artifact_authority.read(
                    _scope_uuid(record.scope_id), UUID(record.research_id)
                )
            except StorageConflictError as exc:
                raise HTTPException(
                    status_code=getattr(exc, "status_code", 503),
                    detail="Artifact authority unavailable",
                ) from exc
            if retained.set_digest != record.authority_set_digest:
                raise HTTPException(
                    status_code=503, detail="Artifact integrity unavailable"
                )
            reports = {
                item.artifact_id: (item.body, item.content_digest)
                for item in retained.artifacts
            }
        elif record.journey is not None:
            reports = {
                report.artifact.artifact_id: (
                    report.body,
                    report.artifact.content_digest,
                )
                for report in record.journey.reports
            }
        else:
            reports = record.durable_artifacts
        artifact = reports.get(artifact_id)
        if artifact is not None:
            body, content_digest = artifact
            if hashlib.sha256(body).hexdigest() != content_digest:
                raise HTTPException(
                    status_code=503, detail="Artifact integrity unavailable"
                )
            return Response(body, media_type="text/markdown")
    if _artifact_authority_enabled():
        authority = _artifact_authority(request)
        try:
            retained = await authority.lookup(
                _scope_uuid(scope), artifact_id=artifact_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=getattr(exc, "status_code", 404),
                detail="Retained artifact unavailable",
            ) from exc
        _catalog_record(retained, scope, authority)
        item = next(
            (item for item in retained.artifacts if item.artifact_id == artifact_id),
            None,
        )
        if item is not None:
            return Response(item.body, media_type="text/markdown")
    raise HTTPException(status_code=404, detail="Artifact not found")


@router.get(f"{_ROUTE_PREFIX}/research/{{research_id}}/evidence/{{snapshot_id}}")
async def get_experimental_evidence(
    research_id: str, snapshot_id: str, request: Request
) -> dict[str, Any]:
    """Return bounded exact fixture evidence for one scoped snapshot."""
    _require_runs()
    scope = _scope_id(request)
    for record in _RUNS.values():
        if record.scope_id != scope or record.research_id != research_id:
            continue
        _check_retained_ledger(record)
        if record.artifact_authority is not None:
            await _workspace_lifetime(record)
        if record.deleted:
            raise HTTPException(status_code=410, detail="Research evidence deleted")
        if record.journey is None:
            continue
        for source in record.journey.sources:
            if source.reference.snapshot_id == snapshot_id:
                return {
                    "research_id": research_id,
                    "snapshot_id": snapshot_id,
                    "media_type": source.media_type,
                    "body": source.body.decode("utf-8"),
                    "content_digest": hashlib.sha256(source.body).hexdigest(),
                }
    for record in _durable_read_records(request):
        if record.research_id != research_id or record.scope_id != scope:
            continue
        _check_retained_ledger(record)
        if record.artifact_authority is not None:
            await _workspace_lifetime(record)
        if record.deleted:
            raise HTTPException(status_code=410, detail="Research evidence deleted")
        if record.artifact_authority is None:
            material = record.durable_evidence.get(snapshot_id)
            if material is None:
                continue
            body, media_type = material
            return {
                "research_id": research_id,
                "snapshot_id": snapshot_id,
                "media_type": media_type,
                "body": body.decode("utf-8"),
                "content_digest": hashlib.sha256(body).hexdigest(),
            }
        try:
            body, media_type = await record.artifact_authority.read_evidence(
                _scope_uuid(scope), UUID(research_id), snapshot_id
            )
        except StorageConflictError as exc:
            raise HTTPException(status_code=404, detail="Evidence unavailable") from exc
        return {
            "research_id": research_id,
            "snapshot_id": snapshot_id,
            "media_type": media_type,
            "body": body.decode("utf-8"),
            "content_digest": hashlib.sha256(body).hexdigest(),
        }
    if _artifact_authority_enabled():
        authority = _artifact_authority(request)
        try:
            research = UUID(research_id)
            body, media_type = await authority.read_evidence(
                _scope_uuid(scope), research, snapshot_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=getattr(exc, "status_code", 404),
                detail="Retained evidence unavailable",
            ) from exc
        return {
            "research_id": research_id,
            "snapshot_id": snapshot_id,
            "media_type": media_type,
            "body": body.decode("utf-8"),
            "content_digest": hashlib.sha256(body).hexdigest(),
        }
    raise HTTPException(status_code=404, detail="Evidence not found")


@router.delete(f"{_ROUTE_PREFIX}/research/{{research_id}}", status_code=202)
async def delete_experimental_research(
    research_id: str, request: Request
) -> dict[str, Any]:
    """Tombstone the process-local research root before physical cleanup."""
    _require_runs()
    scope = _scope_id(request)
    authority_deleted = False
    if _artifact_authority_enabled():
        authority = _artifact_authority(request)
        try:
            identity = UUID(research_id)
            known = any(
                record.scope_id == scope and record.research_id == research_id
                for record in _RUNS.values()
            )
            if not known and _durable_enabled():
                known = any(
                    run.scope_id == scope
                    and run.payload.get("research_id") == research_id
                    for run in _durable_ledger(request).retained()
                )
            if not known:
                await authority.retention(
                    _scope_uuid(scope), identity
                )  # PostgreSQL-only roots, including expiry
            await authority.delete(_scope_uuid(scope), identity)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Research root not found"
            ) from exc
        authority_deleted = True
    if _durable_enabled():
        ledger = _durable_ledger(request)
        for durable in ledger.retained():
            if (
                durable.scope_id != scope
                or durable.payload.get("research_id") != research_id
            ):
                continue
            ledger.delete(
                durable.run_id,
                terminal_payload={"research_id": research_id, "deleted": True},
            )
            record = _RUNS.get(durable.run_id)
            if record is not None:
                record.deleted = True
            return {"research_id": research_id, "state": "deleted"}
    for record in _RUNS.values():
        if record.scope_id == scope and record.research_id == research_id:
            record.deleted = True
            return {"research_id": research_id, "state": "deleted"}
    if authority_deleted:
        return {"research_id": research_id, "state": "deleted"}
    raise HTTPException(status_code=404, detail="Research root not found")


@router.post(f"{_ROUTE_PREFIX}/sessions/{{session_id}}/attachments", status_code=200)
async def attach_experimental_research_session(
    session_id: str, payload: AttachResearchSessionRequest, request: Request
) -> dict[str, Any]:
    """Attach a completed root with an expected-revision concurrency guard."""
    _require_runs()
    if len(session_id) > 200:
        raise HTTPException(status_code=400, detail="Session ID is too long")
    record = _find_run(payload.run_id, request)
    if record.state != "completed" or record.result is None:
        raise HTTPException(status_code=409, detail="Research run is not completed")
    key = (_scope_id(request), session_id)
    current = _SESSION_ATTACHMENTS.get(key)
    current_revision = current[0] if current is not None else 0
    if payload.expected_revision != current_revision:
        raise HTTPException(status_code=409, detail="Session revision conflict")
    if current is not None and current[1] == payload.run_id:
        return {
            "session_id": session_id,
            "revision": current_revision,
            "run_id": payload.run_id,
            "research_id": record.research_id,
            "artifact_set_id": record.result["artifact_set_id"],
        }
    revision = current_revision + 1
    _SESSION_ATTACHMENTS[key] = (revision, payload.run_id)
    return {
        "session_id": session_id,
        "revision": revision,
        "run_id": payload.run_id,
        "research_id": record.research_id,
        "artifact_set_id": record.result["artifact_set_id"],
    }


def _catalog_record(
    retained: RetainedArtifactSet, scope: str, authority: ArtifactAuthority
) -> _RunRecord:
    record = _RunRecord(
        run_id=str(retained.run_id),
        research_id=str(retained.research_id),
        scope_id=scope,
        objective="Retained completed research",
        request_digest="",
        state="completed",
        result=_retained_artifact_result(retained),
        artifact_authority=authority,
        authority_set_digest=retained.set_digest,
    )
    _RUNS[record.run_id] = record
    return record


async def _select_workspace_record(run_id: str, request: Request) -> _RunRecord:
    original_error = None
    try:
        return _find_run(run_id, request)
    except HTTPException as exc:
        if (
            not _artifact_authority_enabled()
            or exc.status_code not in {404, 410}
            or (exc.status_code == 410 and exc.detail != "Research run expired")
        ):
            raise
        original_error = exc
    authority = _artifact_authority(request)
    try:
        retained = await authority.lookup(
            _scope_uuid(_scope_id(request)), run=UUID(run_id)
        )
    except ValueError as exc:
        if (
            original_error is not None
            and original_error.status_code == 410
            and getattr(exc, "status_code", 404) == 404
        ):
            raise original_error from exc
        raise HTTPException(
            status_code=getattr(exc, "status_code", 404),
            detail="Retained root unavailable",
        ) from exc
    return _catalog_record(retained, _scope_id(request), authority)


async def _workspace_lifetime(record: _RunRecord) -> dict[str, Any]:
    if record.artifact_authority is not None and record.state == "completed":
        lifetime = await record.artifact_authority.retention(
            _scope_uuid(record.scope_id), UUID(record.research_id)
        )
        if lifetime["deleted"] or not lifetime["live"]:
            raise HTTPException(
                status_code=410, detail="Research root expired or deleted"
            )
        return {"authority": "postgres_artifact_authority", **lifetime}
    return {
        "authority": "valkey_retained"
        if record.durable_ledger is not None
        else "process_local",
        "expires_at": None,
        "reason": "Valkey retention enforced on read"
        if record.durable_ledger is not None
        else "fixture process lifetime only",
    }


@router.get(f"{_ROUTE_PREFIX}/workspace")
async def list_research_workspace(
    request: Request, offset: int = 0, limit: int = 50, run_offset: int = 0
) -> dict[str, Any]:
    """Browse caller-scoped retained runs; server records remain authoritative."""
    _require_runs()
    if (
        not 0 <= offset <= 100_000
        or not 0 <= run_offset <= 100_000
        or not 1 <= limit <= 100
    ):
        raise HTTPException(status_code=422, detail="Invalid workspace page")
    scope = _scope_id(request)
    records = {record.run_id: record for record in _durable_read_records(request)}
    records.update(
        {record.run_id: record for record in _RUNS.values() if record.scope_id == scope}
    )
    live = []
    for record in records.values():
        try:
            _check_retained_ledger(record)
        except HTTPException as exc:
            if exc.status_code == 410:
                continue
            raise
        if _artifact_authority_enabled() and record.state == "completed":
            continue  # retained roots are paged independently from temporary execution records
        live.append(record)
    selected = sorted(
        live,
        key=lambda record: record.run_id,
    )
    items = [
        {
            "run_id": record.run_id,
            "research_id": record.research_id,
            "objective": record.objective,
            "state": record.state,
            "url": f"{_ROUTE_PREFIX}/workspace/{record.run_id}",
        }
        for record in selected[
            (run_offset if _artifact_authority_enabled() else offset) : (
                run_offset if _artifact_authority_enabled() else offset
            )
            + limit
        ]
    ]
    authority_next = None
    if _artifact_authority_enabled():
        catalog = await _artifact_authority(request).list_retained(
            _scope_uuid(scope), limit=limit + 1, offset=offset
        )
        for root in catalog[:limit]:
            identity = str(root["run_id"])
            items.append(
                {
                    "run_id": identity,
                    "research_id": str(root["research_id"]),
                    "objective": root.get("objective") or "Retained completed research",
                    "state": "completed",
                    "revision": root["set_digest"],
                    "expires_at": root["expires_at"].isoformat(),
                    "url": f"{_ROUTE_PREFIX}/workspace/{identity}",
                }
            )
        authority_next = offset + limit if len(catalog) > limit else None
    return {
        "items": items,
        "offset": offset,
        "next_offset": authority_next
        if _artifact_authority_enabled()
        else (offset + limit if offset + limit < len(selected) else None),
        "next_run_offset": run_offset + limit
        if _artifact_authority_enabled() and run_offset + limit < len(selected)
        else None,
        "page_bound": "up to limit retained roots plus limit execution records",
        "capabilities": capability_document(),
        "ordering": "run_id per authority; independent offsets, not a snapshot",
    }


@router.get(f"{_ROUTE_PREFIX}/workspace/{{run_id}}")
async def inspect_research_workspace(
    run_id: str, request: Request, expected_revision: str | None = None
) -> dict[str, Any]:
    """Inspect an independent root and explicitly named continuation actions."""
    _require_runs()
    record = await _select_workspace_record(run_id, request)
    await _reconcile_authority(record)
    lifetime = await _workspace_lifetime(record)
    await _hydrate_authority_record(record)
    revision = record.authority_set_digest
    if revision is None and record.journey is not None:
        revision = hashlib.sha256(record.journey.manifest_bytes).hexdigest()
    if revision is None and record.durable_manifest is not None:
        revision = hashlib.sha256(record.durable_manifest).hexdigest()
    if expected_revision is not None and expected_revision != revision:
        raise HTTPException(status_code=409, detail="Research revision conflict")
    result = record.result or {}
    actions: dict[str, Any] = {
        "inspect": {"method": "GET", "url": f"{_ROUTE_PREFIX}/workspace/{run_id}"},
        "delete": {
            "method": "DELETE",
            "url": f"{_ROUTE_PREFIX}/research/{record.research_id}",
        },
    }
    manifest = None
    evidence_links = []
    knowledge_bytes = (
        record.journey.knowledge_bytes
        if record.journey is not None
        else record.durable_knowledge
    )
    if (
        knowledge_bytes is None
        and record.artifact_authority is not None
        and record.state == "completed"
    ):
        try:
            knowledge_bytes = await record.artifact_authority.read_knowledge(
                _scope_uuid(record.scope_id), UUID(record.research_id)
            )
        except StorageConflictError:
            knowledge_bytes = None
    if knowledge_bytes is not None:
        knowledge = json.loads(knowledge_bytes)
        context = knowledge.get("context", knowledge)
        record.objective = context.get("objective", record.objective)
        snapshots = {
            snapshot["snapshot_id"]: snapshot
            for snapshot in context.get("snapshots", [])
        }
        for evidence in context.get("evidence", []):
            evidence_links.append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "snapshot_id": evidence["snapshot_id"],
                    "start": evidence["start"],
                    "end": evidence["end"],
                    "quote_digest": evidence["quote_digest"],
                    "source": snapshots.get(evidence["snapshot_id"]),
                    "url": f"{_ROUTE_PREFIX}/research/{record.research_id}/evidence/{evidence['snapshot_id']}",
                }
            )
    if record.state == "completed" and result.get("artifact_set_id"):
        manifest = await get_experimental_artifact_set(
            result["artifact_set_id"], request
        )
        actions["manifest"] = {
            "method": "GET",
            "url": f"{_ROUTE_PREFIX}/artifact-sets/{result['artifact_set_id']}",
        }
        for layer, url in result.get("artifacts", {}).items():
            actions[f"render_{layer}"] = {"method": "GET", "url": url, "audited": True}
            actions[f"export_{layer}"] = {
                "method": "GET",
                "url": url,
                "media_type": "text/markdown",
            }
        actions["followup_preview"] = {
            "method": "POST",
            "url": "/v2/followup/preview",
            "requires_explicit_selection": True,
            "executes": False,
            "available": importlib.util.find_spec("agent.routes.followup") is not None,
        }
        actions["attach"] = {
            "method": "POST",
            "url_template": f"{_ROUTE_PREFIX}/sessions/{{session_id}}/attachments",
            "available": importlib.util.find_spec("agent.session_scope") is not None,
            "mode": "attachment_only",
            "body": {
                "run_id": run_id,
                "expected_revision": "required session revision",
            },
        }
    elif record.state in {"accepted", "running", "cancel_requested"}:
        actions["cancel"] = {
            "method": "POST",
            "url": f"{_ROUTE_PREFIX}/runs/{run_id}/cancel",
        }
    return {
        **_record_projection(record),
        "status_url": f"{_ROUTE_PREFIX}/workspace/{run_id}",
        "execution_history_available": record.durable_ledger is not None
        or record.journey is not None,
        "events_url": f"{_ROUTE_PREFIX}/runs/{run_id}/events"
        if record.durable_ledger is not None or record.journey is not None
        else None,
        "objective": record.objective,
        "document_context": {
            "mode": "explicit_session_attachment",
            "available": importlib.util.find_spec("agent.routes.documents") is not None,
            "research_attachment_mode": "attachment_only; does not copy root sources into query context",
            "list_url_template": "/v2/session/{session_id}/documents",
            "query_url_template": "/v2/session/{session_id}/step",
        },
        "revision": revision,
        "manifest": manifest,
        "evidence": evidence_links,
        "actions": actions,
        "capabilities": capability_document(),
        "lifetime": lifetime,
        "evidence_recovery": "postgres_artifact_authority"
        if record.artifact_authority is not None
        else "valkey_retained"
        if record.durable_evidence
        or (record.durable_ledger is not None and record.journey is not None)
        else "process_local"
        if record.journey is not None
        else "unavailable",
    }


class WorkspaceActionRequest(BaseModel):
    """An explicit operation on one selected immutable retained revision."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "render",
        "export",
        "request_evidence",
        "attach",
        "cancel",
        "delete",
        "documents",
        "document_evidence",
        "followup_preview",
    ]
    expected_revision: str | None = Field(default=None, max_length=200)
    wording: str | None = Field(default=None, min_length=1, max_length=4000)
    selected: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    ref_id: str | None = Field(default=None, min_length=1, max_length=200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)
    layer: Literal["summary", "analysis", "dossier"] = "summary"
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=200)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_session_revision: int | None = Field(default=None, strict=True, ge=0)


@router.post(f"{_ROUTE_PREFIX}/workspace/{{run_id}}/actions")
async def resume_workspace_action(
    run_id: str, payload: WorkspaceActionRequest, request: Request
) -> Any:
    """Resume a named operation without replaying research or injecting history."""
    selected = await inspect_research_workspace(
        run_id, request, payload.expected_revision
    )
    if selected["revision"] is not None and payload.expected_revision is None:
        raise HTTPException(
            status_code=409, detail="Expected research revision is required"
        )
    if payload.action == "delete":
        return await delete_experimental_research(selected["research_id"], request)
    if payload.action == "cancel":
        if "cancel" not in selected["actions"]:
            raise HTTPException(status_code=409, detail="Research run is terminal")
        return await cancel_experimental_research_run(run_id, request)
    if payload.action == "followup_preview":
        if importlib.util.find_spec("agent.routes.followup") is None:
            raise HTTPException(
                status_code=503, detail="Follow-up preview capability unavailable"
            )
        from ..followup import FollowupRequest
        from .followup import preview_followup

        if payload.wording is None:
            raise HTTPException(status_code=422, detail="Explicit wording is required")
        try:
            body = FollowupRequest.model_validate(
                {"wording": payload.wording, "selected": payload.selected}
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail="Invalid explicit follow-up selection"
            ) from exc
        return await preview_followup(request, body)
    if payload.action in {"documents", "document_evidence", "attach"}:
        if importlib.util.find_spec("agent.session_scope") is None or (
            payload.action != "attach"
            and importlib.util.find_spec("agent.routes.documents") is None
        ):
            raise HTTPException(
                status_code=503, detail="Owned session capability unavailable"
            )
        if payload.session_id is None:
            raise HTTPException(status_code=422, detail="Explicit session is required")
        from ..session_scope import authorize_session
        from ..session_store import SessionStore
        from ._helpers import _get_redis_url

        await authorize_session(
            SessionStore(redis_url=_get_redis_url(request)), payload.session_id, request
        )
        if payload.action == "documents":
            from .documents import list_session_documents

            return await list_session_documents(payload.session_id, request)
        if payload.action == "document_evidence":
            from .documents import read_session_document

            if payload.ref_id is None:
                raise HTTPException(
                    status_code=422, detail="Explicit document reference is required"
                )
            return await read_session_document(
                payload.session_id, payload.ref_id, request, payload.start, payload.end
            )
    if payload.action == "attach":
        if payload.session_id is None or payload.expected_session_revision is None:
            raise HTTPException(
                status_code=422, detail="Session and expected revision are required"
            )
        return await attach_experimental_research_session(
            payload.session_id,
            AttachResearchSessionRequest(
                run_id=run_id, expected_revision=payload.expected_session_revision
            ),
            request,
        )
    if payload.action == "request_evidence":
        if payload.snapshot_id is None or not any(
            item["snapshot_id"] == payload.snapshot_id for item in selected["evidence"]
        ):
            raise HTTPException(status_code=404, detail="Selected citation not found")
        return await get_experimental_evidence(
            selected["research_id"], payload.snapshot_id, request
        )
    result = selected.get("result") or {}
    artifact_url = result.get("artifacts", {}).get(payload.layer)
    if artifact_url is None:
        raise HTTPException(status_code=409, detail="Rendered artifact unavailable")
    response = await get_experimental_artifact(artifact_url.rsplit("/", 1)[-1], request)
    artifact_body = bytes(response.body)
    return {
        "run_id": run_id,
        "research_id": selected["research_id"],
        "revision": selected["revision"],
        "layer": payload.layer,
        "media_type": "text/markdown",
        "markdown": artifact_body.decode("utf-8"),
        "content_digest": hashlib.sha256(artifact_body).hexdigest(),
        "bytes": len(artifact_body),
        "audited": True,
    }
