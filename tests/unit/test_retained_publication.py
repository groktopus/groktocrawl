"""Publication admission binds caller context and exact retained structure."""

import json
from uuid import uuid4

import pytest
from agent.experimental.publication_store import admit_publication
from agent.experimental.revision_store import admit_revision

from tests.storage.publication_fixture import (
    CONTEXT,
    publication_payload,
    supported_revision,
)
from tests.storage.test_revision_store_db import payload


@pytest.fixture
def admitted_inputs():
    scope, root, revision, snapshot, publication = (uuid4() for _ in range(5))
    raw = supported_revision(
        payload(scope, root, revision, snapshot, b"Fixture source.")
    )
    structure = admit_revision(raw, scope, root, revision).structure
    return structure, publication, publication_payload(structure, publication)


def test_exact_outputs_and_canonical_representation(admitted_inputs):
    structure, publication, raw = admitted_inputs
    result = admit_publication(raw, structure, publication, CONTEXT)
    assert result.summary == result.analysis == structure.claims[0].text.encode()
    assert b'"verifications"' in result.dossier
    pretty = json.dumps(json.loads(raw), indent=2).encode()
    assert admit_publication(pretty, structure, publication, CONTEXT) == result


@pytest.mark.parametrize(
    "mutation",
    ["policy", "auditor", "revision", "set", "failed", "layer", "output", "extra"],
)
def test_invalid_context_or_audit_denied(admitted_inputs, mutation):
    structure, publication, raw = admitted_inputs
    context = CONTEXT
    data = json.loads(raw)
    if mutation == "policy":
        context = CONTEXT.model_copy(update={"policy_version": "other"})
    elif mutation == "auditor":
        context = CONTEXT.model_copy(
            update={"auditor": CONTEXT.auditor.model_copy(update={"version": "other"})}
        )
    elif mutation == "revision":
        data["revision_id"] = str(uuid4())
    elif mutation == "set":
        publication = uuid4()
    elif mutation == "failed":
        data["publication"]["audits"][0]["verdict"] = "fail"
    elif mutation == "layer":
        data["publication"]["audits"].pop()
    elif mutation == "output":
        data["publication"]["audits"][0]["checked_output_digest"] = "0" * 64
    else:
        data["extra"] = "unbound"
    with pytest.raises(ValueError):
        admit_publication(json.dumps(data).encode(), structure, publication, context)


def test_rerender_pins_research_independently_of_presentation(admitted_inputs):
    from agent.experimental.publication_store import research_digest

    from tests.storage.publication_fixture import CONTEXT_V2

    structure, publication, raw = admitted_inputs
    original = admit_publication(raw, structure, publication, CONTEXT)
    rerender_id = uuid4()
    rerender = admit_publication(
        publication_payload(structure, rerender_id, CONTEXT_V2),
        structure,
        rerender_id,
        CONTEXT_V2,
    )
    assert original.document.digest != rerender.document.digest
    assert research_digest(original.document) == research_digest(rerender.document)


def test_research_binding_detects_question_change(admitted_inputs):
    from agent.experimental.canonical import admit_canonical_json
    from agent.experimental.publication_store import PUBLICATION_SCHEMA, research_digest

    _, _, raw = admitted_inputs
    original = admit_canonical_json(raw, schema_version=PUBLICATION_SCHEMA)
    data = json.loads(raw)
    data["research"]["questions"][0]["question"] = "Changed question"
    changed = admit_canonical_json(
        json.dumps(data).encode(), schema_version=PUBLICATION_SCHEMA
    )
    assert research_digest(original) != research_digest(changed)
