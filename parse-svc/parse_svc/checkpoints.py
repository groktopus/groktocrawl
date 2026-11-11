"""Single-host page receipts and conservative model admission (ADR-0079)."""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class CheckpointError(RuntimeError):
    """The durable extraction boundary could not be established."""


class DeadlineExceededError(CheckpointError):
    """The admitted execution window expired."""


@contextmanager
def file_lock(path: Path, deadline: float) -> Iterator[None]:
    """Keep the kernel lock until the synchronous operation really finishes."""
    with path.open("a+b") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DeadlineExceededError("admission_timeout")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Checkpoints:
    """SQLite transactions never span rendering or model network calls."""

    def __init__(self, root: Path, max_bytes: int, ttl: int):
        self.root = root
        self.max_bytes = max_bytes
        self.ttl = ttl
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = root / "pages.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 2):
                raise CheckpointError("unsupported_checkpoint_schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_hash TEXT NOT NULL,
                    policy TEXT NOT NULL, source BLOB NOT NULL, created REAL NOT NULL,
                    accessed REAL NOT NULL, page_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pages (
                    execution TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(execution, number));
                CREATE TABLE IF NOT EXISTS attempts (
                    execution TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                    receipt TEXT NOT NULL, PRIMARY KEY(execution, number, ordinal));
                PRAGMA user_version=2;
            """)
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={max(32, self.max_bytes // page_size)}")
        try:
            with db:
                yield db
        finally:
            db.close()

    def execution_id(self, scope: str, source_hash: str, policy: str) -> str:
        return hashlib.sha256(f"{scope}\0{source_hash}\0{policy}".encode()).hexdigest()

    def execution_lock(self, execution: str, deadline: float):
        return file_lock(self.root / f"{execution}.lock", deadline)

    def lane_lock(self, deadline: float):
        return file_lock(self.root / "ocr.lock", deadline)

    def admit(self, execution: str, scope: str, source: bytes, policy: str, pages: int):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM executions WHERE id=?", (execution,)
            ).fetchone():
                db.execute(
                    "UPDATE executions SET accessed=? WHERE id=?", (now, execution)
                )
                return
            used = db.execute(
                "SELECT coalesce(sum(length(source)),0) FROM executions"
            ).fetchone()[0]
            used += db.execute(
                "SELECT coalesce(sum(length(result)),0) FROM pages"
            ).fetchone()[0]
            if used + len(source) > self.max_bytes:
                raise CheckpointError("checkpoint_capacity_exceeded")
            if shutil.disk_usage(self.root).free < len(source) + 16 * 1024 * 1024:
                raise CheckpointError("checkpoint_disk_full")
            db.execute(
                "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)",
                (
                    execution,
                    scope,
                    hashlib.sha256(source).hexdigest(),
                    policy,
                    source,
                    now,
                    now,
                    pages,
                ),
            )

    def page(self, execution: str, number: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT result FROM pages WHERE execution=? AND number=?",
                (execution, number),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_page(self, execution: str, number: int, result: dict[str, Any]):
        with self.connect() as db:
            self._save(db, execution, number, result)

    def _save(
        self,
        db: sqlite3.Connection,
        execution: str,
        number: int,
        result: dict[str, Any],
    ):
        encoded = json.dumps(result, ensure_ascii=False)
        used = db.execute(
            "SELECT coalesce(sum(length(source)),0) FROM executions"
        ).fetchone()[0]
        used += db.execute(
            "SELECT coalesce(sum(length(result)),0) FROM pages WHERE NOT (execution=? AND number=?)",
            (execution, number),
        ).fetchone()[0]
        if used + len(encoded.encode()) > self.max_bytes:
            raise CheckpointError("checkpoint_capacity_exceeded")
        db.execute(
            "INSERT OR REPLACE INTO pages VALUES (?,?,?)", (execution, number, encoded)
        )

    def attempts(self, execution: str, number: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT receipt FROM attempts WHERE execution=? AND number=? ORDER BY ordinal",
                (execution, number),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def begin_attempt(self, execution: str, number: int, limit: int):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ordinal = (
                db.execute(
                    "SELECT count(*) FROM attempts WHERE execution=? AND number=?",
                    (execution, number),
                ).fetchone()[0]
                + 1
            )
            if ordinal > limit:
                raise CheckpointError("page_attempts_exhausted")
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?)",
                (
                    execution,
                    number,
                    ordinal,
                    json.dumps({"outcome": "started", "started_at": time.time()}),
                ),
            )
            return ordinal

    def finish_attempt(self, execution: str, number: int, ordinal: int, outcome: str):
        with self.connect() as db:
            db.execute(
                "UPDATE attempts SET receipt=? WHERE execution=? AND number=? AND ordinal=?",
                (
                    json.dumps({"outcome": outcome, "completed_at": time.time()}),
                    execution,
                    number,
                    ordinal,
                ),
            )

    def cleanup(self):
        """Only expired unlocked executions."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM executions WHERE accessed<?", (time.time() - self.ttl,)
            ).fetchall()
        for (execution,) in rows:
            try:
                with self.execution_lock(execution, time.monotonic()):
                    with self.connect() as db:
                        db.execute(
                            "DELETE FROM executions WHERE id=? AND accessed<?",
                            (execution, time.time() - self.ttl),
                        )
            except DeadlineExceededError:
                continue
