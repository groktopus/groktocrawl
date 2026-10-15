"""Consolidated context rejects malformed identities and unresolved exact evidence."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from agent.experimental.context_sources import (
    ResolvedContextSource,
    admit_knowledge_context,
)
from agent.experimental.knowledge_context import ContentReference

BODY = "A 🧪 café source."


@pytest.fixture
def context_payload():
    digest = hashlib.sha256(BODY.encode()).hexdigest()
    return {
        "schema_version": "knowledge-context-prototype/1",
        "context": {
            "scope_id": "owner",
            "research_id": "research",
            "revision_id": "rev1",
            "parent_revision_id": None,
            "parent_digest": None,
            "created_at": "2026-09-06T00:00:00Z",
            "objective": "Inspect synthetic evidence",
            "as_of": None,
            "policy_version": "fixture-policy/1",
            "snapshots": [
                {
                    "snapshot_id": "s1",
                    "canonical_url": "https://example.test/source",
                    "retrieved_at": "2026-09-05T00:00:00Z",
                    "normalization_version": "utf8-exact/1",
                    "media_type": "text/plain",
                    "content_ref": {
                        "scope_id": "owner",
                        "research_id": "research",
                        "snapshot_id": "s1",
                    },
                    "content_digest": digest,
                    "content_bytes": len(BODY.encode()),
                    "published_at": None,
                    "effective_at": None,
                    "origin_id": None,
                    "lineage_id": None,
                }
            ],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "snapshot_id": "s1",
                    "start": 0,
                    "end": len(BODY),
                    "quote": BODY,
                    "quote_digest": digest,
                }
            ],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "Source attribution",
                    "kind": "source_statement",
                    "qualifiers": ["Synthetic"],
                    "temporal_scope": "historical",
                }
            ],
            "relationships": [
                {
                    "relationship_id": "edge1",
                    "kind": "supports",
                    "source_id": "e1",
                    "target_id": "c1",
                    "rationale": "Fixture only",
                    "rule": None,
                    "assumptions": [],
                }
            ],
            "questions": [
                {
                    "question_id": "q1",
                    "question": "What does the source say?",
                    "status": "answered",
                    "report_claim_id": "c1",
                }
            ],
            "conflicts": [],
        },
    }


class Resolver:
    def __init__(self):
        self.calls = []
        self.result = ResolvedContextSource(
            ContentReference(
                scope_id="owner", research_id="research", snapshot_id="s1"
            ),
            BODY.encode(),
            "utf8-exact/1",
            "text/plain",
        )
        self.error = None

    async def resolve(self, reference):
        self.calls.append(reference)
        if self.error is not None:
            raise self.error
        return self.result


async def admit(payload, resolver=None, **kwargs):
    return await admit_knowledge_context(
        json.dumps(payload, ensure_ascii=False).encode(),
        scope_id=kwargs.get("scope_id", "owner"),
        research_id="research",
        revision_id="rev1",
        resolver=resolver or Resolver(),
    )


@pytest.mark.asyncio
async def test_exact_unicode_source_roundtrip_and_owned_canonical_bytes(
    context_payload,
):
    resolver = Resolver()
    result = await admit(context_payload, resolver)
    assert len(resolver.calls) == 1
    assert result.context.evidence[0].quote == BODY
    assert result.context.snapshots[0].content_bytes > len(BODY)
    assert json.loads(result.document.data) == context_payload
    assert await admit(json.loads(result.document.data)) == result
    assert not hasattr(result, "bodies")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["as_of", "parent_digest", "objective"])
async def test_missing_explicit_context_fields_fail_before_resolution(
    context_payload, field
):
    del context_payload["context"][field]
    resolver = Resolver()
    with pytest.raises(ValueError):
        await admit(context_payload, resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "version",
        "missing_date",
        "missing_rule",
        "integer_string",
        "integer_bool",
        "fraction",
        "invalid_date",
        "offset_date",
        "future",
        "parent_pair",
        "self_parent",
        "scope",
        "alias",
        "missing_snapshot",
        "target",
        "unsupported_media",
    ],
)
async def test_malformed_context_fails_before_source_access(context_payload, change):
    c = context_payload["context"]
    s = c["snapshots"][0]
    if change == "unknown":
        c["human_reviewed"] = True
    elif change == "version":
        context_payload["schema_version"] = "knowledge-ir/1"
    elif change == "missing_date":
        del s["published_at"]
    elif change == "missing_rule":
        del c["relationships"][0]["rule"]
    elif change == "integer_string":
        s["content_bytes"] = str(s["content_bytes"])
    elif change == "integer_bool":
        c["evidence"][0]["start"] = False
    elif change == "fraction":
        c["evidence"][0]["start"] = 0.5
    elif change == "invalid_date":
        c["created_at"] = "2026-02-30T00:00:00Z"
    elif change == "offset_date":
        c["created_at"] = "2026-09-06T00:00:00+00:00"
    elif change == "future":
        s["retrieved_at"] = "2027-01-01T00:00:00Z"
    elif change == "parent_pair":
        c["parent_revision_id"] = "rev0"
    elif change == "self_parent":
        c.update(parent_revision_id="rev1", parent_digest="0" * 64)
    elif change == "scope":
        s["content_ref"]["scope_id"] = "other"
    elif change == "alias":
        c["questions"][0]["question_id"] = "c1"
    elif change == "missing_snapshot":
        c["evidence"][0]["snapshot_id"] = "missing"
    elif change == "target":
        c["relationships"][0]["target_id"] = "missing"
    else:
        s["media_type"] = "application/pdf"
    resolver = Resolver()
    with pytest.raises(ValueError):
        await admit(context_payload, resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_caller_identity_checked_before_resolution(context_payload):
    resolver = Resolver()
    with pytest.raises(ValueError, match="caller identity"):
        await admit(context_payload, resolver, scope_id="other")
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "body",
        "length",
        "normalization",
        "media",
        "reference",
        "span",
        "quote",
        "quote_hash",
    ],
)
async def test_resolver_and_evidence_mismatches_are_rejected(context_payload, change):
    resolver = Resolver()
    if change == "body":
        resolver.result = replace(resolver.result, body=b"x" * len(BODY.encode()))
    elif change == "length":
        resolver.result = replace(resolver.result, body=b"short")
    elif change == "normalization":
        resolver.result = replace(resolver.result, normalization_version="other/1")
    elif change == "media":
        resolver.result = replace(resolver.result, media_type="text/markdown")
    elif change == "reference":
        resolver.result = replace(
            resolver.result,
            reference=ContentReference(
                scope_id="other", research_id="research", snapshot_id="s1"
            ),
        )
    else:
        e = context_payload["context"]["evidence"][0]
        if change == "span":
            e["end"] = len(BODY.encode())
        elif change == "quote":
            e["quote"] = "wrong"
        else:
            e["quote_digest"] = "0" * 64
    with pytest.raises(ValueError):
        await admit(context_payload, resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [PermissionError("denied"), LookupError("deleted"), asyncio.CancelledError()],
)
async def test_authority_failure_and_cancellation_propagate(context_payload, error):
    resolver = Resolver()
    resolver.error = error
    with pytest.raises(type(error)):
        await admit(context_payload, resolver)
    assert len(resolver.calls) == 1


@pytest.mark.asyncio
async def test_conflicting_claim_group_and_derivation_cycle(context_payload):
    c = context_payload["context"]
    c["claims"].append(
        {**c["claims"][0], "claim_id": "c2", "text": "Conflicting statement"}
    )
    c["evidence"].append({**c["evidence"][0], "evidence_id": "e2"})
    c["relationships"][0].update(kind="contradicts")
    c["questions"][0]["status"] = "unresolved"
    c["conflicts"] = [
        {
            "conflict_id": "conflict1",
            "question_id": "q1",
            "claim_ids": ["c1", "c2"],
            "evidence_ids": ["e1", "e2"],
            "reason": "Synthetic disagreement",
        }
    ]
    assert len((await admit(context_payload)).context.conflicts[0].claim_ids) == 2
    c["conflicts"][0]["claim_ids"] = ["c2"]
    with pytest.raises(ValueError, match="matching conflict"):
        await admit(context_payload)
    c["conflicts"][0]["claim_ids"] = ["c1", "c2"]
    for claim in c["claims"]:
        claim["kind"] = "inference"
    c["relationships"] = [
        {
            "relationship_id": f"d{i}",
            "kind": "derived_from",
            "source_id": source,
            "target_id": target,
            "rationale": "Test",
            "rule": "Test rule",
            "assumptions": [],
        }
        for i, (source, target) in enumerate([("c1", "c2"), ("c2", "c1")])
    ]
    with pytest.raises(ValueError, match="acyclic"):
        await admit(context_payload)


@pytest.mark.asyncio
async def test_source_body_limit_and_long_text_are_independent_of_quote_limit(
    context_payload,
):
    resolver = Resolver()
    body = b"a" * (10 * 1024 * 1024 - 4) + "🧪".encode()
    resolver.result = replace(resolver.result, body=body)
    c = context_payload["context"]
    c["snapshots"][0].update(
        content_bytes=len(body), content_digest=hashlib.sha256(body).hexdigest()
    )
    c["evidence"][0].update(
        start=len(body) - 4,
        end=len(body) - 3,
        quote="🧪",
        quote_digest=hashlib.sha256("🧪".encode()).hexdigest(),
    )
    assert (await admit(context_payload, resolver)).context.snapshots[
        0
    ].content_bytes == 10 * 1024 * 1024
    c["snapshots"][0]["content_bytes"] += 1
    resolver.calls.clear()
    with pytest.raises(ValueError):
        await admit(context_payload, resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_matching_digest_does_not_admit_invalid_utf8(context_payload):
    resolver = Resolver()
    resolver.result = replace(resolver.result, body=b"\xff")
    context_payload["context"]["snapshots"][0].update(
        content_bytes=1, content_digest=hashlib.sha256(b"\xff").hexdigest()
    )
    with pytest.raises(UnicodeError):
        await admit(context_payload, resolver)
