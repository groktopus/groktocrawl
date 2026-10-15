"""Complete fixture history interchange; offline integrity is not import authority."""

import json
from datetime import UTC, datetime
from uuid import UUID

from .artifact_bundle import (
    LAYERS,
    MAX_SNAPSHOTS,
    VerifiedBundle,
    _bundle_parts,
    bundle_member,
)
from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .publication_store import PublicationContext
from .research_publication import admit_research_publication
from .research_publication_store import ResearchPublicationStore
from .research_revision import _decode, admit_research_revision
from .source_store import Connection, StorageConflictError

RESEARCH_BUNDLE_SCHEMA = "retained-research-bundle-prototype/1"


def admit_research_bundle(
    raw: bytes,
    *,
    expected_digest: str,
    scope: UUID,
    root: UUID,
    publication: UUID,
    context: PublicationContext,
    now: datetime,
) -> VerifiedBundle:
    document, revisions, snapshots, decoded, sources = _bundle_parts(
        raw,
        schema_version=RESEARCH_BUNDLE_SCHEMA,
        expected_digest=expected_digest,
        scope=scope,
        root=root,
        publication=publication,
        now=now,
    )
    prefix = []
    referenced = set()
    for identity in revisions:
        member = decoded[f"revisions/{identity}.json"]
        revision = _decode(member)
        structure = revision.revision.research.verifications.structure
        if (
            member != revision.document.data
            or structure.revision_id != str(identity)
            or structure.scope_id != str(scope)
            or structure.research_id != str(root)
        ):
            raise ValueError(
                "complete bundle revision identity or canonical bytes differ"
            )
        for source in structure.snapshots:
            referenced.add(source.snapshot_id)
            if source.snapshot_id not in sources:
                raise ValueError("complete bundle source missing")
            body, descriptor = sources[source.snapshot_id]
            if (
                source.text.encode() != body
                or source.digest != descriptor["body_sha256"]
                or source.canonical_url != descriptor["url"]
                or source.normalization_version != descriptor["normalization"]
            ):
                raise ValueError("complete bundle source closure differs")
        prefix.append(member)
    # Validate the complete chain once, including immutable assessments/questions
    # and removal/reintroduction declarations, not just structural ancestry.
    selected = admit_research_revision(
        prefix[-1],
        scope_id=str(scope),
        research_id=str(root),
        revision_id=str(revisions[-1]),
        parent_revision_id=str(revisions[-2]) if len(revisions) > 1 else None,
        prior=tuple(prefix[:-1]),
    )
    if referenced != set(sources):
        raise ValueError("complete bundle includes unreferenced source")
    result = admit_research_publication(
        decoded["publication.json"], selected, publication, context
    )
    if result.document.data != decoded["publication.json"] or any(
        decoded[f"outputs/{layer}.md"] != getattr(result, layer) for layer in LAYERS
    ):
        raise ValueError("complete bundle publication or output bytes differ")
    return VerifiedBundle(document, scope, root, publication, revisions, snapshots)


class ResearchBundleStore(ResearchPublicationStore):
    async def export_research_publication(
        self,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> CanonicalDocument:
        async with self._transaction(read=True) as conn:
            await self._require_complete_publications(conn)
            return await self._export_research_publication(
                conn, scope, root, publication, context
            )

    async def _export_research_publication(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> CanonicalDocument:
        retained = await self._read_research_publication(
            conn, scope, root, publication, context
        )
        history = await self._research_history(
            conn, scope, root, UUID(json.loads(retained.document.data)["revision_id"])
        )
        row = await (
            await conn.execute(
                "SELECT expires_at FROM research_staging.roots WHERE scope_id=%s AND root_id=%s AND NOT deleted AND expires_at>now()",
                (scope, root),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("complete export root unavailable")
        snapshots = sorted(
            {
                UUID(s.snapshot_id)
                for r in history
                for s in r.revision.research.verifications.structure.snapshots
            },
            key=str,
        )
        if len(snapshots) > MAX_SNAPSHOTS:
            raise StorageConflictError("complete export source count exceeded")
        members = {}
        encoded_bytes = 0

        def include(name: str, data: bytes) -> None:
            nonlocal encoded_bytes
            encoded_bytes += 4 * ((len(data) + 2) // 3)
            if encoded_bytes > MAX_BYTES:
                raise StorageConflictError(
                    "complete export encoded byte limit exceeded"
                )
            members[name] = bundle_member(data)

        for revision in history:
            identity = revision.revision.research.verifications.structure.revision_id
            include(f"revisions/{identity}.json", revision.document.data)
        for snapshot in snapshots:
            source = await self._read_source(conn, scope, root, snapshot)
            include(f"sources/{snapshot}.json", source.descriptor.data)
            include(f"sources/{snapshot}.body", source.body)
        include("publication.json", retained.document.data)
        for layer in LAYERS:
            include(f"outputs/{layer}.md", getattr(retained, layer))
        document = admit_canonical_json(
            json.dumps(
                {
                    "schema_version": RESEARCH_BUNDLE_SCHEMA,
                    "scope_id": str(scope),
                    "root_id": str(root),
                    "publication_id": str(publication),
                    "revision_ids": [
                        r.revision.research.verifications.structure.revision_id
                        for r in history
                    ],
                    "snapshot_ids": list(map(str, snapshots)),
                    "retained_until": row["expires_at"].isoformat(),
                    "members": members,
                }
            ).encode(),
            schema_version=RESEARCH_BUNDLE_SCHEMA,
        )
        return admit_research_bundle(
            document.data,
            expected_digest=document.digest,
            scope=scope,
            root=root,
            publication=publication,
            context=context,
            now=datetime.now(UTC),
        ).document
