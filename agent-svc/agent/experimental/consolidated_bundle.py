"""Bounded export and offline integrity validation for consolidated publications."""

import base64
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .artifact_bundle import LAYERS, bundle_member
from .canonical import MAX_BYTES, CanonicalDocument, admit_canonical_json
from .checked_knowledge import CHECKED_SCHEMA, CheckedKnowledge, admit_checked_history
from .consolidated_journey import RenderedReport
from .consolidated_storage_material import RetainedConsolidated
from .context_sources import ResolvedContextSource
from .manifest_outputs import ResolvedOutput, admit_render_manifest
from .render_manifest import MANIFEST_SCHEMA, OutputReference, RenderManifest
from .source_store import SCHEMA, StorageConflictError, source_descriptor

CONSOLIDATED_BUNDLE_SCHEMA = "retained-consolidated-bundle-prototype/1"
MAX_SNAPSHOTS = 100


class _BundleMaterial:
    def __init__(self, retained: RetainedConsolidated) -> None:
        self.retained = retained
        self.knowledge = CheckedKnowledge.model_validate_json(retained.knowledge_bytes)
        self.sources = {
            source.reference.snapshot_id: source for source in retained.sources
        }
        self.outputs = {
            report.artifact.artifact_id: report for report in retained.reports
        }

    async def resolve(self, reference):
        source = self.sources[reference.snapshot_id]
        if source.reference != reference:
            raise ValueError("bundle source reference differs")
        return source

    async def resolve_revision(self, scope_id: str, research_id: str, revision_id: str):
        context = self.knowledge.context
        if (scope_id, research_id, revision_id) != (
            context.scope_id,
            context.research_id,
            context.revision_id,
        ):
            raise ValueError("bundle revision identity differs")
        return self.retained.knowledge_bytes

    async def resolve_output(self, reference: OutputReference) -> ResolvedOutput:
        report = self.outputs[reference.artifact_id]
        if report.artifact.content_ref != reference:
            raise ValueError("bundle output reference differs")
        return ResolvedOutput(reference, report.body)


def _member(data: Any, name: str) -> bytes:
    if (
        not isinstance(data, dict)
        or set(data) != {"sha256", "data"}
        or not isinstance(data["data"], str)
    ):
        raise ValueError(f"invalid consolidated bundle member: {name}")
    import hashlib

    try:
        decoded = base64.b64decode(data["data"], validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("invalid consolidated member encoding") from error
    if base64.b64encode(decoded).decode("ascii") != data["data"]:
        raise ValueError("noncanonical consolidated member encoding")
    if hashlib.sha256(decoded).hexdigest() != data["sha256"]:
        raise ValueError("consolidated member digest mismatch")
    return decoded


def _retention(value: object, now: datetime) -> None:
    if not isinstance(value, str):
        raise ValueError("consolidated retention must be timezone-qualified text")
    deadline = datetime.fromisoformat(value)
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or deadline.tzinfo is None
        or deadline.utcoffset() is None
        or deadline <= now
    ):
        raise ValueError("consolidated retention expired")


