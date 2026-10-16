"""Resolve exact context evidence through a caller-established authority."""

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .canonical import CanonicalDocument, admit_canonical_json
from .knowledge import text_digest
from .knowledge_context import (
    CONTEXT_SCHEMA,
    ContentReference,
    ContextEnvelope,
    KnowledgeContext,
    MediaType,
)


@dataclass(frozen=True)
class ResolvedContextSource:
    reference: ContentReference
    body: bytes
    normalization_version: str
    media_type: MediaType


class ContextSourceResolver(Protocol):
    async def resolve(self, reference: ContentReference) -> ResolvedContextSource:
        """Authorize current caller/lifecycle and return exact bytes, or raise.

        Implementations own principal checks, root liveness/generation, imported
        mappings and read consistency. A payload URL must never drive acquisition.
        """
        ...


@dataclass(frozen=True)
class AdmittedContext:
    document: CanonicalDocument
    context: KnowledgeContext


async def admit_knowledge_context(
    raw: bytes,
    *,
    scope_id: str,
    research_id: str,
    revision_id: str,
    resolver: ContextSourceResolver,
) -> AdmittedContext:
    """Check supplied context and sources; not retained-history or publication proof.

    Resolution is sequential and bodies are not retained in the result. Callers
    own deadlines/cancellation and must recheck lifecycle at a later commit.
    """
    document = admit_canonical_json(raw, schema_version=CONTEXT_SCHEMA)
    context = ContextEnvelope.model_validate_json(document.data).context
    if (context.scope_id, context.research_id, context.revision_id) != (
        scope_id,
        research_id,
        revision_id,
    ):
        raise ValueError("knowledge context differs from caller identity")
    for snapshot in context.snapshots:
        source = await resolver.resolve(snapshot.content_ref)
        if (
            source.reference != snapshot.content_ref
            or not isinstance(source.body, bytes)
            or len(source.body) != snapshot.content_bytes
            or hashlib.sha256(source.body).hexdigest() != snapshot.content_digest
            or source.normalization_version != snapshot.normalization_version
            or source.media_type != snapshot.media_type
        ):
            raise ValueError("resolved source differs from pinned descriptor")
        text = source.body.decode("utf-8", errors="strict")
        for evidence in context.evidence:
            if evidence.snapshot_id != snapshot.snapshot_id:
                continue
            if (
                not evidence.start < evidence.end <= len(text)
                or text[evidence.start : evidence.end] != evidence.quote
                or text_digest(evidence.quote) != evidence.quote_digest
            ):
                raise ValueError("evidence differs from exact resolved source span")
        del text, source
    return AdmittedContext(document, context)
