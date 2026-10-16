"""Valkey ownership and conservative recovery for accepted background jobs.

The API keeps its existing job IDs and public ``processing`` status. New job
records carry an internal execution marker. A worker claims that marker with a
fencing generation and renews a lease while it runs. After a process restart,
jobs that never left ``queued`` can be dispatched from their stored request;
jobs whose worker had started an external operation are failed as
``OUTCOME_UNKNOWN`` instead of replaying a possibly paid or non-idempotent call.

This module deliberately does not claim exactly-once execution. External work
without an acknowledged checkpoint is never retried automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator, Coroutine
from typing import Any
from uuid import uuid4

from redis import Redis

from .store import _EXECUTION_CONTEXT, _WEBHOOK_STORE_CONTEXT, JobStore
from .tasks import TaskTracker

logger = logging.getLogger(__name__)

LEASE_SECONDS = 45
RECOVERY_INTERVAL_SECONDS = 2
_CLAIM_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return -1 end
local meta = cjson.decode(raw)
if meta.status ~= 'processing' then return 0 end
local e = meta.execution
if type(e) ~= 'table' or e.version ~= 1 then return -2 end
if e.state ~= 'queued' then return 0 end
local fence = tonumber(e.fence or 0) + 1
e.fence = fence
e.state = 'running'
e.lease_owner = ARGV[1]
e.lease_expires_at = tonumber(ARGV[2])
e.started_at = ARGV[3]
e.pending_operation = 'worker_execution'
e.checkpoint = {kind='worker_started', at=ARGV[3]}
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
return fence
"""
_HEARTBEAT_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
local e = meta.execution
if (meta.status ~= 'processing' and meta.status ~= 'retry_scheduled') or type(e) ~= 'table' then return 0 end
if e.lease_owner ~= ARGV[1] or tonumber(e.fence) ~= tonumber(ARGV[2]) then return 0 end
e.lease_expires_at = tonumber(ARGV[3])
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
return 1
"""
_RELEASE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
local e = meta.execution
if type(e) ~= 'table' then return 0 end
if e.lease_owner ~= ARGV[1] or tonumber(e.fence) ~= tonumber(ARGV[2]) then return 0 end
e.lease_expires_at = 0
e.lease_owner = nil
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
return 1
"""
_FAIL_UNKNOWN_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local meta = cjson.decode(raw)
if meta.status ~= 'processing' and meta.status ~= 'retry_scheduled' then return 0 end
local e = meta.execution
if ARGV[1] == 'stale' then
  if type(e) ~= 'table' or e.state ~= 'running' or tonumber(e.lease_expires_at or 0) > tonumber(ARGV[2]) then return 0 end
elseif ARGV[1] == 'owner' then
  if type(e) ~= 'table' or e.lease_owner ~= ARGV[3] or tonumber(e.fence) ~= tonumber(ARGV[4]) then return 0 end
else
  if type(e) == 'table' then return 0 end
end
meta.status = 'failed'
meta.error = ARGV[5]
meta.error_code = 'OUTCOME_UNKNOWN'
meta.completed_at = ARGV[6]
if type(e) == 'table' then
  e.state = 'outcome_unknown'
  e.pending_operation = nil
  e.lease_owner = nil
  e.lease_expires_at = 0
  e.failure_code = 'OUTCOME_UNKNOWN'
end
redis.call('SET', KEYS[1], cjson.encode(meta), 'KEEPTTL')
if ARGV[8] ~= '' and not redis.call('GET', KEYS[3]) then
  redis.call('HSET', KEYS[2], ARGV[9], ARGV[10])
  redis.call('SET', KEYS[3], ARGV[9], 'EX', ARGV[7])
  redis.call('EXPIRE', KEYS[2], ARGV[7])
