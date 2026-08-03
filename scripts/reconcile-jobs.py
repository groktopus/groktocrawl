#!/usr/bin/env python3
"""reconcile-jobs.py — identify and reconcile jobs stranded in ``processing``.

GroktoCrawl executes async jobs in-process through ``TaskTracker``
(ADR-0035). A crash, forced termination, or restart does not resume or
reclaim interrupted work, so job records can remain ``processing`` until
their 24-hour Valkey TTL expires (ADR-0047).

This operator tool lists those stranded jobs and can mark them ``failed``.

Non-interactive and idempotent:

- Default is a dry-run listing; nothing is modified.
- ``--fail`` transitions stale ``processing`` jobs to ``failed``. Only
  ``processing`` records are ever modified — ``completed``, ``cancelled``,
  and ``failed`` records are preserved (same guard as ``JobStore.fail_job``).
- Re-running ``--fail`` is a no-op once jobs are reconciled.

Exit codes (usable as an alerting check):

- 0 — no stale processing jobs remain (or all reconciled)
- 1 — stale processing jobs found (dry-run) or still remaining
- 2 — error (connection, bad arguments)

Run this while ``agent-svc`` is stopped (for example, immediately after a
restart or deploy) so a genuinely live long-running job is not mistaken for
a stranded one. Choose ``--stale-after`` above your longest legitimate job
runtime: the default 3600s exceeds the crawl cap
(``CRAWL_MAX_DURATION_SECONDS`` default 1800s) and typical agent jobs, but
agent research jobs have no hard duration cap.

Examples:

    # Dry-run inside the agent-svc container (inherits VALKEY_URL):
    docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py

    # Reconcile jobs stuck for more than an hour, machine-readable:
    docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py \\
        --stale-after 3600 --fail --json

    # From a checkout, against a custom endpoint:
    python3 scripts/reconcile-jobs.py --redis-url redis://localhost:6379/0 \\
        --kind crawl --stale-after 1800
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import redis
from redis import Redis

META_PATTERN = "job:*:meta"
DEFAULT_STALE_AFTER = 3600  # seconds; above CRAWL_MAX_DURATION_SECONDS default (1800)
RECONCILE_ERROR = "interrupted: no liveness signal (reconciled by operator)"


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp written by ``JobStore`` (UTC)."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def create_client(redis_url: str) -> Redis:
    """Build the Valkey client used by ``run``. Injectable for tests."""
    return Redis.from_url(redis_url, decode_responses=True)


def _job_label(meta: dict[str, Any]) -> str:
    """Human-readable label for a job: URL or prompt from its payload."""
    payload = meta.get("payload")
    if isinstance(payload, dict):
        url = payload.get("url")
        if isinstance(url, str) and url:
            return url
        prompt = payload.get("prompt")
        if isinstance(prompt, str) and prompt:
            return prompt[:80]
    return "-"


def find_stranded_jobs(
    client: Redis,
    stale_after: int,
    *,
    now: datetime | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (stranded, skipped) processing jobs older than *stale_after*.

    A job is stranded when it is still ``processing`` and its ``created_at``
    is more than *stale_after* seconds before *now* (exclusive boundary).
    Jobs whose ``created_at`` cannot be parsed, and corrupt job records, are
    reported in *skipped* so operators can inspect them manually.
    """
    now = now or datetime.now(UTC)
    stranded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cursor = 0
    while len(stranded) < limit:
        cursor, keys = client.scan(cursor=cursor, match=META_PATTERN, count=100)
        for key in keys:
            raw = client.get(key)
            if raw is None:
                continue
            try:
                meta = json.loads(raw)
            except json.JSONDecodeError:
                # Corrupt record (e.g., a partial write); surface it instead
                # of aborting the whole sweep.
                job_id = key[len("job:") : -len(":meta")]
                skipped.append({"id": job_id, "corrupt": True})
                continue
            if meta.get("status") != "processing":
                continue
            if kind is not None and meta.get("kind") != kind:
                continue
            created = _parse_ts(meta.get("created_at", ""))
            if created is None:
                skipped.append(meta)
                continue
            age = now - created
            if age > timedelta(seconds=stale_after):
                stranded.append(meta)
                if len(stranded) >= limit:
                    break
        if cursor == 0:
            break
    return stranded, skipped


