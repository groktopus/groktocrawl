"""Complete synthetic revision construction; never inferred provenance or truth."""

import json
from uuid import uuid4

from agent.experimental.publication import FixtureResearch
from agent.experimental.research_revision import RESEARCH_REVISION_SCHEMA
from agent.experimental.revision_store import admit_revision
from agent.experimental.revisions import FixtureRevision, _entities
from publication_fixture import publication_payload, supported_revision
from test_revision_store_db import payload


def research_payload(scope, root, revision, snapshot, body, prior=()):
    structure = admit_revision(
        supported_revision(payload(scope, root, revision, snapshot, body)),
        scope,
        root,
        revision,
    ).structure
    data = json.loads(publication_payload(structure, uuid4()))["research"]
    data["objective"] = "Inspect retained synthetic evidence"
    for record in data["verifications"]["records"]:
        record["verification_id"] += f"-{revision}"
    for assessment in data["verifications"]["assessments"]:
        assessment["assessment_id"] += f"-{revision}"
    for link in data["verifications"]["assessment_links"]:
        link["assessment_ids"] = [
            value + f"-{revision}" for value in link["assessment_ids"]
        ]
    research = FixtureResearch.model_validate(data)
    # Build local entities through the existing typed model; introductions validated at admission.
    candidate = FixtureRevision(
        schema_version="fixture-revision/1",
        parent_revision_id=prior[-1].research.verifications.structure.revision_id
        if prior
        else None,
        created_at="2026-09-05T01:00:00Z",
        research=research,
        introductions=[],
    )
    known = {key for item in prior for key in _entities(item)}
    declarations = [
        {"kind": kind, "entity_id": key, "predecessor_id": None}
        for key, (kind, _) in _entities(candidate).items()
        if key not in known
    ]
    candidate = FixtureRevision.model_validate(
        {**candidate.model_dump(mode="json"), "introductions": declarations}
    )
    return json.dumps(
        {
            "schema_version": RESEARCH_REVISION_SCHEMA,
            "revision": candidate.model_dump(mode="json"),
        }
    ).encode()
