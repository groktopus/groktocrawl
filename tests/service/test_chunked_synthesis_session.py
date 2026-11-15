"""Session API vertical slice for durable outline/section synthesis resume."""

import json

import pytest


@pytest.mark.asyncio
async def test_outline_checkpoint_resume_and_deterministic_assembly(monkeypatch):
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                "r-ottoman": {
                    "url": "https://example.test/ottoman",
                    "title": "Ottoman context",
                    "markdown": "The Ottoman administration shaped the provinces.",
                },
                "r-mandate": {
                    "url": "https://example.test/mandate",
                    "title": "British mandate",
                    "markdown": "Britain governed under a mandate framework.",
                },
            }
            self.steps = []

        async def aget_refs(self, _session_id):
            return dict(self.refs)

        async def aadd_refs(self, _session_id, refs):
            self.refs.update(refs)
            return True

        async def aappend_step(self, _session_id, step):
            self.steps.append(step)
            return len(self.steps)

    class FakeLLM:
        calls = 0
        contexts = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, *, stage, **_kwargs):
            self.__class__.calls += 1
            self.__class__.contexts.append(_kwargs.get("context", ""))
            if stage == "synthesis_outline":
                return json.dumps(
                    {
                        "sections": [
                            {"id": "ottoman", "title": "Ottoman context"},
                            {"id": "mandate", "title": "British mandate"},
                        ]
                    }
                )
            if stage == "synthesis_section":
                return json.dumps(
                    {
                        "markdown": "Evidence-backed finding [1]"
                        if self.__class__.calls == 2
                        else "Evidence-backed finding [2]",
                        "continuity": {
                            "claims": ["claim"],
                            "terminology": ["mandate"],
                            "transitions": ["next"],
                            "unresolved": [],
                        },
                    }
                )
            raise AssertionError(stage)

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", FakeLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 2048}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()

    initial = await manager._step_synthesize(
        session_id="session-1",
        params={
            "question": "Iraq history",
            "synthesis_id": "iraq-run-001",
            "ref_ids": ["r-ottoman", "r-mandate"],
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert initial["status"] == "outline_ready"
    assert initial["manifest_sha256"]
    assert initial["resume_required"] is True

    first = await manager._step_synthesize(
        session_id="session-1",
        params={
            "question": "Iraq history",
            "synthesis_id": "iraq-run-001",
            "resume": True,
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert first["status"] == "in_progress"
    assert first["completed_chunk_count"] == 1

    final = await manager._step_synthesize(
        session_id="session-1",
        params={
            "question": "Iraq history",
            "synthesis_id": "iraq-run-001",
            "resume": True,
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert final["status"] == "complete"
    assert "Ottoman context" in final["answer"]
    assert "British mandate" in final["answer"]
    assert final["completed_chunk_count"] == final["total_chunk_count"] == 2
    assert FakeLLM.calls == 3
    assert "Stable outline" in FakeLLM.contexts[1]
    assert "Evidence-backed finding [1]" in FakeLLM.contexts[2]

    replay = await manager._step_synthesize(
        session_id="session-1",
        params={
            "question": "Iraq history",
            "synthesis_id": "iraq-run-001",
            "resume": True,
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert replay["answer"] == final["answer"]
    assert FakeLLM.calls == 3


@pytest.mark.asyncio
async def test_resume_requires_explicit_confirmation_for_unknown_call(monkeypatch):
    from agent.session import SessionManager

    manager = SessionManager.__new__(SessionManager)
    manager.store = type(
        "Store",
        (),
        {
            "aget_refs": lambda self, _sid: {},
        },
    )()
    # The state fixture is supplied by the async store seam to exercise the
    # fail-closed unknown-operation transition without an LLM call.
    state = {
        "synthesis_id": "resume-unknown-1",
        "question": "q",
        "status": "in_progress",
        "pending": {"kind": "section_chunk", "chunk_id": "c", "outcome": "unknown"},
        "manifest": {
            "sources": [],
            "chunks": [],
            "source_count": 0,
            "source_chars": 0,
            "manifest_sha256": "x",
        },
        "completed_chunk_ids": [],
        "chunk_outputs": [],
        "completed_sections": [],
        "outline": {"sections": [{"id": "s1", "title": "One"}]},
        "assignments": {"sections": {"s1": ["c"]}},
    }
    checkpoint_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"resume-unknown-1").hexdigest()[:32]
    )

    async def get_refs(_sid):
        return {checkpoint_ref: {"state": state}}

    manager.store.aget_refs = get_refs
    with pytest.raises(ValueError, match="retry_pending=true"):
        await manager._step_synthesize(
            session_id="session-1",
            params={
                "question": "q",
                "synthesis_id": "resume-unknown-1",
                "resume": True,
            },
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )


@pytest.mark.asyncio
async def test_small_input_budget_partitions_without_dropping_snapshot(monkeypatch):
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                "r1": {
                    "url": "https://example.test/a",
                    "title": "Ottoman",
                    "markdown": "Ottoman " * 1000,
                },
                "r2": {
                    "url": "https://example.test/b",
                    "title": "Mandate",
                    "markdown": "Mandate " * 1000,
                },
            }

        async def aget_refs(self, _sid):
            return dict(self.refs)

        async def aadd_refs(self, _sid, refs):
            self.refs.update(refs)
            return True

        async def aappend_step(self, _sid, _step):
            return 1

    class FakeLLM:
        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, *, stage, **_kwargs):
            assert stage == "synthesis_outline"
            return json.dumps(
                {"sections": [{"id": "origins", "title": "Ottoman context"}]}
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", FakeLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 2048}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()
    result = await manager._step_synthesize(
        session_id="session-1",
        params={
            "question": "history",
            "synthesis_id": "budget-case-1",
            "ref_ids": ["r1", "r2"],
            "input_budget_chars": 8000,
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    checkpoint_ref = (
        "__synthesis_" + __import__("hashlib").sha256(b"budget-case-1").hexdigest()[:32]
    )
    checkpoint = manager.store.refs[checkpoint_ref]["state"]
    assert result["status"] == "outline_ready"
    assert checkpoint["input_budget_chars"] == 8000
    assert max(chunk["chars"] for chunk in checkpoint["manifest"]["chunks"]) <= 2000
    assert checkpoint["manifest"]["source_chars"] == 16_000
    assert len(checkpoint["manifest"]["chunks"]) > 2


@pytest.mark.asyncio
async def test_refusal_is_terminal_and_output_truncation_requires_larger_explicit_retry(
    monkeypatch,
):
    from agent.exceptions import OutputTruncatedError, ProviderRefusalError
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                "r1": {
                    "url": "https://example.test/a",
                    "title": "Source",
                    "markdown": "Evidence.",
                }
            }

        async def aget_refs(self, _sid):
            return dict(self.refs)

        async def aadd_refs(self, _sid, refs):
            self.refs.update(refs)
            return True

        async def aappend_step(self, _sid, _step):
            return 1

    class RefusingLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, **_kwargs):
            self.__class__.calls += 1
            raise ProviderRefusalError("refused")

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", RefusingLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 2048}
    )
    refused = SessionManager.__new__(SessionManager)
    refused.store = Store()
    params = {
        "question": "history",
        "synthesis_id": "refusal-case-1",
        "ref_ids": ["r1"],
    }
    with pytest.raises(ProviderRefusalError):
        await refused._step_synthesize(
            session_id="session-1",
            params=params,
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    refusal_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"refusal-case-1").hexdigest()[:32]
    )
    assert refused.store.refs[refusal_ref]["state"]["status"] == "refused"
    with pytest.raises(ValueError, match="not retryable"):
        await refused._step_synthesize(
            session_id="session-1",
            params={**params, "resume": True, "retry_pending": True},
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    assert RefusingLLM.calls == 1

    class TruncatingLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, *, stage, **_kwargs):
            self.__class__.calls += 1
            if stage == "synthesis_outline":
                return json.dumps({"sections": [{"id": "sources", "title": "Sources"}]})
            if self.__class__.calls == 2:
                raise OutputTruncatedError(
                    "length",
                    details={
                        "finish_reason": "length",
                        "refusal_present": False,
                        "completion_tokens": 1024,
                    },
                )
            return json.dumps(
                {
                    "markdown": "Finding [1]",
                    "continuity": {
                        "claims": [],
                        "terminology": [],
                        "transitions": [],
                        "unresolved": [],
                    },
                }
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", TruncatingLLM)
    resumable = SessionManager.__new__(SessionManager)
    resumable.store = Store()
    trunc_params = {
        "question": "history",
        "synthesis_id": "truncate-case-1",
        "ref_ids": ["r1"],
    }
    await resumable._step_synthesize(
        session_id="session-1",
        params=trunc_params,
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    with pytest.raises(
        OutputTruncatedError, match="larger output_budget_tokens"
    ) as error:
        await resumable._step_synthesize(
            session_id="session-1",
            params={
                "question": "history",
                "synthesis_id": "truncate-case-1",
                "resume": True,
            },
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    assert error.value.details == {
        "finish_reason": "length",
        "refusal_present": False,
        "completion_tokens": 1024,
    }
    result = await resumable._step_synthesize(
        session_id="session-1",
        params={
            "question": "history",
            "synthesis_id": "truncate-case-1",
            "resume": True,
            "retry_pending": True,
            "output_budget_tokens": 2048,
        },
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert result["status"] == "complete"
    assert TruncatingLLM.calls == 3


def test_imported_snapshot_rejects_duplicate_or_unsafe_urls():
    from agent.models import SessionEvidenceImportRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unique"):
        SessionEvidenceImportRequest(
            sources=[
                {"url": "https://example.test/a", "markdown": "one"},
                {"url": "https://example.test/a", "markdown": "two"},
            ]
        )
    with pytest.raises(ValidationError, match="absolute HTTP"):
        SessionEvidenceImportRequest(
            sources=[
                {"url": "file:///etc/passwd", "markdown": "not fetched"},
            ]
        )


@pytest.mark.asyncio
async def test_snapshot_import_serializes_with_synthesis_and_releases_lock(monkeypatch):
    from types import SimpleNamespace

    from agent.models import SessionEvidenceImportRequest
    from agent.routes import session as routes

    events = []

    class Store:
        async def acquire_lock(self, session_id, *, timeout, lease_ttl):
            events.append(("lock", session_id, timeout, lease_ttl))
            return "owner-token"

        def set_lock_owner(self, token):
            events.append(("owner", token))
            return "context"

        def reset_lock_owner(self, context):
            events.append(("reset", context))

        async def arelease_lock(self, session_id, token):
            events.append(("release", session_id, token))

        async def ahas_pending_steps(self, _session_id):
            return False

    class Manager:
        _SERIAL_LOCK_LEASE_TTL = 120

        def __init__(self, **_kwargs):
            self.store = Store()

        async def _store_refs(self, session_id, refs):
            events.append(("store", session_id, tuple(refs)))
            return True

    async def authorize(*_args):
        events.append(("authorize",))

    monkeypatch.setattr(routes, "_get_redis_url", lambda _request: "fixture")
    monkeypatch.setattr(routes, "authorize_session", authorize)
    monkeypatch.setattr("agent.session.SessionManager", Manager)
    body = SessionEvidenceImportRequest(
        sources=[
            {
                "url": "https://example.test/a",
                "title": "A",
                "markdown": "body",
            }
        ]
    )

    result = await routes.import_session_evidence(SimpleNamespace(), "s1", body)

    assert result["source_count"] == 1
    assert events[0][0] == "authorize"
    assert events[1] == ("lock", "s1", 2, 120)
    assert next(i for i, event in enumerate(events) if event[0] == "store") > 2
    assert events[-2:] == [("reset", "context"), ("release", "s1", "owner-token")]


@pytest.mark.asyncio
async def test_snapshot_import_rejects_while_session_is_locked(monkeypatch):
    from types import SimpleNamespace

    from agent.exceptions import ConflictError
    from agent.models import SessionEvidenceImportRequest
    from agent.routes import session as routes

    class Store:
        async def acquire_lock(self, *_args, **_kwargs):
            return None

    class Manager:
        _SERIAL_LOCK_LEASE_TTL = 120

        def __init__(self, **_kwargs):
            self.store = Store()

    async def authorize(*_args):
        return None

    monkeypatch.setattr(routes, "_get_redis_url", lambda _request: "fixture")
    monkeypatch.setattr(routes, "authorize_session", authorize)
    monkeypatch.setattr("agent.session.SessionManager", Manager)
    body = SessionEvidenceImportRequest(
        sources=[{"url": "https://example.test/a", "markdown": "body"}]
    )

    with pytest.raises(ConflictError, match="executing another operation"):
        await routes.import_session_evidence(SimpleNamespace(), "s1", body)


@pytest.mark.asyncio
async def test_outline_catalog_cannot_exceed_declared_input_budget(monkeypatch):
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                f"ref-{index}": {
                    "url": f"https://example.test/{index}",
                    "title": f"Topic {index} " + ("x" * 900),
                    "markdown": "Evidence body.",
                }
                for index in range(10)
            }

        async def aget_refs(self, _session_id):
            return dict(self.refs)

        async def aadd_refs(self, _session_id, refs):
            self.refs.update(refs)
            return True

    class NeverCalledLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, **_kwargs):
            self.__class__.calls += 1
            raise AssertionError("oversized catalog must fail before dispatch")

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", NeverCalledLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 1024}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()
    with pytest.raises(ValueError, match="exceeds input_budget_chars"):
        await manager._step_synthesize(
            session_id="session-1",
            params={
                "question": "q",
                "synthesis_id": "outline-budget-01",
                "input_budget_chars": 8_000,
                "ref_ids": list(manager.store.refs),
            },
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    checkpoint_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"outline-budget-01").hexdigest()[:32]
    )
    checkpoint = manager.store.refs[checkpoint_ref]["state"]
    assert checkpoint["status"] == "failed"
    assert checkpoint["pending"] is None
    assert NeverCalledLLM.calls == 0


@pytest.mark.asyncio
async def test_provider_context_limit_is_terminal_for_snapshot_id(monkeypatch):
    from agent.exceptions import ContextLimitError
    from agent.session import SessionManager

    class Store:
        refs = {
            "r1": {
                "url": "https://example.test/a",
                "title": "Source",
                "markdown": "Evidence.",
            }
        }

        async def aget_refs(self, _sid):
            return dict(self.refs)

        async def aadd_refs(self, _sid, refs):
            self.refs.update(refs)
            return True

    class ContextLimitedLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, **_kwargs):
            self.__class__.calls += 1
            raise ContextLimitError("context limit")

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", ContextLimitedLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 1024}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()
    params = {
        "question": "q",
        "synthesis_id": "context-limit-01",
        "ref_ids": ["r1"],
    }
    with pytest.raises(ContextLimitError, match="new synthesis_id"):
        await manager._step_synthesize(
            session_id="session-1",
            params=params,
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    checkpoint_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"context-limit-01").hexdigest()[:32]
    )
    assert Store.refs[checkpoint_ref]["state"]["status"] == "failed"
    with pytest.raises(ValueError, match="retry budget is exhausted"):
        await manager._step_synthesize(
            session_id="session-1",
            params={**params, "resume": True, "retry_pending": True},
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    assert ContextLimitedLLM.calls == 1


@pytest.mark.asyncio
async def test_missing_assigned_source_citation_is_not_checkpointed(monkeypatch):
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                "r1": {
                    "url": "https://example.test/a",
                    "title": "Source",
                    "markdown": "Evidence.",
                }
            }

        async def aget_refs(self, _sid):
            return dict(self.refs)

        async def aadd_refs(self, _sid, refs):
            self.refs.update(refs)
            return True

        async def aappend_step(self, _sid, _step):
            return 1

    class CitationLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, *, stage, **_kwargs):
            self.__class__.calls += 1
            if stage == "synthesis_outline":
                return json.dumps({"sections": [{"id": "sources", "title": "Sources"}]})
            marker = " [1]" if self.__class__.calls > 2 else ""
            return json.dumps(
                {
                    "markdown": f"Evidence finding{marker}",
                    "continuity": {
                        "claims": [],
                        "terminology": [],
                        "transitions": [],
                        "unresolved": [],
                    },
                }
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", CitationLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 1024}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()
    params = {
        "question": "q",
        "synthesis_id": "citation-required-01",
        "ref_ids": ["r1"],
    }
    await manager._step_synthesize(
        session_id="session-1",
        params=params,
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    with pytest.raises(ValueError, match="explicit retry_pending=true"):
        await manager._step_synthesize(
            session_id="session-1",
            params={**params, "resume": True},
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    checkpoint_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"citation-required-01").hexdigest()[:32]
    )
    assert manager.store.refs[checkpoint_ref]["state"]["completed_chunk_ids"] == []
    result = await manager._step_synthesize(
        session_id="session-1",
        params={**params, "resume": True, "retry_pending": True},
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert result["status"] == "complete"
    assert "[1]" in result["answer"]


@pytest.mark.asyncio
async def test_response_is_durable_before_outline_and_chunk_validation(monkeypatch):
    import asyncio

    import agent.session as session_module
    from agent.session import SessionManager

    class Store:
        def __init__(self):
            self.refs = {
                "r1": {
                    "url": "https://example.test/a",
                    "title": "Source",
                    "markdown": "Evidence.",
                }
            }

        async def aget_refs(self, _sid):
            return dict(self.refs)

        async def aadd_refs(self, _sid, refs):
            self.refs.update(refs)
            return True

        async def aappend_step(self, _sid, _step):
            return 1

    class FakeLLM:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        async def generate(self, *, stage, **_kwargs):
            self.__class__.calls += 1
            if stage == "synthesis_outline":
                return json.dumps({"sections": [{"id": "sources", "title": "Sources"}]})
            return json.dumps(
                {
                    "markdown": "Evidence finding [1]",
                    "continuity": {
                        "claims": [],
                        "terminology": [],
                        "transitions": [],
                        "unresolved": [],
                    },
                }
            )

        async def close(self):
            pass

    monkeypatch.setattr("agent.session.LLMClient", FakeLLM)
    monkeypatch.setattr(
        "agent.session._token_budget_params", lambda: {"max_tokens": 1024}
    )
    manager = SessionManager.__new__(SessionManager)
    manager.store = Store()
    params = {
        "question": "q",
        "synthesis_id": "durable-response-01",
        "ref_ids": ["r1"],
    }
    checkpoint_ref = (
        "__synthesis_"
        + __import__("hashlib").sha256(b"durable-response-01").hexdigest()[:32]
    )

    original_validate = session_module.validate_outline

    def interrupt_before_outline_validation(_value):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        session_module, "validate_outline", interrupt_before_outline_validation
    )
    with pytest.raises(asyncio.CancelledError):
        await manager._step_synthesize(
            session_id="session-1",
            params=params,
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    pending = manager.store.refs[checkpoint_ref]["state"]["pending"]
    assert pending["outcome"] == "response_received"
    assert "raw_output" in pending
    monkeypatch.setattr(session_module, "validate_outline", original_validate)
    outline = await manager._step_synthesize(
        session_id="session-1",
        params={**params, "resume": True},
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert outline["status"] == "outline_ready"
    assert FakeLLM.calls == 1

    original_findall = session_module.re.findall

    def interrupt_before_citation_validation(pattern, string, *args, **kwargs):
        if pattern == r"(?<!\w)\[(\d+)\](?!\w)":
            raise asyncio.CancelledError
        return original_findall(pattern, string, *args, **kwargs)

    monkeypatch.setattr(
        session_module.re, "findall", interrupt_before_citation_validation
    )
    with pytest.raises(asyncio.CancelledError):
        await manager._step_synthesize(
            session_id="session-1",
            params={**params, "resume": True},
            llm_base_url="fixture",
            llm_api_key="",
            llm_model="fixture",
        )
    pending = manager.store.refs[checkpoint_ref]["state"]["pending"]
    assert pending["outcome"] == "response_received"
    assert "raw_output" in pending
    monkeypatch.setattr(session_module.re, "findall", original_findall)
    complete = await manager._step_synthesize(
        session_id="session-1",
        params={**params, "resume": True},
        llm_base_url="fixture",
        llm_api_key="",
        llm_model="fixture",
    )
    assert complete["status"] == "complete"
    assert FakeLLM.calls == 2
