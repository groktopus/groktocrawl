"""Deterministic OpenAI-compatible chat completions fixture."""

import json
import logging
import re

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from common.logging import setup_logging
from common.metrics import METRICS
from common.middleware import add_request_id_middleware

logger = logging.getLogger(__name__)

# Marker injected by agent-svc LLM client when output_schema is requested
_SCHEMA_MARKER = "MUST respond with valid JSON matching this schema:"


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
            if key in prop_schema.get("required", []):
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
        if key in schema.get("required", []):
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


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(title="GroktoCrawl LLM Fixture", version="0.1.0")

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

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest):
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

        if req.response_format and req.response_format.get("type") == "json_object":
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
            content = "Synthesized answer from provided context."
        return {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 0,
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    return app


app = create_app()
