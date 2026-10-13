from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from tests.integration.host_bridge_sentinel import (
    POSITIVE_CONTROL_PREFIX,
    configured_host_bridge_target,
    validate_capture_address_plan,
    validate_host_bridge_target,
    verify_host_bridge_positive_control,
)
from tests.integration.sentinel_ledger import snapshot, verify


def test_default_and_nondefault_private_gateway_targets_are_valid():
    assert validate_host_bridge_target(
        "172.31.254.0/29", "172.31.254.1", "172.31.254.1", "172.31.254.1"
    ) == "172.31.254.1"
    assert validate_host_bridge_target(
        "10.73.19.0/29", "10.73.19.1", "10.73.19.1", "10.73.19.1", ("10.73.19.2", "10.73.19.3")
    ) == "10.73.19.1"


@pytest.mark.parametrize(
    ("subnet", "expected", "bind", "target"),
    [
        (None, "172.31.254.1", "172.31.254.1", "172.31.254.1"),
        ("172.31.254.0/29", None, None, None),
        ("172.31.254.0/29", "172.31.254.1", "172.31.254.2", "172.31.254.1"),
        ("172.31.254.0/29", "172.31.254.1", "172.31.254.1", "172.31.254.3"),
        ("172.31.254.0/29", "172.31.254.9", "172.31.254.9", "172.31.254.9"),
        ("172.31.254.0/29", "0.0.0.0", "0.0.0.0", "0.0.0.0"),
        ("172.31.254.0/29", "8.8.8.8", "8.8.8.8", "8.8.8.8"),
    ],
)
def test_missing_or_unrelated_gateway_target_fails_closed(subnet, expected, bind, target):
    with pytest.raises(RuntimeError):
        validate_host_bridge_target(subnet, expected, bind, target)


def test_host_gateway_cannot_collide_with_capture_proxy_or_peer():
    for reserved in ("172.31.254.2", "172.31.254.3"):
        with pytest.raises(RuntimeError):
            validate_host_bridge_target(
                "172.31.254.0/29", reserved, reserved, reserved, ("172.31.254.2", "172.31.254.3")
            )


def test_configured_target_uses_one_gateway_value_for_all_roles():
    assert configured_host_bridge_target(
        {
            "CAPTURE_HOST_BRIDGE_SUBNET": "10.73.19.0/29",
            "CAPTURE_HOST_BRIDGE_IP": "10.73.19.1",
            "CAPTURE_HOST_BRIDGE_IP_RANGE": "10.73.19.4/30",
            "CAPTURE_EGRESS_IP": "10.73.19.2",
            "CAPTURE_PEER_IP": "10.73.19.3",
        }
    ) == "10.73.19.1"

    with pytest.raises(RuntimeError, match="overlaps or is out of range"):
        configured_host_bridge_target(
            {
                "CAPTURE_HOST_BRIDGE_SUBNET": "172.31.254.0/29",
                "CAPTURE_HOST_BRIDGE_IP": "172.31.254.3",
                "CAPTURE_HOST_BRIDGE_IP_RANGE": "172.31.254.4/30",
                "CAPTURE_EGRESS_IP": "172.31.254.2",
                "CAPTURE_PEER_IP": "172.31.254.3",
            }
        )


@pytest.mark.parametrize(
    ("subnet", "gateway", "pool", "egress", "peer"),
    [
        ("10.73.19.0/29", "10.73.19.3", "10.73.19.4/30", "10.73.19.2", "10.73.19.3"),
        ("10.73.19.0/29", "10.73.19.1", "10.73.20.4/30", "10.73.19.2", "10.73.19.3"),
        ("10.73.19.0/29", "10.73.19.1", "10.73.19.0/30", "10.73.19.2", "10.73.19.3"),
        ("10.73.19.0/29", "10.73.19.1", "10.73.19.4/30", "10.73.19.4", "10.73.19.3"),
        ("10.73.19.0/29", "10.73.19.1", "10.73.19.4/30", "10.73.19.2", "10.73.19.2"),
    ],
)
def test_capture_address_plan_rejects_collisions_and_out_of_subnet_values(
    subnet, gateway, pool, egress, peer
):
    with pytest.raises(RuntimeError):
        validate_capture_address_plan(subnet, gateway, pool, egress, peer)


