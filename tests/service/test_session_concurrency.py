"""Regression tests for opt-in independent session step concurrency."""

import asyncio
import os
import time

import pytest


@pytest.mark.asyncio
async def test_failed_heartbeat_does_not_skip_serial_owner_cleanup():
    from unittest.mock import AsyncMock, MagicMock

    from agent.session import SessionManager

    manager = SessionManager.__new__(SessionManager)
    manager.store = MagicMock()
    manager.store.aget = AsyncMock(return_value={"id": "session"})
    manager.store.acquire_lock = AsyncMock(return_value="owner")
    manager.store.ahas_pending_steps = AsyncMock(return_value=False)
    manager.store.arelease_lock = AsyncMock()
    failed = asyncio.Event()

    async def renew(*_args):
        failed.set()
        raise RuntimeError("fixture renewal failure")

    async def execute(**_kwargs):
        await failed.wait()
        await asyncio.sleep(0)
        return {"step_index": 1}

    manager._renew_serial_lock = renew
    manager._execute_step = execute
    assert await manager.step("session", "query", {}, llm_model="fixture") == {
        "step_index": 1
    }
    manager.store.reset_lock_owner.assert_called_once()
    manager.store.arelease_lock.assert_awaited_once_with("session", "owner")


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_heartbeat_before_escape():
    from agent.session import SessionManager

    started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def renew():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()

    heartbeat = asyncio.create_task(renew())
    await started.wait()
    stopping = asyncio.create_task(SessionManager._stop_lock_heartbeat(heartbeat))
    await cleaning.wait()
    stopping.cancel()
    await asyncio.sleep(0)
    stopping.cancel()
    await asyncio.sleep(0)
    assert not stopping.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert heartbeat.done()


class ParallelStore:
    def __init__(self):
        self.reservations = {}
        self.steps = []
        self.refs = {}
        self.artifact = ""
        self.deleted = False
        self.next_index = 0
        self._guard = asyncio.Lock()

    async def aget(self, _session_id):
        return None if self.deleted else {"id": "session-1", "revision": 0}

    async def areserve_step(
        self, _session_id, key, _lease_ttl, _max_pending=8, request_fingerprint=None
    ):
        async with self._guard:
            if self.deleted:
                return None
            if key in self.reservations:
                existing = dict(self.reservations[key])
                existing.pop("acquired", None)
                return existing
            self.next_index += 1
            reservation = {
                "status": "pending",
                "acquired": True,
                "idempotency_key": key,
                "token": f"token-{len(self.reservations) + 1}",
                "index": self.next_index,
                "revision": len(self.steps),
                "fingerprint": request_fingerprint or "",
            }
            self.reservations[key] = reservation
            return dict(reservation)

    async def acommit_step(
        self, _session_id, reservation, step, refs, artifact, result
    ):
        async with self._guard:
            if self.deleted:
                return None
            current = self.reservations[reservation["idempotency_key"]]
            if current.get("status") == "committed":
                return current["result"]
            self.steps.append(dict(step))
            self.refs.update(refs)
            self.artifact += artifact
            committed = dict(result)
            current.update(status="committed", result=committed)
            return committed

    async def arelease_step(self, _session_id, reservation):
        async with self._guard:
            current = self.reservations.get(reservation["idempotency_key"])
            if (
                current
                and current.get("status") == "pending"
                and current.get("token") == reservation["token"]
            ):
                self.reservations.pop(reservation["idempotency_key"], None)


