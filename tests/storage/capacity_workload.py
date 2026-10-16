"""Opt-in bounded actual-database workload; stdout is an append-only event stream."""

import asyncio
import json
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.source_store import (
    MAX_RESERVATION,
    ROOT_QUOTA,
    SCOPE_QUOTA,
    StorageConflictError,
)
from capacity_common import (
    BODY_SIZE,
    DESIGN,
    GENERATOR,
    MIB,
    SEED,
    body_for,
    digest,
    format_boundary_payload,
)
from publication_fixture import CONTEXT
from research_fixture import research_payload
from research_publication_fixture import research_publication_payload


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


class Probe:
    def __init__(self):
        self.store = ResearchImportStore()
        self.manifest = {
            "sources": [],
            "revisions": [],
            "publications": [],
            "roots": [],
            "cancelled": [],
        }
        self.charges = {}

    async def operation(self, phase, root, kind, call, byte_count=0):
        start = time.monotonic()
        outcome = "success"
        try:
            return await call()
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            end = time.monotonic()
            emit(
                "operation",
                phase=phase,
                root=str(root),
                kind=kind,
                start=start,
                end=end,
                duration_seconds=end - start,
                byte_count=byte_count,
                outcome=outcome,
            )

    async def sql(self, query, args=()):
        async with self.store._transaction(read=True) as conn:
            return await (await conn.execute(query, args)).fetchall()

    async def root(self, scope):
        root = await self.store.create_research_root(scope)
        self.charges[str(root)] = 0
        self.manifest["roots"].append({"scope": str(scope), "root": str(root)})
        return root

    async def source(self, scope, root, phase, ordinal, index, size):
        op = await self.operation(
            phase,
            ordinal,
            "source_reserve",
            lambda: self.store.reserve(scope, root, 1, size + 1000),
            size + 1000,
        )
        body = body_for(phase, ordinal, index, size)
        source = await self.operation(
            phase,
            ordinal,
            "source_commit",
            lambda: self.store.commit_source(
                scope, root, 1, op, body, "https://example.test/revision"
            ),
            size,
        )
        # The expected descriptor is derived independently from the submitted bytes.
        from agent.experimental.source_store import source_descriptor

        descriptor = source_descriptor(body, "https://example.test/revision")
        self.charges[str(root)] += len(body) + len(descriptor.data)
        self.manifest["sources"].append(
            {
                "scope": str(scope),
                "root": str(root),
                "source": str(source),
                "operation": str(op),
                "phase": phase,
                "ordinal": ordinal,
                "index": index,
                "size": size,
                "body_digest": digest(body),
                "descriptor_digest": descriptor.digest,
                "descriptor_bytes": len(descriptor.data),
            }
        )
        return source, body

    async def ingest(self, scope, root, phase, ordinal):
        for index in range(11):
            source, body = await self.source(
                scope, root, phase, ordinal, index, BODY_SIZE
            )
            del source, body

    async def research(self, scope, root, phase):
        source, body = await self.source(scope, root, phase, 0, 0, 128)
        revision = await self.store.reserve_research(scope, root, 1, None, MIB)
        raw = research_payload(scope, root, revision, source, body)
        await self.store.commit_research(scope, root, 1, revision, raw)
        pinned = await self.store.read_research(scope, root, revision)
        self.charges[str(root)] += len(pinned.document.data)
        self.manifest["revisions"].append(
            {
                "scope": str(scope),
                "root": str(root),
                "revision": str(revision),
                "digest": pinned.document.digest,
                "canonical_bytes": len(pinned.document.data),
            }
        )
        return revision, pinned

    async def publish(self, scope, root, revision, pinned, *, large=False):
        phase = "format-boundary" if large else "concurrent"
        identity = await self.operation(
            phase,
            "publication",
            "publication_reserve",
            lambda: self.store.reserve_research_publication(
                scope, root, 1, revision, MIB, CONTEXT
            ),
        )
        raw = research_publication_payload(pinned, identity, CONTEXT)
        if large:
            raw = format_boundary_payload(raw)
        await self.operation(
            phase,
            "publication",
            "publication_commit",
            lambda: self.store.commit_research_publication(
                scope, root, 1, revision, identity, raw, CONTEXT
            ),
            len(raw),
        )
        value = await self.operation(
            phase,
            "publication",
            "publication_read",
            lambda: self.store.read_research_publication(
                scope, root, identity, CONTEXT
            ),
        )
        self.charges[str(root)] += value.size
        self.manifest["publications"].append(
            {
                "scope": str(scope),
                "root": str(root),
                "publication": str(identity),
                "digest": value.document.digest,
                "size": value.size,
                "canonical_bytes": len(value.document.data),
                "output_bytes": sum(
                    len(getattr(value, key))
                    for key in ("summary", "analysis", "dossier")
                ),
                "outputs": {
                    key: digest(getattr(value, key))
                    for key in ("summary", "analysis", "dossier")
                },
            }
        )
        return identity

    async def reject(self, phase, root, call, error):
        async def expected():
            try:
                await call()
            except error:
                return
            raise AssertionError("required boundary rejection missing")

        await self.operation(phase, root, "expected_rejection", expected)

    async def state(self):
        return await self.sql(
            "SELECT root_id,charged,current_research_revision,(SELECT count(*) FROM research_staging.snapshots s WHERE s.root_id=r.root_id) AS sources FROM research_staging.roots r ORDER BY root_id"
        )

    async def scope_boundary(self):
        scope = uuid4()
        await self.store.provision_scope(scope)
        remaining = SCOPE_QUOTA
        last = None
        for _ in range(11):
            root = await self.root(scope)
            last = root
            root_free = ROOT_QUOTA
            while remaining and root_free:
                size = min(remaining, root_free, MAX_RESERVATION)
                op = await self.operation(
                    "scope-boundary",
                    root,
                    "source_reserve",
                    lambda root=root, size=size: self.store.reserve(
                        scope, root, 1, size
                    ),
                    size,
                )
                self.manifest["cancelled"].append(
                    {"scope": str(scope), "root": str(root), "operation": str(op)}
                )
                remaining -= size
                root_free -= size
        assert remaining == 0 and last is not None
        before = await self.state()
        await self.reject(
            "scope-boundary",
            last,
            lambda: self.store.reserve(scope, last, 1, 1),
            StorageConflictError,
        )
        assert before == await self.state()
        for item in self.manifest["cancelled"]:
            await self.store.cancel_reservation(
                scope, UUID(item["root"]), UUID(item["operation"])
            )
        emit(
            "boundary",
            kind="scope",
            logical_reserved_bytes=SCOPE_QUOTA,
            extra_byte_denied=True,
        )

    async def sizes(self):
        row = (
            await self.sql(
                "SELECT pg_database_size(current_database()) AS database_bytes, pg_current_wal_lsn()::text AS wal_lsn, version() AS postgres_version"
            )
        )[0]
        row["relations"] = await self.sql(
            "SELECT relname,pg_total_relation_size(c.oid) AS total_bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='research_staging' AND c.relkind='r' ORDER BY relname"
        )
        return row

    async def run(self):
        for method in (
            "install",
            "migrate_revisions",
            "migrate_publications",
            "migrate_rerenders",
            "migrate_imports",
            "migrate_expiry",
            "migrate_research",
            "migrate_research_publications",
            "migrate_research_imports",
        ):
            await getattr(self.store, method)()
        before = await self.sizes()
        emit("storage_before", **before)
        sequential = uuid4()
        await self.store.provision_scope(sequential)
        root = await self.root(sequential)
        start = time.monotonic()
        await self.ingest(sequential, root, "sequential", 0)
        emit(
            "phase",
            phase="sequential",
            start=start,
            end=time.monotonic(),
            body_bytes=11 * BODY_SIZE,
        )
        concurrent = uuid4()
        await self.store.provision_scope(concurrent)
        roots = [await self.root(concurrent) for _ in range(4)]
        pubroot = await self.root(concurrent)
        revision, pinned = await self.research(concurrent, pubroot, "publication-input")
        publication_intervals = []

        async def publications():
            for _ in range(10):
                begin = time.monotonic()
                await self.publish(concurrent, pubroot, revision, pinned)
                publication_intervals.append((begin, time.monotonic()))

        async def ingestion():
            begin = time.monotonic()
            async with asyncio.TaskGroup() as group:
                for ordinal, target in enumerate(roots):
                    group.create_task(
                        self.ingest(concurrent, target, "concurrent", ordinal)
                    )
            return begin, time.monotonic()

        async with asyncio.TaskGroup() as group:
            writers = group.create_task(ingestion())
            group.create_task(publications())
        begin, end = writers.result()
        overlap = sum(a < end and b > begin for a, b in publication_intervals)
        assert overlap > 0, "no publication overlapped ingestion"
        emit(
            "phase",
            phase="concurrent",
            start=begin,
            end=end,
            body_bytes=44 * BODY_SIZE,
            publication_overlap=overlap,
        )
        before_root = await self.state()
        await self.reject(
            "root-boundary",
            root,
            lambda: self.store.reserve(sequential, root, 1, 2 * MIB),
            StorageConflictError,
        )
        assert before_root == await self.state()
        emit("boundary", kind="root", extra_bytes_denied=2 * MIB)
        await self.scope_boundary()
        format_scope = uuid4()
        await self.store.provision_scope(format_scope)
        format_root = await self.root(format_scope)
        rev, pin = await self.research(format_scope, format_root, "format-input")
        publication = await self.publish(
            format_scope, format_root, rev, pin, large=True
        )
        before_format = await self.state()
        await self.reject(
            "format-boundary",
            format_root,
            lambda: self.store.export_research_publication(
                format_scope, format_root, publication, CONTEXT
            ),
            ValueError,
        )
        assert before_format == await self.state()
        emit("boundary", kind="format", export_limit_bytes=MIB, rejected=True)
        after = await self.sizes()
        wal = (
            await self.sql(
                "SELECT pg_wal_lsn_diff(%s::pg_lsn,%s::pg_lsn)::bigint AS bytes",
                (after["wal_lsn"], before["wal_lsn"]),
            )
        )[0]["bytes"]
        emit("storage_after", **after, cluster_wal_delta_bytes=wal)
        assert [
            len(self.manifest[key]) for key in ("sources", "revisions", "publications")
        ] == [57, 2, 11]
        self.manifest["charges"] = self.charges
        emit(
            "manifest",
            manifest=self.manifest,
            digest=digest(json.dumps(self.manifest, sort_keys=True).encode()),
        )


async def main():
    emit(
        "identity",
        design=DESIGN,
        database_schema=9,
        generator=GENERATOR,
        seed=SEED,
        python=platform.python_version(),
        psycopg=psycopg.__version__,
        utc_start=datetime.now(UTC).isoformat(),
    )
    try:
        await Probe().run()
    finally:
        emit(
            "resources",
            peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            peak_rss_units="KiB" if sys.platform == "linux" else "bytes",
            platform=sys.platform,
            utc_end=datetime.now(UTC).isoformat(),
        )


if __name__ == "__main__":
    asyncio.run(main())
