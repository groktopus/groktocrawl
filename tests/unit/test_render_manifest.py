"""Pinned output bytes and audit inputs cannot silently diverge."""

import asyncio
import hashlib
from copy import deepcopy

import pytest
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.checked_knowledge import CHECKED_SCHEMA
from agent.experimental.manifest_outputs import ResolvedOutput, admit_render_manifest
from agent.experimental.render_manifest import (
    AUDIT_SCHEMA,
    MANIFEST_SCHEMA,
    RenderAuditInput,
)

from tests.unit.test_checked_knowledge import REVIEWER, encode, record
from tests.unit.test_knowledge_context import context_payload  # noqa: F401

BODY = "Fixture 🧪 report."
BODY_BYTES = BODY.encode()


def audits(value):
    core = {k: v for k, v in value.items() if k not in {"audit_inputs", "audits"}}
    checked = {
        "schema_version": AUDIT_SCHEMA,
        "input_id": "render-input",
        "reviewer": REVIEWER.model_dump(),
        "manifest_core": deepcopy(core),
    }
    digest = RenderAuditInput.model_validate_json(encode(checked)).input_digest()
    value["audit_inputs"] = [checked]
    value["audits"] = [
        {
            "audit_id": "render-audit",
            "input_id": "render-input",
            "input_digest": digest,
            "verdict": "pass",
            "checked_at": value["created_at"],
            "reason": "Authored fixture only",
        }
    ]


@pytest.fixture
def payload(request):
    knowledge = record(request.getfixturevalue("context_payload")["context"])
    context = knowledge["context"]
    value = {
        "schema_version": MANIFEST_SCHEMA,
        "scope_id": context["scope_id"],
        "research_id": context["research_id"],
        "revision_id": context["revision_id"],
        "artifact_set_id": "outputs-1",
        "revision_digest": admit_canonical_json(
            encode(knowledge), schema_version=CHECKED_SCHEMA
        ).digest,
        "created_at": "2026-09-09T00:00:00Z",
        "renderer": {
            "identity": "fixture",
            "version": "1",
            "configuration_digest": "a" * 64,
        },
        "coverage": "complete",
        "artifacts": [],
    }
    for layer in ("summary", "analysis", "dossier"):
        value["artifacts"].append(
            {
                "artifact_id": layer,
                "layer": layer,
                "content_ref": {
                    "scope_id": context["scope_id"],
                    "research_id": context["research_id"],
                    "artifact_set_id": "outputs-1",
                    "artifact_id": layer,
                },
                "content_digest": hashlib.sha256(BODY.encode()).hexdigest(),
                "content_bytes": len(BODY.encode()),
                "statements": [
                    {
                        "start": 0,
                        "end": len(BODY),
                        "text": BODY,
                        "claim_ids": ["c1"],
                        "evidence_ids": ["e1"],
                    }
                ],
                "question_ids": ["q1"],
                "conflict_ids": [],
            }
        )
    audits(value)
    return value, knowledge


class Resolver:
    def __init__(self, knowledge, body=BODY_BYTES):
        self.knowledge = encode(knowledge)
        self.body = body
        self.calls = []

    async def resolve_revision(self, *identity):
        self.calls.append(identity)
        return self.knowledge

    async def resolve_output(self, reference):
        self.calls.append(reference.artifact_id)
        return ResolvedOutput(reference, self.body)


async def admit(value, resolver, **kwargs):
    args = {
        k: value[k]
        for k in ("scope_id", "research_id", "revision_id", "artifact_set_id")
    }
    args.update(kwargs)
    return await admit_render_manifest(
        encode(value), resolver=resolver, reviewers=(REVIEWER,), **args
    )