class _Response:
    status = 200
    headers = {
        "Content-Type": "text/plain",
        "Access-Control-Allow-Origin": "*",
    }

    def __init__(self, nonce: str):
        self.body = POSITIVE_CONTROL_PREFIX + nonce.encode("ascii")
        self.headers = {**self.headers, "Content-Length": str(len(self.body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def test_positive_control_requires_nonce_bound_response_from_expected_gateway():
    requests = []

    def opener(url: str, *, timeout: float):
        requests.append((url, timeout))
        nonce = url.rsplit("/", 1)[-1]
        return _Response(nonce)

    nonce = verify_host_bridge_positive_control("10.73.19.6", opener=opener)
    assert len(nonce) == 32
    assert requests == [
        (f"http://10.73.19.6:19001/positive-control/{nonce}", 2.0)
    ]


def test_positive_control_rejects_unrelated_sentinel_and_unreachable_gateway():
    class WrongPeer(_Response):
        def __init__(self, _nonce: str):
            super().__init__("not-the-request-nonce")

    with pytest.raises(RuntimeError, match="positive control was invalid"):
        verify_host_bridge_positive_control("172.31.254.1", opener=lambda *_a, **_k: WrongPeer(""))

    def unreachable(*_args, **_kwargs):
        raise URLError("unreachable")

    with pytest.raises(URLError):
        verify_host_bridge_positive_control("172.31.254.1", opener=unreachable)


def test_ledger_verification_preserves_prefix_and_matches_new_nonce(tmp_path):
    (tmp_path / "events.jsonl").write_bytes(b"")
    previous_private = b'{"kind":"positive-control","nonce":"' + b"a" * 32 + b'"}\n'
    previous_host = b'{"kind":"positive-control","nonce":"' + b"b" * 32 + b'"}\n'
    (tmp_path / "positive-control.jsonl").write_bytes(previous_private)
    (tmp_path / "host-bridge-positive-control.jsonl").write_bytes(previous_host)
    baseline = snapshot(tmp_path)
    private_nonce = "c" * 32
    host_nonce = "d" * 32
    with (tmp_path / "positive-control.jsonl").open("ab") as stream:
        stream.write(json.dumps({"kind": "positive-control", "nonce": private_nonce}).encode() + b"\n")
    with (tmp_path / "host-bridge-positive-control.jsonl").open("ab") as stream:
        stream.write(json.dumps({"kind": "positive-control", "nonce": host_nonce}).encode() + b"\n")
    verify(baseline, private_nonce, host_nonce, tmp_path)

    current = (tmp_path / "positive-control.jsonl").read_bytes()
    (tmp_path / "positive-control.jsonl").write_bytes(current.replace(b'"' + b"a" * 32, b'"' + b"9" * 32, 1))
    with pytest.raises(RuntimeError, match="prefix changed"):
        verify(baseline, private_nonce, host_nonce, tmp_path)


def test_ledger_verification_rejects_wrong_nonce_and_forbidden_event(tmp_path):
    for name in ("events.jsonl", "positive-control.jsonl", "host-bridge-positive-control.jsonl"):
        (tmp_path / name).write_bytes(b"")
    baseline = snapshot(tmp_path)
    (tmp_path / "positive-control.jsonl").write_text(
        json.dumps({"kind": "positive-control", "nonce": "e" * 32}) + "\n"
    )
    (tmp_path / "host-bridge-positive-control.jsonl").write_text(
        json.dumps({"kind": "positive-control", "nonce": "f" * 32}) + "\n"
    )
    with pytest.raises(RuntimeError, match="nonce did not match"):
        verify(baseline, "1" * 32, "f" * 32, tmp_path)
    (tmp_path / "events.jsonl").write_text('{"kind":"http"}\n')
    with pytest.raises(RuntimeError, match="forbidden traffic"):
        verify(baseline, "e" * 32, "f" * 32, tmp_path)


def test_ledger_snapshot_is_bounded_and_baseline_schema_is_strict(tmp_path):
    for name in ("events.jsonl", "positive-control.jsonl", "host-bridge-positive-control.jsonl"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "events.jsonl").write_bytes(b"x" * 65_537)
    with pytest.raises(RuntimeError, match="inspection limit"):
        snapshot(tmp_path)

    (tmp_path / "events.jsonl").write_bytes(b"")
    baseline = snapshot(tmp_path)
    baseline["negative"]["count"] = True
    with pytest.raises(RuntimeError, match="baseline is invalid"):
        verify(baseline, "1" * 32, "2" * 32, tmp_path)