@pytest.mark.asyncio
async def test_opt_in_search_steps_overlap_and_commit_unique_refs(monkeypatch):
    from agent.session import SessionManager

    store = ParallelStore()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store
    started = []

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, **kwargs):
            started.append(kwargs["query"])
            await asyncio.sleep(0.05)
            return (
                [
                    {
                        "url": f"https://example.test/{kwargs['query']}",
                        "title": kwargs["query"],
                        "description": "summary",
                    }
                ],
                None,
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.SearXNGClient", Search)
    start = time.perf_counter()
    results = await asyncio.gather(
        manager.step(
            "session-1",
            "search",
            {"query": "one"},
            llm_model="model",
            parallel=True,
            idempotency_key="one-request",
        ),
        manager.step(
            "session-1",
            "search",
            {"query": "two"},
            llm_model="model",
            parallel=True,
            idempotency_key="two-request",
        ),
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 0.09
    assert started == ["one", "two"]
    assert {result["step_index"] for result in results} == {1, 2}
    assert set(store.refs) == {"ref_1_1", "ref_2_1"}
    assert len(store.steps) == 2

    pending = await store.areserve_step("session-1", "pending", 120)
    duplicate_pending = await store.areserve_step("session-1", "pending", 120)
    assert pending["acquired"] is True
    assert duplicate_pending.get("acquired", False) is False

    # A completed idempotency key returns its recorded result without another
    # remote search or a second reference/artifact commit.
    repeat = await manager.step(
        "session-1",
        "search",
        {"query": "one"},
        llm_model="model",
        parallel=True,
        idempotency_key="one-request",
    )
    assert repeat == results[0]
    assert started == ["one", "two"]
    assert len(store.steps) == 2


@pytest.mark.asyncio
async def test_failed_parallel_step_releases_reservation_for_retry(monkeypatch):
    from agent.session import SessionManager

    store = ParallelStore()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store
    attempts = 0

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("search unavailable")
            return ([{"url": "https://example.test/retry", "description": "ok"}], None)

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.SearXNGClient", Search)
    with pytest.raises(RuntimeError):
        await manager.step(
            "session-1",
            "search",
            {"query": "retry"},
            llm_model="model",
            parallel=True,
            idempotency_key="retry-request",
        )
    result = await manager.step(
        "session-1",
        "search",
        {"query": "retry"},
        llm_model="model",
        parallel=True,
        idempotency_key="retry-request",
    )
    assert attempts == 2
    assert result["step_index"] == 2


@pytest.mark.asyncio
async def test_late_parallel_commit_after_delete_is_rejected():
    from agent.session import SessionManager

    store = ParallelStore()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store
    reservation = await store.areserve_step("session-1", "late", 120)
    store.deleted = True
    assert (
        await manager._store_call(
            "acommit_step",
            "commit_step",
            "session-1",
            reservation,
            {"action": "search", "index": 1},
            {},
            "late",
            {"step_index": 1},
        )
        is None
    )


@pytest.mark.asyncio
async def test_cancellation_after_reservation_releases_pending_step():
    from agent.session import SessionManager

    class DelayedReserveStore(ParallelStore):
        async def areserve_step(
            self,
            session_id,
            key,
            lease_ttl,
            max_pending=8,
            request_fingerprint=None,
        ):
            await asyncio.sleep(0.03)
            return await super().areserve_step(
                session_id,
                key,
                lease_ttl,
                max_pending,
                request_fingerprint,
            )

    store = DelayedReserveStore()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store
    task = asyncio.create_task(
        manager.step(
            "session-1",
            "search",
            {"query": "cancelled"},
            llm_model="model",
            parallel=True,
            idempotency_key="cancelled-request",
        )
    )
    await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.reservations == {}


@pytest.mark.asyncio
async def test_cancellation_after_commit_preserves_idempotency_result(monkeypatch):
    from agent.session import SessionManager

    class CommitThenPauseStore(ParallelStore):
        async def acommit_step(
            self, session_id, reservation, step, refs, artifact, result
        ):
            committed = await super().acommit_step(
                session_id, reservation, step, refs, artifact, result
            )
            self.committed = asyncio.Event()
            self.committed.set()
            await asyncio.sleep(0.03)
            return committed

    store = CommitThenPauseStore()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, **_kwargs):
            return ([{"url": "https://example.test/cancel", "description": "ok"}], None)

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.SearXNGClient", Search)
    task = asyncio.create_task(
        manager.step(
            "session-1",
            "search",
            {"query": "cancelled"},
            llm_model="model",
            parallel=True,
            idempotency_key="committed-request",
        )
    )
    await store_wait(store, "committed")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.reservations["committed-request"]["status"] == "committed"


async def store_wait(store, attribute):
    while not hasattr(store, attribute):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_parallel_commit_is_atomic_fenced_and_idempotent():
    from agent.session_store import SessionStore

    from tests.outcome_governance import governed_skip

    redis_url = os.getenv("SESSION_STORE_TEST_URL")
    if not redis_url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for real parallel session contract test",
            owner="repository-maintainer",
            issue="#625",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )

    store = SessionStore(redis_url=redis_url)
    session_id = await store.acreate(ttl=120)
    try:
        reservations = await asyncio.gather(
            store.areserve_step(
                session_id,
                "one",
                lease_ttl=120,
                request_fingerprint="fingerprint-one",
            ),
            store.areserve_step(
                session_id,
                "two",
                lease_ttl=120,
                request_fingerprint="fingerprint-two",
            ),
        )
        assert all(
            reservation and reservation.get("acquired") for reservation in reservations
        )

        committed = await asyncio.gather(
            *(
                store.acommit_step(
                    session_id,
                    reservation,
                    {"action": "search", "index": reservation["index"]},
                    {f"ref_{reservation['index']}_1": {"url": f"https://{key}.test"}},
                    f"artifact-{key}",
                    {"step_index": reservation["index"], "key": key},
                )
                for key, reservation in zip(("one", "two"), reservations, strict=True)
            )
        )
        assert {result["key"] for result in committed if result} == {"one", "two"}

        session = await store.aget(session_id)
        assert session["step_count"] == 2
        assert {step["index"] for step in session["steps"]} == {
            reservations[0]["index"],
            reservations[1]["index"],
        }
        refs = await store.aget_refs(session_id)
        assert set(refs) == {"ref_1_1", "ref_2_1"}
        assert await store.aget_artifact(session_id) in {
            "artifact-oneartifact-two",
            "artifact-twoartifact-one",
        }

        duplicate = await store.areserve_step(
            session_id,
            "one",
            lease_ttl=120,
            request_fingerprint="fingerprint-one",
        )
        assert duplicate["status"] == "committed"
        assert duplicate["result"]["key"] == "one"
        tampered = dict(reservations[0], fingerprint="different-payload")
        assert (
            await store.acommit_step(
                session_id,
                tampered,
                {"action": "search", "index": tampered["index"]},
                {},
                "tampered-artifact",
                {"step_index": tampered["index"], "key": "tampered"},
            )
            is None
        )
        store.release_step(session_id, reservations[0])
        still_committed = await store.areserve_step(
            session_id,
            "one",
            lease_ttl=120,
            request_fingerprint="fingerprint-one",
        )
        assert still_committed["status"] == "committed"
        conflict = await store.areserve_step(
            session_id,
            "one",
            lease_ttl=120,
            request_fingerprint="different-payload",
        )
        assert conflict["status"] == "conflict"

        lock_token = await store.acquire_lock(session_id, timeout=1, lease_ttl=120)
        try:
            replay_while_locked = await store.areserve_step(
                session_id,
                "one",
                lease_ttl=120,
                request_fingerprint="fingerprint-one",
            )
            assert replay_while_locked["status"] == "committed"
            blocked = await store.areserve_step(
                session_id,
                "blocked",
                lease_ttl=120,
                request_fingerprint="fingerprint-blocked",
            )
            assert blocked["status"] == "busy"
        finally:
            await store.arelease_lock(session_id, lock_token)

        late = await store.areserve_step(
            session_id,
            "late",
            lease_ttl=1,
            request_fingerprint="fingerprint-late",
        )
        await asyncio.sleep(1.2)
        assert (
            await store.acommit_step(
                session_id,
                late,
                {"action": "search", "index": late["index"]},
                {},
                "late-artifact",
                {"step_index": late["index"]},
            )
            is None
        )
        assert (await store.aget(session_id))["step_count"] == 2
    finally:
        await store.adelete(session_id)
        store.redis.close()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_serial_owner_cannot_write_after_its_lease_expires():
    """An expired serial worker cannot append after parallel work is admitted."""
    from agent.session_store import SessionStore

    from tests.outcome_governance import governed_skip

    redis_url = os.getenv("SESSION_STORE_TEST_URL")
    if not redis_url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for serial owner fencing test",
            owner="repository-maintainer",
            issue="#625",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )

    store = SessionStore(redis_url=redis_url)
    session_id = await store.acreate(ttl=120)
    owner = await store.acquire_lock(session_id, timeout=1, lease_ttl=1)
    assert owner is not None
    context = store.set_lock_owner(owner)
    try:
        await asyncio.sleep(1.2)
        reservation = await store.areserve_step(
            session_id,
            "parallel-after-expiry",
            lease_ttl=120,
            request_fingerprint="parallel-after-expiry",
        )
        assert reservation and reservation["acquired"]

        assert await store.aappend_step(session_id, {"action": "late"}) is None
        assert not await store.aadd_ref(session_id, "late-ref", {"url": "late"})
        assert not await store.aappend_artifact(session_id, "late artifact")
        assert not await store.aupdate_meta(session_id, {"status": "late"})
        assert (await store.aget(session_id))["step_count"] == 0
    finally:
        store.reset_lock_owner(context)
        await store.adelete(session_id)
        store.redis.close()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_mixed_steps_keep_indexes_and_refs_after_a_failed_reservation():
    """Parallel and serial writers share one monotonic ref namespace."""
    from agent.session_store import SessionStore

    from tests.outcome_governance import governed_skip

    redis_url = os.getenv("SESSION_STORE_TEST_URL")
    if not redis_url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for mixed session index test",
            owner="repository-maintainer",
            issue="#625",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )

    store = SessionStore(redis_url=redis_url)
    session_id = await store.acreate(ttl=120)

    async def commit_parallel(key: str, label: str) -> dict:
        reservation = await store.areserve_step(
            session_id,
            key,
            lease_ttl=120,
            request_fingerprint=key,
        )
        assert reservation and reservation["acquired"]
        result = await store.acommit_step(
            session_id,
            reservation,
            {"action": "search", "index": reservation["index"]},
            {f"ref_{reservation['index']}_1": {"url": f"https://{label}.test"}},
            label,
            {"step_index": reservation["index"], "label": label},
        )
        assert result is not None
        return reservation

    try:
        first = await commit_parallel("parallel-1", "parallel-1")
        assert first["index"] == 1

        owner = await store.acquire_lock(session_id, timeout=1, lease_ttl=120)
        assert owner is not None
        context = store.set_lock_owner(owner)
        try:
            assert await store.aappend_step(session_id, {"action": "serial"}) == 2
            assert await store.aadd_ref(
                session_id, "ref_2_1", {"url": "https://serial-2.test"}
            )
            assert await store.aappend_artifact(session_id, "serial-2")
        finally:
            store.reset_lock_owner(context)
            await store.arelease_lock(session_id, owner)

        third = await commit_parallel("parallel-3", "parallel-3")
        assert third["index"] == 3

        failed = await store.areserve_step(
            session_id,
            "failed-4",
            lease_ttl=120,
            request_fingerprint="failed-4",
        )
        assert failed and failed["index"] == 4
        await store.arelease_step(session_id, failed)

        fifth = await commit_parallel("parallel-5", "parallel-5")
        assert fifth["index"] == 5
        refs = await store.aget_refs(session_id)
        assert refs == {
            "ref_1_1": {"url": "https://parallel-1.test"},
            "ref_2_1": {"url": "https://serial-2.test"},
            "ref_3_1": {"url": "https://parallel-3.test"},
            "ref_5_1": {"url": "https://parallel-5.test"},
        }
        assert {step["index"] for step in (await store.aget(session_id))["steps"]} == {
            1,
            2,
            3,
            5,
        }
    finally:
        await store.adelete(session_id)
        store.redis.close()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_serial_heartbeat_prevents_parallel_admission(monkeypatch):
    from agent.session import SessionManager
    from agent.session_store import SessionStore

    from tests.outcome_governance import governed_skip

    redis_url = os.getenv("SESSION_STORE_TEST_URL")
    if not redis_url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for serial lease heartbeat test",
            owner="repository-maintainer",
            issue="#625",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )

    started = asyncio.Event()

    class SlowSearch:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, **_kwargs):
            started.set()
            await asyncio.sleep(1.3)
            return ([{"url": "https://slow.test", "description": "slow"}], None)

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.SearXNGClient", SlowSearch)
    manager = SessionManager(redis_url=redis_url)
    manager._SERIAL_LOCK_LEASE_TTL = 1
    manager._SERIAL_LOCK_RENEW_INTERVAL = 0.2
    session_id = await manager.create_session(ttl=120)
    contender = SessionStore(redis_url=redis_url)
    try:
        serial = asyncio.create_task(
            manager.step(
                session_id,
                "search",
                {"query": "slow"},
                llm_model="model",
            )
        )
        await started.wait()
        await asyncio.sleep(0.85)
        blocked = await contender.areserve_step(
            session_id,
            "parallel-during-serial",
            lease_ttl=120,
            request_fingerprint="parallel-during-serial",
        )
        assert blocked and blocked["status"] == "busy"
        assert (await serial)["step_index"] == 1
    finally:
        await manager.delete_session(session_id)
        manager.store.redis.close()
        contender.redis.close()


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_export_snapshot_is_coherent_during_parallel_commits():
    from agent.session_store import SessionStore

    from tests.outcome_governance import governed_skip

    redis_url = os.getenv("SESSION_STORE_TEST_URL")
    if not redis_url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for atomic export snapshot test",
            owner="repository-maintainer",
            issue="#625",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )

    store = SessionStore(redis_url=redis_url)
    session_id = await store.acreate(ttl=120)
    try:
        reservations = [
            await store.areserve_step(
                session_id,
                f"snapshot-{index}",
                lease_ttl=120,
                request_fingerprint=f"snapshot-fingerprint-{index}",
            )
            for index in range(8)
        ]
        commit_tasks = [
            asyncio.create_task(
                store.acommit_step(
                    session_id,
                    reservation,
                    {"action": "search", "index": reservation["index"]},
                    {
                        f"snapshot-ref-{index}": {
                            "url": f"https://snapshot.test/{index}"
                        }
                    },
                    f"snapshot-artifact-{index}",
                    {"step_index": reservation["index"]},
                )
            )
            for index, reservation in enumerate(reservations)
        ]
        snapshots = []
        while not all(task.done() for task in commit_tasks):
            snapshots.append(await store.aexport_snapshot(session_id))
            await asyncio.sleep(0)
        await asyncio.gather(*commit_tasks)
        snapshots.append(await store.aexport_snapshot(session_id))

        assert snapshots
        for snapshot in snapshots:
            assert snapshot is not None
            session = snapshot["session"]
            assert session["step_count"] == len(snapshot["steps"])
            assert session["step_count"] == len(snapshot["refs"])
            assert (
                snapshot["artifact"].count("snapshot-artifact-")
                == session["step_count"]
            )
    finally:
        await store.adelete(session_id)
        store.redis.close()
