"""Input provenance, assessment separation and immutable supplied history."""

import json
from copy import deepcopy

import pytest
from agent.experimental.checked_knowledge import admit_checked_history
from agent.experimental.knowledge_checks import FixtureReviewer, KnowledgeCheckInput

from tests.unit.test_knowledge_context import Resolver, context_payload  # noqa: F401


@pytest.fixture
def checked_context(request):
    return request.getfixturevalue("context_payload")


REVIEWER = FixtureReviewer(kind="fixture", identity="test", version="1")


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


def record(context, *, state="supported", reviewer=None):
    c = deepcopy(context)
    c["as_of"] = "2026-09-05T00:00:00Z"
    inputs, verifications, assessments = [], [], []
    for kind in (
        "structural",
        "semantic_support",
        "freshness",
        "conflict_coverage",
        "assessment",
    ):
        raw = {
            "schema_version": "knowledge-check-input-prototype/1",
            "input_id": f"input-{kind}-{c['revision_id']}",
            "check_type": kind,
            "subject_id": c["revision_id"]
            if kind in {"structural", "conflict_coverage"}
            else "c1",
            "policy_version": c["policy_version"],
            "reviewer": reviewer or REVIEWER.model_dump(),
            "context": c,
            "evidence_ids": ["e1"],
            "freshness": {
                "evaluated_at": c["created_at"],
                "sources": [
                    {
                        "snapshot_id": "s1",
                        "basis": "historical_snapshot",
                        "max_age_seconds": 604800,
                        "reason": "Fixture",
                    }
                ],
            }
            if kind == "freshness"
            else None,
        }
        checked = KnowledgeCheckInput.model_validate_json(encode(raw))
        inputs.append(raw)
        result = {
            "input_id": raw["input_id"],
            "input_digest": checked.input_digest(),
            "checked_at": c["created_at"],
            "reason": "Authored fixture only",
        }
        if kind == "assessment":
            assessments.append(
                {
                    **result,
                    "assessment_id": f"assessment-{c['revision_id']}",
                    "outcome": state,
                }
            )
        else:
            verifications.append(
                {
                    **result,
                    "verification_id": f"result-{kind}-{c['revision_id']}",
                    "verdict": "pass",
                }
            )
    value = {
        "schema_version": "checked-knowledge-prototype/1",
        "context": c,
        "verification_inputs": inputs,
        "verifications": verifications,
        "assessments": assessments,
        "assessment_links": [
            {
                "claim_id": "c1",
                "state": state,
                "assessment_ids": [assessments[0]["assessment_id"]],
            }
        ],
        "introductions": [],
        "coverage": "complete",
    }
    declarations(value)
    return value


def declarations(value, prior=None):
    def collect(v):
        groups = [
            (kind, field, v["context"][key])
            for kind, field, key in [
                ("snapshot", "snapshot_id", "snapshots"),
                ("evidence", "evidence_id", "evidence"),
                ("claim", "claim_id", "claims"),
                ("relationship", "relationship_id", "relationships"),
                ("question", "question_id", "questions"),
                ("conflict", "conflict_id", "conflicts"),
            ]
        ]
        groups += [
            ("input", "input_id", v["verification_inputs"]),
            ("verification", "verification_id", v["verifications"]),
            ("assessment", "assessment_id", v["assessments"]),
        ]
        return {r[field]: kind for kind, field, rows in groups for r in rows}

    old = collect(prior) if prior else {}
    value["introductions"] = [
        {"kind": kind, "entity_id": key, "predecessor_id": None}
        for key, kind in collect(value).items()
        if key not in old
    ]


async def admit(value, *, prior=(), resolver=None, reviewers=(REVIEWER,)):
    return await admit_checked_history(
        encode(value),
        prior=tuple(encode(v) for v in prior),
        scope_id="owner",
        research_id="research",
        revision_id=value["context"]["revision_id"],
        resolver=resolver or Resolver(),
        reviewers=reviewers,
    )


