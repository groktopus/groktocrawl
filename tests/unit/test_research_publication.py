"""Publication cannot replace retained research even with self-consistent audits."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from agent.experimental.knowledge import text_digest
from agent.experimental.publication import (
    FixtureRenderAudit,
    FixtureResearch,
    RenderInput,
)
from agent.experimental.research_publication import (
    RESEARCH_PUBLICATION_SCHEMA,
    admit_research_publication,
)
from agent.experimental.research_revision import admit_research_revision
from agent.experimental.revision_store import admit_revision
from agent.experimental.revisions import FixtureRevision, _entities

from tests.storage.publication_fixture import (
    CONTEXT,
    CONTEXT_V2,
    publication_payload,
    supported_revision,
)
from tests.storage.test_revision_store_db import payload


def rendered(pinned, publication, context=CONTEXT, *, research=None):
    research = research or pinned.revision.research
    structure = research.verifications.structure
    data = json.loads(publication_payload(structure, publication, context))
    data.update(
        schema_version=RESEARCH_PUBLICATION_SCHEMA,
        revision_digest=pinned.document.digest,
        research=research.model_dump(mode="json"),
    )
    for item in data["publication"]["audits"]:
        item["checked_input"]["research"] = research.model_dump(mode="json")
        checked = RenderInput.model_validate(item["checked_input"])
        item["checked_input_digest"] = checked.input_digest()
        item["checked_output_digest"] = text_digest(checked.rendered_text())
        FixtureRenderAudit.model_validate(item)
    return json.dumps(data).encode()


@pytest.fixture
def pinned():
    scope, root, revision, snapshot = (uuid4() for _ in range(4))
    structure = admit_revision(
        supported_revision(
            payload(scope, root, revision, snapshot, b"Fixture source.")
        ),
        scope,
        root,
        revision,
    ).structure
    data = json.loads(publication_payload(structure, uuid4()))["research"]
    data["objective"] = "Inspect retained synthetic evidence"
    candidate = FixtureRevision(
        schema_version="fixture-revision/1",
        parent_revision_id=None,
        created_at="2026-09-05T01:00:00Z",
        research=FixtureResearch.model_validate(data),
        introductions=[],
    )
    value = candidate.model_dump(mode="json")
    value["introductions"] = [
        {"kind": kind, "entity_id": key, "predecessor_id": None}
        for key, (kind, _) in _entities(candidate).items()
    ]
    return admit_research_revision(
        json.dumps(
            {
                "schema_version": "retained-research-revision-prototype/1",
                "revision": value,
            }
        ).encode(),
        scope_id=str(scope),
        research_id=str(root),
        revision_id=str(revision),
        parent_revision_id=None,
    )


def test_exact_publication_and_new_renderer_preserve_complete_digest(pinned):
    first_id, second_id = uuid4(), uuid4()
    first = admit_research_publication(
        rendered(pinned, first_id), pinned, first_id, CONTEXT
    )
    second = admit_research_publication(
        rendered(pinned, second_id, CONTEXT_V2), pinned, second_id, CONTEXT_V2
    )
    assert first.document.digest != second.document.digest
    for result in (first, second):
        assert (
            json.loads(result.document.data)["revision_digest"]
            == pinned.document.digest
        )
        assert (
            result.summary
            == pinned.revision.research.verifications.structure.claims[0].text.encode()
        )
    assert (
        admit_research_publication(first.document.data, pinned, first_id, CONTEXT)
        == first
    )


@pytest.mark.parametrize(
    "field", ["objective", "question", "assessment", "verification"]
)
def test_self_consistent_rewritten_research_is_denied(pinned, field):
    data = pinned.revision.research.model_dump(mode="json")
    if field == "objective":
        data["objective"] = "Different objective"
    elif field == "question":
        data["questions"][0]["question"] = "Different question"
    elif field == "assessment":
        data["verifications"]["assessments"][0]["reason"] = (
            "Different assessment rationale"
        )
    else:
        data["verifications"]["records"][0]["reason"] = (
            "Different verification rationale"
        )
    research = FixtureResearch.model_validate(data)
    identity = uuid4()
    raw = rendered(pinned, identity, research=research)
    with pytest.raises(ValueError, match="complete pinned research"):
        admit_research_publication(raw, pinned, identity, CONTEXT)


@pytest.mark.parametrize(
    "field",
    [
        "digest",
        "revision",
        "schema",
        "extra",
        "audit",
        "context",
        "identity",
        "container",
    ],
)
def test_invalid_binding_and_audits_are_denied(pinned, field):
    identity = uuid4()
    data = json.loads(rendered(pinned, identity))
    context = CONTEXT
    if field == "digest":
        data["revision_digest"] = "0" * 64
    elif field == "revision":
        data["revision_id"] = str(uuid4())
    elif field == "schema":
        data["schema_version"] = "retained-fixture-publication/1"
    elif field == "extra":
        data["extra"] = True
    elif field == "audit":
        data["publication"]["audits"][0]["verdict"] = "fail"
    elif field == "context":
        context = CONTEXT_V2
    elif field == "identity":
        identity = uuid4()
    else:
        pinned = replace(pinned, document=replace(pinned.document, digest="0" * 64))
    with pytest.raises(ValueError):
        admit_research_publication(json.dumps(data).encode(), pinned, identity, context)
