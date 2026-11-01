"""Server-owned live adapters with persisted, non-replaying external operations."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from ..admission import get_admission
from ..barrier_guard import is_block_flagged
from ..searxng_client import SearXNGClient
from ..session_store import SessionStore
from ..settings import load_settings
from .durable_plan import plan_queries
from .durable_research import OutcomeUnknownError
from .live_policy import execution_timeout_seconds
from .model_failure import ModelOutputLimitError
from .model_review import ModelReply
from .model_transport import ReviewTransport
from .query_construction import CapturedSource
from .query_runner import run_query
from .source_scope import SourceScope, SourceScopeUnavailableError


async def run_live(record: Any, app: Any):
    """Execute an admitted objective; credentials stay in server-owned clients."""
    ledger = record.durable_ledger
    if ledger is None or record.durable_owner_id is None:
        raise ValueError("live research requires durable ownership")
    admitted = await asyncio.to_thread(ledger.get, record.run_id)
    if admitted is None:
        raise ValueError("admission unavailable")
    evaluated = datetime.fromisoformat(admitted.payload["evaluation_time"])
    model = admitted.payload["model"]
    source_bound_policy = admitted.payload.get("policy_version") in {
        "durable-live/4",
        "durable-live/5",
        "durable-live/6",
    }
    execution_timeout = execution_timeout_seconds(
        admitted.payload.get("policy_version")
    )
    bounded_evidence_policy = admitted.payload.get("policy_version") in {
        "durable-live/3",
        "durable-live/4",
        "durable-live/5",
        "durable-live/6",
    }
    planned_policy = admitted.payload.get("policy_version") in {
        "durable-live/2",
        "durable-live/3",
        "durable-live/4",
        "durable-live/5",
        "durable-live/6",
    }
    scope = SourceScope.model_validate(admitted.payload.get("source_scope") or {})
    selected_sources = tuple(
        CapturedSource(**source)
        for source in admitted.payload.get("selected_sources", [])
    )
    if scope.constrained and any(
        not scope.permits_url(source.url) for source in selected_sources
    ):
        raise SourceScopeUnavailableError(
            "selected evidence is outside the admitted source scope"
        )

    async def validate_selected():
        for source in selected_sources:
            if not source.origin_id or not source.lineage_id:
                raise ValueError("selected source has no retained authority")
            kind, container, reference = source.origin_id.split(":", 2)
            if kind == "root":
                body, _ = await record.artifact_authority.read_evidence(
                    uuid5(
                        NAMESPACE_URL, f"groktocrawl-x:research-scope:{record.scope_id}"
                    ),
                    UUID(container),
                    reference,
                )
            elif kind == "session":
                settings = load_settings()
                store = SessionStore(
                    redis_url=f"redis://{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}"
                )
                try:
                    session = await store.aget(container)
                    if (
                        not session
                        or not session.get("owner_scope")
                        or not admitted.payload.get("session_scope")
                        or session.get("owner_scope")
                        != admitted.payload.get("session_scope")
                    ):
                        raise ValueError(
                            "selected session is unavailable to admitted scope"
                        )
                    ref = await store.aget_ref(container, reference)
                    if not ref:
                        raise ValueError("selected session source is unavailable")
                    pointer = ref.get("research_source")
                    if pointer is not None:
                        if pointer.get("kind") != "root":
                            raise ValueError("invalid linked research source")
                        body, _ = await record.artifact_authority.read_evidence(
                            uuid5(
                                NAMESPACE_URL,
                                f"groktocrawl-x:research-scope:{record.scope_id}",
                            ),
                            UUID(pointer["container_id"]),
                            pointer["ref_id"],
                        )
                        if hashlib.sha256(body).hexdigest() != ref.get(
                            "content_digest"
                        ):
                            raise ValueError("linked research source changed")
                    elif ref.get("markdown"):
                        body = ref["markdown"].encode()
                    else:
                        raise ValueError("selected session source is unavailable")
                finally:
                    await asyncio.to_thread(store.redis.close)
            else:
                raise ValueError("selected source authority is unsupported")
            actual = hashlib.sha256(body).hexdigest()
            if actual != source.lineage_id or body != source.text.encode():
                raise ValueError("selected source changed or failed integrity")

    await validate_selected()

    async def effect(identity, inputs, call):
        encoded = json.dumps(
            inputs, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        input_digest = hashlib.sha256(encoded.encode()).hexdigest()
        replay, output = await asyncio.to_thread(
            ledger.step,
            record.run_id,
            record.durable_owner_id,
            record.durable_generation,
            identity,
            input_digest,
        )
        if replay:
            if identity.startswith("model:") and "model_failure" in output:
                raise ModelOutputLimitError.from_receipt(output)
            return output
        # Only a positively received, sanitized model failure can become a
        # failure receipt. All other exceptions leave dispatch ambiguous.
        try:
            output = await call()
        except asyncio.CancelledError:
            raise
        except ModelOutputLimitError as exc:
            if not identity.startswith("model:"):
                raise OutcomeUnknownError(
                    "external dispatch outcome is unknown"
                ) from exc
            await asyncio.to_thread(
                ledger.step,
                record.run_id,
                record.durable_owner_id,
                record.durable_generation,
                identity,
                input_digest,
                output=exc.receipt(),
                complete=True,
            )
            raise
        except Exception as exc:
            raise OutcomeUnknownError("external dispatch outcome is unknown") from exc
        await asyncio.to_thread(
            ledger.step,
            record.run_id,
            record.durable_owner_id,
            record.durable_generation,
            identity,
            input_digest,
            output=output,
            complete=True,
        )
        return output

    async def search(objective, limit):
        async def fetch():
            search_client = SearXNGClient(
                getattr(app.state, "searxng_url", None)
                or app.state.searxng_client.base_url,
                max_searches=1,
            )
            try:
                rows, health = await search_client.search(
                    objective, limit=limit, raise_on_rate_limit=True
                )
            finally:
                await search_client.close()
            return {
                "urls": list(dict.fromkeys(row["url"] for row in rows))[:limit],
                "empty": health.empty_result,
            }

        identity = (
            "search:" + hashlib.sha256(objective.encode()).hexdigest()
            if planned_policy
            else "search"
        )
        result = await effect(identity, {"objective": objective, "limit": limit}, fetch)
        if (
            (result["empty"] or not result["urls"])
            and not selected_sources
            and not planned_policy
        ):
            raise ValueError("search returned no usable sources")
        return tuple(result["urls"])

    async def acquire(url):
        async def fetch():
            result = await app.state.scraper_client.scrape(url, lightweight_only=True)
            return {
                "response": {
                    "success": result.get("success"),
                    "warning": bool(result.get("warning")),
                    "data": {"markdown": (result.get("data") or {}).get("markdown")},
                },
                "blocked": is_block_flagged(result),
                "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

        result = await effect(
            "acquire:" + hashlib.sha256(url.encode()).hexdigest(), {"url": url}, fetch
        )
        value = result["response"]
        if not value.get("success") or value.get("warning") or result["blocked"]:
            raise ValueError("source acquisition failed or was qualified")
        text = (value.get("data") or {}).get("markdown")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("source acquisition returned no text")
        return CapturedSource(url, text, result["retrieved_at"])

    model_calls = 0
    async with httpx.AsyncClient() as client:
        token_parameter, token_budget = next(
            iter(admitted.payload["token_params"].items())
        )
        completion_budget = min(token_budget, 8192) if bounded_evidence_policy else 2048
        transport = ReviewTransport(
            client,
            base_url=app.state.llm_base_url,
            api_key=app.state.llm_api_key,
            token_parameter=token_parameter,
            token_budget=token_budget,
            # The outer admission deadline bounds all source-bound work together.
            # Do not abandon a valid slow draft at the legacy 90-second limit.
            timeout=execution_timeout if source_bound_policy else 90,
        )

        async def complete(request):
            nonlocal model_calls
            model_calls += 1
            await validate_selected()
            if model_calls > 64:
                raise ValueError("model call budget exhausted")

            async def fetch():
                reply = await transport(request)
                return {**asdict(reply), "content": reply.content.decode("utf-8")}

            admission = get_admission()
            weight = admission.weight_for("llm")
            await admission.acquire("llm", weight=weight)
            try:
                values = await effect(
                    f"model:{model_calls}",
                    {**asdict(request), "payload": request.payload.decode("utf-8")},
                    fetch,
                )
            finally:
                admission.release("llm", weight=weight)
            return ModelReply(
                **{**values, "content": values["content"].encode("utf-8")}
            )

        remaining = (
            admitted.created_at_ms / 1000
            + execution_timeout
            - datetime.now(UTC).timestamp()
        )
        if remaining <= 0:
            raise ValueError("research execution deadline expired")
        async with asyncio.timeout(remaining):
            queries = (
                (record.objective,)
                if scope.mode == "seeds_only"
                else await plan_queries(
                    record.objective,
                    complete=complete,
                    model=model,
                    selected_sources=selected_sources,
                    as_of=admitted.payload.get("as_of"),
                    max_output_tokens=completion_budget,
                )
                if planned_policy and len(selected_sources) < 8
                else None
            )
            return await run_query(
                record.objective,
                search=search,
                acquire=acquire,
                complete=complete,
                scope_id=record.scope_id,
                model=model,
                clock=lambda: evaluated,
                research_id=record.research_id,
                revision_id=str(uuid5(NAMESPACE_URL, f"{record.run_id}:revision")),
                artifact_set_id=str(
                    uuid5(NAMESPACE_URL, f"groktocrawl-x:artifact-set:{record.run_id}")
                ),
                selected_sources=selected_sources,
                seed_urls=scope.seed_urls,
                allowed_hosts=scope.allowed_hosts,
                discover=scope.mode != "seeds_only",
                as_of=admitted.payload.get("as_of"),
                queries=queries,
                source_limit=8 if queries is not None or scope.constrained else 3,
                allow_partial=planned_policy,
                max_output_tokens=completion_budget,
                evidence_budget_chars=32000 if bounded_evidence_policy else None,
                source_bound=source_bound_policy,
                timeout_seconds=execution_timeout,
                model_timeout_seconds=execution_timeout
                if admitted.payload.get("policy_version")
                in {
                    "durable-live/5",
                    "durable-live/6",
                }
                else None,
            )