@pytest.mark.asyncio
async def test_exact_roundtrip_and_assessment_changes_preserve_claim_identity(
    checked_context,
):
    root = record(checked_context["context"])
    first = await admit(root)
    assert (
        first.document.digest
        == "1f19e1f702b7b8415aaa67f36ce6ded12899ef3b3c0fe51171290b10c556a19b"
    )
    assert (
        first.knowledge.verification_inputs[0].input_digest()
        == "a27f3ae4e1e62e9a855769f8a96d9994db3555b3d588cfa5f4cb30837ac795a0"
    )
    c = deepcopy(root["context"])
    c.update(
        revision_id="rev2",
        parent_revision_id="rev1",
        parent_digest=first.document.digest,
        created_at="2026-09-07T00:00:00Z",
    )
    child = record(c, state="contested")
    declarations(child, root)
    second = await admit(child, prior=(root,))
    assert first.knowledge.context.claims == second.knowledge.context.claims
    assert second.knowledge.assessment_links[0].state == "contested"
    assert await admit(json.loads(second.document.data), prior=(root,)) == second


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "context",
        "digest",
        "reviewer",
        "human",
        "missing_model",
        "evidence",
        "duplicate_evidence",
        "subject",
        "policy",
        "missing_result",
        "duplicate_result",
        "result_kind",
        "time",
        "freshness",
        "unknown_basis",
        "assessment_state",
        "assessment_subject",
        "missing_link",
        "coverage",
        "introductions",
        "alias",
    ],
)
async def test_invalid_records_reject_before_source_resolution(checked_context, change):
    value = record(checked_context["context"])
    inp = value["verification_inputs"][1]
    if change == "context":
        inp["context"] = {**inp["context"], "objective": "Different"}
    elif change == "digest":
        value["verifications"][0]["input_digest"] = "0" * 64
    elif change == "reviewer":
        inp["reviewer"] = {**inp["reviewer"], "identity": "unconfigured"}
    elif change == "human":
        inp["reviewer"] = {
            "kind": "human",
            "identity": "human",
            "version": "1",
            "attestation": "fake",
        }
    elif change == "missing_model":
        inp["reviewer"] = {"kind": "model", "identity": "model", "version": "1"}
    elif change == "evidence":
        inp["evidence_ids"] = []
    elif change == "duplicate_evidence":
        inp["evidence_ids"] *= 2
    elif change == "subject":
        inp["subject_id"] = "rev1"
    elif change == "policy":
        inp["policy_version"] = "other"
    elif change == "missing_result":
        value["verifications"].pop()
    elif change == "duplicate_result":
        value["verifications"].append(
            {**value["verifications"][0], "verification_id": "another"}
        )
    elif change == "result_kind":
        value["verifications"][0]["input_id"] = value["assessments"][0]["input_id"]
    elif change == "time":
        value["verifications"][0]["checked_at"] = "2026-09-01T00:00:00Z"
    elif change == "freshness":
        value["verification_inputs"][2]["freshness"]["sources"] = []
    elif change == "unknown_basis":
        fresh = value["verification_inputs"][2]
        fresh["freshness"]["sources"][0]["basis"] = "unknown"
        value["verifications"][2]["input_digest"] = (
            KnowledgeCheckInput.model_validate_json(encode(fresh)).input_digest()
        )
    elif change == "assessment_state":
        value["assessment_links"][0]["state"] = "unassessed"
    elif change == "assessment_subject":
        value["assessment_links"][0]["claim_id"] = "missing"
    elif change == "missing_link":
        value["assessment_links"] = []
    elif change == "coverage":
        value["coverage"] = "insufficient"
    elif change == "introductions":
        value["introductions"] = []
    else:
        value["verifications"][0]["verification_id"] = "c1"
    resolver = Resolver()
    with pytest.raises(ValueError):
        await admit(value, resolver=resolver)
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "parent",
        "missing_parent",
        "changed_claim",
        "changed_result",
        "bad_predecessor",
        "old_revision_alias",
        "chronology",
    ],
)
async def test_history_rejects_reassignment_and_incomplete_ancestry(
    checked_context, change
):
    root = record(checked_context["context"])
    first = await admit(root)
    c = deepcopy(root["context"])
    c.update(
        revision_id="rev2",
        parent_revision_id="rev1",
        parent_digest=first.document.digest,
        created_at="2026-09-07T00:00:00Z",
    )
    if change == "parent":
        c["parent_digest"] = "0" * 64
    elif change == "changed_claim":
        c["claims"][0]["text"] = "Changed meaning"
    elif change == "old_revision_alias":
        c["questions"][0]["question_id"] = "rev1"
    elif change == "chronology":
        c["created_at"] = "2026-09-05T12:00:00Z"
    child = record(c)
    declarations(child, root)
    if change == "changed_result":
        child["verifications"][0]["verification_id"] = root["verifications"][0][
            "verification_id"
        ]
        declarations(child, root)
    elif change == "bad_predecessor":
        child["introductions"][0]["predecessor_id"] = "c1"
    resolver = Resolver()
    with pytest.raises(ValueError):
        await admit(
            child,
            prior=() if change == "missing_parent" else (root,),
            resolver=resolver,
        )
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_known_reviewer_metadata_is_not_human_authentication(checked_context):
    reviewer = {
        "kind": "model",
        "identity": "adapter",
        "version": "1",
        "provider": "example",
        "requested_model": "alias",
        "resolved_model": None,
        "prompt_digest": "1" * 64,
        "generation_configuration_digest": "2" * 64,
    }
    value = record(checked_context["context"], reviewer=reviewer)
    configured = KnowledgeCheckInput.model_validate_json(
        encode(value["verification_inputs"][0])
    ).reviewer
    assert (await admit(value, reviewers=(configured,))).knowledge.verification_inputs[
        0
    ].reviewer == configured
    with pytest.raises(ValueError, match="catalogue"):
        await admit(value)


