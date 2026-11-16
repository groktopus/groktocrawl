#!/usr/bin/env python3
"""Synthesize a saved source JSONL snapshot through the session API.

The first invocation imports evidence and stores a private resume file. Later
invocations use ``--resume`` and never call search or scrape endpoints.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Iterate physical JSONL lines without treating Unicode separators as LF."""
    with path.open(encoding="utf-8") as stream:
        yield from enumerate(stream, 1)


def load_snapshot_jsonl(
    path: Path, question: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Read acquired source events, binding them to an explicit or captured question."""
    query = question.strip() if isinstance(question, str) and question.strip() else None
    captured_query: str | None = None
    by_url: dict[str, dict[str, str]] = {}
    for line_number, line in _jsonl_lines(path):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if row.get("type") != "source_scraped":
            continue
        if not all(isinstance(row.get(key), str) for key in ("url", "markdown")):
            raise ValueError(f"acquired source at line {line_number} is incomplete")
        if not row["markdown"]:
            raise ValueError(f"acquired source at line {line_number} has no Markdown")
        row_query = row.get("query")
        if row_query is not None:
            if not isinstance(row_query, str) or not row_query.strip():
                raise ValueError(
                    f"acquired source query at line {line_number} is invalid"
                )
            if captured_query is None:
                captured_query = row_query.strip()
            elif captured_query != row_query.strip():
                raise ValueError("source JSONL contains multiple research questions")
            if query is not None and query != row_query.strip():
                raise ValueError("source JSONL contains multiple research questions")
        if row["url"] in by_url:
            if by_url[row["url"]]["markdown"] != row["markdown"]:
                raise ValueError("source JSONL contains conflicting bodies for one URL")
            continue
        title = str(row.get("title", ""))
        if not title:
            title = next(
                (
                    line.lstrip("# ").strip()
                    for line in row["markdown"].splitlines()
                    if line.startswith("#") and line.lstrip("# ").strip()
                ),
                "",
            )[:2_000]
        by_url[row["url"]] = {
            "url": row["url"],
            "title": title,
            "markdown": row["markdown"],
        }
    query = query or captured_query
    if not by_url:
        raise ValueError("source JSONL contains no acquired source bodies")
    if query is None:
        raise ValueError("a research question is required for snapshot synthesis")
    return query, list(by_url.values())


def load_snapshot_index(path: Path, question: str) -> list[dict[str, str]]:
    """Recover acquired bodies from a validated pyramid source index."""
    pattern = re.compile(
        r"^\| acquired \| available \| `([^`]+)` \| (.*?) \| (https?://\S+) \|$"
    )
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = pattern.fullmatch(line)
        if not match:
            continue
        filename, indexed_title, indexed_url = match.groups()
        if Path(filename).name != filename:
            raise ValueError(f"unsafe dossier filename at index line {line_number}")
        if indexed_url in seen:
            raise ValueError("dossier index contains duplicate acquired URLs")
        dossier = path.parent / filename
        text = dossier.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"dossier metadata is missing for {filename}")
        _, frontmatter, body = text.split("---\n", 2)
        metadata: dict[str, str] = {}
        for metadata_line in frontmatter.splitlines():
            if ":" in metadata_line:
                key, value = metadata_line.split(":", 1)
                metadata[key.strip()] = value.strip()
        if (
            metadata.get("url") != indexed_url
            or metadata.get("content_status") != "available"
        ):
            raise ValueError(f"dossier provenance does not match index for {filename}")
        try:
            expected_chars = int(metadata["char_count"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"dossier character count is invalid for {filename}"
            ) from exc
        prefix_chars = len(body) - expected_chars
        if prefix_chars < 0 or prefix_chars > 2 or body[:prefix_chars].strip():
            raise ValueError(
                f"dossier body length does not match metadata for {filename}"
            )
        markdown = body[prefix_chars:]
        title = indexed_title
        if not title or title in {"playwright", "content-negotiation", "browser-svc"}:
            title = next(
                (
                    item.lstrip("# ").strip()
                    for item in markdown.splitlines()
                    if item.startswith("#") and item.lstrip("# ").strip()
                ),
                title,
            )
        sources.append(
            {"url": indexed_url, "title": title[:2_000], "markdown": markdown}
        )
        seen.add(indexed_url)
    if not sources:
        raise ValueError("dossier index contains no acquired source bodies")
    if not question.strip():
        raise ValueError("a research question is required for snapshot synthesis")
    return sources


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def resolve_budgets(
    input_argument: int | None,
    output_argument: int | None,
    state: dict[str, Any],
) -> tuple[int, int]:
    """Reuse persisted budgets on resume unless the caller explicitly overrides."""
    input_budget = (
        input_argument
        if input_argument is not None
        else state.get("input_budget_chars", 32_000)
    )
    if (
        input_argument is not None
        and "input_budget_chars" in state
        and input_argument != state["input_budget_chars"]
    ):
        raise ValueError(
            "input budget is fixed for a synthesis snapshot; use a new synthesis_id or re-import the same evidence"
        )
    output_budget = (
        output_argument
        if output_argument is not None
        else state.get("output_budget_tokens", 1_024)
    )
    if type(input_budget) is not int or not 8_000 <= input_budget <= 64_000:
        raise ValueError("input budget must be between 8000 and 64000 characters")
    if type(output_budget) is not int or output_budget < 256:
        raise ValueError("output token budget must be an integer of at least 256")
    return input_budget, output_budget


def _request(
    base_url: str, path: str, api_key: str, body: dict[str, Any]
) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--sources-jsonl", type=Path)
    parser.add_argument("--sources-index", type=Path)
    parser.add_argument("--question")
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--synthesis-id", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-pending", action="store_true")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=2000,
        help="Maximum outline/section calls in this invocation (1-2000)",
    )
    parser.add_argument("--input-budget-chars", type=int)
    parser.add_argument("--output-budget-tokens", type=int)
    args = parser.parse_args()
    api_key = os.environ.get("GROKTOCRAWL_API_KEY", "")
    if not api_key:
        parser.error("set GROKTOCRAWL_API_KEY in the environment")
    if not 1 <= args.max_steps <= 2000:
        parser.error("--max-steps must be between 1 and 2000")

    if args.resume:
        if not args.state_file.exists():
            parser.error("--resume requires an existing --state-file")
        state = json.loads(args.state_file.read_text(encoding="utf-8"))
        try:
            input_budget, output_budget = resolve_budgets(
                args.input_budget_chars, args.output_budget_tokens, state
            )
        except ValueError as exc:
            parser.error(str(exc))
    else:
        try:
            # Validate before creating a session or importing a large snapshot.
            input_budget, output_budget = resolve_budgets(
                args.input_budget_chars, args.output_budget_tokens, {}
            )
        except ValueError as exc:
            parser.error(str(exc))
        if bool(args.sources_jsonl) == bool(args.sources_index):
            parser.error(
                "initial synthesis requires exactly one of --sources-jsonl or --sources-index"
            )
        if args.sources_jsonl is not None:
            question, sources = load_snapshot_jsonl(
                args.sources_jsonl, question=args.question
            )
        else:
            terminal_path = (
                args.sources_index.parent.parent / "00-terminal-response.json"
            )
            if args.question:
                question = args.question
            elif terminal_path.exists():
                question = str(
                    json.loads(terminal_path.read_text(encoding="utf-8")).get(
                        "query", ""
                    )
                )
            else:
                parser.error(
                    "--sources-index requires --question or a neighboring terminal response"
                )
            sources = load_snapshot_index(args.sources_index, question)
        session = _request(args.base_url, "/v2/session/create", api_key, {"ttl": 86400})
        session_id = session["session_id"]
        imported = _request(
            args.base_url,
            f"/v2/session/{session_id}/evidence/import",
            api_key,
            {"sources": sources},
        )
        state = {
            "version": 1,
            "session_id": session_id,
            "question": question,
            "synthesis_id": args.synthesis_id or str(uuid.uuid4()),
            "ref_ids": [source["id"] for source in imported["sources"]],
            "manifest_sources": len(imported["sources"]),
            "manifest_chars": imported["source_chars"],
            "last_status": "imported",
        }

    state["input_budget_chars"] = input_budget
    state["output_budget_tokens"] = output_budget
    _atomic_private_json(args.state_file, state)

    result: dict[str, Any] = {}
    for attempt in range(args.max_steps):
        params: dict[str, Any] = {
            "question": state["question"],
            "synthesis_id": state["synthesis_id"],
            "input_budget_chars": input_budget,
            "output_budget_tokens": output_budget,
        }
        if args.resume or state.get("last_status") != "imported":
            params["resume"] = True
        if state.get("last_status") == "imported":
            params["ref_ids"] = state["ref_ids"]
        if args.retry_pending and attempt == 0:
            params["retry_pending"] = True
        result = _request(
            args.base_url,
            f"/v2/session/{state['session_id']}/step",
            api_key,
            {"action": "synthesize", "params": params},
        )["result"]
        state["last_status"] = result.get("status", "unknown")
        state["last_progress"] = {
            key: result[key]
            for key in (
                "manifest_sha256",
                "completed_chunk_count",
                "total_chunk_count",
                "section_id",
            )
            if key in result
        }
        _atomic_private_json(args.state_file, state)
        if result.get("status") == "complete":
            break
    if result.get("status") == "complete":
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            _atomic_private_json(
                args.output,
                {
                    "answer": result["answer"],
                    "citations": result.get("citations", []),
                    "synthesis_id": state["synthesis_id"],
                    "manifest_sha256": result.get("manifest_sha256"),
                },
            )
        print(
            json.dumps(
                {
                    "status": "complete",
                    "synthesis_id": state["synthesis_id"],
                    "answer_chars": len(result["answer"]),
                    "citation_count": len(result.get("citations", [])),
                }
            )
        )
    else:
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "synthesis_id": state["synthesis_id"],
                    "completed_chunks": result.get("completed_chunk_count", 0),
                    "total_chunks": result.get("total_chunk_count"),
                    "resume_command": f"--resume --state-file {args.state_file}",
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