async def admit_consolidated_bundle(
    raw: bytes,
    *,
    expected_digest: str,
    scope: UUID,
    root: UUID,
    operation: UUID,
    now: datetime,
) -> CanonicalDocument:
    """Validate exact consolidated bytes without retrieval or model execution."""
    document = admit_canonical_json(raw, schema_version=CONSOLIDATED_BUNDLE_SCHEMA)
    if document.digest != expected_digest:
        raise ValueError("consolidated bundle differs from expected digest")
    fields = json.loads(document.data)
    expected_fields = {
        "schema_version",
        "scope_id",
        "root_id",
        "operation_id",
        "retained_until",
        "fixture_only",
        "receipt_digest",
        "snapshot_ids",
        "members",
    }
    if set(fields) != expected_fields:
        raise ValueError("unexpected consolidated bundle fields")
    if (fields["scope_id"], fields["root_id"], fields["operation_id"]) != (
        str(scope),
        str(root),
        str(operation),
    ):
        raise ValueError("consolidated bundle differs from expected origin")
    _retention(fields["retained_until"], now)
    if type(fields["fixture_only"]) is not bool:
        raise ValueError("consolidated provenance marker must be boolean")
    if (
        not isinstance(fields["receipt_digest"], str)
        or len(fields["receipt_digest"]) != 64
        or any(c not in "0123456789abcdef" for c in fields["receipt_digest"])
    ):
        raise ValueError("invalid consolidated receipt digest")
    snapshot_ids = fields["snapshot_ids"]
    if (
        not isinstance(snapshot_ids, list)
        or len(snapshot_ids) > MAX_SNAPSHOTS
        or any(
            not isinstance(value, str) or not 1 <= len(value) <= 200
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
            for value in snapshot_ids
        )
        or len(set(snapshot_ids)) != len(snapshot_ids)
        or snapshot_ids != sorted(snapshot_ids)
    ):
        raise ValueError("invalid consolidated snapshot identities")
    members = fields["members"]
    names = {
        "knowledge.json",
        "manifest.json",
        *(f"sources/{index}.{extension}" for index in range(len(snapshot_ids)) for extension in ("body", "json")),
        *(f"outputs/{layer}.md" for layer in LAYERS),
    }
    if not isinstance(members, dict) or set(members) != names:
        raise ValueError("missing, extra or unsafe consolidated member")
    decoded = {name: _member(member, name) for name, member in members.items()}

    knowledge_document = admit_canonical_json(
        decoded["knowledge.json"], schema_version=CHECKED_SCHEMA
    )
    knowledge = CheckedKnowledge.model_validate_json(knowledge_document.data)
    context = knowledge.context
    actual_snapshot_ids = sorted(s.snapshot_id for s in context.snapshots)
    if actual_snapshot_ids != snapshot_ids:
        raise ValueError("consolidated snapshot identities differ from knowledge")
    sources = []
    for index, snapshot in enumerate(sorted(context.snapshots, key=lambda item: item.snapshot_id)):
        body = decoded[f"sources/{index}.body"]
        descriptor = admit_canonical_json(
            decoded[f"sources/{index}.json"], schema_version=SCHEMA
        )
        expected = source_descriptor(body, snapshot.canonical_url)
        if descriptor.data != expected.data:
            raise ValueError("consolidated source descriptor differs from body")
        sources.append(
            ResolvedContextSource(
                snapshot.content_ref,
                body,
                snapshot.normalization_version,
                snapshot.media_type,
            )
        )
    manifest_document = admit_canonical_json(
        decoded["manifest.json"], schema_version=MANIFEST_SCHEMA
    )
    manifest = RenderManifest.model_validate_json(manifest_document.data)
    reports = tuple(
        RenderedReport(artifact, decoded[f"outputs/{artifact.layer}.md"])
        for artifact in manifest.artifacts
    )
    retained = RetainedConsolidated(
        knowledge_document.data,
        manifest_document.data,
        tuple(sources),
        reports,
        fields["fixture_only"],
        fields["receipt_digest"],
    )
    material = _BundleMaterial(retained)
    await admit_checked_history(
        retained.knowledge_bytes,
        prior=(),
        scope_id=context.scope_id,
        research_id=context.research_id,
        revision_id=context.revision_id,
        resolver=material,
        reviewers=tuple({item.reviewer for item in knowledge.verification_inputs}),
    )
    admitted = await admit_render_manifest(
        retained.manifest_bytes,
        scope_id=context.scope_id,
        research_id=context.research_id,
        revision_id=context.revision_id,
        artifact_set_id=manifest.artifact_set_id,
        resolver=material,
        reviewers=tuple({item.reviewer for item in manifest.audit_inputs}),
    )
    from .publication_gate import _check_eligibility

    _check_eligibility(admitted)
    if fields["receipt_digest"] != manifest_document.digest:
        raise ValueError("consolidated receipt differs from manifest digest")
    return document


async def build_consolidated_bundle(
    retained: RetainedConsolidated,
    *,
    scope: UUID,
    root: UUID,
    operation: UUID,
    retained_until: datetime,
) -> CanonicalDocument:
    """Build and independently validate an exact bounded consolidated bundle."""
    if retained_until.tzinfo is None or retained_until.utcoffset() is None:
        raise ValueError("timezone-qualified retention required")
    knowledge = CheckedKnowledge.model_validate_json(retained.knowledge_bytes)
    members: dict[str, dict[str, str]] = {
        "knowledge.json": bundle_member(retained.knowledge_bytes),
        "manifest.json": bundle_member(retained.manifest_bytes),
    }
    sources = {source.reference.snapshot_id: source for source in retained.sources}
    snapshots = sorted(knowledge.context.snapshots, key=lambda item: item.snapshot_id)
    for index, snapshot in enumerate(snapshots):
        source = sources[snapshot.snapshot_id]
        descriptor = source_descriptor(source.body, snapshot.canonical_url)
        members[f"sources/{index}.json"] = bundle_member(descriptor.data)
        members[f"sources/{index}.body"] = bundle_member(source.body)
    for report in retained.reports:
        members[f"outputs/{report.artifact.layer}.md"] = bundle_member(report.body)
    encoded = sum(
        4 * ((len(base64.b64decode(member["data"])) + 2) // 3)
        for member in members.values()
    )
    if encoded > MAX_BYTES:
        raise StorageConflictError("consolidated export encoded byte limit exceeded")
    raw = json.dumps(
        {
            "schema_version": CONSOLIDATED_BUNDLE_SCHEMA,
            "scope_id": str(scope),
            "root_id": str(root),
            "operation_id": str(operation),
            "retained_until": retained_until.isoformat(),
            "fixture_only": retained.fixture_only,
            "receipt_digest": retained.receipt_digest,
            "snapshot_ids": [s.snapshot_id for s in snapshots],
            "members": members,
        }
    ).encode()
    document = admit_canonical_json(raw, schema_version=CONSOLIDATED_BUNDLE_SCHEMA)
    await admit_consolidated_bundle(
        document.data,
        expected_digest=document.digest,
        scope=scope,
        root=root,
        operation=operation,
        now=datetime.now(UTC),
    )
    return document
