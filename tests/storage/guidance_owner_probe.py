"""Exercise guidance resume through the real Valkey W5 fencing ledger."""

import json
import os
import time
from uuid import uuid4

from agent.experimental.durable_research import (
    DurableConflictError,
    DurableResearchLedger,
)
from agent.experimental.guidance_runtime import (
    DurableResearchGuidanceOwner,
    GuidanceError,
)


def admitted(ledger, scope, name, *, now_ms=None):
    return ledger.admit(
        scope,
        f"guidance-{name}-{uuid4()}",
        f"digest-{name}",
        run_id=str(uuid4()),
        now_ms=now_ms,
    )


def rejected(action, expected):
    try:
        action()
    except (GuidanceError, DurableConflictError) as error:
        assert expected in str(error)
        return str(error)
    raise AssertionError(f"expected rejection containing {expected!r}")


def main():
    namespace = f"groktocrawl:guidance-probe:{uuid4()}"
    ledger = DurableResearchLedger(
        os.environ["VALKEY_URL"],
        namespace=namespace,
        lease_ms=60_000,
        retention_ms=120_000,
        retry_window_ms=60_000,
    )
    owner = DurableResearchGuidanceOwner(ledger)
    try:
        resumable = admitted(ledger, "scope-a", "resume")
        first = owner.claim(resumable.run_id, "scope-a", "worker-before-loss")
        owner.checkpoint(first, "awaiting_guidance", "checkpoint-digest")
        ledger.redis.delete(ledger._lease_key(resumable.run_id))
        second = owner.claim(resumable.run_id, "scope-a", "worker-after-loss")
        assert second.generation == first.generation + 1
        owner.complete(second, "result-digest")

        cancelled = admitted(ledger, "scope-a", "cancelled")
        ledger.cancel(cancelled.run_id)
        cancelled_error = rejected(
            lambda: owner.claim(cancelled.run_id, "scope-a", "worker"), "terminal"
        )

        deleted = admitted(ledger, "scope-a", "deleted")
        ledger.delete(deleted.run_id)
        deleted_error = rejected(
            lambda: owner.claim(deleted.run_id, "scope-a", "worker"), "terminal"
        )

        foreign = admitted(ledger, "scope-a", "foreign")
        foreign_error = rejected(
            lambda: owner.claim(foreign.run_id, "scope-b", "worker"), "another scope"
        )

        expired = admitted(
            ledger,
            "scope-a",
            "expired",
            now_ms=int(time.time() * 1000) - 120_000,
        )
        expired_error = rejected(
            lambda: owner.claim(expired.run_id, "scope-a", "worker"), "expired"
        )

        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "resume_generation_before": first.generation,
                    "resume_generation_after": second.generation,
                    "checkpoint_digest": "checkpoint-digest",
                    "terminal_state": ledger.get(resumable.run_id).state,
                    "rejections": {
                        "cancelled": cancelled_error,
                        "deleted": deleted_error,
                        "foreign_scope": foreign_error,
                        "expired": expired_error,
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        keys = list(ledger.redis.scan_iter(match=f"{namespace}:*"))
        if keys:
            ledger.redis.delete(*keys)


if __name__ == "__main__":
    main()
