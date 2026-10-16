"""Bounded fixture export integrity, not cross-scope import or authorization."""

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .publication_store import PublicationContext, PublicationStore, admit_publication
from .revision_store import (
    MAX_REVISIONS,
    RetainedRevision,
    admit_revision,
    entity_records,
)
from .source_store import SCHEMA, Connection, StorageConflictError, source_descriptor

BUNDLE_SCHEMA = "retained-artifact-bundle-prototype/1"
MAX_SNAPSHOTS = 100
LAYERS = ("summary", "analysis", "dossier")


@dataclass(frozen=True)
class VerifiedBundle:
    document: CanonicalDocument
    scope_id: UUID
    root_id: UUID
    publication_id: UUID
    revision_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]


def bundle_member(data: bytes) -> dict[str, str]:
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode("ascii"),
    }


def _ids(value: object, limit: int, *, allow_empty: bool = False) -> tuple[UUID, ...]:
    if (
        not isinstance(value, list)
        or not (0 if allow_empty else 1) <= len(value) <= limit
    ):
        raise ValueError("bundle identity count invalid")
    result = []
    for item in value:
        if not isinstance(item, str) or str(UUID(item)) != item:
            raise ValueError("bundle identity must be canonical UUID text")
        result.append(UUID(item))
    if len(set(result)) != len(result):
        raise ValueError("duplicate bundle identity")
    return tuple(result)


