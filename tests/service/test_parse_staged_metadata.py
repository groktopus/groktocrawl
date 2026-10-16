"""Regression tests for staged Parse upload consumption."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import agent
import httpx
import pytest
from agent.exceptions import UpstreamError


def _load_parse_route():
    """Load the route without importing unrelated optional route dependencies."""
    routes_dir = Path(agent.__file__).resolve().parent / "routes"
    package = ModuleType("agent.routes")
    package.__path__ = [str(routes_dir)]  # type: ignore[attr-defined]
    previous_package = sys.modules.get("agent.routes")
    previous_module = sys.modules.get("agent.routes.parse")
    sys.modules["agent.routes"] = package
    try:
        spec = importlib.util.spec_from_file_location(
            "agent.routes.parse", routes_dir / "parse.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["agent.routes.parse"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_package is None:
            sys.modules.pop("agent.routes", None)
        else:
            sys.modules["agent.routes"] = previous_package
        if previous_module is None:
            sys.modules.pop("agent.routes.parse", None)
        else:
            sys.modules["agent.routes.parse"] = previous_module


_parse_route = _load_parse_route()
_consume_upload = _parse_route._consume_upload
_parse_upstream_response = _parse_route._parse_upstream_response
request_parse_upload_url = _parse_route.request_parse_upload_url


class _FakeRedis:
    def __init__(self, results):
        self.results = iter(results)
        self.keys = []

    def register_script(self, _script):
        def execute(*, keys):
            self.keys.append(keys)
            return next(self.results)

        return execute


@pytest.mark.asyncio
async def test_upload_reservation_creates_bounded_pending_marker(monkeypatch):
    stored = {}

    class _ReservationRedis:
        def set(self, key, value, *, ex):
            stored[key] = (value, ex)

    monkeypatch.setattr(
        "redis.Redis.from_url", lambda *_args, **_kwargs: _ReservationRedis()
    )
    request = SimpleNamespace(
        url=SimpleNamespace(scheme="https", netloc="api.example"), headers={}
    )

    result = await request_parse_upload_url(request)

    assert result.success is True
    assert stored == {
        f"parse:upload:{result.upload_id}": (b"pending", _parse_route.PARSE_UPLOAD_TTL),
        f"parse:upload:{result.upload_id}:owner": (
            b"anonymous",
            _parse_route.PARSE_UPLOAD_TTL,
        ),
    }
    assert result.upload_url == (
        f"https://api.example/v2/parse/upload/{result.upload_id}"
    )


def test_consume_upload_preserves_metadata_and_remains_single_use():
    redis = _FakeRedis([[b"document body", b"text/plain", b"report.txt"], None])

    assert _consume_upload(redis, "upload-1") == (
        b"document body",
        "text/plain",
        "report.txt",
    )
    assert _consume_upload(redis, "upload-1") is None
    assert redis.keys[0] == [
        "parse:upload:upload-1:data",
        "parse:upload:upload-1:content_type",
        "parse:upload:upload-1:filename",
        "parse:upload:upload-1",
    ]


def test_consume_upload_uses_safe_metadata_defaults():
    redis = _FakeRedis([[b"document body", None, None]])

    assert _consume_upload(redis, "upload-2") == (
        b"document body",
        "application/octet-stream",
        "uploaded_file",
    )


def test_parse_upstream_response_returns_success_payload():
    response = httpx.Response(
        200,
        json={"success": True, "data": {"markdown": "ok"}, "error": None},
    )

    assert _parse_upstream_response(response)["success"] is True


def test_parse_upstream_response_turns_failure_into_upstream_error():
    response = httpx.Response(400, json={"detail": "Unsupported format"})

    with pytest.raises(UpstreamError, match="Unsupported format") as exc_info:
        _parse_upstream_response(response)

    assert exc_info.value.details == {"status_code": 400}
