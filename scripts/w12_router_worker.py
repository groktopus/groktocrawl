"""Isolated installed Router process: no proxy startup, listener or database client.

Original identity is used only for a normal-auth self-policy read. Provider
credentials remain in the existing gateway process environment/configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

POLICY_ORIGIN = "http://127.0.0.1:4000"
CONFIG = Path("/app/config.yaml")
EMPTY_KEY_FIELDS = (
    "max_budget",
    "budget_duration",
    "tpm_limit",
    "rpm_limit",
    "max_parallel_requests",
    "blocked",
    "expires",
    "soft_budget_cooldown",
    "budget_id",
    "budget_limits",
    "budget_fallbacks",
    "policies",
    "access_group_ids",
    "agent_id",
    "aliases",
    "allowed_routes",
    "allowed_cache_controls",
    "config",
    "router_settings",
    "metadata",
    "permissions",
    "jwt_key_mappings",
    "model_max_budget",
    "litellm_budget_table",
    "litellm_organization_table",
    "litellm_project_table",
    "object_permission",
    "object_permission_id",
    "team_id",
    "organization_id",
    "project_id",
)
EMPTY_USER_FIELDS = (
    "max_budget",
    "budget_duration",
    "tpm_limit",
    "rpm_limit",
    "metadata",
    "model_max_budget",
    "object_permission",
    "object_permission_id",
    "teams",
    "max_parallel_requests",
    "organization_id",
    "organization_memberships",
    "policies",
    "allowed_cache_controls",
    "team_id",
    "litellm_organization_table",
    "password",
)


KNOWN_KEY_FIELDS = set(EMPTY_KEY_FIELDS) | {
    "auto_rotate",
    "budget_reset_at",
    "created_at",
    "created_by",
    "key_alias",
    "key_name",
    "key_rotation_at",
    "key_type",
    "last_active",
    "last_rotation_at",
    "model_spend",
    "models",
    "rotation_count",
    "rotation_interval",
    "settings_updated_at",
    "soft_budget_cooldown",
    "spend",
    "team_alias",
    "token",
    "updated_at",
    "updated_by",
    "user_id",
}
KNOWN_USER_FIELDS = set(EMPTY_USER_FIELDS) | {
    "budget_reset_at",
    "created_at",
    "model_spend",
    "models",
    "password",
    "spend",
    "sso_user_id",
    "updated_at",
    "user_alias",
    "user_email",
    "user_id",
    "user_role",
    "model_max_budget_usage",
    "invitations_created",
    "invitations_updated",
    "invitations_user",
}


class PolicyRefusedError(ValueError):
    """No provider dispatch is allowed after a failed or changed policy read."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def verify_original_identity(client_key: str, expected_fingerprint: str) -> None:
    if not hmac.compare_digest(
        hashlib.sha256(client_key.encode()).hexdigest(), expected_fingerprint
    ):
        raise PolicyRefusedError("original client identity changed")


def policy_snapshot(report: dict, client_key: str, user_binding: str) -> dict:
    """Correlate the current key internally; never return identity/credential data."""
    user = report.get("user_info")
    if (
        not isinstance(user, dict)
        or report.get("user_id") != user_binding
        or user.get("user_id") != user_binding
    ):
        raise PolicyRefusedError("user binding changed")
    token_hash = hashlib.sha256(client_key.encode()).hexdigest()
    matches = [
        entry
        for entry in report.get("keys", [])
        if isinstance(entry, dict) and entry.get("token") == token_hash
    ]
    if len(matches) != 1:
        raise PolicyRefusedError("current key missing or ambiguous")
    key = matches[0]
    if key.get("user_id") != user_binding or key.get("key_type") not in (
        None,
        "default",
    ):
        raise PolicyRefusedError("key binding or type changed")
    if set(key) - KNOWN_KEY_FIELDS or set(user) - KNOWN_USER_FIELDS:
        raise PolicyRefusedError(
            "unknown policy report schema: key="
            + ",".join(sorted(set(key) - KNOWN_KEY_FIELDS))
            + ";user="
            + ",".join(sorted(set(user) - KNOWN_USER_FIELDS))
        )
    for entry, fields in ((key, EMPTY_KEY_FIELDS), (user, EMPTY_USER_FIELDS)):

        def empty(name, entry=entry):
            value = entry.get(name)
            return (
                value is None
                or (name in {"blocked", "soft_budget_cooldown"} and value is False)
                or (type(value) in (list, dict) and not value)
            )

        if any(not empty(name) for name in fields):
            raise PolicyRefusedError(
                "a restricted policy needs a new reviewed profile: "
                + ",".join(name for name in fields if not empty(name))
            )
        models = entry.get("models")
        if not isinstance(models, list) or "free" not in models:
            raise PolicyRefusedError("free model not explicitly allowed")
    # Freeze access-relevant policy, excluding usage, timestamps and personal data.
    return {
        "key_model_policy_sha256": digest(key["models"]),
        "user_model_policy_sha256": digest(user["models"]),
        "key_type": key.get("key_type"),
        "user_role": user.get("user_role"),
        "empty_key_fields": list(EMPTY_KEY_FIELDS),
        "empty_user_fields": list(EMPTY_USER_FIELDS),
        "user_binding_sha256": hashlib.sha256(user_binding.encode()).hexdigest(),
    }