end
return 1
"""


class JobExecutionCoordinator:
    """Fenced in-process executor with a durable restart scan."""

    def __init__(
        self,
        store: JobStore,
        task_tracker: TaskTracker,
        app: Any,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self.store = store
        self.redis: Redis = store.redis
        self.task_tracker = task_tracker
        self.app = app
        self.lease_seconds = lease_seconds

    @staticmethod
    def _key(job_id: str) -> str:
        return f"job:{job_id}:meta"

    def claim(self, job_id: str) -> tuple[str, int] | None:
        owner = str(uuid4())
        now = time.time()
        fence = self.redis.eval(
            _CLAIM_SCRIPT,
            1,
            self._key(job_id),
            owner,
            now + self.lease_seconds,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        )
        if not isinstance(fence, int) or fence <= 0:
            return None
        return owner, fence

    def heartbeat(self, job_id: str, owner: str, fence: int) -> bool:
        result = self.redis.eval(
            _HEARTBEAT_SCRIPT,
            1,
            self._key(job_id),
            owner,
            fence,
            time.time() + self.lease_seconds,
        )
        return result == 1

    def release(self, job_id: str, owner: str, fence: int) -> None:
        self.redis.eval(_RELEASE_SCRIPT, 1, self._key(job_id), owner, fence)

    def mark_outcome_unknown(
        self,
        job_id: str,
        reason: str,
        *,
        stale: bool = False,
        owner: str | None = None,
        fence: int | None = None,
    ) -> bool:
        record = self.store.get_job(job_id) or {}
        reason_event = {
            "error": reason,
            "error_code": "OUTCOME_UNKNOWN",
        }
        from .webhook import build_webhook_intent

        webhook_event = build_webhook_intent(
            (record.get("payload") or {}).get("webhook"),
            "failed",
            job_id,
            reason_event,
            success=False,
            error=reason,
        )
        mode = "stale" if stale else "owner" if owner is not None else "legacy"
        result = self.redis.eval(
            _FAIL_UNKNOWN_SCRIPT,
            3,
            self._key(job_id),
            self.store._webhook_key(job_id),
            f"job:{job_id}:webhook-fingerprint:{webhook_event['fingerprint']}"
            if webhook_event
            else f"job:{job_id}:webhook-fingerprint:none",
            mode,
            time.time(),
            owner or "",
            fence or 0,
            reason,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            86400,
            webhook_event["fingerprint"] if webhook_event else "",
            webhook_event["event"]["webhookId"] if webhook_event else "",
            json.dumps(webhook_event["event"]) if webhook_event else "",
        )
        return result == 1

    def submit(self, job_id: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run one accepted job under a fenced lease."""
        return self.task_tracker.create_background_task(
            self._run(job_id, coro), job_id=job_id
        )

    async def stream(
        self, job_id: str, events: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """Run an inline SSE job under the same lease as polling jobs."""
        claim = await asyncio.to_thread(self.claim, job_id)
        if claim is None:
            await events.aclose()
            yield 'data: {"type":"error","error":"job execution is already owned or unavailable"}\n\n'
            return
        owner, fence = claim
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id, owner, fence))
        context_token = _EXECUTION_CONTEXT.set((job_id, owner, fence))
        webhook_token = _WEBHOOK_STORE_CONTEXT.set(self.store)
        try:
            async for event in events:
                yield event
        finally:
            record = self.store.get_job(job_id)
            if record and record.get("status") in ("processing", "retry_scheduled"):
                await asyncio.to_thread(
                    self.mark_outcome_unknown,
                    job_id,
                    "Streaming job ended without a terminal result; external outcome is unknown.",
                    owner=owner,
                    fence=fence,
                )
            _EXECUTION_CONTEXT.reset(context_token)
            _WEBHOOK_STORE_CONTEXT.reset(webhook_token)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.to_thread(self.release, job_id, owner, fence)

    async def _run(self, job_id: str, coro: Coroutine[Any, Any, Any]) -> None:
        claim = await asyncio.to_thread(self.claim, job_id)
        if claim is None:
            coro.close()
            return
        owner, fence = claim
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id, owner, fence))
        context_token = _EXECUTION_CONTEXT.set((job_id, owner, fence))
        webhook_token = _WEBHOOK_STORE_CONTEXT.set(self.store)
        try:
            await coro
            record = self.store.get_job(job_id)
            if record and record.get("status") == "processing":
                await asyncio.to_thread(
                    self.mark_outcome_unknown,
                    job_id,
                    "Worker stopped without a terminal result; external outcome is unknown.",
                    owner=owner,
                    fence=fence,
                )
        except asyncio.CancelledError:
            record = self.store.get_job(job_id)
            if record and record.get("status") == "processing":
                await asyncio.to_thread(
                    self.mark_outcome_unknown,
                    job_id,
                    "Worker was interrupted; external outcome is unknown and was not replayed.",
                    owner=owner,
                    fence=fence,
                )
            raise
        except Exception:
            logger.exception("Background job executor failed for %s", job_id)
            await asyncio.to_thread(
                self.mark_outcome_unknown,
                job_id,
                "Worker executor failed before recording a terminal result; external outcome is unknown.",
                owner=owner,
                fence=fence,
            )
        finally:
            _EXECUTION_CONTEXT.reset(context_token)
            _WEBHOOK_STORE_CONTEXT.reset(webhook_token)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.to_thread(self.release, job_id, owner, fence)

    async def _heartbeat_loop(self, job_id: str, owner: str, fence: int) -> None:
        while True:
            await asyncio.sleep(max(1, self.lease_seconds // 3))
            if not await asyncio.to_thread(self.heartbeat, job_id, owner, fence):
                logger.warning("Lost execution lease for job %s", job_id)
                return

    async def recover_queued_jobs(self) -> None:
        """Dispatch never-started records and fail closed on interrupted work."""
        for record in await asyncio.to_thread(self.store.list_processing_jobs):
            execution = record.get("execution")
            job_id = str(record.get("id", ""))
            if not job_id:
                continue
            if not isinstance(execution, dict) or execution.get("version") != 1:
                changed = await asyncio.to_thread(
                    self.mark_outcome_unknown,
                    job_id,
                    "Job predates durable ownership; its external outcome is unknown and it was not replayed.",
                )
                if changed:
                    logger.error(
                        "Marked legacy interrupted job %s outcome unknown", job_id
                    )
                continue
            if record.get("status") == "retry_scheduled":
                await asyncio.to_thread(
                    self.mark_outcome_unknown,
                    job_id,
                    "Job was interrupted between attempts; retry was not replayed because the prior external outcome cannot be proven.",
                    stale=True,
                )
                continue
            if execution.get("state") == "queued":
                try:
                    coro = self._rebuild(record)
                except (KeyError, TypeError, ValueError) as exc:
                    await asyncio.to_thread(
                        self.mark_outcome_unknown,
                        job_id,
                        f"Queued job cannot be reconstructed safely ({type(exc).__name__}); submit it again explicitly.",
                    )
                    continue
                self.submit(job_id, coro)
                continue
            if execution.get("state") == "running":
                expires = execution.get("lease_expires_at") or 0
                if float(expires) <= time.time():
                    changed = await asyncio.to_thread(
                        self.mark_outcome_unknown,
                        job_id,
                        "Worker lease expired after an external operation began; outcome is unknown and the operation was not replayed.",
                        stale=True,
                    )
                    if changed:
                        logger.error(
                            "Marked interrupted job %s outcome unknown", job_id
                        )

    def _rebuild(self, record: dict[str, Any]) -> Coroutine[Any, Any, Any]:
        """Reconstruct a worker only when its durable state proves it never ran."""
        from . import worker

        job_id = str(record["id"])
        kind = record.get("kind")
        payload = record.get("payload") or {}
        webhook = payload.get("webhook")
        if kind == "crawl":
            return worker._process_crawl_async(
                job_id=job_id,
                url=payload["url"],
                max_pages=payload.get(
                    "effective_max_pages", payload.get("max_pages", 0)
                ),
                max_depth=payload.get("max_depth", 2),
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
                task_tracker=self.task_tracker,
                ignore_query_parameters=payload.get("ignore_query_parameters", False),
                include_paths=payload.get("include_paths"),
                exclude_paths=payload.get("exclude_paths"),
                regex_on_full_url=payload.get("regex_on_full_url", False),
                verbose=payload.get("verbose", False),
                sitemap_mode=payload.get("sitemap", "include"),
                crawl_entire_domain=payload.get("crawl_entire_domain", False),
                allow_subdomains=payload.get("allow_subdomains", False),
                allow_external_links=payload.get("allow_external_links", False),
                max_concurrency=payload.get("max_concurrency", 3),
                delay=payload.get("delay"),
                ignore_robots_txt=payload.get("ignore_robots_txt", False),
                robots_user_agent=payload.get("robots_user_agent"),
                scrape_options=payload.get("scrape_options"),
            )
        if kind == "agent":
            from .models import AgentRequest
            from .routes.agent import fingerprint_from_agent_request

            body = AgentRequest.model_validate(payload)
            return worker._process_agent_async(
                job_id=job_id,
                prompt=body.prompt,
                urls=body.urls,
                schema_=body.output_schema or body.schema_,
                llm_base_url=self.app.state.llm_base_url,
                llm_api_key=self.app.state.llm_api_key,
                llm_model=self.app.state.llm_model,
                searxng_url=self.app.state.searxng_url,
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
                requested_model=body.model,
                include_images=body.include_images,
                include_source_content=body.include_source_content,
                citation_style=body.citation_style,
                force_fresh=body.force_fresh,
                stale_while_revalidate=body.stale_while_revalidate,
                max_stale_hours=body.max_stale_hours,
                user_id=payload.get("recovery_user_id"),
                research_memory=self.app.state.research_memory,
                search_type=body.search_type,
                max_searches_per_request=self.app.state.max_searches_per_request,
                max_credits=body.max_credits,
                fingerprint=fingerprint_from_agent_request(body),
                task_tracker=self.task_tracker,
                evidence_budget_chars=body.evidence_budget_chars,
            )
        if kind == "batch_scrape":
            return worker._process_batch_scrape_async(
                job_id=job_id,
                urls=payload["urls"],
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
                task_tracker=self.task_tracker,
                max_concurrency=payload.get("max_concurrency", 3),
            )
        if kind == "extract":
            return worker._process_extract_async(
                job_id=job_id,
                urls=payload["urls"],
                prompt=payload.get("prompt"),
                schema_=payload.get("schema"),
                llm_base_url=self.app.state.llm_base_url,
                llm_api_key=self.app.state.llm_api_key,
                llm_model=self.app.state.llm_model,
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
            )
        if kind == "llmstxt":
            return worker._process_llmstxt_async(
                job_id=job_id,
                url=payload["url"],
                max_pages=payload.get("max_pages", 10),
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
            )
        if kind == "plan_execute":
            return worker._process_plan_execution_async(
                job_id=job_id,
                prompt=payload["prompt"],
                plan=payload["plan"],
                modifications=payload.get("modifications"),
                llm_base_url=self.app.state.llm_base_url,
                llm_api_key=self.app.state.llm_api_key,
                llm_model=self.app.state.llm_model,
                searxng_url=self.app.state.searxng_url,
                scraper_url=self.app.state.scraper_url,
                webhook_config=webhook,
            )
        raise ValueError(f"unsupported background job kind: {kind!r}")


async def recover_legacy_jobs(app: Any, shutdown_event: asyncio.Event) -> None:
    """Install the executor and scan queued or interrupted jobs until shutdown."""
    coordinator = JobExecutionCoordinator(
        app.state.job_store,
        app.state.task_tracker,
        app,
    )
    app.state.job_executor = coordinator
    while not shutdown_event.is_set():
        from .webhook import replay_pending_webhooks

        await replay_pending_webhooks(coordinator.store, coordinator.task_tracker)
        await coordinator.recover_queued_jobs()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=RECOVERY_INTERVAL_SECONDS
            )


async def stop_legacy_jobs(app: Any) -> None:
    """The shared TaskTracker drains accepted tasks before this hook runs."""
    app.state.job_executor = None


def submit_job(request: Any, job_id: str, coro: Coroutine[Any, Any, Any]) -> Any:
    """Submit through the durable owner when app lifespan has started."""
    executor = getattr(request.app.state, "job_executor", None)
    if executor is not None:
        return executor.submit(job_id, coro)
    return request.app.state.task_tracker.create_background_task(coro, job_id=job_id)


def stream_job(
    request: Any, job_id: str, events: AsyncGenerator[str, None]
) -> AsyncGenerator[str, None]:
    """Apply ownership to inline crawl SSE while retaining test-app fallback."""
    executor = getattr(request.app.state, "job_executor", None)
    if executor is not None:
        return executor.stream(job_id, events)

    async def passthrough() -> AsyncGenerator[str, None]:
        async for event in events:
            yield event

    return passthrough()
