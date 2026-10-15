"""Independent connection/restart verification against the external probe manifest."""

import asyncio
import json
import sys
from uuid import UUID

from agent.experimental.source_store import source_descriptor
from capacity_common import body_for, digest
from capacity_workload import Probe, emit
from publication_fixture import CONTEXT


async def verify(manifest):
    probe = Probe()
    counts = {"sources": 0, "revisions": 0, "publications": 0, "receipts": 0}
    for item in manifest["sources"]:
        scope, root, source, op = (
            UUID(item[key]) for key in ("scope", "root", "source", "operation")
        )
        value = await probe.operation(
            "verification",
            item["ordinal"],
            "source_read",
            lambda scope=scope, root=root, source=source: probe.store.read_source(
                scope, root, source
            ),
            item["size"],
        )
        expected = body_for(item["phase"], item["ordinal"], item["index"], item["size"])
        assert value.body == expected and digest(expected) == item["body_digest"]
        descriptor = source_descriptor(expected, "https://example.test/revision")
        assert (
            value.descriptor == descriptor
            and descriptor.digest == item["descriptor_digest"]
        )
        receipt = await probe.store.receipt(scope, root, op)
        assert (
            receipt is not None
            and receipt.snapshot_id == source
            and receipt.input_digest == descriptor.digest
            and receipt.generation == 1
        )
        counts["sources"] += 1
        counts["receipts"] += 1
        del value, expected
    for item in manifest["revisions"]:
        scope, root, revision = (
            UUID(item[key]) for key in ("scope", "root", "revision")
        )
        value = await probe.store.read_research(scope, root, revision)
        assert value.document.digest == item["digest"]
        assert (
            await probe.store.research_receipt(scope, root, revision) == item["digest"]
        )
        counts["revisions"] += 1
        counts["receipts"] += 1
    for item in manifest["publications"]:
        scope, root, publication = (
            UUID(item[key]) for key in ("scope", "root", "publication")
        )
        value = await probe.operation(
            "verification",
            root,
            "publication_read",
            lambda scope=scope, root=root, publication=publication: (
                probe.store.read_research_publication(scope, root, publication, CONTEXT)
            ),
        )
        assert value.document.digest == item["digest"] and value.size == item["size"]
        for key, expected in item["outputs"].items():
            assert digest(getattr(value, key)) == expected
        assert (
            await probe.store.research_publication_receipt(scope, root, publication)
            == item["digest"]
        )
        counts["publications"] += 1
        counts["receipts"] += 1
    for item in manifest["cancelled"]:
        assert (
            await probe.store.receipt(
                *(UUID(item[key]) for key in ("scope", "root", "operation"))
            )
            is None
        )
    roots = await probe.sql(
        "SELECT root_id,scope_id,charged FROM research_staging.roots"
    )
    assert {str(row["root_id"]): row["charged"] for row in roots} == manifest["charges"]
    scopes = await probe.sql("SELECT scope_id,charged FROM research_staging.scopes")
    for scope in scopes:
        assert scope["charged"] == sum(
            row["charged"] for row in roots if row["scope_id"] == scope["scope_id"]
        )
    actual = (
        await probe.sql(
            "SELECT (SELECT count(*) FROM research_staging.snapshots) AS sources, (SELECT count(*) FROM research_staging.research_revisions) AS revisions, (SELECT count(*) FROM research_staging.research_publications) AS publications, (SELECT count(*) FROM research_staging.operations WHERE state='pending') AS pending"
        )
    )[0]
    assert actual["pending"] == 0
    for key in ("sources", "revisions", "publications"):
        assert counts[key] == len(manifest[key]) == actual[key]
    totals = {
        "source_body_bytes": sum(item["size"] for item in manifest["sources"]),
        "source_descriptor_bytes": sum(
            item["descriptor_bytes"] for item in manifest["sources"]
        ),
        "revision_canonical_bytes": sum(
            item["canonical_bytes"] for item in manifest["revisions"]
        ),
        "publication_canonical_bytes": sum(
            item["canonical_bytes"] for item in manifest["publications"]
        ),
        "output_bytes": sum(item["output_bytes"] for item in manifest["publications"]),
    }
    assert sum(totals.values()) == sum(manifest["charges"].values())
    emit(
        "verification",
        expected={
            key: len(manifest[key]) for key in ("sources", "revisions", "publications")
        },
        actual=counts,
        mismatch_count=0,
        byte_totals=totals,
        logical_charged_bytes=sum(manifest["charges"].values()),
        manifest_digest=digest(json.dumps(manifest, sort_keys=True).encode()),
    )


if __name__ == "__main__":
    asyncio.run(verify(json.load(sys.stdin)))