async def policy_read(path: str, client_key: str, events: list, *, client=None) -> dict:
    """One nonredirecting read; never log a key, identity, URL or raw response."""
    owned = client is None
    client = client or httpx.AsyncClient(
        timeout=12,
        follow_redirects=False,
        trust_env=False,
        transport=httpx.AsyncHTTPTransport(retries=0),
    )
    event: dict[str, Any] = {"kind": "policy_http", "state": "reserved"}
    events.append(event)
    try:
        async with client.stream(
            "GET",
            POLICY_ORIGIN + path,
            headers={"Authorization": "Bearer " + client_key},
        ) as response:
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                if length > 1024 * 1024:
                    raise PolicyRefusedError("policy response byte cap")
                chunks.append(chunk)
            event.update(
                state="response_complete",
                status_code=response.status_code,
                bytes=length,
            )
            if response.status_code != 200:
                raise PolicyRefusedError("policy verification refused or unavailable")
            result = json.loads(b"".join(chunks))
            if not isinstance(result, dict):
                raise PolicyRefusedError("invalid policy report")
            return result
    finally:
        if owned:
            await client.aclose()


async def describe_policy(client_key: str) -> dict:
    events: list[dict] = []
    key = await policy_read("/key/info", client_key, events)
    binding = key.get("info", {}).get("user_id")
    if not isinstance(binding, str) or not binding:
        raise PolicyRefusedError("user-bound identity required")
    report = await policy_read(
        "/user/info?" + urlencode({"user_id": binding}), client_key, events
    )
    snapshot = policy_snapshot(report, client_key, binding)
    return {
        "_private_user_binding": binding,
        "_private_key_fingerprint": hashlib.sha256(client_key.encode()).hexdigest(),
        "policy_snapshot": snapshot,
        "policy_snapshot_sha256": digest(snapshot),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "isolated_mode": "router_process",
        "effective_drop_params": False,
        "policy_http_per_model": 1,
        "provider_http_per_model": 1,
        "model_gateway_http_per_model": 0,
        "concurrency_scope": "process_local",
        "policy_events": events,
    }


class ProviderBoundary(httpx.AsyncBaseTransport):
    """Check the final merged SDK body; at most one actual provider HTTP send."""

    def __init__(self, inner, endpoint: str, events: list):
        self.inner, self.endpoint, self.events = inner, endpoint, events
        self.sent = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.sent or str(request.url) != self.endpoint or request.method != "POST":
            raise PolicyRefusedError("provider retry or route refused")
        body = json.loads(request.content)
        if set(body) - {
            "model",
            "messages",
            "max_tokens",
            "temperature",
            "response_format",
            "tags",
            "stream",
        }:
            raise PolicyRefusedError("unsupported merged provider options")
        if body.get("model") != "ornith/ornith-1.5-122b:free":
            raise PolicyRefusedError("merged provider model changed")
        if (
            body.get("max_tokens") != 256
            or body.get("temperature") != 0.3
            or body.get("stream")
        ):
            raise PolicyRefusedError("merged provider budget or sampler changed")
        if body.get("tags") != ["user=omniroute"] or any(
            k in body for k in ("max_completion_tokens", "thinking", "enable_thinking")
        ):
            raise PolicyRefusedError("merged provider configuration changed")
        if len(request.content) > 12000:
            raise PolicyRefusedError("merged provider request byte cap")
        self.sent += 1
        event = {
            "kind": "provider_http",
            "state": "reserved",
            "output_token_reservation": 256,
            "input_bytes": len(request.content),
            "request_sha256": hashlib.sha256(request.content).hexdigest(),
        }
        self.events.append(event)
        response = await self.inner.handle_async_request(request)
        chunks, length = [], 0
        async for chunk in response.aiter_bytes():
            length += len(chunk)
            if length > 1024 * 1024:
                raise PolicyRefusedError("provider response byte cap")
            chunks.append(chunk)
        await response.aclose()
        event.update(
            state="response_complete", status_code=response.status_code, bytes=length
        )
        headers = response.headers.copy()
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=b"".join(chunks),
            request=request,
        )

    async def aclose(self):
        await self.inner.aclose()


