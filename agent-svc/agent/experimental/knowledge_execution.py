"""Process-local evidence that configured callbacks returned exact check results."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter

from .canonical import MAX_BYTES, admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge
from .knowledge import Text
from .knowledge_checks import (
    CheckAssessment,
    CheckResult,
    KnowledgeCheckInput,
    Reviewer,
)
from .knowledge_context import StrictRecord, moment


class ExecutionDecision(StrictRecord):
    outcome: Literal[
        "pass",
        "fail",
        "indeterminate",
        "supported",
        "contested",
        "insufficient",
        "refuted",
    ]
    reason: Text


CheckExecutor = Callable[[KnowledgeCheckInput], Awaitable[ExecutionDecision]]
ExecutedCheck = CheckResult | CheckAssessment
_REVIEWER: TypeAdapter[Reviewer] = TypeAdapter(Reviewer)


def _now() -> datetime:
    return datetime.now(UTC)


class KnowledgeExecutionLedger:
    """One trusted controller/event-loop owner; no serialized receipt admission.

    Registrations and clock are server configuration, never request parameters.
    Callbacks are trusted code, not sandboxed or semantically validated here.
    """

    def __init__(
        self,
        registrations: tuple[tuple[Reviewer, CheckExecutor], ...],
        *,
        max_operations: int = 64,
        max_inflight: int = 8,
        max_result_bytes: int = 16_384,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        limits = (
            (max_operations, 6000),
            (max_inflight, 8),
            (max_result_bytes, MAX_BYTES),
        )
        if any(
            type(value) is not int or not 1 <= value <= limit for value, limit in limits
        ):
            raise ValueError("execution ledger limits are invalid")
        if not 1 <= len(registrations) <= 32:
            raise ValueError("register between one and 32 trusted executors")
        self._executors: dict[str, CheckExecutor] = {}
        for reviewer, executor in registrations:
            checked = _REVIEWER.validate_json(reviewer.model_dump_json())
            key = checked.model_dump_json()
            if key in self._executors or not callable(executor):
                raise ValueError("executor registrations must be unique and callable")
            self._executors[key] = executor
        self._clock = clock
        self._max_operations = max_operations
        self._max_inflight = max_inflight
        self._max_result_bytes = max_result_bytes
        self._issued: set[tuple[str, str, str]] = set()
        self._completed: dict[str, tuple[str, str, bytes]] = {}
        self._contexts: dict[tuple[str, str, str], str] = {}
        self._retained = 0
        self._reserved = 0
        self._inflight = 0
        self._active = True

    def close(self) -> None:
        """Invalidate receipts and prevent late callback completion from recording."""
        self._active = False
        self._completed.clear()
        self._retained = 0
        self._issued.clear()
        self._contexts.clear()

    async def execute(self, supplied: KnowledgeCheckInput) -> ExecutedCheck:
        document = admit_canonical_json(
            supplied.model_dump_json().encode(),
            schema_version="knowledge-check-input-prototype/1",
        )
        checked = KnowledgeCheckInput.model_validate_json(document.data)
        key = checked.reviewer.model_dump_json()
        executor = self._executors.get(key)
        identity = (
            checked.context.scope_id,
            checked.context.research_id,
            checked.input_id,
        )
        if not self._active or executor is None:
            raise ValueError("execution owner is closed or reviewer is not configured")
        if identity in self._issued:
            raise ValueError("check input identity has already been issued")
        if (
            len(self._issued) >= self._max_operations
            or self._inflight >= self._max_inflight
            or self._retained + self._reserved + self._max_result_bytes > MAX_BYTES
        ):
            raise ValueError("execution capacity exhausted")
        self._issued.add(identity)
        self._contexts[identity] = hashlib.sha256(
            checked.context.model_dump_json().encode()
        ).hexdigest()
        self._inflight += 1
        self._reserved += self._max_result_bytes
        try:
            # A child task cannot erase the owner's cancellation count by uncancelling itself.
            decision = await asyncio.ensure_future(executor(checked))
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                raise asyncio.CancelledError
            if not self._active:
                raise ValueError("execution owner closed before callback completed")
            decision = ExecutionDecision.model_validate(decision)
            result = self._result(checked, decision, document.digest)
            encoded = result.model_dump_json().encode()
            if len(encoded) > self._max_result_bytes:
                raise ValueError("executor result exceeds reserved byte limit")
            result_id = (
                result.assessment_id
                if isinstance(result, CheckAssessment)
                else result.verification_id
            )
            if result_id in self._completed:
                raise ValueError("execution result identity collision")
            self._completed[result_id] = (document.digest, key, encoded)
            self._retained += len(encoded)
            return result
        finally:
            self._reserved -= self._max_result_bytes
            self._inflight -= 1

    def _result(
        self, checked: KnowledgeCheckInput, decision: ExecutionDecision, digest: str
    ) -> ExecutedCheck:
        now = self._clock()
        offset = now.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("execution clock must return explicit UTC")
        earliest = moment(checked.context.created_at)
        if checked.freshness is not None:
            earliest = max(earliest, moment(checked.freshness.evaluated_at))
        if now < earliest:
            raise ValueError("execution clock predates checked inputs")
        common = {
            "input_id": checked.input_id,
            "input_digest": digest,
            "checked_at": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "reason": decision.reason,
        }
        identity = str(uuid4())
        if checked.check_type == "assessment":
            return CheckAssessment.model_validate(
                {**common, "assessment_id": identity, "outcome": decision.outcome}
            )
        result = CheckResult.model_validate(
            {**common, "verification_id": identity, "verdict": decision.outcome}
        )
        if result.verdict == "pass" and not checked.freshness_allows_pass():
            raise ValueError("freshness basis cannot authorize executor pass")
        return result

    def check_bindings(self, supplied: CheckedKnowledge) -> bool:
        """Require exact locally executed results; return whether any are fixtures.

        This is not publication eligibility or semantic quality. Call again at use;
        a previously returned bool does not survive owner closure or process loss.
        """
        document = admit_canonical_json(
            supplied.model_dump_json().encode(), schema_version=CHECKED_SCHEMA
        )
        knowledge = CheckedKnowledge.model_validate_json(document.data)
        if not self._active:
            raise ValueError("execution owner is closed")
        inputs = {item.input_id: item for item in knowledge.verification_inputs}
        results: tuple[ExecutedCheck, ...] = (
            *knowledge.verifications,
            *knowledge.assessments,
        )
        if not results:
            raise ValueError("knowledge has no executed check results")
        for result in results:
            checked = inputs[result.input_id]
            identity = (
                result.assessment_id
                if isinstance(result, CheckAssessment)
                else result.verification_id
            )
            expected = (
                checked.input_digest(),
                checked.reviewer.model_dump_json(),
                result.model_dump_json().encode(),
            )
            if self._completed.get(identity) != expected:
                raise ValueError(
                    "knowledge result was not returned by this execution owner"
                )
        context_digest = hashlib.sha256(
            knowledge.context.model_dump_json().encode()
        ).hexdigest()
        issued_inputs = {
            key[2] for key, digest in self._contexts.items() if digest == context_digest
        }
        if issued_inputs != set(inputs):
            raise ValueError("knowledge omits issued checks for this context")
        return any(
            item.reviewer.kind == "fixture" for item in knowledge.verification_inputs
        )
