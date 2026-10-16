import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from agent.experimental.canonical import admit_canonical_json
from agent.experimental.consolidated_bundle import (
    CONSOLIDATED_BUNDLE_SCHEMA,
    admit_consolidated_bundle,
    build_consolidated_bundle,
)
from agent.experimental.consolidated_example import example_journey
from agent.experimental.consolidated_storage_material import RetainedConsolidated


@pytest.mark.asyncio
async def test_consolidated_bundle_round_trip_preserves_exact_material():
    result = await example_journey().run()
    scope, root, operation = uuid4(), uuid4(), uuid4()
    retained = RetainedConsolidated(
        result.knowledge_bytes,
        result.manifest_bytes,
        result.sources,
        result.reports,
        True,
        result.candidate.admitted.document.digest,
    )
    document = await build_consolidated_bundle(
        retained,
        scope=scope,
        root=root,
        operation=operation,
        retained_until=datetime.now(UTC) + timedelta(days=1),
    )
    assert (
        await admit_consolidated_bundle(
            document.data,
            expected_digest=document.digest,
            scope=scope,
            root=root,
            operation=operation,
            now=datetime.now(UTC),
        )
    ).data == document.data


@pytest.mark.asyncio
async def test_consolidated_bundle_rejects_changed_output_bytes():
    result = await example_journey().run()
    scope, root, operation = uuid4(), uuid4(), uuid4()
    retained = RetainedConsolidated(
        result.knowledge_bytes,
        result.manifest_bytes,
        result.sources,
        result.reports,
        True,
        result.candidate.admitted.document.digest,
    )
    document = await build_consolidated_bundle(
        retained,
        scope=scope,
        root=root,
        operation=operation,
        retained_until=datetime.now(UTC) + timedelta(days=1),
    )
    payload = json.loads(document.data)
    member = payload["members"]["outputs/summary.md"]
    member["data"] = member["data"][:-4] + "AAAA"
    changed = json.dumps(payload).encode()
    changed_document = admit_canonical_json(
        changed, schema_version=CONSOLIDATED_BUNDLE_SCHEMA
    )
    with pytest.raises((ValueError, UnicodeDecodeError)):
        await admit_consolidated_bundle(
            changed_document.data,
            expected_digest=changed_document.digest,
            scope=scope,
            root=root,
            operation=operation,
            now=datetime.now(UTC),
        )
