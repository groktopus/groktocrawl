"""Experimental W12.3 boundary for blinded claim-level verification."""

import json
from typing import Literal, Self

from pydantic import Field, model_validator

from .knowledge import Digest, Identity, Record, Text, text_digest


class VerificationEvidenceSpan(Record):
    span_id: Identity
    source_id: Identity
    source_kind: Literal["primary", "independent", "derivative"]
    temporal_status: Literal["current", "historical", "unknown"]
    text: Text
    contains_instructions: bool = False


class IndependentVerifier(Record):
    route: Identity
    model: Identity
    prompt_version: Identity


class ClaimVerificationPacket(Record):
    schema_version: Literal["claim-verification-input/1"]
    case_id: Identity
    claim_id: Identity
    claim: Text
    risk: Literal["low", "high"]
    evidence_obligation: Text
    evidence: tuple[VerificationEvidenceSpan, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_evidence(self) -> Self:
        spans = [item.span_id for item in self.evidence]
        if len(spans) != len(set(spans)):
            raise ValueError("verification evidence span IDs must be unique")
        return self

    def input_digest(self) -> str:
        checked = ClaimVerificationPacket.model_validate(self)
        encoded = json.dumps(
            checked.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return text_digest("claim-verification-input/1\0" + encoded)


class IndependentClaimVerification(Record):
    schema_version: Literal["independent-claim-verification/1"]
    verification_id: Identity
    verifier: IndependentVerifier
    checked_input: ClaimVerificationPacket
    checked_input_digest: Digest
    verdict: Literal["supported", "contradicted", "insufficient", "indeterminate"]
    evidence_span_ids: tuple[Identity, ...] = Field(max_length=20)
    contradiction_span_ids: tuple[Identity, ...] = Field(max_length=20)
    confidence: int = Field(strict=True, ge=0, le=100)
    publish_recommendation: bool
    reason: Text

    @model_validator(mode="after")
    def bind_to_exact_input(self) -> Self:
        if self.checked_input_digest != self.checked_input.input_digest():
            raise ValueError("verification input digest mismatch")
        available = {item.span_id for item in self.checked_input.evidence}
        if len(self.evidence_span_ids) != len(set(self.evidence_span_ids)) or len(
            self.contradiction_span_ids
        ) != len(set(self.contradiction_span_ids)):
            raise ValueError("verification evidence references must be unique")
        cited = set(self.evidence_span_ids) | set(self.contradiction_span_ids)
        if not cited <= available:
            raise ValueError("verification references unavailable evidence")
        if not set(self.contradiction_span_ids) <= set(self.evidence_span_ids):
            raise ValueError("contradiction spans must be included in cited evidence")
        if self.verdict == "supported" and not self.evidence_span_ids:
            raise ValueError("supported verdict requires supporting evidence")
        if self.verdict == "contradicted" and not self.contradiction_span_ids:
            raise ValueError("contradicted verdict requires contradictory evidence")
        if self.publish_recommendation and (
            self.verdict != "supported" or self.confidence < 70
        ):
            raise ValueError("publication recommendation exceeds verifier verdict")
        return self


class VerificationReference(Record):
    verdict: Literal["supported", "contradicted", "insufficient", "indeterminate"]
    publish: bool
    critical_false_accept: bool
    required_span_ids: tuple[Identity, ...] = Field(max_length=20)
    reason: Text


class ClaimVerificationCase(Record):
    schema_version: Literal["claim-verification-case/1"]
    packet: ClaimVerificationPacket
    control_publish: bool
    reference: VerificationReference
    stratum: Literal[
        "supported",
        "unsupported",
        "missing_evidence",
        "contradiction",
        "abstention",
        "stale",
        "derivative",
        "ambiguous_identity",
        "hostile_source",
        "high_consequence",
    ]

    @model_validator(mode="after")
    def local_reference_spans(self) -> Self:
        available = {item.span_id for item in self.packet.evidence}
        if not set(self.reference.required_span_ids) <= available:
            raise ValueError("reference requires unavailable evidence")
        return self


class ClaimVerificationCorpus(Record):
    schema_version: Literal["claim-verification-corpus/1"]
    cases: tuple[ClaimVerificationCase, ...] = Field(min_length=12, max_length=100)

    @model_validator(mode="after")
    def distinct_cases(self) -> Self:
        identities = [case.packet.case_id for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("claim verification case IDs must be unique")
        if sum(case.reference.publish for case in self.cases) < 4:
            raise ValueError("corpus requires at least four publishable claims")
        high_risk_false_accepts = sum(
            case.packet.risk == "high"
            and case.control_publish
            and not case.reference.publish
            for case in self.cases
        )
        if high_risk_false_accepts < 3:
            raise ValueError("corpus requires at least three high-risk false accepts")
        return self


def load_claim_verification_corpus(path: str) -> ClaimVerificationCorpus:
    with open(path, encoding="utf-8") as handle:
        return ClaimVerificationCorpus.model_validate(json.load(handle))


def validate_independent_verification(
    payload: object,
    *,
    packet: ClaimVerificationPacket,
    verifier: IndependentVerifier,
) -> IndependentClaimVerification:
    """Bind untrusted verifier output to caller-established input and identity."""
    result = IndependentClaimVerification.model_validate(payload)
    if result.checked_input != ClaimVerificationPacket.model_validate(packet):
        raise ValueError("verification differs from expected input")
    if result.verifier != IndependentVerifier.model_validate(verifier):
        raise ValueError("verification differs from expected verifier")
    return result


def verifier_output_schema(packet: ClaimVerificationPacket) -> dict:
    span_ids = [item.span_id for item in packet.evidence]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["supported", "contradicted", "insufficient", "indeterminate"],
            },
            "evidence_span_ids": {
                "type": "array",
                "uniqueItems": True,
                "maxItems": 20,
                "items": {"type": "string", "enum": span_ids},
            },
            "contradiction_span_ids": {
                "type": "array",
                "uniqueItems": True,
                "maxItems": 20,
                "items": {"type": "string", "enum": span_ids},
            },
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "publish_recommendation": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1, "maxLength": 3000},
        },
        "required": [
            "verdict",
            "evidence_span_ids",
            "contradiction_span_ids",
            "confidence",
            "publish_recommendation",
            "reason",
        ],
    }
