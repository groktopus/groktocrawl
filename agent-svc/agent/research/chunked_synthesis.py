"""Pure planning and assembly primitives for resumable synthesis.

The caller owns persistence and model calls. These functions make evidence
coverage, input budgeting, continuity selection, and assembly deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_evidence_manifest(
    sources: list[dict[str, Any]], *, chunk_chars: int = 24_000
) -> dict[str, Any]:
    """Create a lossless URL-identified manifest with stable source chunks."""
    if type(chunk_chars) is not int or chunk_chars < 1:
        raise ValueError("chunk_chars must be a positive integer")
    seen: set[str] = set()
    manifest_sources: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    for source_index, source in enumerate(sources, start=1):
        source_id = source.get("id") or source.get("url")
        body = source.get("markdown")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("each source requires a stable id or URL")
        if source_id in seen:
            raise ValueError("source ids must be unique")
        if not isinstance(body, str) or not body:
            raise ValueError("each source requires non-empty Markdown")
        seen.add(source_id)
        source_digest = _digest(body)
        manifest_sources.append(
            {
                "id": source_id,
                "url": source.get("url", source_id),
                "title": str(source.get("title", "")),
                "citation_index": source_index,
                "chars": len(body),
                "content_sha256": source_digest,
            }
        )
        start = 0
        ordinal = 0
        while start < len(body):
            end = min(start + chunk_chars, len(body))
            if end < len(body):
                boundary = body.rfind("\n", start + chunk_chars // 2, end)
                if boundary > start:
                    end = boundary + 1
            text = body[start:end]
            chunk_id = hashlib.sha256(
                f"{source_id}\0{start}\0{end}\0{_digest(text)}".encode()
            ).hexdigest()
            chunks.append(
                {
                    "id": chunk_id,
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "start": start,
                    "end": end,
                    "chars": len(text),
                    "content_sha256": _digest(text),
                }
            )
            start = end
            ordinal += 1
    return {
        "version": 1,
        "source_count": len(manifest_sources),
        "source_chars": sum(item["chars"] for item in manifest_sources),
        "sources": manifest_sources,
        "chunks": chunks,
        "manifest_sha256": _digest(
            json.dumps(
                {"sources": manifest_sources, "chunks": chunks},
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }


def validate_outline(value: Any) -> dict[str, Any]:
    """Validate model-generated outline IDs, titles, and bounded section count."""
    if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
        raise ValueError("outline must be an object with sections")
    sections = value["sections"]
    if not 1 <= len(sections) <= 12:
        raise ValueError("outline section count must be between 1 and 12")
    ids: set[str] = set()
    normalized: list[dict[str, str]] = []
    for item in sections:
        if not isinstance(item, dict):
            raise ValueError("outline sections must be objects")
        section_id = item.get("id")
        title = item.get("title")
        if (
            not isinstance(section_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", section_id)
            or section_id in ids
        ):
            raise ValueError("outline section ids must be unique safe identifiers")
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("outline section title is invalid")
        ids.add(section_id)
        normalized.append({"id": section_id, "title": title.strip()})
    return {"sections": normalized}


def assign_chunks_to_sections(
    manifest: dict[str, Any],
    outline: dict[str, Any],
    chunk_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assign every source chunk once, favoring title/outline lexical overlap.

    Unmatched chunks use a stable hash-based fallback and are explicitly
    counted by ``fallback_count`` in the returned assignment manifest.
    """
    sections = outline["sections"]
    ignored_terms = {"a", "an", "and", "for", "from", "in", "of", "the", "to", "iraq"}
    terms = {
        section["id"]: set(re.findall(r"[\w]+", section["title"].casefold()))
        - ignored_terms
        for section in sections
    }
    titles = {source["id"]: source.get("title", "") for source in manifest["sources"]}
    assigned: dict[str, list[str]] = {section["id"]: [] for section in sections}
    fallback = 0
    chunks = list(manifest["chunks"])
    if len(chunks) < len(sections):
        raise ValueError("outline has more sections than evidence chunks")

    # Reserve one chunk per section first, preferring the strongest lexical
    # match; this makes every outline section reachable and assemblable.
    remaining = list(chunks)
    for section in sections:
        scored = [
            (
                len(
                    (
                        set(re.findall(r"[\w]+", titles[chunk["source_id"]].casefold()))
                        | set(
                            re.findall(
                                r"[\w]+",
                                (chunk_texts or {}).get(chunk["id"], "").casefold(),
                            )
                        )
                    )
                    & terms[section["id"]]
                ),
                -index,
                chunk,
            )
            for index, chunk in enumerate(remaining)
        ]
        _, _, chosen = max(scored, key=lambda item: (item[0], item[1]))
        assigned[section["id"]].append(chosen["id"])
        remaining.remove(chosen)

    for chunk in remaining:
        source_terms = set(
            re.findall(r"[\w]+", titles[chunk["source_id"]].casefold())
        ) | set(
            re.findall(r"[\w]+", (chunk_texts or {}).get(chunk["id"], "").casefold())
        )
        scores = [
            (len(source_terms & terms[section["id"]]), section["id"])
            for section in sections
        ]
        best_score = max(score for score, _ in scores)
        if best_score:
            section_id = next(item for score, item in scores if score == best_score)
        else:
            fallback += 1
            bucket = int(chunk["id"][:12], 16) % len(sections)
            section_id = sections[bucket]["id"]
        assigned[section_id].append(chunk["id"])
    all_ids = [chunk_id for group in assigned.values() for chunk_id in group]
    expected = [chunk["id"] for chunk in manifest["chunks"]]
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(expected):
        raise ValueError(
            "outline assignment does not cover each evidence chunk exactly once"
        )
    order = {chunk_id: index for index, chunk_id in enumerate(expected)}
    assigned = {
        section_id: sorted(chunk_ids, key=order.__getitem__)
        for section_id, chunk_ids in assigned.items()
    }
    return {"sections": assigned, "fallback_count": fallback}