@pytest.mark.asyncio
async def test_twenty_one_revision_bound_fails_before_decode():
    with pytest.raises(ValueError, match="nineteen"):
        await admit_checked_history(
            b"invalid",
            prior=(b"invalid",) * 20,
            scope_id="owner",
            research_id="research",
            revision_id="rev",
            resolver=Resolver(),
            reviewers=(),
        )


@pytest.mark.asyncio
async def test_twenty_revision_history_and_removed_identity_reintroduction(
    checked_context,
):
    first = record(checked_context["context"])
    first["context"]["claims"].append(
        {**first["context"]["claims"][0], "claim_id": "extra"}
    )
    # Rebuild inputs after adding the otherwise unused claim.
    first = record(first["context"])
    first["assessment_links"].append(
        {"claim_id": "extra", "state": "unassessed", "assessment_ids": []}
    )
    history = [first]
    previous = await admit(first)
    for number in range(2, 21):
        c = deepcopy(previous.knowledge.context.model_dump(mode="json"))
        c.update(
            revision_id=f"rev{number}",
            parent_revision_id=c["revision_id"],
            parent_digest=previous.document.digest,
        )
        c["claims"] = [claim for claim in c["claims"] if claim["claim_id"] != "extra"]
        child = record(c)
        declarations(child, history[-1])
        previous = await admit(child, prior=tuple(history))
        history.append(child)
    assert previous.knowledge.context.revision_id == "rev20"
    # A removed identity remains reserved across the full prefix.
    root, second = history[:2]
    second_admitted = await admit(second, prior=(root,))
    c = deepcopy(second["context"])
    c.update(
        revision_id="rev3",
        parent_revision_id="rev2",
        parent_digest=second_admitted.document.digest,
    )
    c["claims"].append({**root["context"]["claims"][1], "text": "Different meaning"})
    changed = record(c)
    changed["assessment_links"].append(
        {"claim_id": "extra", "state": "unassessed", "assessment_ids": []}
    )
    declarations(changed, root)
    with pytest.raises(ValueError, match="reassigned"):
        await admit(changed, prior=(root, second))


def test_premise_and_contradictory_evidence_cannot_be_omitted(checked_context):
    value = record(checked_context["context"])
    raw = deepcopy(value["verification_inputs"][1])
    c = raw["context"]
    c["claims"].append({**c["claims"][0], "claim_id": "premise"})
    c["claims"][0]["kind"] = "inference"
    c["evidence"].append({**c["evidence"][0], "evidence_id": "e2"})
    c["relationships"].extend(
        [
            {
                "relationship_id": "derive",
                "kind": "derived_from",
                "source_id": "c1",
                "target_id": "premise",
                "rationale": "Test",
                "rule": "Test rule",
                "assumptions": [],
            },
            {
                "relationship_id": "contradict",
                "kind": "contradicts",
                "source_id": "e2",
                "target_id": "premise",
                "rationale": "Test",
                "rule": None,
                "assumptions": [],
            },
        ]
    )
    c["questions"][0]["status"] = "unresolved"
    c["conflicts"] = [
        {
            "conflict_id": "conflict",
            "question_id": "q1",
            "claim_ids": ["premise"],
            "evidence_ids": ["e1", "e2"],
            "reason": "Test",
        }
    ]
    with pytest.raises(ValueError, match="complete closure"):
        KnowledgeCheckInput.model_validate_json(encode(raw))
    raw["evidence_ids"] = ["e1", "e2"]
    assert KnowledgeCheckInput.model_validate_json(encode(raw)).evidence_ids == (
        "e1",
        "e2",
    )
