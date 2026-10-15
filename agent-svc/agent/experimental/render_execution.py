"""Bounded local render audit execution over exact reports and pinned knowledge."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .canonical import admit_canonical_json
from .checked_knowledge import CheckedKnowledge, entities
from .knowledge_checks import Reviewer
from .knowledge_context import moment
from .knowledge_execution import ExecutionDecision
from .manifest_outputs import (
    ManifestResolver,
    resolve_manifest_knowledge,
    resolve_manifest_output,
)
from .render_manifest import (
    AUDIT_SCHEMA,
    MANIFEST_SCHEMA,
    RenderAudit,
    RenderAuditInput,
    RenderManifest,
)


@dataclass(frozen=True)
class RenderInspection:
    checked_input: RenderAuditInput
    knowledge: CheckedKnowledge
    outputs: tuple[bytes, ...]


RenderExecutor = Callable[[RenderInspection], Awaitable[ExecutionDecision]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RenderExecutionLedger:
    """Server-owned callbacks; at most 32 issued audits and one active inspection.

    Each result is at most 16 KiB, so retained results total at most 512 KiB.
    Three output bodies total at most 3 MiB while inspecting. Callbacks are trusted
    code; no sandbox, provider integration, automatic retry or durable attestation.
    """

    def __init__(
        self,
        registrations: tuple[tuple[Reviewer, RenderExecutor], ...],
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not 1 <= len(registrations) <= 32:
            raise ValueError("render executor catalogue must contain 1-32 entries")
        self._callbacks = dict(registrations)
        if len(self._callbacks) != len(registrations) or any(
            not callable(callback) for callback in self._callbacks.values()
        ):
            raise ValueError("render executors must be unique and callable")
        self._clock = clock
        self._issued: set[tuple[str, str, str]] = set()
        self._receipts: dict[str, tuple[str, bytes]] = {}
        self._cores: dict[tuple[str, str, str], str] = {}
        self._busy = False
        self._closed = False

    @property
    def reviewers(self) -> tuple[Reviewer, ...]:
        return tuple(self._callbacks)

    def close(self) -> None:
        self._closed = True
        self._receipts.clear()

    async def execute(
        self,
        supplied: RenderAuditInput,
        *,
        scope_id: str,
        research_id: str,
        revision_id: str,
        artifact_set_id: str,
        resolver: ManifestResolver,
    ) -> RenderAudit:
        document = admit_canonical_json(
            supplied.model_dump_json().encode(), schema_version=AUDIT_SCHEMA
        )
        checked = RenderAuditInput.model_validate_json(document.data)
        callback = self._callbacks.get(checked.reviewer)
        if self._closed or callback is None:
            raise ValueError("render owner closed or reviewer unconfigured")
        core = checked.manifest_core
        if (
            core.scope_id,
            core.research_id,
            core.revision_id,
            core.artifact_set_id,
        ) != (
            scope_id,
            research_id,
            revision_id,
            artifact_set_id,
        ):
            raise ValueError("render input differs from caller identity")
        identity = (scope_id, research_id, checked.input_id)
        if self._busy or len(self._issued) >= 32 or identity in self._issued:
            raise ValueError("render execution capacity or input identity exhausted")
        self._issued.add(identity)
        self._cores[identity] = hashlib.sha256(
            core.model_dump_json().encode()
        ).hexdigest()
        self._busy = True
        try:
            knowledge = await resolve_manifest_knowledge(
                core,
                scope_id=scope_id,
                research_id=research_id,
                revision_id=revision_id,
                artifact_set_id=artifact_set_id,
                resolver=resolver,
            )
            reserved_ids = {
                core.revision_id,
                core.artifact_set_id,
                *(a.artifact_id for a in core.artifacts),
                *entities(knowledge),
            }
            if checked.input_id in reserved_ids:
                raise ValueError("render audit input aliases an existing identity")
            outputs = tuple(
                [await resolve_manifest_output(a, resolver) for a in core.artifacts]
            )
            self._assert_live()
            decision = await asyncio.ensure_future(
                callback(RenderInspection(checked, knowledge, outputs))
            )
            self._assert_live()
            decision = ExecutionDecision.model_validate(decision)
            if decision.outcome not in {"pass", "fail", "indeterminate"}:
                raise ValueError("render audit requires a verification verdict")
            now = self._clock()
            offset = now.utcoffset()
            if (
                offset is None
                or offset.total_seconds() != 0
                or now < moment(core.created_at)
            ):
                raise ValueError("render clock must be UTC and follow input creation")
            audit = RenderAudit.model_validate(
                {
                    "audit_id": str(uuid4()),
                    "input_id": checked.input_id,
                    "input_digest": document.digest,
                    "verdict": decision.outcome,  # validated again by the strict result model
                    "checked_at": now.isoformat(timespec="microseconds").replace(
                        "+00:00", "Z"
                    ),
                    "reason": decision.reason,
                }
            )
            encoded = audit.model_dump_json().encode()
            if len(encoded) > 16_384:
                raise ValueError("render audit result exceeds 16 KiB")
            if (
                audit.audit_id in self._receipts
                or audit.audit_id in reserved_ids
                or audit.audit_id == checked.input_id
            ):
                raise ValueError("render audit result identity collision")
            self._receipts[audit.audit_id] = (document.digest, encoded)
            return audit
        finally:
            self._busy = False

    def _assert_live(self) -> None:
        owner = asyncio.current_task()
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
        if self._closed:
            raise ValueError("render owner closed before completion")

    def check_bindings(self, manifest: RenderManifest) -> bool:
        """Check live exact receipts, returning fixture provenance, not quality."""
        document = admit_canonical_json(
            manifest.model_dump_json().encode(), schema_version=MANIFEST_SCHEMA
        )
        checked = RenderManifest.model_validate_json(document.data)
        if self._closed:
            raise ValueError("render owner closed")
        for audit in checked.audits:
            if self._receipts.get(audit.audit_id) != (
                audit.input_digest,
                audit.model_dump_json().encode(),
            ):
                raise ValueError("audit was not returned by this render owner")
        core_digest = hashlib.sha256(
            checked.core().model_dump_json().encode()
        ).hexdigest()
        issued_inputs = {
            key[2] for key, digest in self._cores.items() if digest == core_digest
        }
        if issued_inputs != {i.input_id for i in checked.audit_inputs}:
            raise ValueError("manifest omits issued audits for this core")
        return any(i.reviewer.kind == "fixture" for i in checked.audit_inputs)
