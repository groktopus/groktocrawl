#!/usr/bin/env python3
"""Exercise installed router/SDK with fake HTTP only; deny all socket connects."""

import asyncio
import inspect
import json
import os
import socket

blocked_connects = 0


def deny_connect(*args, **kwargs):
    global blocked_connects
    blocked_connects += 1
    raise RuntimeError("network disabled for offline gateway probe")


socket.socket.connect = deny_connect

import httpx
import litellm
from litellm import Router
from litellm.proxy.route_llm_request import route_request
from openai import AsyncOpenAI

litellm.drop_params = (
    os.getenv("W12_PROBE_DROP_PARAMS", "true") == "true"
)  # Match the inspected gateway in this detached probe.
litellm.telemetry = False  # Detached probe process only, not the gateway server.


async def check(status):
    sent = []

    async def send(request):
        sent.append(json.loads(request.content))
        if status == 200:
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            )
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": json.dumps({"detail": [{"loc": ["body", "max_tokens"]}]})
                    if status == 422
                    else (
                        "Conversation roles must alternate user/assistant"
                        if status == 400
                        else "fixture failure"
                    ),
                    "type": "fixture",
                    "code": str(status),
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(send))
    sdk = AsyncOpenAI(
        api_key="synthetic-no-credential",
        base_url="https://fixture.invalid/v1",
        http_client=http,
        max_retries=0,
    )
    router = Router(
        model_list=[
            {
                "model_name": "free",
                "litellm_params": {
                    "model": "openai/fixture",
                    "api_key": "synthetic-no-credential",
                    "api_base": "https://fixture.invalid/v1",
                },
            }
        ],
        num_retries=4,
    )
    error = None
    try:
        routed = await route_request(
            data={
                "model": "free",
                "messages": [{"role": "user", "content": "synthetic probe"}],
                "max_tokens": 8,
                "drop_params": False,
                "num_retries": 0,
                "max_retries": 0,
                "disable_fallbacks": True,
                "model_group_retry_policy": {},
                "client": sdk,
                "router_settings_override": {
                    "num_retries": 4,
                    "model_group_retry_policy": {"free": {"RateLimitErrorRetries": 4}},
                },
            },
            llm_router=router,
            user_model=None,
            route_type="acompletion",
        )
        if inspect.isawaitable(routed):
            await routed
    except Exception as exc:
        error = type(exc).__name__
    finally:
        await sdk.close()
    return {
        "fixture_status": status,
        "provider_http_dispatches": len(sent),
        "error_type": error,
        "requested_token_caps": [x.get("max_tokens") for x in sent],
    }


async def main():
    results = [await check(status) for status in (200, 400, 422, 429, 503)]
    print(
        json.dumps(
            {
                "schema_version": "w12.4-installed-gateway-offline-probe/1",
                "results": results,
                "real_socket_connects_denied": blocked_connects,
                "real_provider_calls": 0,
                "drop_params": litellm.drop_params,
                "request_drop_params": False,
                "token_cap_removal_reproduced": any(
                    None in x["requested_token_caps"] for x in results
                ),
                "live_admission_permitted": False,
                "fanout_bound_verified": all(
                    1 <= x["provider_http_dispatches"] <= 2 for x in results
                ),
            }
        )
    )


asyncio.run(main())
