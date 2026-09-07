"""Focused regression tests for asynchronous, batched session persistence."""

import asyncio
import json
import time

import pytest

from tests.outcome_governance import governed_skip


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        results = []
        for name, args, kwargs in self.operations:
            results.append(getattr(self.redis, name)(*args, **kwargs))
        self.operations = []
        return results


class FakeRedis:
    """Small Redis double that records commands and applies pipelines."""

    def __init__(self):
        self.data = {}
        self.expiries = {}
        self.commands = []
        self.delay = 0

    def _call(self, name):
        self.commands.append(name)
        if self.delay:
            time.sleep(self.delay)

    def hset(self, key, field=None, value=None, *, mapping=None):
        self._call("hset")
        current = self.data.setdefault(key, {})
        if mapping is not None:
            current.update(mapping)
            return len(mapping)
        current[field] = value
        return 1

    def hgetall(self, key):
        self._call("hgetall")
        return dict(self.data.get(key, {}))

    def hget(self, key, field):
        self._call("hget")
        return self.data.get(key, {}).get(field)

    def hdel(self, key, field):
        self._call("hdel")
        return int(self.data.get(key, {}).pop(field, None) is not None)

    def exists(self, key):
        self._call("exists")
        return int(key in self.data)

    def set(self, key, value, **kwargs):
        self._call("set")
        self.data[key] = value
        return True

    def get(self, key):
        self._call("get")
        return self.data.get(key)

    def append(self, key, value):
        self._call("append")
        self.data[key] = str(self.data.get(key, "")) + value
        return len(self.data[key].encode())

    def hincrby(self, key, field, amount):
        self._call("hincrby")
        current = int(self.data.setdefault(key, {}).get(field, 0)) + amount
        self.data[key][field] = str(current)
        return current

    def expire(self, key, ttl):
        self._call("expire")
        self.expiries[key] = ttl
        return True

    def eval(self, script, key_count, *values):
        self._call("eval")
        keys, args = values[:key_count], values[key_count:]
        if "session_read_steps_v1" in script:
            return [self.data.get(keys[0], "[]"), self.data.get(keys[1], [])]
        meta = self.data.get(keys[0])
        if not meta:
            return None if "session_append_step_v1" in script else 0
        if "session_append_artifact_v1" in script:
            previous = self.data.get(keys[2], "")
            meta["artifact_chars"] = str(
                int(meta.get("artifact_chars", len(previous))) + int(args[1])
            )
            self.data[keys[2]] = previous + args[0]
            meta["expires_at"] = args[2]
        elif "session_add_refs_v1" in script:
            refs = self.data.setdefault(keys[3], {})
            refs.update(dict(zip(args[3::2], args[4::2], strict=True)))
            meta["expires_at"] = args[0]
        elif "session_append_step_v1" in script:
            old = self.data.get(keys[4], [])
            next_index = int(meta.get("next_step_index", meta["step_count"])) + 1
            index = next_index
            step = json.loads(args[0])
            step.update(index=index, timestamp=json.loads(args[1]))
            self.data[keys[4]] = [*old, json.dumps(step)]
            meta["next_step_index"] = str(index)
            meta["step_count"] = str(int(meta["step_count"]) + 1)
            meta["expires_at"] = args[2]
        else:
            raise AssertionError("Unexpected Lua operation")
        for key in keys:
            self.expiries[key] = int(meta["ttl"])
        return index if "session_append_step_v1" in script else 1

    def pipeline(self, **_kwargs):
        self._call("pipeline")
        return _Pipeline(self)

    def delete(self, *keys):
        self._call("delete")
        removed = 0
        for key in keys:
            removed += int(self.data.pop(key, None) is not None)
            self.expiries.pop(key, None)
        return removed


@pytest.fixture
def store():
    from agent.session_store import SessionStore

    result = SessionStore(redis_url="redis://unused")
    result.redis = FakeRedis()
    return result


