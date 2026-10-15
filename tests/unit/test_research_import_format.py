"""Explicit import format routing preserves old bytes and rejects mixed operations."""

import json
from uuid import UUID

import pytest
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.import_store import ImportStore
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.source_store import StorageConflictError

from tests.storage.publication_fixture import CONTEXT
from tests.unit.test_artifact_bundle import bundle  # noqa: F401
from tests.unit.test_research_bundle import complete_bundle  # noqa: F401
from tests.unit.test_research_publication import pinned  # noqa: F401


@pytest.mark.parametrize("complete", [False, True])
def test_matching_import_format_and_cross_format_rejection(request, complete):
    fields = (
        request.getfixturevalue("complete_bundle")
        if complete
        else request.getfixturevalue("bundle")[0]
    )
    fields["retained_until"] = "2099-01-01T00:00:00Z"
    document = admit_canonical_json(
        json.dumps(fields).encode(), schema_version=fields["schema_version"]
    )
    operation = {
        "bundle_schema": fields["schema_version"],
        "bundle_digest": document.digest,
        "origin_scope_id": UUID(fields["scope_id"]),
        "origin_root_id": UUID(fields["root_id"]),
        "publication_id": UUID(fields["publication_id"]),
        "context_digest": CONTEXT.digest(),
    }
    reader, wrong = (
        (ResearchImportStore, ImportStore)
        if complete
        else (ImportStore, ResearchImportStore)
    )
    assert reader._validate(document.data, operation, CONTEXT).document == document
    with pytest.raises(StorageConflictError, match="format"):
        wrong._validate(document.data, operation, CONTEXT)
    # Operation metadata cannot make one format's bytes into the other.
    operation["bundle_schema"] = wrong.bundle_schema
    with pytest.raises(ValueError):
        wrong._validate(document.data, operation, CONTEXT)


def test_legacy_operation_without_format_metadata_retains_compatibility(request):
    fields = request.getfixturevalue("bundle")[0]
    fields["retained_until"] = "2099-01-01T00:00:00Z"
    document = admit_canonical_json(
        json.dumps(fields).encode(), schema_version=fields["schema_version"]
    )
    operation = {
        "bundle_digest": document.digest,
        "origin_scope_id": UUID(fields["scope_id"]),
        "origin_root_id": UUID(fields["root_id"]),
        "publication_id": UUID(fields["publication_id"]),
        "context_digest": CONTEXT.digest(),
    }
    assert ImportStore._validate(document.data, operation, CONTEXT).document == document
    with pytest.raises(StorageConflictError):
        ResearchImportStore._validate(document.data, operation, CONTEXT)
