"""Synthetic restore rehearsal, never an operator recovery or import command."""

import asyncio
import hashlib
import json
import sys
from uuid import UUID, uuid4

import psycopg
from agent.experimental.expiry_store import ExpiryStore
from agent.experimental.import_store import ImportStore
from agent.experimental.publication_store import PublicationStore
from agent.experimental.research_bundle import (
    RESEARCH_BUNDLE_SCHEMA,
    ResearchBundleStore,
)
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.research_publication_store import ResearchPublicationStore
from agent.experimental.research_store import ResearchStore
from agent.experimental.revision_store import RevisionStore
from agent.experimental.source_store import SourceStore, StorageConflictError
from publication_fixture import (
    CONTEXT,
    CONTEXT_V2,
    publication_payload,
    supported_revision,
)

SCOPE = UUID("37249300-d005-4f26-813c-3a6ecc9f54cc")
DELETED_BODY = b"Fixture evidence deleted AFTER the backup."
RETAINED_BODY = b"Fixture evidence retained across the backup."
RESEARCH_SCOPE = UUID("c4357bfe-a014-422d-b84f-4e1123f83961")
RESEARCH_DELETED = b"Complete history removed after backup."
RESEARCH_RETAINED = b"Complete history retained after backup."


async def rows(query, params=()):
    async with await psycopg.AsyncConnection.connect() as conn:
        return await (await conn.execute(query, params)).fetchall()