def _bundle_parts(
    raw: bytes,
    *,
    schema_version: str,
    expected_digest: str,
    scope: UUID,
    root: UUID,
    publication: UUID,
    now: datetime,
) -> tuple[
    CanonicalDocument,
    tuple[UUID, ...],
    tuple[UUID, ...],
    dict[str, bytes],
    dict[str, Any],
]:
    document = admit_canonical_json(raw, schema_version=schema_version)
    if document.digest != expected_digest:
        raise ValueError("bundle differs from expected digest")
    fields = json.loads(document.data)
    if set(fields) != {
        "schema_version",
        "scope_id",
        "root_id",
        "publication_id",
        "revision_ids",
        "snapshot_ids",
        "retained_until",
        "members",
    }:
        raise ValueError("unexpected bundle fields")
    if (fields["scope_id"], fields["root_id"], fields["publication_id"]) != (
        str(scope),
        str(root),
        str(publication),
    ):
        raise ValueError("bundle differs from expected origin")
    if not isinstance(fields["retained_until"], str):
        raise ValueError("retention must be timezone-qualified text")
    deadline = datetime.fromisoformat(fields["retained_until"])
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or deadline.tzinfo is None
        or deadline.utcoffset() is None
    ):
        raise ValueError("timezone-qualified validation time required")
    if deadline <= now:
        raise ValueError("bundle retention expired")
    revisions = _ids(fields["revision_ids"], MAX_REVISIONS)
    snapshots = _ids(fields["snapshot_ids"], MAX_SNAPSHOTS, allow_empty=True)
    if list(map(str, snapshots)) != sorted(map(str, snapshots)):
        raise ValueError("snapshot identities must be ordered")
    names = {f"revisions/{revision}.json" for revision in revisions}
    names.update(
        f"sources/{snapshot}.{extension}"
        for snapshot in snapshots
        for extension in ("body", "json")
    )
    names.update(f"outputs/{layer}.md" for layer in LAYERS)
    names.add("publication.json")
    members = fields["members"]
    if not isinstance(members, dict) or set(members) != names:
        raise ValueError("missing, extra or unsafe bundle member")
    decoded = {}
    for name, member in members.items():
        if (
            not isinstance(member, dict)
            or set(member) != {"sha256", "data"}
            or not isinstance(member["data"], str)
        ):
            raise ValueError("invalid bundle member")
        try:
            data = base64.b64decode(member["data"], validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError("invalid member encoding") from error
        if base64.b64encode(data).decode("ascii") != member["data"]:
            raise ValueError("noncanonical member encoding")
        if hashlib.sha256(data).hexdigest() != member["sha256"]:
            raise ValueError("bundle member digest mismatch")
        decoded[name] = data
    sources = {}
    for snapshot in snapshots:
        body = decoded[f"sources/{snapshot}.body"]
        descriptor = admit_canonical_json(
            decoded[f"sources/{snapshot}.json"], schema_version=SCHEMA
        )
        source = source_descriptor(body, json.loads(descriptor.data).get("url"))
        if (
            source != descriptor
            or descriptor.data != decoded[f"sources/{snapshot}.json"]
        ):
            raise ValueError("source descriptor or body mismatch")
        sources[str(snapshot)] = (body, json.loads(descriptor.data))
    return document, revisions, snapshots, decoded, sources


def admit_bundle(
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
        schema_version=BUNDLE_SCHEMA,
        expected_digest=expected_digest,
        scope=scope,
        root=root,
        publication=publication,
        now=now,
    )
    history: list[RetainedRevision] = []
    entities: dict[str, tuple[str, str]] = {}
    referenced = set()
    for revision_id in revisions:
        member = decoded[f"revisions/{revision_id}.json"]
        revision = admit_revision(member, scope, root, revision_id)
        if revision.document.data != member:
            raise ValueError("revision bytes are not canonical")
        if revision.parent_id != (history[-1].revision_id if history else None):
            raise ValueError("revision ancestry incomplete or unordered")
        for identity, record in entity_records(revision.structure).items():
            if identity in entities and entities[identity] != record:
                raise ValueError("historical entity identity changed")
            entities[identity] = record
        for record_snapshot in revision.structure.snapshots:
            referenced.add(record_snapshot.snapshot_id)
            if record_snapshot.snapshot_id not in sources:
                raise ValueError("missing retained source")
            body, descriptor_fields = sources[record_snapshot.snapshot_id]
            if (
                record_snapshot.text.encode() != body
                or record_snapshot.digest != descriptor_fields["body_sha256"]
                or record_snapshot.canonical_url != descriptor_fields["url"]
                or record_snapshot.normalization_version
                != descriptor_fields["normalization"]
            ):
                raise ValueError("revision differs from retained source")
        history.append(revision)
    if referenced != set(sources):
        raise ValueError("unreferenced bundle source")
    selected = admit_publication(
        decoded["publication.json"], history[-1].structure, publication, context
    )
    if selected.document.data != decoded["publication.json"]:
        raise ValueError("publication bytes are not canonical")
    if any(
        decoded[f"outputs/{layer}.md"] != getattr(selected, layer) for layer in LAYERS
    ):
        raise ValueError("output differs from audited publication")
    return VerifiedBundle(document, scope, root, publication, revisions, snapshots)


class ArtifactBundleStore(PublicationStore):
    async def export_publication(
        self, scope: UUID, root: UUID, publication: UUID, context: PublicationContext
    ) -> CanonicalDocument:
        async with self._transaction(read=True) as conn:
            await self._require_publication_schema(conn)
            return await self._export_publication(
                conn, scope, root, publication, context
            )

    async def _export_publication(
        self,
        conn: Connection,
        scope: UUID,
        root: UUID,
        publication: UUID,
        context: PublicationContext,
    ) -> CanonicalDocument:
        retained = await self._read_publication(conn, scope, root, publication, context)
        row = await (
            await conn.execute(
                "SELECT expires_at FROM research_staging.roots WHERE scope_id=%s AND root_id=%s AND NOT deleted AND expires_at>now()",
                (scope, root),
            )
        ).fetchone()
        if row is None:
            raise StorageConflictError("export root unavailable")
        revision_id: UUID | None = UUID(
            json.loads(retained.document.data)["revision_id"]
        )
        history: list[RetainedRevision] = []
        seen = set()
        while revision_id is not None:
            if revision_id in seen or len(history) >= MAX_REVISIONS:
                raise StorageConflictError("export history exceeds bounds")
            seen.add(revision_id)
            revision = await self._read_revision(conn, scope, root, revision_id)
            history.append(revision)
            revision_id = revision.parent_id
        history.reverse()
        snapshots = sorted(
            {
                UUID(s.snapshot_id)
                for revision in history
                for s in revision.structure.snapshots
            },
            key=str,
        )
        if len(snapshots) > MAX_SNAPSHOTS:
            raise StorageConflictError("export source count exceeds bounds")
        members: dict[str, dict[str, str]] = {}
        encoded_bytes = 0

        def add(name: str, data: bytes) -> None:
            nonlocal encoded_bytes
            encoded_bytes += 4 * ((len(data) + 2) // 3)
            if encoded_bytes > MAX_BYTES:
                raise StorageConflictError("export byte limit exceeded")
            members[name] = bundle_member(data)

        for revision in history:
            add(f"revisions/{revision.revision_id}.json", revision.document.data)
        for snapshot in snapshots:
            source = await self._read_source(conn, scope, root, snapshot)
            add(f"sources/{snapshot}.json", source.descriptor.data)
            add(f"sources/{snapshot}.body", source.body)
        add("publication.json", retained.document.data)
        for layer in LAYERS:
            add(f"outputs/{layer}.md", getattr(retained, layer))
        raw = json.dumps(
            {
                "schema_version": BUNDLE_SCHEMA,
                "scope_id": str(scope),
                "root_id": str(root),
                "publication_id": str(publication),
                "revision_ids": [str(r.revision_id) for r in history],
                "snapshot_ids": list(map(str, snapshots)),
                "retained_until": row["expires_at"].isoformat(),
                "members": members,
            }
        ).encode()
        document = admit_canonical_json(raw, schema_version=BUNDLE_SCHEMA)
        return admit_bundle(
            document.data,
            expected_digest=document.digest,
            scope=scope,
            root=root,
            publication=publication,
            context=context,
            now=datetime.now(UTC),
        ).document
