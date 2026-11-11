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
_read_upload = _parse_route._read_upload
_parse_upstream_response = _parse_route._parse_upstream_response
request_parse_upload_url = _parse_route.request_parse_upload_url


class _FakeRedis:
    def __init__(self, results):
        self.results = iter(results)
        self.keys = []

    def register_script(self, _script):
        def execute(*, keys, args=None):
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


def test_read_upload_preserves_metadata_and_handles_missing_source():
    redis = _FakeRedis([[b"document body", b"text/plain", b"report.txt"], None])

    assert _read_upload(redis, "upload-1") == (
        b"document body",
        "text/plain",
        "report.txt",
    )
    assert _read_upload(redis, "upload-1") is None
    assert redis.keys[0] == [
        "parse:upload:upload-1:data",
        "parse:upload:upload-1:content_type",
        "parse:upload:upload-1:filename",
        "parse:upload:upload-1",
        "parse:upload:upload-1:owner",
    ]


def test_read_upload_uses_safe_metadata_defaults():
    redis = _FakeRedis([[b"document body", None, None]])

    assert _read_upload(redis, "upload-2") == (
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


def test_success_ack_is_scoped_and_compare_and_delete():
    import hashlib

    observed = {}

    class Redis:
        def register_script(self, script):
            assert "redis.sha1hex(data)" in script

            def execute(*, keys, args):
                observed.update(keys=keys, args=args)
                return 5

            return execute

    assert _parse_route._ack_upload(
        Redis(), "u", "owner", b"old bytes", "text/plain", "x.txt"
    )
    assert observed["args"] == [
        "owner",
        hashlib.sha1(b"old bytes", usedforsecurity=False).hexdigest(),
        "text/plain",
        "x.txt",
    ]
    assert observed["keys"][-1] == "parse:upload:u:owner"


def test_partial_results_are_not_consumed_and_scope_cannot_be_spoofed():
    complete = {
        "success": True,
        "data": {"markdown": "text", "metadata": {"coverage": {"status": "complete"}}},
    }
    partial = {
        "success": True,
        "data": {"metadata": {"coverage": {"status": "partial"}}},
    }
    assert _parse_route.parse_complete(complete)
    assert not _parse_route.parse_complete(partial)
    assert not _parse_route.parse_complete({"success": False})
    request = SimpleNamespace(
        headers={"X-Parse-Scope": "victim", "Authorization": "Bearer actual"}
    )
    assert _parse_route.parse_headers(request)["X-Parse-Scope"] != "victim"


def test_document_transport_timeout_tracks_budget(monkeypatch):
    monkeypatch.setenv("PARSE_DOCUMENT_MAX_SECONDS", "2000")
    assert _parse_route.parse_timeout() == 2060
    monkeypatch.setenv("PARSE_DOCUMENT_MAX_SECONDS", "nan")
    with pytest.raises(ValueError):
        _parse_route.parse_timeout()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_acks", [("complete", 1), ("partial", 0), ("interrupted", 0)]
)
async def test_staged_route_acknowledges_only_complete_result(
    monkeypatch, status, expected_acks
):
    from starlette.requests import Request

    acknowledgements = []
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **kw: object())
    monkeypatch.setattr(
        _parse_route, "_get_redis_url", lambda request: "redis://unused"
    )
    monkeypatch.setattr(
        _parse_route, "_read_upload", lambda *a: (b"pdf", "application/pdf", "x.pdf")
    )
    monkeypatch.setattr(
        _parse_route, "_ack_upload", lambda *a: acknowledgements.append(a)
    )
    captured = {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            captured.update(kw)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "page",
                        "metadata": {"coverage": {"status": status}},
                    },
                },
            )

    monkeypatch.setattr(_parse_route.httpx, "AsyncClient", Client)
    body = b"upload_id=staged&ocr=local"

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/parse",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"authorization", b"Bearer owner"),
                (b"x-parse-scope", b"spoofed"),
            ],
        },
        receive,
    )
    result = await _parse_route.parse_file(request)
    assert result["data"]["metadata"]["coverage"]["status"] == status
    assert len(acknowledgements) == expected_acks
    assert captured["headers"]["X-Parse-Scope"] != "spoofed"


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"markdown": "x", "metadata": {"coverage": None}},
        {"markdown": "x", "metadata": {"coverage": []}},
    ],
)
def test_malformed_upstream_extraction_cannot_ack(data):
    payload = {"success": True, "data": data}
    assert not _parse_route.parse_complete(payload)
    with pytest.raises(UpstreamError):
        _parse_upstream_response(httpx.Response(200, json=payload))