async def run_model(packet: dict) -> dict:
    """Each RPC gets a fresh detached Router, policy verification and one SDK send."""
    verify_original_identity(packet["_client_key"], packet["_original_key_fingerprint"])
    # Import-time model-price download and telemetry are not part of this pilot.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import copy
    import inspect

    import litellm
    import yaml
    from litellm import Router
    from litellm.proxy.route_llm_request import route_request
    from openai import AsyncOpenAI

    started = time.monotonic()
    events: list[dict] = packet.setdefault("_router_events", [])
    key, binding = packet["_client_key"], packet["_user_binding"]
    frozen = packet["_router_freeze"]
    config_bytes = CONFIG.read_bytes()
    if hashlib.sha256(config_bytes).hexdigest() != frozen["config_sha256"]:
        raise PolicyRefusedError("gateway config changed")
    report = await policy_read(
        "/user/info?" + urlencode({"user_id": binding}), key, events
    )
    snapshot = policy_snapshot(report, key, binding)
    if digest(snapshot) != frozen["policy_snapshot_sha256"]:
        raise PolicyRefusedError("policy changed")
    cfg = yaml.safe_load(config_bytes)
    entries = copy.deepcopy(
        [entry for entry in cfg["model_list"] if entry.get("model_name") == "free"]
    )
    if len(entries) != 1:
        raise PolicyRefusedError("free route is ambiguous")
    params = entries[0]["litellm_params"]
    for name in ("api_key", "api_base"):
        value = params[name]
        if isinstance(value, str) and value.startswith("os.environ/"):
            params[name] = os.environ[value[len("os.environ/") :]]
    if params.get("model") != "openai/ornith/ornith-1.5-122b:free" or params.get(
        "extra_body"
    ) != {"tags": ["user=omniroute"]}:
        raise PolicyRefusedError("provider model configuration drift")
    body = dict(packet["body"])
    if (
        body.get("model") != "free"
        or body.get("max_tokens") != 256
        or body.get("temperature", 0.3) != 0.3
    ):
        raise PolicyRefusedError("operator request drift")
    body.update(
        num_retries=0,
        max_retries=0,
        disable_fallbacks=True,
        model_group_retry_policy={},
    )
    litellm.drop_params = False  # This detached process only, never the server.
    litellm.telemetry = False
    boundary = ProviderBoundary(
        httpx.AsyncHTTPTransport(retries=0),
        params["api_base"].rstrip("/") + "/chat/completions",
        events,
    )
    sdk = AsyncOpenAI(
        api_key=params["api_key"],
        base_url=params["api_base"],
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=boundary, timeout=max(0.01, 13 - (time.monotonic() - started))
        ),
    )
    router = Router(model_list=entries, num_retries=0, **cfg.get("router_settings", {}))
    try:
        routed = await route_request(
            data={**body, "client": sdk},
            llm_router=router,
            user_model=None,
            route_type="acompletion",
        )
        result = await routed if inspect.isawaitable(routed) else routed
        return {
            "status_code": 200,
            "body": result.model_dump(mode="json"),
            "headers": {
                "x-litellm-attempted-retries": "0",
                "x-litellm-attempted-fallbacks": "0",
            },
            "events": events,
            "lower_call_upper": {
                "model_gateway_http": 0,
                "policy_http": 1,
                "model_provider_http": 1,
            },
        }
    finally:
        await sdk.close()