async def seed():
    store = SourceStore()
    await store.provision_scope(SCOPE)
    for body in (DELETED_BODY, RETAINED_BODY):
        root = await store.create_root(SCOPE)
        operation = await store.reserve(SCOPE, root, 1, 1000)
        snapshot = await store.commit_source(
            SCOPE, root, 1, operation, body, "https://example.test/revision"
        )
        schema = await rows("SELECT version FROM research_staging.schema_version")
        if schema in (
            [(2,)],
            [(3,)],
            [(4,)],
            [(5,)],
            [(6,)],
            [(7,)],
            [(8,)],
            [(9,)],
            [(10,)], [(11,)], [(12,)],
        ):
            from test_revision_store_db import payload

            revisions = RevisionStore()
            revision = await revisions.reserve_revision(SCOPE, root, 1, None, 10000)
            raw = payload(SCOPE, root, revision, snapshot, body)
            if schema in (
                [(3,)],
                [(4,)],
                [(5,)],
                [(6,)],
                [(7,)],
                [(8,)],
                [(9,)],
                [(10,)], [(11,)], [(12,)],
            ):
                raw = supported_revision(raw)
            await revisions.commit_revision(SCOPE, root, 1, revision, raw)
            if schema in (
                [(3,)],
                [(4,)],
                [(5,)],
                [(6,)],
                [(7,)],
                [(8,)],
                [(9,)],
                [(10,)], [(11,)], [(12,)],
            ):
                publications = PublicationStore()
                publication = await publications.reserve_publication(
                    SCOPE, root, 1, revision, 100000, CONTEXT
                )
                structure = (
                    await revisions.read_revision(SCOPE, root, revision)
                ).structure
                await publications.commit_publication(
                    SCOPE,
                    root,
                    1,
                    revision,
                    publication,
                    publication_payload(structure, publication),
                    CONTEXT,
                )
                if schema in ([(4,)], [(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
                    rerender = await publications.reserve_publication(
                        SCOPE,
                        root,
                        1,
                        revision,
                        100000,
                        CONTEXT_V2,
                        rerender_of=publication,
                        original_context=CONTEXT,
                    )
                    await publications.commit_publication(
                        SCOPE,
                        root,
                        1,
                        revision,
                        rerender,
                        publication_payload(structure, rerender, CONTEXT_V2),
                        CONTEXT_V2,
                    )
                if schema in ([(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
                    imports = ImportStore()
                    recipient = uuid4()
                    await imports.provision_scope(recipient)
                    bundle = await imports.export_publication(
                        SCOPE, root, publication, CONTEXT
                    )
                    target = await imports.reserve_import(
                        recipient,
                        SCOPE,
                        root,
                        publication,
                        bundle.data,
                        bundle.digest,
                        CONTEXT,
                    )
                    await imports.commit_import(recipient, target, bundle.data, CONTEXT)
    if schema in ([(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        await seed_research()
    print("Seeded bounded restore fixtures")


async def seed_research():
    from research_fixture import research_payload

    store = ResearchStore()
    await store.provision_scope(RESEARCH_SCOPE)
    for body in (RESEARCH_DELETED, RESEARCH_RETAINED):
        root = await store.create_research_root(RESEARCH_SCOPE)
        operation = await store.reserve(RESEARCH_SCOPE, root, 1, 1000)
        snapshot = await store.commit_source(
            RESEARCH_SCOPE, root, 1, operation, body, "https://example.test/revision"
        )
        prior = []
        for _ in range(2):
            parent = (
                UUID(prior[-1].research.verifications.structure.revision_id)
                if prior
                else None
            )
            revision = await store.reserve_research(
                RESEARCH_SCOPE, root, 1, parent, 100000
            )
            raw = research_payload(
                RESEARCH_SCOPE, root, revision, snapshot, body, tuple(prior)
            )
            await store.commit_research(RESEARCH_SCOPE, root, 1, revision, raw)
            prior.append(
                (await store.read_research(RESEARCH_SCOPE, root, revision)).revision
            )
            schema = await rows("SELECT version FROM research_staging.schema_version")
            if schema in ([(8,)], [(9,)], [(10,)], [(11,)], [(12,)]) and len(prior) == 1:
                from research_publication_fixture import research_publication_payload

                publications = ResearchPublicationStore()
                pinned = await store.read_research(RESEARCH_SCOPE, root, revision)
                publication = await publications.reserve_research_publication(
                    RESEARCH_SCOPE, root, 1, revision, 100000, CONTEXT
                )
                await publications.commit_research_publication(
                    RESEARCH_SCOPE,
                    root,
                    1,
                    revision,
                    publication,
                    research_publication_payload(pinned, publication, CONTEXT),
                    CONTEXT,
                )
        if schema in ([(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
            original_revision = UUID(
                prior[0].research.verifications.structure.revision_id
            )
            rerender = await publications.reserve_research_publication(
                RESEARCH_SCOPE,
                root,
                1,
                original_revision,
                100000,
                CONTEXT_V2,
                rerender_of=publication,
                original_context=CONTEXT,
            )
            await publications.commit_research_publication(
                RESEARCH_SCOPE,
                root,
                1,
                original_revision,
                rerender,
                research_publication_payload(pinned, rerender, CONTEXT_V2),
                CONTEXT_V2,
            )
        if schema in ([(9,)], [(10,)], [(11,)], [(12,)]):
            imports = ResearchImportStore()
            recipient = uuid4()
            await imports.provision_scope(recipient)
            bundle = await imports.export_research_publication(
                RESEARCH_SCOPE, root, rerender, CONTEXT_V2
            )
            target = await imports.reserve_import(
                recipient,
                RESEARCH_SCOPE,
                root,
                rerender,
                bundle.data,
                bundle.digest,
                CONTEXT_V2,
            )
            await imports.commit_import(recipient, target, bundle.data, CONTEXT_V2)


async def research_control():
    control = await rows(
        "SELECT r.root_id,r.current_research_revision FROM research_staging.roots r JOIN research_staging.snapshots s USING(scope_id,root_id) JOIN research_staging.blobs b ON b.scope_id=s.scope_id AND b.digest=s.body_digest WHERE r.scope_id=%s AND b.body=%s",
        (RESEARCH_SCOPE, RESEARCH_DELETED),
    )
    if len(control) != 1:
        raise AssertionError("complete research restore control missing")
    return control[0]


async def marker():
    result = await rows(
        "SELECT s.root_id,s.snapshot_id FROM research_staging.snapshots s "
        "JOIN research_staging.blobs b ON b.scope_id=s.scope_id AND b.digest=s.body_digest "
        "WHERE s.scope_id=%s AND b.body=%s",
        (SCOPE, DELETED_BODY),
    )
    if len(result) != 1:
        raise ValueError("expected one pre-deletion restore fixture")
    return result[0]


async def delete_and_inventory():
    root, _ = await marker()
    schema = await rows("SELECT version FROM research_staging.schema_version")
    if schema in ([(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        await rows(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (SCOPE, root),
        )
        outcomes = await ExpiryStore().collect_expired(SCOPE)
        if not any(
            value.root_id == root and value.status == "purged" for value in outcomes
        ):
            raise AssertionError("post-backup expiry collection did not purge control")
    else:
        await SourceStore().delete_root(SCOPE, root)
    if schema in ([(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        research_root, _ = await research_control()
        await rows(
            "UPDATE research_staging.roots SET expires_at=now()-interval '1 second' WHERE scope_id=%s AND root_id=%s RETURNING root_id",
            (RESEARCH_SCOPE, research_root),
        )
        await ResearchStore().collect_expired(RESEARCH_SCOPE)
    deleted = await rows(
        "SELECT scope_id,root_id FROM research_staging.roots WHERE deleted ORDER BY scope_id,root_id"
    )
    print(
        json.dumps(
            {
                "schema_version": "fixture-deletion-inventory/1",
                "deleted": [[str(scope), str(root)] for scope, root in deleted],
            }
        )
    )


async def verify():
    # This DB has no serving endpoint. Stay quarantined on absent/bad inventory.
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("bounded current deletion inventory required")
    inventory = json.loads(raw)
    if (
        set(inventory) != {"schema_version", "deleted"}
        or inventory["schema_version"] != "fixture-deletion-inventory/1"
    ):
        raise ValueError("unsupported deletion inventory")
    deleted = {(UUID(scope), UUID(root)) for scope, root in inventory["deleted"]}
    root, snapshot = await marker()
    if (SCOPE, root) not in deleted:
        raise ValueError(
            "post-backup deletion history missing; keep restore quarantined"
        )
    store = SourceStore()
    # Prove this is the old backup, not an accidentally fresh post-deletion copy.
    if (await store.read_source(SCOPE, root, snapshot)).body != DELETED_BODY:
        raise AssertionError("backup fixture changed")
    schema = await rows("SELECT version FROM research_staging.schema_version")
    deleted_publications = []
    if schema in ([(3,)], [(4,)], [(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        deleted_publications = await rows(
            "SELECT publication_id FROM research_staging.publications WHERE scope_id=%s AND root_id=%s",
            (SCOPE, root),
        )
        if len(deleted_publications) != (
            2
            if schema in ([(4,)], [(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)])
            else 1
        ):
            raise AssertionError("pre-deletion published control missing")
        for (publication,) in deleted_publications:
            await PublicationStore().read_publication(
                SCOPE,
                root,
                publication,
                await fixture_context(SCOPE, root, publication),
            )
    deleted_imports = []
    if schema in ([(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        deleted_imports = await rows(
            "SELECT scope_id,root_id FROM research_staging.import_operations WHERE origin_scope_id=%s AND origin_root_id=%s AND state='committed'",
            (SCOPE, root),
        )
        if len(deleted_imports) != 1:
            raise AssertionError("pre-deletion imported control missing")
        for recipient, target in deleted_imports:
            await ImportStore().read_import(recipient, target, CONTEXT)
    deleted_complete_imports = []
    deleted_complete_publications = []
    deleted_research = None
    if schema in ([(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        deleted_research = await research_control()
        if (RESEARCH_SCOPE, deleted_research[0]) not in deleted:
            raise ValueError(
                "complete research deletion history missing; keep quarantined"
            )
        await ResearchStore().read_research(RESEARCH_SCOPE, *deleted_research)
        if schema in ([(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
            deleted_complete_publications = await rows(
                "SELECT publication_id,context_digest FROM research_staging.research_publications WHERE scope_id=%s AND root_id=%s",
                (RESEARCH_SCOPE, deleted_research[0]),
            )
            if len(deleted_complete_publications) != 2:
                raise AssertionError(
                    "pre-deletion complete publication controls missing"
                )
            for identity, digest in deleted_complete_publications:
                await ResearchPublicationStore().read_research_publication(
                    RESEARCH_SCOPE,
                    deleted_research[0],
                    identity,
                    complete_context(digest),
                )
    if schema in ([(9,)], [(10,)], [(11,)], [(12,)]):
        deleted_complete_imports = await rows(
            "SELECT scope_id,root_id FROM research_staging.import_operations WHERE origin_scope_id=%s AND origin_root_id=%s AND state='committed'",
            (RESEARCH_SCOPE, deleted_research[0]),
        )
        if len(deleted_complete_imports) != 1:
            raise AssertionError("pre-deletion complete import control missing")
        for recipient, target in deleted_complete_imports:
            await ResearchImportStore().read_import(recipient, target, CONTEXT_V2)
    for scope, deleted_root in deleted:
        existing = await rows(
            "SELECT 1 FROM research_staging.roots WHERE scope_id=%s AND root_id=%s",
            (scope, deleted_root),
        )
        if existing:
            await store.delete_root(scope, deleted_root)
    try:
        await store.read_source(SCOPE, root, snapshot)
    except StorageConflictError:
        pass
    else:
        raise AssertionError("deleted evidence was re-exposed")
    for (publication,) in deleted_publications:
        try:
            await PublicationStore().read_publication(SCOPE, root, publication, CONTEXT)
        except StorageConflictError:
            pass
        else:
            raise AssertionError("deleted publication was re-exposed")
    for identity, digest in deleted_complete_publications:
        try:
            await ResearchPublicationStore().read_research_publication(
                RESEARCH_SCOPE, deleted_research[0], identity, complete_context(digest)
            )
        except StorageConflictError:
            pass
        else:
            raise AssertionError("deleted complete publication re-exposed")
    for recipient, target in deleted_complete_imports:
        try:
            await ResearchImportStore().read_import(recipient, target, CONTEXT_V2)
        except StorageConflictError:
            pass
        else:
            raise AssertionError("deleted complete import re-exposed")
    for recipient, target in deleted_imports:
        try:
            await ImportStore().read_import(recipient, target, CONTEXT)
        except StorageConflictError:
            pass
        else:
            raise AssertionError("deleted imported evidence was re-exposed")
    if deleted_research is not None:
        try:
            await ResearchStore().read_research(RESEARCH_SCOPE, *deleted_research)
        except StorageConflictError:
            pass
        else:
            raise AssertionError("deleted complete research re-exposed")
    retained = await rows(
        "SELECT s.scope_id,s.root_id,s.snapshot_id FROM research_staging.snapshots s "
        "JOIN research_staging.roots r USING(scope_id,root_id) WHERE NOT r.deleted AND r.expires_at>now()"
    )
    found_control = False
    for scope, retained_root, retained_snapshot in retained:
        source = await store.read_source(scope, retained_root, retained_snapshot)
        found_control |= scope == SCOPE and source.body == RETAINED_BODY
    if not found_control:
        raise AssertionError("retained control source missing")
    unresolved = await rows(
        "SELECT count(*) FROM research_staging.operations o "
        "JOIN research_staging.roots r USING(scope_id,root_id) "
        "LEFT JOIN research_staging.snapshots s ON s.scope_id=o.scope_id AND s.root_id=o.root_id AND s.snapshot_id=o.snapshot_id "
        "WHERE o.state='committed' AND NOT r.deleted AND s.snapshot_id IS NULL"
    )
    if unresolved[0][0] != 0:
        raise AssertionError("live receipt reference unresolved")
    schema = await rows("SELECT version FROM research_staging.schema_version")
    revision_count = 0
    if schema in (
        [(2,)],
        [(3,)],
        [(4,)],
        [(5,)],
        [(6,)],
        [(7,)],
        [(8,)],
        [(9,)],
        [(10,)], [(11,)], [(12,)],
    ):
        revisions = await rows(
            "SELECT v.scope_id,v.root_id,v.revision_id FROM research_staging.revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE NOT r.deleted AND r.expires_at>now()"
        )
        for scope, root, revision in revisions:
            await RevisionStore().read_revision(scope, root, revision)
        revision_count = len(revisions)
        unresolved_revisions = await rows(
            "SELECT count(*) FROM research_staging.revision_operations o JOIN research_staging.roots r USING(scope_id,root_id) LEFT JOIN research_staging.revisions v ON v.scope_id=o.scope_id AND v.root_id=o.root_id AND v.revision_id=o.revision_id WHERE o.state='committed' AND NOT r.deleted AND v.revision_id IS NULL"
        )
        if unresolved_revisions[0][0] != 0:
            raise AssertionError("live revision receipt reference unresolved")
        deleted_revisions = await rows(
            "SELECT count(*) FROM research_staging.revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE r.deleted"
        )
        if deleted_revisions[0][0] != 0:
            raise AssertionError("deleted revision bodies survived reconciliation")
    publication_count = 0
    if schema in ([(3,)], [(4,)], [(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        publications = await rows(
            "SELECT p.scope_id,p.root_id,p.publication_id FROM research_staging.publications p JOIN research_staging.roots r USING(scope_id,root_id) WHERE NOT r.deleted AND r.expires_at>now()"
        )
        for scope, root, publication in publications:
            retained_publication = await PublicationStore().read_publication(
                scope,
                root,
                publication,
                await fixture_context(scope, root, publication),
            )
            if (
                await PublicationStore().publication_receipt(scope, root, publication)
                != retained_publication.document.digest
            ):
                raise AssertionError("publication receipt digest mismatch")
        publication_count = len(publications)
        unresolved = await rows(
            "SELECT count(*) FROM research_staging.publication_operations o JOIN research_staging.roots r USING(scope_id,root_id) LEFT JOIN research_staging.publications p ON p.scope_id=o.scope_id AND p.root_id=o.root_id AND p.publication_id=o.publication_id WHERE o.state='committed' AND NOT r.deleted AND p.publication_id IS NULL"
        )
        purged = await rows(
            "SELECT count(*) FROM research_staging.publications p JOIN research_staging.roots r USING(scope_id,root_id) WHERE r.deleted"
        )
        if unresolved[0][0] or purged[0][0]:
            raise AssertionError("publication restore closure failed")
        if not any(scope == SCOPE for scope, _, _ in publications):
            raise AssertionError("retained publication control missing")
    import_count = 0
    complete_import_count = 0
    if schema in ([(5,)], [(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        imported = await rows(
            "SELECT o.scope_id,o.root_id,o.context_digest,o.origin_scope_id FROM research_staging.import_operations o JOIN research_staging.roots r USING(scope_id,root_id) WHERE o.state='committed' AND NOT r.deleted AND r.expires_at>now()"
        )
        trusted = {context.digest(): context for context in (CONTEXT, CONTEXT_V2)}
        for recipient, target, context_digest, _ in imported:
            if context_digest not in trusted:
                raise AssertionError("unknown fixture import context")
            reader = await import_reader(recipient, target)
            value = await reader.read_import(recipient, target, trusted[context_digest])
            if (
                await reader.import_receipt(recipient, target)
                != value.bundle.document.digest
            ):
                raise AssertionError("import receipt digest mismatch")
            if isinstance(reader, ResearchImportStore):
                complete_import_count += 1
            else:
                import_count += 1
        if schema in ([(9,)], [(10,)], [(11,)], [(12,)]):
            controls = await rows(
                "SELECT count(*) FROM research_staging.import_operations o JOIN research_staging.roots r USING(scope_id,root_id) WHERE o.origin_scope_id=%s AND o.state='committed' AND NOT r.deleted AND r.expires_at>now()",
                (RESEARCH_SCOPE,),
            )
            if controls[0][0] != 1:
                raise AssertionError("retained complete import control missing")
        if not any(origin == SCOPE for _, _, _, origin in imported):
            raise AssertionError("retained imported control missing")
        purged = await rows(
            "SELECT count(*) FROM research_staging.imported_bundles b JOIN research_staging.import_operations o USING(scope_id,root_id) JOIN research_staging.roots t USING(scope_id,root_id) JOIN research_staging.roots s ON s.scope_id=o.origin_scope_id AND s.root_id=o.origin_root_id WHERE t.deleted OR s.deleted"
        )
        if purged[0][0]:
            raise AssertionError("deleted imported bodies survived reconciliation")
    research_count = 0
    if schema in ([(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]):
        research = await rows(
            "SELECT v.scope_id,v.root_id,v.revision_id FROM research_staging.research_revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE NOT r.deleted AND r.expires_at>now()"
        )
        for scope, root, revision in research:
            value = await ResearchStore().read_research(scope, root, revision)
            if (
                await ResearchStore().research_receipt(scope, root, revision)
                != value.document.digest
            ):
                raise AssertionError("complete research receipt mismatch")
        research_count = len(research)
        if not any(scope == RESEARCH_SCOPE for scope, _, _ in research):
            raise AssertionError("retained complete research control missing")
        unresolved = await rows(
            "SELECT count(*) FROM research_staging.research_revision_operations o JOIN research_staging.roots r USING(scope_id,root_id) LEFT JOIN research_staging.research_revisions v ON v.scope_id=o.scope_id AND v.root_id=o.root_id AND v.revision_id=o.revision_id WHERE o.state='committed' AND NOT r.deleted AND v.revision_id IS NULL"
        )
        purged = await rows(
            "SELECT count(*) FROM research_staging.research_revisions v JOIN research_staging.roots r USING(scope_id,root_id) WHERE r.deleted"
        )
        if unresolved[0][0] or purged[0][0]:
            raise AssertionError("complete research restore closure failed")
    complete_publication_count = (
        await verify_complete_publications()
        if schema in ([(8,)], [(9,)], [(10,)], [(11,)], [(12,)])
        else 0
    )
    version = await rows("SHOW server_version")
    print(
        json.dumps(
            {
                "schema_version": "fixture-restore-result/1",
                "postgres_version": version[0][0],
                "verified_live_sources": len(retained),
                "verified_live_revisions": revision_count,
                "verified_complete_research_revisions": research_count,
                "verified_live_publications": publication_count,
                "verified_complete_research_publications": complete_publication_count,
                "verified_complete_research_exports": complete_publication_count,
                "verified_live_imports": import_count,
                "verified_complete_research_imports": complete_import_count,
                "deletion_inventory_entries": len(deleted),
                "post_backup_deletion_denied": True,
                "post_backup_expiry_collection_reconciled": schema
                in ([(6,)], [(7,)], [(8,)], [(9,)], [(10,)], [(11,)], [(12,)]),
                "live_receipt_references_resolve": True,
            }
        )
    )


def complete_context(digest):
    trusted = {context.digest(): context for context in (CONTEXT, CONTEXT_V2)}
    if digest not in trusted:
        raise AssertionError("unknown complete fixture context")
    return trusted[digest]


async def verify_complete_publications():
    store = ResearchBundleStore()
    values = await rows(
        "SELECT p.scope_id,p.root_id,p.publication_id,p.context_digest FROM research_staging.research_publications p JOIN research_staging.roots r USING(scope_id,root_id) WHERE NOT r.deleted AND r.expires_at>now()"
    )
    for scope, root, identity, digest in values:
        value = await store.read_research_publication(
            scope, root, identity, complete_context(digest)
        )
        if (
            await store.research_publication_receipt(scope, root, identity)
            != value.document.digest
        ):
            raise AssertionError("complete publication receipt mismatch")
        await store.export_research_publication(
            scope, root, identity, complete_context(digest)
        )
    if sum(scope == RESEARCH_SCOPE for scope, _, _, _ in values) != 2:
        raise AssertionError("retained complete publication/rerender controls missing")
    unresolved = await rows(
        "SELECT count(*) FROM research_staging.research_publication_operations o JOIN research_staging.roots r USING(scope_id,root_id) LEFT JOIN research_staging.research_publications p USING(scope_id,root_id,publication_id) WHERE o.state='committed' AND NOT r.deleted AND p.publication_id IS NULL"
    )
    purged = await rows(
        "SELECT count(*) FROM research_staging.research_publications p JOIN research_staging.roots r USING(scope_id,root_id) WHERE r.deleted"
    )
    if unresolved[0][0] or purged[0][0]:
        raise AssertionError("complete publication restore closure failed")
    return len(values)


async def source_state():
    retained = await rows(
        "SELECT scope_id,root_id,snapshot_id FROM research_staging.snapshots ORDER BY scope_id,root_id,snapshot_id"
    )
    manifest = []
    for scope, root, snapshot in retained:
        source = await SourceStore().read_source(scope, root, snapshot)
        manifest.append(
            [
                str(scope),
                str(root),
                str(snapshot),
                source.descriptor.digest,
                hashlib.sha256(source.body).hexdigest(),
            ]
        )
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(json.dumps({"sources": len(manifest), "fixture_manifest_sha256": digest}))


async def fixture_context(scope, root, publication):
    # Fixed trusted fixture allowlist; never construct arbitrary trust from payload.
    result = await rows(
        "SELECT context_digest FROM research_staging.publications WHERE scope_id=%s AND root_id=%s AND publication_id=%s",
        (scope, root, publication),
    )
    trusted = {context.digest(): context for context in (CONTEXT, CONTEXT_V2)}
    if len(result) != 1 or result[0][0] not in trusted:
        raise AssertionError("unknown fixture publication context")
    return trusted[result[0][0]]


async def research_state():
    retained = await rows(
        "SELECT scope_id,root_id,revision_id FROM research_staging.research_revisions ORDER BY scope_id,root_id,revision_id"
    )
    manifest = []
    for scope, root, revision in retained:
        value = await ResearchStore().read_research(scope, root, revision)
        manifest.append([str(scope), str(root), str(revision), value.document.digest])
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(
        json.dumps(
            {
                "complete_research_revisions": len(manifest),
                "fixture_manifest_sha256": digest,
            }
        )
    )


async def import_reader(scope, root):
    schema = await rows("SELECT version FROM research_staging.schema_version")
    if schema in ([(9,)], [(10,)], [(11,)], [(12,)]):
        value = await rows(
            "SELECT bundle_schema FROM research_staging.import_operations WHERE scope_id=%s AND root_id=%s",
            (scope, root),
        )
        if value == [(RESEARCH_BUNDLE_SCHEMA,)]:
            return ResearchImportStore()
    return ImportStore()


async def research_publication_state():
    store = ResearchBundleStore()
    values = await rows(
        "SELECT scope_id,root_id,publication_id,context_digest FROM research_staging.research_publications ORDER BY scope_id,root_id,publication_id"
    )
    manifest = []
    for scope, root, publication, digest in values:
        value = await store.export_research_publication(
            scope, root, publication, complete_context(digest)
        )
        manifest.append([str(scope), str(root), str(publication), value.digest])
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(
        json.dumps(
            {"complete_publications": len(manifest), "fixture_manifest_sha256": digest}
        )
    )


async def import_state():
    retained = await rows(
        "SELECT o.scope_id,o.root_id,o.context_digest FROM research_staging.import_operations o JOIN research_staging.imported_bundles b USING(scope_id,root_id) ORDER BY o.scope_id,o.root_id"
    )
    trusted = {context.digest(): context for context in (CONTEXT, CONTEXT_V2)}
    manifest = []
    for scope, root, digest in retained:
        if digest not in trusted:
            raise AssertionError("unknown fixture import context")
        reader = await import_reader(scope, root)
        value = await reader.read_import(scope, root, trusted[digest])
        manifest.append([str(scope), str(root), value.bundle.document.digest])
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(json.dumps({"imports": len(manifest), "fixture_manifest_sha256": digest}))


async def publication_state():
    retained = await rows(
        "SELECT scope_id,root_id,publication_id FROM research_staging.publications ORDER BY scope_id,root_id,publication_id"
    )
    manifest = []
    for scope, root, publication in retained:
        value = await PublicationStore().read_publication(
            scope, root, publication, await fixture_context(scope, root, publication)
        )
        manifest.append(
            [
                str(scope),
                str(root),
                str(publication),
                value.document.digest,
                *[
                    hashlib.sha256(getattr(value, layer)).hexdigest()
                    for layer in ("summary", "analysis", "dossier")
                ],
            ]
        )
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(
        json.dumps({"publications": len(manifest), "fixture_manifest_sha256": digest})
    )


async def revision_state():
    retained = await rows(
        "SELECT scope_id,root_id,revision_id FROM research_staging.revisions ORDER BY scope_id,root_id,revision_id"
    )
    manifest = []
    for scope, root, revision in retained:
        value = await RevisionStore().read_revision(scope, root, revision)
        manifest.append([str(scope), str(root), str(revision), value.document.digest])
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":")).encode()
    ).hexdigest()
    print(json.dumps({"revisions": len(manifest), "fixture_manifest_sha256": digest}))


if __name__ == "__main__":
    modes = {
        "source-state": source_state,
        "revision-state": revision_state,
        "publication-state": publication_state,
        "import-state": import_state,
        "research-state": research_state,
        "research-publication-state": research_publication_state,
        "restore-seed": seed,
        "restore-delete": delete_and_inventory,
        "restore-verify": verify,
    }
    asyncio.run(modes[sys.argv[1]]())