def fail_jobs(client: Redis, jobs: list[dict[str, Any]]) -> int:
    """Transition stale ``processing`` jobs to ``failed``.

    Re-reads each record immediately before writing and only transitions
    when the status is still ``processing`` (best-effort read-check-write,
    matching ``JobStore.fail_job`` semantics — not atomic). Returns the
    number of records actually transitioned.
    """
    failed = 0
    now = datetime.now(UTC).isoformat()
    for job in jobs:
        raw = client.get(f"job:{job['id']}:meta")
        if raw is None:
            continue
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt record between sweep and fail pass; leave it for
            # manual inspection rather than crashing the run.
            continue
        if meta.get("status") != "processing":
            continue
        meta["status"] = "failed"
        meta["error"] = RECONCILE_ERROR
        meta["completed_at"] = now
        # keepttl preserves the original 24h job-record TTL so reconciled
        # records still expire on schedule.
        client.set(f"job:{job['id']}:meta", json.dumps(meta), keepttl=True)
        failed += 1
    return failed


def run(client: Redis, args: argparse.Namespace, now: datetime | None = None) -> int:
    """Core logic; hermetically testable with a fake client."""
    now = now or datetime.now(UTC)
    stranded, skipped = find_stranded_jobs(
        client,
        args.stale_after,
        now=now,
        kind=args.kind,
        limit=args.limit,
    )
    remaining = stranded

    if args.fail and stranded:
        failed = fail_jobs(client, stranded)
        # Re-list to report what remains after reconciliation.
        remaining, _ = find_stranded_jobs(
            client,
            args.stale_after,
            now=now,
            kind=args.kind,
            limit=args.limit,
        )
    else:
        failed = 0

    if args.json:
        payload = {
            "stale_after_seconds": args.stale_after,
            "dry_run": not args.fail,
            "failed": failed,
            "stranded": [
                {
                    "id": j["id"],
                    "kind": j.get("kind", "unknown"),
                    "created_at": j.get("created_at", ""),
                    "url": _job_label(j),
                }
                for j in stranded
            ],
            "remaining": [j["id"] for j in remaining],
            "skipped": [j["id"] for j in skipped],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if stranded:
            print(f"Stranded processing jobs ({len(stranded)}):\n")
            for job in stranded:
                created = _parse_ts(job.get("created_at", ""))
                age_s = int((now - created).total_seconds()) if created else None
                age = f"{age_s}s" if age_s is not None else "unknown"
                print(
                    f"  {job['id']}  kind={job.get('kind', 'unknown')}  "
                    f"created={job.get('created_at', '')}  age={age}  {_job_label(job)}"
                )
            if args.fail:
                print(f"\nReconciled {failed} job(s) to failed.")
                if remaining:
                    print(f"{len(remaining)} job(s) remain in processing.")
            else:
                print("\nDry run — nothing changed. Re-run with --fail to reconcile.")
        else:
            print("No stranded processing jobs.")
        if skipped:
            print(
                f"{len(skipped)} processing job(s) skipped (unparseable or "
                "corrupt); inspect manually via valkey-cli.",
                file=sys.stderr,
            )

    return 1 if remaining else 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reconcile-jobs",
        description=(
            "List or reconcile GroktoCrawl jobs stranded in 'processing' "
            "after a process loss (ADR-0047)."
        ),
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("VALKEY_URL", "redis://localhost:6379/0"),
        help="Valkey URL (default: $VALKEY_URL or redis://localhost:6379/0)",
    )
    parser.add_argument(
        "--stale-after",
        type=int,
        default=DEFAULT_STALE_AFTER,
        help=(
            "Flag processing jobs older than this many seconds "
            f"(default: {DEFAULT_STALE_AFTER})"
        ),
    )
    parser.add_argument(
        "--kind",
        default=None,
        help="Only consider jobs of this kind (e.g. crawl, agent, extract)",
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="Max jobs to flag (default: 100)"
    )
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Transition flagged processing jobs to failed (default: dry run)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        client = create_client(args.redis_url)
    except Exception as exc:  # redis URL parse errors
        print(f"reconcile-jobs: cannot connect to Valkey: {exc}", file=sys.stderr)
        return 2
    try:
        return run(client, args)
    except redis.exceptions.RedisError as exc:
        # Redis.from_url is lazy; connection failures surface here on the
        # first scan/get. Keep exit code 2 distinct from "stale jobs found".
        print(f"reconcile-jobs: Valkey operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
