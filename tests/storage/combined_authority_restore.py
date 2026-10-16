"""Combined PostgreSQL artifact and Valkey execution-state restore rehearsal."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from agent.experimental.artifact_authority import ArtifactAuthority
from agent.experimental.durable_research import DurableResearchLedger
from agent.experimental.source_store import StorageConflictError

NAMESPACE = "combined-authority-restore"
SCOPE_LABEL = "anonymous"
SCOPE = uuid5(NAMESPACE_URL, f"groktocrawl-x:research-scope:{SCOPE_LABEL}")
COMPLETED_RUN = UUID("20000000-0000-0000-0000-000000000001")
COMPLETED_RESEARCH = UUID("30000000-0000-0000-0000-000000000001")
COMPLETED_SET = UUID("40000000-0000-0000-0000-000000000001")
DELETED_RUN = UUID("20000000-0000-0000-0000-000000000002")
DELETED_RESEARCH = UUID("30000000-0000-0000-0000-000000000002")
DELETED_SET = UUID("40000000-0000-0000-0000-000000000002")
INTERRUPTED_RUN = UUID("20000000-0000-0000-0000-000000000003")
INTERRUPTED_RESEARCH = UUID("30000000-0000-0000-0000-000000000003")
INTERRUPTED_SET = UUID("40000000-0000-0000-0000-000000000003")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _material(
    research: UUID, artifact_set: UUID
) -> tuple[bytes, dict[str, tuple[str, bytes]]]:
    manifest = json.dumps(
        {
            "schema_version": "render-manifest-prototype/1",
            "research_id": str(research),
            "revision_id": f"revision-{research}",
            "artifact_set_id": str(artifact_set),
            "coverage": 1.0,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    artifacts = {
        layer: (f"{layer}-{research}", f"{layer} bytes for {research}".encode())
        for layer in ("summary", "analysis", "dossier")
    }
    return manifest, artifacts


def _pointer(retained) -> dict[str, Any]:
    return {
        "schema_version": "postgres-artifact-authority/1",
        "scope_id": str(retained.scope_id),
        "research_id": str(retained.research_id),
        "run_id": str(retained.run_id),
        "artifact_set_id": str(retained.artifact_set_id),
        "manifest_digest": retained.manifest_digest,
        "set_digest": retained.set_digest,
        "artifacts": {
            item.layer: {
                "artifact_id": item.artifact_id,
                "content_digest": item.content_digest,
            }
            for item in retained.artifacts
        },
    }


def _admit(ledger: DurableResearchLedger, run: UUID, research: UUID, key: str):
    return ledger.admit(
        SCOPE_LABEL,
        key,
        _digest(f"request:{run}".encode()),
        run_id=str(run),
        payload={"research_id": str(research), "objective": key},
    )


async def seed() -> dict[str, Any]:
    authority = ArtifactAuthority()
    await authority.ensure_scope(SCOPE)
    ledger = DurableResearchLedger(os.environ["VALKEY_URL"], namespace=NAMESPACE)

    completed_manifest, completed_artifacts = _material(
        COMPLETED_RESEARCH, COMPLETED_SET
    )
    completed = await authority.commit(
        SCOPE,
        COMPLETED_RESEARCH,
        COMPLETED_RUN,
        COMPLETED_SET,
        completed_manifest,
        completed_artifacts,
    )
    admitted = _admit(ledger, COMPLETED_RUN, COMPLETED_RESEARCH, "completed")
    owned = ledger.claim(admitted.run_id, "seed-owner", attempt_id="completed-attempt")
    ledger.commit_result(
        admitted.run_id,
        "seed-owner",
        owned.owner_generation,
        completed.set_digest,
        terminal_payload={
            "research_id": str(COMPLETED_RESEARCH),
            "result": {"artifact_set_id": str(COMPLETED_SET)},
            "events": [],
            "artifact_authority": _pointer(completed),
        },
    )

    deleted_manifest, deleted_artifacts = _material(DELETED_RESEARCH, DELETED_SET)
    deleted = await authority.commit(
        SCOPE,
        DELETED_RESEARCH,
        DELETED_RUN,
        DELETED_SET,
        deleted_manifest,
        deleted_artifacts,
    )
    deleted_admitted = _admit(ledger, DELETED_RUN, DELETED_RESEARCH, "deleted")
    deleted_owned = ledger.claim(
        deleted_admitted.run_id, "delete-owner", attempt_id="delete-attempt"
    )
    ledger.commit_result(
        deleted_admitted.run_id,
        "delete-owner",
        deleted_owned.owner_generation,
        deleted.set_digest,
        terminal_payload={"artifact_authority": _pointer(deleted)},
    )
    await authority.delete(SCOPE, DELETED_RESEARCH)
    ledger.delete(
        deleted_admitted.run_id,
        terminal_payload={"research_id": str(DELETED_RESEARCH), "deleted": True},
    )

    interrupted_manifest, interrupted_artifacts = _material(
        INTERRUPTED_RESEARCH, INTERRUPTED_SET
    )
    interrupted = await authority.commit(
        SCOPE,
        INTERRUPTED_RESEARCH,
        INTERRUPTED_RUN,
        INTERRUPTED_SET,
        interrupted_manifest,
        interrupted_artifacts,
    )
    interrupted_admitted = _admit(
        ledger, INTERRUPTED_RUN, INTERRUPTED_RESEARCH, "interrupted"
    )
    ledger.claim(
        interrupted_admitted.run_id,
        "crashed-owner",
        attempt_id="interrupted-attempt",
    )

    return {
        "schema_version": "combined-authority-backup/1",
        "valkey_snapshot": ledger.export_snapshot(),
        "expected": {
            "completed_set_digest": completed.set_digest,
            "completed_manifest_digest": completed.manifest_digest,
            "completed_artifact_digests": {
                item.layer: item.content_digest for item in completed.artifacts
            },
            "interrupted_set_digest": interrupted.set_digest,
        },
    }


async def verify(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "combined-authority-backup/1":
        raise ValueError("unsupported combined backup fixture")
    authority = ArtifactAuthority()
    target_url = os.environ["RESTORE_VALKEY_URL"]
    ledger = DurableResearchLedger(target_url, namespace=NAMESPACE)
    restored_keys = ledger.restore_snapshot(payload["valkey_snapshot"])
    expected = payload["expected"]

    completed = await authority.read(SCOPE, COMPLETED_RESEARCH)
    durable_completed = ledger.get(str(COMPLETED_RUN))
    if durable_completed is None or durable_completed.state != "completed":
        raise AssertionError("completed Valkey projection missing after restore")
    pointer = (durable_completed.terminal_payload or {}).get("artifact_authority")
    if (
        not isinstance(pointer, dict)
        or pointer.get("set_digest") != completed.set_digest
    ):
        raise AssertionError("restored authorities disagree on completed artifact set")
    if completed.set_digest != expected["completed_set_digest"]:
        raise AssertionError("completed PostgreSQL set digest changed after restore")
    if completed.manifest_digest != expected["completed_manifest_digest"]:
        raise AssertionError("completed manifest bytes changed after restore")
    if {item.layer: item.content_digest for item in completed.artifacts} != expected[
        "completed_artifact_digests"
    ]:
        raise AssertionError("completed artifact bytes changed after restore")

    try:
        await authority.read(SCOPE, DELETED_RESEARCH)
    except StorageConflictError:
        pass
    else:
        raise AssertionError("deleted PostgreSQL artifacts resurrected after restore")
    durable_deleted = ledger.get(str(DELETED_RUN))
    if durable_deleted is None or durable_deleted.state != "deleted":
        raise AssertionError("Valkey deletion tombstone missing after restore")

    interrupted = await authority.read_run(SCOPE, INTERRUPTED_RUN)
    if interrupted.set_digest != expected["interrupted_set_digest"]:
        raise AssertionError("interrupted PostgreSQL commit changed after restore")
    reclaimable = {item.run_id for item in ledger.reclaimable()}
    if str(INTERRUPTED_RUN) not in reclaimable:
        raise AssertionError("interrupted run did not become reclaimable")
    claimed = ledger.claim(
        str(INTERRUPTED_RUN), "recovery-owner", attempt_id="recovery-attempt"
    )
    ledger.commit_result(
        str(INTERRUPTED_RUN),
        "recovery-owner",
        claimed.owner_generation,
        interrupted.set_digest,
        terminal_payload={"artifact_authority": _pointer(interrupted)},
    )
    replayed = await authority.commit(
        SCOPE,
        INTERRUPTED_RESEARCH,
        INTERRUPTED_RUN,
        INTERRUPTED_SET,
        interrupted.manifest,
        {item.layer: (item.artifact_id, item.body) for item in interrupted.artifacts},
    )
    if replayed != interrupted:
        raise AssertionError("reconciled PostgreSQL replay was not idempotent")

    return {
        "schema_version": "combined-authority-restore-result/1",
        "restored_valkey_keys": restored_keys,
        "completed_exact_bytes": True,
        "authority_pointer_matched": True,
        "deletion_continuity": True,
        "interrupted_run_reclaimed": True,
        "postgres_replay_idempotent": True,
    }


async def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "verify"}:
        raise SystemExit("usage: combined_authority_restore.py seed|verify")
    if sys.argv[1] == "seed":
        result = await seed()
    else:
        result = await verify(json.load(sys.stdin))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
