"""Bounded append-only snapshots for protected sentinel qualification evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_LEDGER_BYTES = 65_536
LEDGERS = {
    "negative": "events.jsonl",
    "private_positive": "positive-control.jsonl",
    "host_positive": "host-bridge-positive-control.jsonl",
}


def _read(root: Path, name: str) -> bytes:
    path = root / name
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("sentinel evidence is unavailable")
    with path.open("rb") as stream:
        data = stream.read(MAX_LEDGER_BYTES + 1)
    if len(data) > MAX_LEDGER_BYTES:
        raise RuntimeError("sentinel evidence exceeds the inspection limit")
    return data


def snapshot(root: Path = Path("/state")) -> dict[str, dict[str, int | str]]:
    """Capture bounded line counts and digests before positive controls run."""
    result: dict[str, dict[str, int | str]] = {}
    for key, filename in LEDGERS.items():
        data = _read(root, filename)
        if data and not data.endswith(b"\n"):
            raise RuntimeError("sentinel evidence has an incomplete line")
        result[key] = {
            "count": len(data.splitlines()),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return result


def verify(
    baseline: dict[str, dict[str, Any]],
    private_nonce: str,
    host_nonce: str,
    root: Path = Path("/state"),
) -> None:
    """Require unchanged prefixes and exactly one nonce-matched positive append."""
    if not re.fullmatch(r"[0-9a-f]{32}", private_nonce) or not re.fullmatch(
        r"[0-9a-f]{32}", host_nonce
    ):
        raise RuntimeError("positive-control nonce is invalid")
    for key, filename in LEDGERS.items():
        data = _read(root, filename)
        old = baseline.get(key)
        if (
            not isinstance(old, dict)
            or not isinstance(old.get("count"), int)
            or isinstance(old.get("count"), bool)
            or old["count"] < 0
            or not isinstance(old.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", old["sha256"])
        ):
            raise RuntimeError("sentinel evidence baseline is invalid")
        old_count = old["count"]
        if data and not data.endswith(b"\n"):
            raise RuntimeError("sentinel evidence has an incomplete line")
        lines = data.splitlines(keepends=True)
        if old_count > len(lines):
            raise RuntimeError("sentinel evidence prefix was truncated")
        prefix = b"".join(lines[:old_count])
        if hashlib.sha256(prefix).hexdigest() != old.get("sha256"):
            raise RuntimeError("sentinel evidence prefix changed")
        if key == "negative" and lines:
            raise RuntimeError("sentinel recorded forbidden traffic")
        expected_delta = 0 if key == "negative" else 1
        if len(lines) != old_count + expected_delta:
            raise RuntimeError("sentinel evidence append count changed")
        if key == "negative":
            continue
        expected_nonce = private_nonce if key == "private_positive" else host_nonce
        event = json.loads(lines[-1])
        if (
            not isinstance(event, dict)
            or event.get("kind") != "positive-control"
            or event.get("nonce") != expected_nonce
        ):
            raise RuntimeError("positive-control evidence nonce did not match")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "snapshot":
        print(json.dumps(snapshot(), separators=(",", ":")))
        return
    if len(sys.argv) == 5 and sys.argv[1] == "verify":
        verify(json.loads(sys.argv[2]), sys.argv[3], sys.argv[4])
        print("sentinel_ledger=pass")
        return
    raise SystemExit("usage: sentinel_ledger.py snapshot | verify BASELINE PRIVATE_NONCE HOST_NONCE")


if __name__ == "__main__":
    main()