@pytest.mark.asyncio
async def test_exact_manifest_round_trip_and_separate_audit_digest(payload):
    value, knowledge = payload
    resolver = Resolver(knowledge)
    result = await admit(value, resolver)
    assert result.manifest.core() == result.manifest.audit_inputs[0].manifest_core
    assert len(resolver.calls) == 4
    assert (
        result.document.digest
        == "65414d165b83e1811f2170681b983504ac1b05a76b492d9c9cb6c587793b9626"
    )
    assert (
        result.manifest.audits[0].input_digest
        == "28a636554ddeff735977b1d1163d1de3092a29ef350d7d61b05856f8f29455c8"
    )
    second = await admit_render_manifest(
        result.document.data,
        scope_id=value["scope_id"],
        research_id=value["research_id"],
        revision_id=value["revision_id"],
        artifact_set_id=value["artifact_set_id"],
        resolver=resolver,
        reviewers=(REVIEWER,),
    )
    assert second.document == result.document
    before = value["audits"][0]["input_digest"]
    value["audits"][0]["reason"] = "Different explanation"
    changed = await admit(value, resolver)
    assert changed.document.digest != result.document.digest
    assert changed.manifest.audits[0].input_digest == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "version",
        "extra",
        "missing",
        "coercion",
        "duplicate-layer",
        "cross-scope",
        "input-core",
        "digest",
        "early-audit",
        "duplicate-result",
    ],
)
async def test_malformed_or_unbound_manifests_fail_before_resolution(payload, change):
    value, knowledge = payload
    if change == "version":
        value["schema_version"] = "render-manifest/1"
    elif change == "extra":
        value["trusted"] = True
    elif change == "missing":
        del value["renderer"]["configuration_digest"]
    elif change == "coercion":
        value["artifacts"][0]["content_bytes"] = str(len(BODY.encode()))
    elif change == "duplicate-layer":
        value["artifacts"][0]["layer"] = "analysis"
    elif change == "cross-scope":
        value["artifacts"][0]["content_ref"]["scope_id"] = "elsewhere"
    elif change == "input-core":
        value["audit_inputs"][0]["manifest_core"]["renderer"]["version"] = "2"
    elif change == "digest":
        value["audits"][0]["input_digest"] = "0" * 64
    elif change == "early-audit":
        value["audits"][0]["checked_at"] = "2020-01-01T00:00:00Z"
    else:
        value["audits"].append(deepcopy(value["audits"][0]))
    resolver = Resolver(knowledge)
    with pytest.raises(ValueError):
        await admit(value, resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_independent_caller_scope_and_reviewer_catalogue_precede_io(payload):
    value, knowledge = payload
    resolver = Resolver(knowledge)
    with pytest.raises(ValueError, match="caller identity"):
        await admit(value, resolver, scope_id="other")
    value["audit_inputs"][0]["reviewer"]["version"] = "2"
    value["audits"][0]["input_digest"] = RenderAuditInput.model_validate_json(
        encode(value["audit_inputs"][0])
    ).input_digest()
    with pytest.raises(ValueError, match="not configured"):
        await admit(value, resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "pin",
        "coverage",
        "unknown-claim",
        "unknown-evidence",
        "question",
        "conflict",
        "entity-alias",
        "early-render",
    ],
)
async def test_semantically_consistent_manifest_must_match_pinned_knowledge(
    payload, change
):
    value, knowledge = payload
    if change == "pin":
        value["revision_digest"] = "0" * 64
    elif change == "coverage":
        value["coverage"] = "partial"
    elif change == "unknown-claim":
        value["artifacts"][0]["statements"][0]["claim_ids"] = ["not-local"]
    elif change == "unknown-evidence":
        value["artifacts"][0]["statements"][0]["evidence_ids"] = ["not-local"]
    elif change == "question":
        value["artifacts"][0]["question_ids"] = ["not-local"]
    elif change == "conflict":
        value["artifacts"][0]["conflict_ids"] = ["not-local"]
    elif change == "entity-alias":
        value["artifact_set_id"] = "c1"
        [a["content_ref"].update(artifact_set_id="c1") for a in value["artifacts"]]
    else:
        value["created_at"] = "2020-01-01T00:00:00Z"
    audits(value)
    resolver = Resolver(knowledge)
    with pytest.raises(ValueError):
        await admit(value, resolver)
    assert len(resolver.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"changed", b"\xff", b"x" * len(BODY.encode())])
async def test_output_bytes_cannot_be_substituted(payload, body):
    value, knowledge = payload
    with pytest.raises(ValueError):
        await admit(value, Resolver(knowledge, body))


@pytest.mark.asyncio
async def test_consistent_hashes_do_not_waive_unicode_span_checks(payload):
    value, knowledge = payload
    for artifact in value["artifacts"]:
        artifact["statements"][0]["text"] = "X" * len(BODY)
    audits(value)
    with pytest.raises(ValueError, match="output span"):
        await admit(value, Resolver(knowledge))


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["fail", "indeterminate"])
async def test_negative_audits_remain_inspectable_not_publication_permission(
    payload, verdict
):
    value, knowledge = payload
    value["audits"][0]["verdict"] = verdict
    result = await admit(value, Resolver(knowledge))
    assert result.manifest.audits[0].verdict == verdict


@pytest.mark.asyncio
async def test_resolver_cancellation_propagates(payload):
    value, knowledge = payload

    class Cancelled(Resolver):
        async def resolve_output(self, reference):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await admit(value, Cancelled(knowledge))


@pytest.mark.asyncio
async def test_matching_descriptor_does_not_make_invalid_utf8_admissible(payload):
    value, knowledge = payload
    for artifact in value["artifacts"]:
        artifact["content_digest"] = hashlib.sha256(b"\xff").hexdigest()
        artifact["content_bytes"] = 1
    audits(value)
    with pytest.raises(UnicodeDecodeError):
        await admit(value, Resolver(knowledge, b"\xff"))


@pytest.mark.asyncio
async def test_oversized_envelope_is_rejected_before_resolution(payload):
    value, knowledge = payload
    resolver = Resolver(knowledge)
    raw = encode(value) + b" " * 1_048_576
    with pytest.raises(ValueError):
        await admit_render_manifest(
            raw,
            scope_id=value["scope_id"],
            research_id=value["research_id"],
            revision_id=value["revision_id"],
            artifact_set_id=value["artifact_set_id"],
            resolver=resolver,
            reviewers=(REVIEWER,),
        )
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_checkless_record_is_inspectable_but_not_verified(payload):
    value, knowledge = payload
    knowledge["verifications"] = []
    knowledge["verification_inputs"] = []
    knowledge["assessments"] = []
    knowledge["assessment_links"][0].update(state="unassessed", assessment_ids=[])
    from tests.unit.test_checked_knowledge import declarations

    declarations(knowledge)
    value["revision_digest"] = admit_canonical_json(
        encode(knowledge), schema_version=CHECKED_SCHEMA
    ).digest
    audits(value)
    result = await admit(value, Resolver(knowledge))
    assert not result.knowledge.verifications
