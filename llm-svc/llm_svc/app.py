"""Deterministic OpenAI-compatible chat completions fixture."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel

from common.logging import setup_logging
from common.metrics import METRICS
from common.middleware import add_request_id_middleware

logger = logging.getLogger(__name__)

# Marker injected by agent-svc LLM client when output_schema is requested
_SCHEMA_MARKER = "MUST respond with valid JSON matching this schema:"
SCHEMA_VERSION = "v1"
FIXTURE_VERSION = "v2"
MAX_DELAY_MS = 2_000
MAX_DIAGNOSTICS = 200
SCENARIOS = {
    "default",
    "streaming",
    "stream-malformed",
    "stream-truncated",
    "stream-refusal",
    "delayed",
    "timeout",
    "rate-limit",
    "server-error",
    "malformed-json",
    "schema-invalid",
    "truncated",
    "empty",
    "refusal",
    "contradictory",
    "citation-free",
    "echo",
    # Grounded-answer eval scenarios (#570): additive, deterministic.
    "grounded-answer",
    "contradictory-evidence",
    "miscited",
}


@dataclass
class FixtureState:
    """Process-local diagnostics, deliberately excluding prompt and secret data."""

    entries: list[dict[str, object]] = field(default_factory=list)
    request_number: int = 0

    def record(self, **entry: object) -> None:
        self.request_number += 1
        self.entries.append(
            {
                "request_id": self.request_number,
                "schema_version": SCHEMA_VERSION,
                **entry,
            }
        )
        del self.entries[:-MAX_DIAGNOSTICS]


def _resolve_type(prop_schema: dict) -> str:
    """Return the canonical type string, handling type arrays like ["string","null"]."""
    t = prop_schema.get("type", "string")
    if isinstance(t, list):
        for item in t:
            if item != "null":
                return item
        return "string"
    return t


def _dummy_value(prop_schema: dict) -> object:
    """Build a dummy value that satisfies *prop_schema*."""
    t = _resolve_type(prop_schema)
    if t == "string":
        if "enum" in prop_schema:
            return prop_schema["enum"][0]
        return "value"
    if t == "array":
        items = prop_schema.get("items", {"type": "string"})
        if isinstance(items, list):
            # Tuple-style validation: each element validates a position
            return [
                _dummy_value(item) if isinstance(item, dict) else "value"
                for item in items
            ]
        return [_dummy_value(items), _dummy_value(items)]
    if t == "object":
        obj = {}
        for key, subschema in prop_schema.get("properties", {}).items():
            obj[key] = _dummy_value(subschema)
        return obj
    if t in ("integer", "number"):
        return 42
    if t == "boolean":
        return True
    return "value"


def _generate_schema_response(system_text: str) -> str:
    """Parse the JSON Schema from the system prompt and return a conformant response."""
    try:
        idx = system_text.index(_SCHEMA_MARKER)
        schema_json = system_text[idx + len(_SCHEMA_MARKER) :].strip()
        schema = json.loads(schema_json)
    except (ValueError, json.JSONDecodeError):
        return json.dumps({"result": "structured response"})

    if schema.get("type") != "object" or "properties" not in schema:
        return json.dumps({"result": "structured response"})

    response: dict = {}
    for key, prop in schema.get("properties", {}).items():
        response[key] = _dummy_value(prop)

    return json.dumps(response)


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict | None = None
    stream: bool = False


def create_app() -> FastAPI:
    setup_logging()

    state = FixtureState()
    app = FastAPI(title="GroktoCrawl LLM Fixture", version=FIXTURE_VERSION)

    # Register a basic metric so /metrics output has content
    METRICS.counter(
        "chat_completions_total", "Total chat completion requests", ["status"]
    )

    # Request-ID tracing middleware (skips /health and /metrics)
    def _record_metric(labels: dict[str, str], value: float) -> None:
        METRICS.histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path"],
        ).observe(labels, value)

    add_request_id_middleware(app, record_metric=_record_metric)

    logger.info("llm-svc starting up", extra={"extra_fields": {"service": "llm-svc"}})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return PlainTextResponse(
            content=METRICS.generate_openmetrics(),
            media_type="application/openmetrics-text; version=1.0.0",
        )

    @app.get("/diagnostics")
    async def diagnostics(run_id: str | None = None) -> dict[str, object]:
        entries = state.entries
        if run_id:
            entries = [entry for entry in entries if entry.get("run_id") == run_id]
        return {
            "schema_version": SCHEMA_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "entries": entries,
        }

    @app.post("/diagnostics/reset")
    async def reset_diagnostics(run_id: str | None = None) -> dict[str, object]:
        if run_id is None:
            state.entries.clear()
        else:
            state.entries[:] = [
                entry for entry in state.entries if entry.get("run_id") != run_id
            ]
        return {"status": "ok"}

    async def _chat_completions(
        req: ChatCompletionRequest,
        scenario: str,
        scenario_version: str = Query(default=SCHEMA_VERSION),
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
        chunks: int = Query(default=3, ge=1, le=32),
        run_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$"),
    ):
        if scenario_version != SCHEMA_VERSION:
            return JSONResponse(
                {"error": "unsupported fixture scenario version"}, status_code=400
            )
        if scenario not in SCENARIOS:
            return JSONResponse({"error": "unknown fixture scenario"}, status_code=400)

        effective_delay = max(
            delay_ms, 200 if scenario in {"delayed", "timeout"} else 0
        )
        try:
            if effective_delay:
                await asyncio.sleep(effective_delay / 1000)
        except asyncio.CancelledError:
            state.record(
                scenario=scenario,
                scenario_version=SCHEMA_VERSION,
                fixture_version=FIXTURE_VERSION,
                run_id=run_id,
                status=499,
                classification="cancelled",
                model=req.model,
                stream=req.stream,
            )
            raise

        state.record(
            scenario=scenario,
            scenario_version=SCHEMA_VERSION,
            fixture_version=FIXTURE_VERSION,
            run_id=run_id,
            status=200,
            classification="success",
            model=req.model,
            stream=req.stream,
        )
        classifications = {
            "delayed": "delayed",
            "timeout": "delayed",
            "schema-invalid": "schema_invalid",
            "truncated": "truncated",
            "empty": "empty",
            "refusal": "refusal",
            "contradictory": "contradictory",
            "citation-free": "citation_free",
            "stream-malformed": "malformed_stream",
            "stream-truncated": "truncated_stream",
        }
        if scenario in classifications:
            state.entries[-1]["classification"] = classifications[scenario]
        if scenario == "rate-limit":
            state.entries[-1].update(status=429, classification="rate_limited")
            return JSONResponse(
                {"error": "fixture rate limited", "scenario": scenario},
                status_code=429,
                headers={"Retry-After": "2"},
            )
        if scenario == "server-error":
            state.entries[-1].update(status=503, classification="upstream_error")
            return JSONResponse({"error": "fixture unavailable"}, status_code=503)
        if scenario == "malformed-json":
            state.entries[-1].update(status=200, classification="malformed_envelope")
            return Response(
                "{malformed", status_code=200, media_type="application/json"
            )

        def text(message: ChatMessage) -> str:
            if isinstance(message.content, str):
                return message.content
            return "\n".join(
                item.get("text", "")
                for item in message.content
                if item.get("type") == "text"
            )

        user_text = "\n".join(text(m) for m in req.messages if m.role == "user")
        system_text = "\n".join(text(m) for m in req.messages if m.role == "system")

        if scenario == "schema-invalid":
            content = json.dumps({"unexpected": True})
        elif scenario == "truncated":
            content = "The completion was truncated"
        elif scenario == "empty":
            content = ""
        elif scenario == "refusal":
            content = None
        elif scenario == "contradictory":
            content = "The source says yes. The source says no."
        elif scenario == "citation-free":
            content = "Synthesized answer without source citations."
        elif scenario == "grounded-answer":
            # Grounded-answer eval scenario (#570): a known fixture fact with a
            # [1] citation marker resolved by the agent's citation post-processing.
            content = "The Fixture Site pricing page states the Pro plan costs $10 per month. [1]"
        elif scenario == "contradictory-evidence":
            # Abstain only when the supplied context contains both conflicting
            # fixture claims; the scenario alone cannot manufacture conflict.
            if "Pro: $10" in user_text and "Pro: $99" in user_text:
                content = (
                    "The retrieved sources conflict: the pricing page says Pro costs $10 "
                    "while the pricing-v2 page says Pro costs $99. Because the evidence "
                    "is contradictory, I cannot provide a confident answer."
                )
            else:
                content = "The supplied evidence is consistent."
        elif scenario == "miscited":
            # Grounded-answer eval scenario (#570): deliberately cites index [2]
            # for a claim that only appears in source [1], so the citation-support
            # grader must fail.
            content = "The Fixture Site Pro plan costs $10 per month. [2]"
        elif scenario == "echo":
            content = json.dumps(
                {
                    "model": req.model,
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens,
                }
            )
        elif req.response_format and req.response_format.get("type") == "json_object":
            if "Select fixture tiles" in user_text:
                content = json.dumps({"tiles": [0, 4, 8], "submit": True})
            # Handle recovery prompts — extract iframe URLs from page content
            elif (
                iframe_match := re.search(r'<iframe[^>]+src="([^"]+)"', user_text)
            ) and ("iframe_url" in system_text or "recovery" in system_text.lower()):
                content = json.dumps(
                    {
                        "action": "iframe_url",
                        "url": iframe_match.group(1),
                    }
                )
            elif "cloudflare" in system_text.lower() or "block_type" in system_text:
                content = json.dumps(
                    {
                        "block_type": "js_challenge",
                        "confidence": "medium",
                        "page_indicators": ["challenge platform detected"],
                        "alternative_paths": [],
                        "human_action_required": False,
                        "message": "Cloudflare JS challenge detected — could not bypass with available tools",
                    }
                )
            else:
                content = _generate_schema_response(system_text)
        else:
            citation_requested = (
                "cite sources" in (system_text + "\n" + user_text).lower()
            )
            content = (
                "Synthesized answer from the provided source context. [1]"
                if citation_requested
                else "Synthesized answer from provided context."
            )
        response: dict[str, Any] = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 0,
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "length" if scenario == "truncated" else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
        if scenario == "refusal":
            response["choices"][0]["message"]["refusal"] = "fixture refusal"
        if not req.stream or scenario not in {
            "default",
            "streaming",
            "stream-malformed",
            "stream-truncated",
            "stream-refusal",
        }:
            return response

        text_content = response["choices"][0]["message"]["content"]
        boundaries = max(1, min(chunks, len(text_content) or 1))
        step = max(1, (len(text_content) + boundaries - 1) // boundaries)

        async def events():
            if scenario == "stream-malformed":
                yield "data: {not-json\n\n"
            if scenario == "stream-refusal":
                refusal_chunk = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"refusal": "fixture refusal"},
                        }
                    ],
                }
                yield f"data: {json.dumps(refusal_chunk)}\n\n"
            for index in range(0, len(text_content), step):
                payload = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text_content[index : index + step]},
                        }
                    ],
                }
                yield f"data: {json.dumps(payload)}\n\n"
            if scenario == "stream-truncated":
                limit_chunk = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 42,
                        "completion_tokens": 8192,
                    },
                }
                yield f"data: {json.dumps(limit_chunk)}\n\n"
            else:
                yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/v1/scenarios/{scenario}/chat/completions")
    async def scenario_chat_completions(
        scenario: str,
        req: ChatCompletionRequest,
        scenario_version: str = Query(default=SCHEMA_VERSION),
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
        chunks: int = Query(default=3, ge=1, le=32),
        run_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$"),
    ):
        return await _chat_completions(
            req, scenario, scenario_version, delay_ms, chunks, run_id
        )

    @app.post("/v1/chat/completions")
    async def default_chat_completions(
        req: ChatCompletionRequest,
        scenario: str = Query(default="default"),
        scenario_version: str = Query(default=SCHEMA_VERSION),
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
        chunks: int = Query(default=3, ge=1, le=32),
        run_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$"),
    ):
        return await _chat_completions(
            req, scenario, scenario_version, delay_ms, chunks, run_id
        )

    return app


app = create_app()