def checkpoint_section(
    state: dict[str, Any], section: dict[str, Any]
) -> dict[str, Any]:
    """Return a new checkpoint with one completed section, without mutation."""
    outline = state.get("outline")
    completed = state.get("completed_sections")
    if not isinstance(outline, dict) or not isinstance(completed, list):
        raise ValueError("synthesis checkpoint is incomplete")
    section_id = section.get("id")
    expected = [item.get("id") for item in outline.get("sections", [])]
    if section_id not in expected:
        raise ValueError("section is not present in the persisted outline")
    if any(item.get("id") == section_id for item in completed):
        raise ValueError("section has already been checkpointed")
    if not isinstance(section.get("markdown"), str) or not section["markdown"].strip():
        raise ValueError("cannot checkpoint an empty section")
    if not isinstance(section.get("continuity"), dict):
        raise ValueError("section requires a structured continuity digest")
    return {
        **state,
        "completed_sections": [*completed, dict(section)],
        "next_section_id": next(
            (
                item_id
                for item_id in expected
                if item_id not in {s["id"] for s in [*completed, section]}
            ),
            None,
        ),
        "status": "complete" if len(completed) + 1 == len(expected) else "in_progress",
    }


def choose_continuity(
    outline: dict[str, Any],
    completed_sections: list[dict[str, Any]],
    *,
    max_chars: int,
    evidence_chars: int,
) -> dict[str, Any]:
    """Use full prior prose when it fits; otherwise use its explicit digest.

    ``evidence_chars`` is reserved before continuity is chosen, so prior prose
    can never consume the current section's required evidence allowance.
    """
    if max_chars <= 0 or evidence_chars < 0 or evidence_chars > max_chars:
        raise ValueError("invalid synthesis input budget")
    outline_text = json.dumps(outline, ensure_ascii=False, sort_keys=True)
    available = max_chars - evidence_chars
    full_text = "\n\n".join(
        f"## {section.get('title', section['id'])}\n\n{section['markdown']}"
        for section in completed_sections
    )
    full = f"Stable outline:\n{outline_text}\n\nCompleted sections:\n{full_text}"
    if len(full) <= available:
        return {
            "mode": "full_prior_sections",
            "text": full,
            "chars": len(full),
            "evidence_reserved_chars": evidence_chars,
        }

    structured: dict[str, list[str]] = {
        "claims": [],
        "terminology": [],
        "transitions": [],
        "unresolved": [],
    }
    for section in completed_sections:
        digest = section.get("continuity")
        if not isinstance(digest, dict):
            raise ValueError("completed section lacks structured continuity")
        for key in structured:
            values = digest.get(key)
            if not isinstance(values, list) or any(
                not isinstance(v, str) for v in values
            ):
                raise ValueError(f"invalid continuity field: {key}")
            structured[key].extend(values)
    structured = {
        key: list(dict.fromkeys(values)) for key, values in structured.items()
    }
    digest_text = (
        f"Stable outline:\n{outline_text}\n\n"
        "Continuity digest (structured, derived from completed sections):\n"
        + json.dumps(structured, ensure_ascii=False, sort_keys=True)
    )
    if len(digest_text) > available:
        raise ValueError("outline and continuity digest exceed reserved input budget")
    return {
        "mode": "structured_digest",
        "text": digest_text,
        "chars": len(digest_text),
        "evidence_reserved_chars": evidence_chars,
        "digest": structured,
    }


def assemble_sections(
    outline: dict[str, Any], sections: list[dict[str, Any]], *, source_count: int
) -> str:
    """Assemble only a complete, nonduplicated, in-range cited section set."""
    expected = outline.get("sections")
    if not isinstance(expected, list) or not expected:
        raise ValueError("outline requires sections")
    expected_ids: list[str] = []
    for item in expected:
        section_id = item.get("id")
        if not isinstance(section_id, str) or not section_id:
            raise ValueError("outline section ids are invalid")
        expected_ids.append(section_id)
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError("outline section ids must be unique")
    section_by_id: dict[str, dict[str, Any]] = {}
    for section in sections:
        section_id = section.get("id")
        if not isinstance(section_id, str) or not section_id:
            raise ValueError("section id is invalid")
        if section_id in section_by_id:
            raise ValueError("each section must appear exactly once")
        section_by_id[section_id] = section
    if set(section_by_id) != set(expected_ids):
        raise ValueError("each section must appear exactly once")
    pieces: list[str] = []
    for item in expected:
        section = section_by_id[item["id"]]
        markdown = section.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("section Markdown is empty")
        markers = [
            int(number) for number in re.findall(r"(?<!\w)\[(\d+)\](?!\w)", markdown)
        ]
        if any(number < 1 or number > source_count for number in markers):
            raise ValueError("section contains an invalid citation index")
        title = str(item.get("title") or item["id"])
        pieces.append(f"## {title}\n\n{markdown.strip()}")
    return "\n\n".join(pieces)
