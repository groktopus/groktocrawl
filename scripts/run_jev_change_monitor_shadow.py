#!/usr/bin/env python3
"""Validate and optionally execute the frozen #373 Jev shadow packet.

Provider traffic is opt-in, sequential, one-shot, and requires an external proxy.
No credentials are accepted or stored by this runner. Labels and source URLs are
never sent to the provider. Full receipts and the append-only journal must live
outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs/experiments/evidence/jev-change-373"
MODEL = "jev-1.13.0"
FREEZE_SHA256 = "67781243e22b7b753fb059a61a452216a80cc0aeac0ff168a8177218af1156d5"
PUBLICATION_SHA256 = "d26c6371e04f60759a2f939d5cd4e8b78e5b80e504b595af748653b27295ece3"
EXPECTED_IDS = (
    "k8s-status-v121-to-v122",
    "k8s-address-types-and-conditions",
    "k8s-nonpod-endpoint-qualification",
    "k8s-private-registry-formatting",
    "python-sysmonitoring-lifecycle",
    "python-cmdline-leading-whitespace",
    "cosign-airgap-trust-root",
    "cosign-community-invite-intent",
    "cosign-community-invite-crypto-intent",
    "k8s-nav-weight-content-intent",
    "k8s-nav-weight-navigation-intent",
)


def canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_packet() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((PACKET / "freeze-manifest.json").read_text())
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("freeze manifest file ledger is missing")
    ledger: list[dict[str, Any]] = []
    for row in files:
        rel = Path(row["path"])
        if rel.is_absolute() or ".." in rel.parts or rel.name == "freeze-manifest.json":
            raise ValueError("invalid path in freeze ledger")
        ledger.append(row)
    computed = canonical_digest(ledger)
    if computed != FREEZE_SHA256 or manifest.get("freeze_sha256") != FREEZE_SHA256:
        raise ValueError("freeze digest does not match the reviewed protocol")
    amended_paths, redactions = _validate_publication_amendment(ledger)
    changed_paths: set[str] = set()
    for row in ledger:
        rel = row["path"]
        content = (PACKET / rel).read_bytes()
        current = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        expected = {"bytes": row["bytes"], "sha256": row["sha256"]}
        if current == expected:
            if rel in amended_paths:
                raise ValueError("publication amendment lists an unchanged file")
            continue
        amendment = amended_paths.get(rel)
        if amendment is None or current != {
            "bytes": amendment["published_bytes"],
            "sha256": amendment["published_sha256"],
        }:
            raise ValueError(f"frozen file integrity mismatch: {rel}")
        if expected != {
            "bytes": amendment["original_bytes"],
            "sha256": amendment["original_sha256"],
        }:
            raise ValueError(
                f"publication amendment does not match original freeze: {rel}"
            )
        changed_paths.add(rel)
    if changed_paths != set(amended_paths):
        raise ValueError("publication amendment does not enumerate exact changed files")
    packet = json.loads((PACKET / "request-packets.json").read_text())
    if packet.get("model") != MODEL or len(packet.get("requests", [])) != 11:
        raise ValueError("request packet model/count mismatch")
    requests = packet["requests"]
    ids = [r.get("case_id") for r in requests]
    if tuple(ids) != EXPECTED_IDS or len(set(ids)) != len(EXPECTED_IDS):
        raise ValueError("request IDs/order differ from frozen allocation")
    if [r.get("split") for r in requests].count("calibration") != 4 or [
        r.get("split") for r in requests
    ].count("validation") != 7:
        raise ValueError("calibration/validation allocation mismatch")
    for row in requests:
        req = row.get("request")
        if not isinstance(req, dict) or req.get("model") != MODEL:
            raise ValueError("request model mismatch")
        if canonical_digest(req) != row.get("input_sha256"):
            raise ValueError("request input digest mismatch")
        if set(req) != {"model", "questions", "state"} or set(req["questions"]) != {
            "q0"
        }:
            raise ValueError("request contains unexpected fields")
        q = req["questions"]["q0"]
        if q.get("type") != "noul" or not isinstance(q.get("instructions"), str):
            raise ValueError("request is not the frozen typed proposition")
        state = req.get("state")
        if not isinstance(state, dict) or set(state) != {
            "intent",
            "before_excerpts",
            "after_excerpts",
        }:
            raise ValueError("model state contains unexpected fields")
        for side in ("before_excerpts", "after_excerpts"):
            blocks = state[side]
            if not isinstance(blocks, list) or not blocks:
                raise ValueError("missing bounded excerpt")
            if any(
                set(block) != {"line_start", "line_end", "text"}
                or not isinstance(block["text"], str)
                for block in blocks
            ):
                raise ValueError("malformed excerpt block")
    _validate_excerpt_source_spans(requests, redactions)
    return manifest, requests


def _validate_publication_amendment(
    ledger: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    amendment_path = PACKET / "publication-amendment.json"
    if not amendment_path.exists():
        return {}, []
    amendment = json.loads(amendment_path.read_text())
    files = amendment.get("files")
    if amendment.get("freeze_sha256") != FREEZE_SHA256 or not isinstance(files, list):
        raise ValueError("publication amendment is not tied to the reviewed freeze")
    body = {
        "freeze_sha256": amendment["freeze_sha256"],
        "files": files,
        "redactions_sha256": amendment.get("redactions_sha256"),
    }
    if (
        canonical_digest(body) != amendment.get("publication_sha256")
        or amendment.get("publication_sha256") != PUBLICATION_SHA256
    ):
        raise ValueError("publication amendment digest mismatch")
    allowed = {
        "README.md",
        "source-snapshots.json",
        "pages/cosign-link/before.md",
        "pages/cosign-link/after.md",
        "pages/cosign-offline/before.md",
        "pages/cosign-offline/after.md",
    }
    changed = {row.get("path"): row for row in files if isinstance(row, dict)}
    if set(changed) != allowed or len(changed) != len(files):
        raise ValueError("publication amendment changes an unexpected path set")
    ledger_by_path = {row["path"]: row for row in ledger}
    if not set(changed) <= set(ledger_by_path):
        raise ValueError("publication amendment references a path outside the freeze")
    red_path = PACKET / "publication-redactions.json"
    red_bytes = red_path.read_bytes()
    if hashlib.sha256(red_bytes).hexdigest() != amendment.get("redactions_sha256"):
        raise ValueError("publication redaction manifest hash mismatch")
    red_doc = json.loads(red_bytes)
    if (
        red_doc.get("format") != "jev-publication-redactions/1"
        or red_doc.get("freeze_sha256") != FREEZE_SHA256
    ):
        raise ValueError("invalid publication redaction manifest")
    redactions = red_doc.get("redactions")
    if not isinstance(redactions, list) or len(redactions) != 4:
        raise ValueError("expected four explicit source redactions")
    sources = json.loads((PACKET / "source-snapshots.json").read_text())["sources"]
    source_index = {(row["case"], row["side"]): row for row in sources}
    expected_redaction_paths = set()
    for entry in redactions:
        rel = entry.get("path")
        expected_redaction_paths.add(rel)
        change = changed.get(rel)
        source = source_index.get((entry.get("source_pair_id"), entry.get("side")))
        if (
            not change
            or not source
            or entry.get("original_sha256") != source.get("sha256")
        ):
            raise ValueError("redaction source hash/identity mismatch")
        if entry.get("published_sha256") != source.get(
            "published_snapshot_sha256"
        ) or change.get("published_sha256") != source.get("published_snapshot_sha256"):
            raise ValueError("redacted snapshot publication hash mismatch")
        if change.get("original_sha256") != source.get("sha256") or source.get(
            "published_snapshot_bytes"
        ) != change.get("published_bytes"):
            raise ValueError("redaction amendment byte/hash mapping mismatch")
        if (
            entry.get("line_count_preserved") is not True
            or entry.get("line_mapping")
            != "identity: original and published line numbers are identical"
        ):
            raise ValueError("redaction line mapping is not preserved")
        page_lines = (PACKET / rel).read_text().splitlines(keepends=True)
        start, end = entry.get("original_line_start"), entry.get("original_line_end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or end < start
            or end - start + 1 != 3
            or end > len(page_lines)
        ):
            raise ValueError("invalid redaction source line span")
        if page_lines[start - 1].strip() != entry.get("marker") or any(
            line.strip() for line in page_lines[start:end]
        ):
            raise ValueError("redaction marker/padding does not match mapped span")
    if expected_redaction_paths != {
        path for path in changed if path.startswith("pages/")
    }:
        raise ValueError("source redaction paths differ from amended page paths")
    return changed, redactions


def _validate_excerpt_source_spans(
    requests: list[dict[str, Any]], redactions: list[dict[str, Any]]
) -> None:
    excerpts = json.loads((PACKET / "evaluation-excerpts.json").read_text())
    sources = json.loads((PACKET / "source-snapshots.json").read_text())["sources"]
    source_index = {(row["case"], row["side"]): row for row in sources}
    redaction_index = {row["path"]: row for row in redactions}
    for row in requests:
        pair_id = row["source_pair_id"]
        pair = excerpts.get("pairs", {}).get(pair_id)
        if not isinstance(pair, dict):
            raise ValueError("request references missing source excerpt pair")
        for request_side, snapshot_side in (
            ("before_excerpts", "before"),
            ("after_excerpts", "after"),
        ):
            expected = pair.get(snapshot_side)
            source = source_index.get((pair_id, snapshot_side))
            if not isinstance(expected, dict) or not isinstance(source, dict):
                raise ValueError("excerpt lacks exact source snapshot identity")
            page_path = PACKET / "pages" / pair_id / f"{snapshot_side}.md"
            page_bytes = page_path.read_bytes()
            published_hash = hashlib.sha256(page_bytes).hexdigest()
            if source.get("sha256") != expected.get("page_sha256"):
                raise ValueError(
                    "excerpt hash differs from attributed original source snapshot"
                )
            if source.get("published_snapshot_sha256") != published_hash:
                raise ValueError("published source page hash mismatch")
            page_lines = page_bytes.decode("utf-8").splitlines(keepends=True)
            blocks = expected.get("blocks")
            if not isinstance(blocks, list) or not blocks:
                raise ValueError("source excerpt block list is empty")
            for block in blocks:
                start, end = block.get("line_start"), block.get("line_end")
                if (
                    not isinstance(start, int)
                    or not isinstance(end, int)
                    or start < 1
                    or end < start
                    or end > len(page_lines)
                ):
                    raise ValueError("excerpt line span is outside source page")
                redaction = redaction_index.get(f"pages/{pair_id}/{snapshot_side}.md")
                if (
                    redaction
                    and start <= redaction["original_line_end"]
                    and end >= redaction["original_line_start"]
                ):
                    raise ValueError("judged excerpt overlaps a publication redaction")
                if "".join(page_lines[start - 1 : end]) != block.get("text"):
                    raise ValueError("excerpt text does not match exact source lines")
            if canonical_digest(blocks) != expected.get("excerpt_sha256"):
                raise ValueError("excerpt block digest mismatch")
            byte_count = sum(len(block["text"].encode("utf-8")) for block in blocks)
            if byte_count != expected.get("excerpt_utf8_bytes"):
                raise ValueError("excerpt byte count mismatch")
            projected = [
                {key: block[key] for key in ("line_start", "line_end", "text")}
                for block in blocks
            ]
            if row["request"]["state"][request_side] != projected:
                raise ValueError("request excerpt differs from source-verified excerpt")


def _outside_repo(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return resolved
    raise ValueError(f"{description} must be outside the repository")


def _write_private(path: Path, content: bytes, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _reserve_journal(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_journal(path: Path, row: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fchmod(fd, 0o600)
        data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("proxy response is not an object")
    model = data.get("model")
    answers = data.get("answers")
    if model != MODEL or not isinstance(answers, dict) or set(answers) != {"q0"}:
        raise ValueError("response model or question mapping mismatch")
    answer = answers["q0"]
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise ValueError("response is not a Noul probability")
    value = answer.get("noul")
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("Noul probability must be finite and in [0,1]")
    return {"model": model, "q0_noul": float(value)}


def run(args: argparse.Namespace) -> int:
    _, requests = validate_packet()
    if not args.execute:
        print(
            json.dumps(
                {
                    "valid": True,
                    "freeze_sha256": FREEZE_SHA256,
                    "requests": len(requests),
                    "provider_calls": 0,
                }
            )
        )
        return 0
    if (PACKET / "outcome.json").exists() or (
        PACKET / "casewise-outcome-ledger.json"
    ).exists():
        raise ValueError(
            "study already has recorded outcomes; refusing repeat provider calls"
        )
    if args.expected_freeze_sha256 != FREEZE_SHA256:
        raise ValueError("explicit expected freeze SHA-256 is required and must match")
    if not args.proxy_command:
        raise ValueError("--proxy-command is required for execution")
    if len(args.proxy_command) == 1 and " " in args.proxy_command[0]:
        raise ValueError("pass proxy argv as separate --proxy-command arguments")
    journal = _outside_repo(Path(args.journal), "journal")
    receipts = _outside_repo(Path(args.receipts_dir), "receipt directory")
    # Fail before traffic if the append-only run paths already exist.
    if journal.exists() or receipts.exists():
        raise FileExistsError(
            "journal or private receipt directory already exists; refusing rerun"
        )
    journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipts.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(receipts, 0o700)
    _reserve_journal(journal)
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(
            term in k.lower()
            for term in ("key", "token", "secret", "password", "credential")
        )
    }
    for index, row in enumerate(requests, 1):
        attempted = {
            "event": "attempted",
            "index": index,
            "case_id": row["case_id"],
            "split": row["split"],
            "freeze_sha256": FREEZE_SHA256,
            "input_sha256": row["input_sha256"],
            "model": MODEL,
        }
        _append_journal(journal, attempted)
        started = time.monotonic()
        status = "error"
        public_result: dict[str, Any] = {}
        raw = b""
        try:
            proc = subprocess.run(
                args.proxy_command,
                input=(json.dumps(row["request"], ensure_ascii=False) + "\n").encode(),
                capture_output=True,
                timeout=args.timeout,
                env=env,
                check=False,
            )
            raw = proc.stdout
            if proc.returncode != 0:
                raise RuntimeError(f"proxy exited {proc.returncode}")
            response = json.loads(proc.stdout)
            validated = _validate_response(response)
            status = "ok"
            public_result = validated
        except Exception as exc:  # preserve failures without printing proxy output
            public_result = {"error_type": type(exc).__name__}
        elapsed = round((time.monotonic() - started) * 1000)
        receipt = receipts / f"{index:02d}-{row['case_id']}.json"
        _write_private(receipt, raw, exclusive=True)
        outcome = {
            "event": "outcome",
            "index": index,
            "case_id": row["case_id"],
            "split": row["split"],
            "status": status,
            "elapsed_ms": elapsed,
            "receipt_file": receipt.name,
            **public_result,
        }
        _append_journal(journal, outcome)
        print(
            json.dumps(
                {
                    "index": index,
                    "case_id": row["case_id"],
                    "status": status,
                    "elapsed_ms": elapsed,
                }
            )
        )
    print(
        json.dumps(
            {
                "complete": True,
                "freeze_sha256": FREEZE_SHA256,
                "journal": str(journal),
                "receipt_count": len(requests),
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--validate-only",
        action="store_true",
        help="validate frozen packet offline (default)",
    )
    modes.add_argument(
        "--execute", action="store_true", help="execute the approved bounded calls"
    )
    parser.add_argument("--expected-freeze-sha256")
    parser.add_argument("--proxy-command", nargs="+")
    parser.add_argument("--journal", default="/tmp/jev-change-373-journal.jsonl")
    parser.add_argument("--receipts-dir", default="/tmp/jev-change-373-receipts")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"runner preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
