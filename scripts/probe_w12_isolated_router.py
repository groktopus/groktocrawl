"""Installed-image offline probe. Read frozen helper source as JSON on stdin.

No real network: synthetic auth policy and provider HTTP; actual free-entry merge.
The production config is read only. Synthetic config file is deleted on exit.
"""

import asyncio
import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import types
from pathlib import Path

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
blocked_connects = 0


def deny_connect(*args, **kwargs):
    global blocked_connects
    blocked_connects += 1
    raise RuntimeError("offline probe network denied")


socket.socket.connect = deny_connect

import httpx
import yaml

source = json.load(sys.stdin)
worker = types.ModuleType("__pilot_router__")
exec(compile(source, "<frozen-router>", "exec"), worker.__dict__)
original_bytes = Path("/app/config.yaml").read_bytes()
original = yaml.safe_load(original_bytes)
entries = copy.deepcopy(
    [x for x in original["model_list"] if x.get("model_name") == "free"]
)
assert len(entries) == 1
entries[0]["litellm_params"].update(
    api_key="synthetic-provider", api_base="https://fixture.invalid/v1"
)
fixture_config = {
    "model_list": entries,
    "router_settings": original.get("router_settings", {}),
}
key, binding = "synthetic-client", "synthetic-user"
report = {
    "user_id": binding,
    "user_info": {"user_id": binding, "models": ["free"], "user_role": "internal_user"},
    "keys": [
        {
            "user_id": binding,
            "token": hashlib.sha256(key.encode()).hexdigest(),
            "models": ["free"],
            "key_type": "default",
        }
    ],
}


async def policy(path, client_key, events, **kwargs):
    assert client_key == key and path.startswith("/user/info?")
    events.append(
        {"kind": "policy_http", "state": "response_complete", "fixture_only": True}
    )
    return report


worker.policy_read = policy


async def check(status, config_path):
    sent = []

    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
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
                            "message": {"role": "assistant", "content": "fixture"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        message = (
            json.dumps({"detail": [{"loc": ["body", "max_tokens"]}]})
            if status == 422
            else "synthetic error"
        )
        return httpx.Response(
            status,
            json={
                "error": {"message": message, "type": "fixture", "code": str(status)}
            },
        )

    class MockProviderTransport(httpx.AsyncBaseTransport):
        def __init__(self, **kwargs):
            self.mock = httpx.MockTransport(respond)

        async def handle_async_request(self, request):
            return await self.mock.handle_async_request(request)

        async def aclose(self):
            await self.mock.aclose()

    worker.httpx.AsyncHTTPTransport = MockProviderTransport
    worker.CONFIG = config_path
    packet = {
        "_client_key": key,
        "_original_key_fingerprint": hashlib.sha256(key.encode()).hexdigest(),
        "_user_binding": binding,
        "_router_freeze": {
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "policy_snapshot_sha256": worker.digest(
                worker.policy_snapshot(report, key, binding)
            ),
        },
        "body": {
            "model": "free",
            "max_tokens": 256,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": "synthetic probe"}],
        },
    }
    error = None
    try:
        await worker.run_model(packet)
    except Exception as exc:
        error = type(exc).__name__
    return {
        "fixture_status": status,
        "error_type": error,
        "policy_fixture_reads": sum(
            x["kind"] == "policy_http" for x in packet.get("_router_events", [])
        ),
        "provider_http_dispatches": len(sent),
        "merged_caps": [x.get("max_tokens") for x in sent],
        "temperatures": [x.get("temperature") for x in sent],
        "attribution_preserved": all(x.get("tags") == ["user=omniroute"] for x in sent),
    }


async def main():
    with tempfile.TemporaryDirectory(prefix="w12-offline-router-") as directory:
        config_path = Path(directory) / "synthetic.yaml"
        config_path.write_text(yaml.safe_dump(fixture_config))
        results = [
            await check(status, config_path) for status in (200, 400, 422, 429, 503)
        ]
    passed = blocked_connects == 0 and all(
        x["policy_fixture_reads"] == 1
        and x["provider_http_dispatches"] == 1
        and x["merged_caps"] == [256]
        and x["temperatures"] == [0.3]
        and x["attribution_preserved"]
        for x in results
    )
    print(
        json.dumps(
            {
                "schema_version": "w12-isolated-router-offline/1",
                "original_config_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "helper_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "real_provider_calls": 0,
                "socket_connect_attempts_denied": blocked_connects,
                "results": results,
                "passed": passed,
            }
        )
    )
    if not passed:
        raise SystemExit(1)


asyncio.run(main())
