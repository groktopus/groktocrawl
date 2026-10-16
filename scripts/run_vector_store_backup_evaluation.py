#!/usr/bin/env python3
"""Rehearse provider-native backup and restore for isolated vector stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts.run_vector_store_evaluation import (
    CORPUS,
    QUERIES,
    PostgresStore,
    QdrantStore,
    _check_gates,
    _manifest,
)

SCHEMA_VERSION = "vector-store-backup-evaluation/1"


def _safe_id(run_id: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in run_id)


def _request(
    url: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def _json_request(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    _, response = _request(
        url, method, body, {"Content-Type": "application/json"} if body else None
    )
    return json.loads(response)


def _qdrant_snapshot(qdrant_url: str, collection: str, output: Path) -> dict:
    started = time.perf_counter()
    created = _json_request(
        f"{qdrant_url.rstrip('/')}/collections/{urllib.parse.quote(collection, safe='')}/snapshots",
        "POST",
    )
    snapshot_name = created["result"]["name"]
    encoded_collection = urllib.parse.quote(collection, safe="")
    encoded_snapshot = urllib.parse.quote(snapshot_name, safe="")
    _, snapshot = _request(
        f"{qdrant_url.rstrip('/')}/collections/{encoded_collection}/snapshots/{encoded_snapshot}"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(snapshot)
    return {
        "provider": "qdrant",
        "snapshot_name": snapshot_name,
        "path": str(output),
        "bytes": len(snapshot),
        "sha256": hashlib.sha256(snapshot).hexdigest(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _upload_qdrant_snapshot(qdrant_url: str, collection: str, snapshot: Path) -> dict:
    boundary = "----groktocrawl-x-vector-backup"
    content = snapshot.read_bytes()
    part = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="snapshot"; filename="backup.snapshot"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    started = time.perf_counter()
    encoded = urllib.parse.quote(collection, safe="")
    status, _ = _request(
        f"{qdrant_url.rstrip('/')}/collections/{encoded}/snapshots/upload?priority=snapshot",
        "POST",
        part,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return {
        "provider": "qdrant",
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _evidence(store, deletion_ids: list[str]) -> dict:
    before = [{"query_id": q.query_id, "results": store.search(q)} for q in QUERIES]
    for record_id in deletion_ids:
        store.delete(record_id)
    after = [{"query_id": q.query_id, "results": store.search(q)} for q in QUERIES]
    evidence = {
        "name": store.name,
        "deletion_ids": deletion_ids,
        "searches": before,
        "post_delete": after[0],
        "before": before,
        "after": after,
        "errors": [],
        "ok": True,
    }
    evidence["gates"] = _check_gates(evidence, list(CORPUS))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("seed", "backup", "restore-verify"), required=True
    )
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--target-qdrant-url")
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--target-postgres-dsn")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--deletion-manifest", type=Path)
    parser.add_argument("--allow-isolated-database", action="store_true")
    args = parser.parse_args()
    if not args.allow_isolated_database:
        raise SystemExit("refusing provider access without --allow-isolated-database")
    safe = _safe_id(args.run_id)
    collection = f"groktocrawl_x_backup_{safe}"
    table = f"backup_{safe}"
    result = {
        "schema_version": SCHEMA_VERSION,
        "phase": args.phase,
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest": _manifest(),
        "production_change": False,
        "qdrant_removal": False,
    }
    if args.phase == "seed":
        stores = [
            QdrantStore(args.qdrant_url, collection),
            PostgresStore(args.postgres_dsn, table),
        ]
        try:
            for store in stores:
                store.upsert(list(CORPUS))
            result["seeded"] = [store.name for store in stores]
        finally:
            for store in stores:
                store.close(False)
    elif args.phase == "backup":
        snapshot = _qdrant_snapshot(
            args.qdrant_url, collection, args.backup_dir / f"{collection}.snapshot"
        )
        result["qdrant_backup"] = snapshot
        result["postgres_backup"] = {
            "managed_by": "pg_dump",
            "path": str(args.backup_dir / "postgres.dump"),
        }
    else:
        if (
            not args.target_qdrant_url
            or not args.target_postgres_dsn
            or not args.deletion_manifest
        ):
            raise SystemExit(
                "restore-verify requires target URLs/DSN and --deletion-manifest"
            )
        deletion_ids = json.loads(args.deletion_manifest.read_text())["delete"]
        upload = _upload_qdrant_snapshot(
            args.target_qdrant_url,
            collection,
            args.backup_dir / f"{collection}.snapshot",
        )
        stores = [
            QdrantStore(args.target_qdrant_url, collection, create=False),
            PostgresStore(args.target_postgres_dsn, table, create=False),
        ]
        try:
            result["qdrant_restore"] = upload
            result["candidates"] = [_evidence(store, deletion_ids) for store in stores]
            result["deletion_manifest"] = {
                "delete": deletion_ids,
                "sha256": hashlib.sha256(
                    args.deletion_manifest.read_bytes()
                ).hexdigest(),
            }
            result["gates"] = all(
                all(candidate["gates"].values()) for candidate in result["candidates"]
            )
        finally:
            for store in stores:
                store.close(False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "phase": args.phase}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