@pytest.mark.asyncio
async def test_async_session_lifecycle_batches_refs_and_preserves_unicode(store):
    session_id = await store.acreate(ttl=120)
    refs = {
        f"ref_1_{i}": {"url": f"https://example.test/{i}", "markdown": "é"}
        for i in range(100)
    }

    before_refs = len(store.redis.commands)
    assert await store.aadd_refs(session_id, refs)
    ref_commands = store.redis.commands[before_refs:]
    # One TTL metadata read and one guarded server-side bulk commit.
    assert ref_commands == ["hget", "eval"]

    assert await store.aappend_artifact(session_id, "é🙂")
    assert await store.aappend_artifact(session_id, "\nmore")
    store.redis.commands.clear()
    session = await store.aget(session_id)
    assert session["artifact_length"] == len("é🙂\nmore")
    # History is read in Lua; metadata length never fetches the artifact.
    assert store.redis.commands.count("get") == 0
    assert (await store.aget_refs(session_id))["ref_1_0"]["markdown"] == "é"
    assert list((await store.aget_refs(session_id)).keys()) == list(refs.keys())

    assert await store.adelete(session_id)
    assert await store.aget(session_id) is None


@pytest.mark.parametrize("ref_count", [1, 20, 100])
def test_bulk_ref_command_count_is_bounded(store, ref_count):
    session_id = store.create()
    refs = {f"ref_{i}": {"url": f"https://example.test/{i}"} for i in range(ref_count)}
    store.redis.commands.clear()
    assert store.add_refs(session_id, refs)
    assert store.redis.commands == ["hget", "eval"]


@pytest.mark.asyncio
async def test_blocked_redis_read_does_not_block_event_loop(store):
    session_id = await store.acreate()
    store.redis.delay = 0.08
    beats = 0

    async def heartbeat():
        nonlocal beats
        deadline = asyncio.get_running_loop().time() + 0.05
        while asyncio.get_running_loop().time() < deadline:
            beats += 1
            await asyncio.sleep(0.005)

    await asyncio.gather(store.aget(session_id), heartbeat())
    assert beats >= 5


