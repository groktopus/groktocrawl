"""Scratch-only consolidated restore rehearsal with external deletion inventory."""

import asyncio
import json
import sys
from uuid import UUID

from agent.experimental.consolidated_example import example_context
from agent.experimental.consolidated_store import ConsolidatedStore
from agent.experimental.source_store import StorageConflictError
from test_consolidated_store_db import stage_journey

SCOPE = UUID("8f7b70ce-ab9f-4a74-8847-710fd9440380")
SCHEMA = "consolidated-restore-fixture/1"


def inventory():
    value = json.loads(sys.stdin.buffer.read(100000))
    if (
        set(value) != {"schema_version", "roots", "deleted"}
        or value["schema_version"] != SCHEMA
    ):
        raise ValueError("restore inventory missing")
    if set(value["roots"]) != {"delete", "retain"}:
        raise ValueError("restore inventory incomplete")
    for entry in value["roots"].values():
        if set(entry) != {"root", "operation", "digest"}:
            raise ValueError("restore entry invalid")
        UUID(entry["root"])
        UUID(entry["operation"])
    return value


async def run(phase):
    store = ConsolidatedStore()
    if phase == "seed":
        await store.provision_scope(SCOPE)
        value = {"schema_version": SCHEMA, "roots": {}, "deleted": []}
        for name in ("delete", "retain"):
            root = await store.create_consolidated_root(SCOPE)
            operation = await store.reserve_consolidated(
                SCOPE, root, 1, 100000, example_context()
            )
            await stage_journey(store, SCOPE, root, operation)
            retained = await store.read_consolidated(SCOPE, root, operation)
            value["roots"][name] = {
                "root": str(root),
                "operation": str(operation),
                "digest": retained.receipt_digest,
            }
        print(json.dumps(value))
        return
    value = inventory()
    deleted = value["roots"]["delete"]
    if phase == "delete":
        await store.delete_root(SCOPE, UUID(deleted["root"]))
        value["deleted"] = [deleted["root"]]
        print(json.dumps(value))
        return
    if phase != "verify" or value["deleted"] != [deleted["root"]]:
        raise ValueError("current post-backup deletion inventory required")
    # Only the externally supplied complete inventory releases this scratch restore.
    await store.delete_root(SCOPE, UUID(deleted["root"]))
    try:
        await store.read_consolidated(
            SCOPE, UUID(deleted["root"]), UUID(deleted["operation"])
        )
    except StorageConflictError:
        pass
    else:
        raise AssertionError("deleted publication readable after restore")
    if (
        await store.consolidated_receipt(
            SCOPE, UUID(deleted["root"]), UUID(deleted["operation"])
        )
        != deleted["digest"]
    ):
        raise AssertionError("deletion receipt changed")
    retained = value["roots"]["retain"]
    actual = await store.read_consolidated(
        SCOPE, UUID(retained["root"]), UUID(retained["operation"])
    )
    if actual.receipt_digest != retained["digest"] or not actual.fixture_only:
        raise AssertionError("retained bytes or fixture provenance changed")
    async with store._transaction(read=True) as conn:
        for table in ("consolidated_publications", "consolidated_sources", "snapshots"):
            # Fixed table names only; no inventory text is interpolated into SQL.
            row = await (
                await conn.execute(
                    f"SELECT count(*) AS count FROM research_staging.{table} WHERE scope_id=%s AND root_id=%s",
                    (SCOPE, UUID(deleted["root"])),
                )
            ).fetchone()
            if row["count"]:
                raise AssertionError("deleted root still retains payload references")
    print(
        json.dumps(
            {
                "schema_version": "consolidated-restore-result/1",
                "retained_exact": True,
                "deleted_unavailable": True,
                "deleted_rows_purged": True,
                "receipt_preserved": True,
                "fixture_only": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
