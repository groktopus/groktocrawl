"""Policy/report and final provider boundary are hermetic: no live requests."""

import hashlib
import json

import httpx
import pytest

from scripts.w12_router_worker import (
    PolicyRefusedError,
    ProviderBoundary,
    policy_snapshot,
)

KEY = "synthetic-client"
BINDING = "synthetic-user"


def report():
    return {
        "user_id": BINDING,
        "user_info": {
            "user_id": BINDING,
            "models": ["free"],
            "user_role": "internal_user",
        },
        "keys": [
            {
                "token": hashlib.sha256(KEY.encode()).hexdigest(),
                "user_id": BINDING,
                "models": ["free"],
                "key_type": "default",
            }
        ],
    }


def test_report_correlates_only_original_identity_without_exporting_identifiers():
    value = report()
    value["keys"].append({"token": "irrelevant-not-exported", "max_budget": 0})
    snapshot = policy_snapshot(value, KEY, BINDING)
    assert BINDING not in json.dumps(snapshot)
    assert KEY not in json.dumps(snapshot)
    assert "irrelevant-not-exported" not in json.dumps(snapshot)


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("key", "blocked", True),
        ("key", "expires", "2099-01-01"),
        ("key", "max_budget", 0),
        ("key", "rpm_limit", 0),
        ("key", "max_budget", 1),
        ("key", "models", ["other"]),
        ("key", "policies", ["restricted"]),
        ("key", "new_policy_limit", 1),
        ("user", "tpm_limit", 1),
        ("user", "max_parallel_requests", 1),
        ("user", "organization_id", "restricted"),
        ("user", "policies", ["restricted"]),
    ],
)
def test_restrictions_and_unknown_policy_stop_admission(section, field, value):
    payload = report()
    target = payload["keys"][0] if section == "key" else payload["user_info"]
    target[field] = value
    with pytest.raises(PolicyRefusedError):
        policy_snapshot(payload, KEY, BINDING)


@pytest.mark.parametrize("mutation", ["revoked", "rebound", "duplicate"])
def test_current_key_must_still_exist_once_in_the_bound_report(mutation):
    payload = report()
    if mutation == "revoked":
        payload["keys"] = []
    elif mutation == "rebound":
        payload["keys"][0]["user_id"] = "other"
    else:
        payload["keys"].append(dict(payload["keys"][0]))
    with pytest.raises(PolicyRefusedError):
        policy_snapshot(payload, KEY, BINDING)


@pytest.mark.asyncio
async def test_final_provider_body_is_checked_before_one_nonretrying_send():
    sent, events = [], []

    def respond(request):
        sent.append(request)
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )

    boundary = ProviderBoundary(
        httpx.MockTransport(respond),
        "https://fixture.invalid/v1/chat/completions",
        events,
    )
    async with httpx.AsyncClient(transport=boundary) as client:
        body = {
            "model": "ornith/ornith-1.5-122b:free",
            "max_tokens": 256,
            "temperature": 0.3,
            "tags": ["user=omniroute"],
        }
        await client.post(boundary.endpoint, json=body)
        with pytest.raises(PolicyRefusedError, match="retry"):
            await client.post(boundary.endpoint, json=body)
    assert len(sent) == 1
    assert events[0]["state"] == "response_complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("max_tokens", None),
        ("max_tokens", 257),
        ("temperature", 1),
        ("tags", []),
        ("stream", True),
    ],
)
async def test_merged_cap_and_sampler_cannot_be_removed_or_changed(field, value):
    sent = []
    boundary = ProviderBoundary(
        httpx.MockTransport(lambda r: sent.append(r)),
        "https://fixture.invalid/v1/chat/completions",
        [],
    )
    body = {
        "model": "ornith/ornith-1.5-122b:free",
        "max_tokens": 256,
        "temperature": 0.3,
        "tags": ["user=omniroute"],
        field: value,
    }
    async with httpx.AsyncClient(transport=boundary) as client:
        with pytest.raises(PolicyRefusedError):
            await client.post(boundary.endpoint, json=body)
    assert not sent


@pytest.mark.asyncio
async def test_valid_compressed_provider_response_is_not_decoded_twice():
    import gzip

    raw = json.dumps({"usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
    compressed = gzip.compress(raw)
    transport = ProviderBoundary(
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=compressed,
                headers={
                    "content-encoding": "gzip",
                    "content-length": str(len(compressed)),
                },
            )
        ),
        "https://fixture.invalid/v1/chat/completions",
        [],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            transport.endpoint,
            json={
                "model": "ornith/ornith-1.5-122b:free",
                "max_tokens": 256,
                "temperature": 0.3,
                "tags": ["user=omniroute"],
            },
        )
    assert response.json()["usage"]["completion_tokens"] == 1
    assert "content-encoding" not in response.headers


@pytest.mark.asyncio
async def test_same_user_replacement_key_is_rejected_before_any_policy_or_provider_read():
    from scripts.w12_router_worker import run_model

    with pytest.raises(PolicyRefusedError, match="original client identity changed"):
        await run_model(
            {
                "_client_key": "replacement",
                "_original_key_fingerprint": hashlib.sha256(KEY.encode()).hexdigest(),
            }
        )