@pytest.mark.asyncio
async def test_search_step_commits_refs_as_one_storage_batch(store, monkeypatch):
    from agent.session import SessionManager

    session_id = await store.acreate()
    manager = SessionManager.__new__(SessionManager)
    manager.store = store

    class Search:
        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, **_kwargs):
            return (
                [
                    {
                        "url": f"https://example.test/{i}",
                        "title": f"Source {i}",
                        "description": "summary",
                    }
                    for i in range(20)
                ],
                None,
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.SearXNGClient", Search)
    before = len(store.redis.commands)
    outcome = await manager._step_search(session_id, {"query": "batch"}, "unused")

    assert outcome["ref_count"] == 20
    assert len(await store.aget_refs(session_id)) == 20
    committed = store.redis.commands[before:]
    assert committed.count("eval") == 3  # step, refs, artifact
    assert committed.count("hget") == 3


@pytest.mark.asyncio
async def test_async_manager_uses_offloaded_store_methods():
    from agent.session import SessionManager

    manager = SessionManager.__new__(SessionManager)
    calls = []

    class Store:
        def get(self, session_id):
            time.sleep(0.03)
            calls.append(session_id)
            return {"id": session_id}

    manager.store = Store()
    result = await manager.get_session("s-1")
    assert result == {"id": "s-1"}
    assert calls == ["s-1"]


def test_sync_append_keeps_legacy_character_count(store):
    session_id = store.create()
    assert store.append_artifact(session_id, "é🙂")
    assert store.get(session_id)["artifact_length"] == 2
    artifact = store.get_artifact(session_id)
    assert json.loads(json.dumps(artifact, ensure_ascii=False)) == "é🙂"


@pytest.mark.parametrize("prefix", ["", "é🙂" * 5_000])
def test_artifact_append_does_not_download_existing_value(store, prefix):
    session_id = store.create()
    if prefix:
        store.append_artifact(session_id, prefix)
    store.redis.commands.clear()
    assert store._append_artifact_atomic(session_id, "追加🙂")
    assert "get" not in store.redis.commands
    assert store.get(session_id)["artifact_length"] == len(prefix) + 3


@pytest.mark.asyncio
async def test_cancellation_drains_native_write_before_returning(store):
    import threading

    entered, finish = threading.Event(), threading.Event()
    writes = []

    def blocked_write():
        entered.set()
        finish.wait(2)
        writes.append("committed")

    task = asyncio.create_task(store._offload(blocked_write))
    await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writes == ["committed"]


@pytest.mark.asyncio
async def test_blocking_storage_admission_is_bounded(store):
    import threading

    finish = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def blocked():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        finish.wait(2)
        with lock:
            active -= 1

    tasks = [asyncio.create_task(store._offload(blocked)) for _ in range(20)]
    for _ in range(100):
        if peak == 8:
            break
        await asyncio.sleep(0.001)
    assert peak == 8
    finish.set()
    await asyncio.gather(*tasks)
    assert peak == 8


@pytest.mark.asyncio
@pytest.mark.enable_socket
async def test_real_valkey_atomic_append_history_and_deleted_session():
    import os

    from agent.session_store import SessionStore, _all_keys, _meta_key, _steps_key

    url = os.getenv("SESSION_STORE_TEST_URL")
    if not url:
        governed_skip(
            "Set SESSION_STORE_TEST_URL for real Valkey contract test",
            owner="repository-maintainer",
            issue="#626",
            classification="retained",
            environment="Requires isolated Valkey; exercised in session-storage CI",
        )
    store = SessionStore(redis_url=url)
    session_id = await store.acreate(ttl=120)
    try:
        legacy = [{"index": 1, "params": {"empty": [], "object": {}}, "action": "old"}]
        store.redis.set(_steps_key(session_id), json.dumps(legacy), ex=120)
        store.redis.hset(_meta_key(session_id), "step_count", 1)
        store.redis.hdel(_meta_key(session_id), "artifact_chars")
        store.redis.set(_all_keys(session_id)[2], "é🙂", ex=120)
        indices = await asyncio.gather(
            *(
                store.aappend_step(
                    session_id,
                    {
                        "action": "search",
                        "params": {"empty": [], "object": {}},
                        "summary": str(i),
                    },
                )
                for i in range(20)
            )
        )
        assert sorted(indices) == list(range(2, 22))
        steps = await store.aget_steps(session_id)
        assert steps[0] == legacy[0]
        assert [step["index"] for step in steps] == list(range(1, 22))
        assert all(step["params"] == {"empty": [], "object": {}} for step in steps)
        await asyncio.gather(
            *(store.aappend_artifact(session_id, "追加🙂") for _ in range(20))
        )
        meta = await store.aget(session_id)
        assert meta["artifact_length"] == 62
        assert await store.aget_artifact(session_id) == "é🙂" + "追加🙂" * 20
        for count in (1, 20, 100):
            assert await store.aadd_refs(
                session_id, {f"ref_{i}": {"markdown": "é🙂"} for i in range(count)}
            )
        assert len(await store.aget_refs(session_id)) == 100
        assert all(0 < store.redis.ttl(key) <= 120 for key in _all_keys(session_id))
        from datetime import datetime

        assert (
            datetime.fromisoformat(meta["expires_at"])
            - datetime.fromisoformat(meta["created_at"])
        ).total_seconds() < 125
        await store.adelete(session_id)
        assert not await store.aappend_artifact(session_id, "late")
        assert not await store.aadd_refs(session_id, {"late": {}})
        assert await store.aappend_step(session_id, {"action": "late"}) is None
        assert not any(store.redis.exists(key) for key in _all_keys(session_id))
    finally:
        await store.adelete(session_id)
        store.redis.close()
