"""The renderer metric relay is finite, sanitized, fresh and transport-preserving."""

import asyncio
import tempfile
from unittest.mock import AsyncMock

import httpx
import pytest
from agent import browser_metrics as relay_module
from agent.browser_metrics import BrowserMetricsRelay

from common.browser_metrics import (
    MAX_METRICS_BYTES,
    METRICS,
    PREFIX,
    append_metrics,
    parse_samples,
    render_samples,
)


def snapshot_body():
    samples = {
        (name, label): 0.0
        for name, spec in METRICS.items()
        if not spec.optional
        for label in spec.values
    }
    samples[("service_start_time_seconds", "")] = 1234
    return render_samples(samples) + "# EOF\n"


def http_transport(monkeypatch, handler):
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://browser-control"
        )

    monkeypatch.setattr(relay_module, "create_browser_client", client)
    return options


@pytest.mark.asyncio
async def test_relay_sanitizes_and_preserves_only_one_eof(monkeypatch):
    source = (
        snapshot_body()
        + 'unrelated_secret_metric{url="https://private"} 123\n# HELP private secret\n'
    )
    options = http_transport(
        monkeypatch, lambda request: httpx.Response(200, text=source)
    )
    relay = BrowserMetricsRelay()
    result = append_metrics("agent_metric 1\n# EOF\n", await relay.collect())
    assert "secret" not in result and "private" not in result
    assert result.count("# EOF") == 1 and result.endswith("# EOF\n")
    assert "groktocrawl_browser_metrics_collection_success 1" in result
    assert relay.last_success > 0
    assert options == [{"socket_path": None, "timeout": 1.0}]
    parse_samples(result)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.replace('owners{phase="active"}', 'owners{phase="private"}'),
        lambda body: body.replace(
            'owners{phase="active"}', 'owners{phase="active",url="secret"}'
        ),
        lambda body: body + PREFIX + "active_sessions 2\n",
        lambda body: body.replace(
            PREFIX + "active_sessions 0.0", PREFIX + "active_sessions NaN"
        ),
        lambda body: body.replace(
            PREFIX + "active_sessions 0.0", PREFIX + "active_sessions 1e999"
        ),
        lambda body: body.replace(
            PREFIX + "active_sessions 0.0", PREFIX + "active_sessions -1"
        ),
        lambda body: body.replace(
            PREFIX + "active_sessions 0.0", PREFIX + "active_sessions 1 1234"
        ),
        lambda body: body.replace(
            PREFIX + "cgroup_collection_success 0.0",
            PREFIX + "cgroup_collection_success 2",
        ),
        lambda body: body.replace(
            PREFIX + "cgroup_collection_success 0.0",
            PREFIX + "cgroup_collection_success 1",
        ),
        lambda body: body + PREFIX + "cgroup_limit_events_total 12\n",
        lambda body: body + PREFIX + "cgroup_task_limit 256\n",
        lambda body: body.replace(PREFIX + "active_sessions 0.0\n", ""),
        lambda body: "browser_sessions_created_total 1\n# EOF\n",
        lambda body: body + "#" * MAX_METRICS_BYTES,
    ],
)
def test_rejects_invalid_or_incomplete_snapshot(mutation):
    with pytest.raises(ValueError):
        parse_samples(mutation(snapshot_body()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "http", "oversize", "malformed", "compressed", "unicode"]
)
async def test_failure_drops_prior_source_samples_and_keeps_freshness(
    monkeypatch, failure
):
    relay = BrowserMetricsRelay()
    http_transport(
        monkeypatch, lambda request: httpx.Response(200, text=snapshot_body())
    )
    await relay.collect()
    previous = relay.last_success

    async def fail(request):
        if failure == "timeout":
            await asyncio.Event().wait()
        return {
            "http": httpx.Response(503),
            "oversize": httpx.Response(200, text="#" * (MAX_METRICS_BYTES + 1)),
            "malformed": httpx.Response(200, text="not metrics"),
            "compressed": httpx.Response(
                200, content=b"", headers={"content-encoding": "gzip"}
            ),
            "unicode": httpx.Response(200, content=b"\xff"),
        }[failure]

    monkeypatch.setattr(relay_module, "COLLECTION_TIMEOUT_SECONDS", 0.01)
    http_transport(monkeypatch, fail)
    result = await relay.collect()
    assert "groktocrawl_browser_metrics_collection_success 0" in result
    assert PREFIX + "active_sessions" not in result
    assert relay.last_success == previous


@pytest.mark.asyncio
async def test_overlapping_scrapes_do_not_fan_out_or_cancel_first(monkeypatch):
    relay = BrowserMetricsRelay()
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch():
        entered.set()
        await release.wait()
        return snapshot_body().removesuffix("# EOF\n")

    mocked = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(relay, "_fetch", mocked)
    first = asyncio.create_task(relay.collect())
    await entered.wait()
    overlap = await relay.collect()
    assert "collection_success 0" in overlap
    assert not first.done()
    release.set()
    assert "collection_success 1" in await first
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_releases_relay_lock(monkeypatch):
    relay = BrowserMetricsRelay()
    entered = asyncio.Event()

    async def fetch():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(relay, "_fetch", fetch)
    task = asyncio.create_task(relay.collect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not relay._lock.locked()
    monkeypatch.setattr(
        relay, "_fetch", AsyncMock(return_value=snapshot_body().removesuffix("# EOF\n"))
    )
    assert "collection_success 1" in await relay.collect()


@pytest.mark.asyncio
async def test_real_uds_relay_ignores_proxy_and_has_no_tcp_fallback(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="metrics-uds-", dir="/tmp") as directory:
        path = f"{directory}/controller.sock"
        requests = []

        async def serve(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            requests.append(headers.decode())
            body = snapshot_body().encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        server = await asyncio.start_unix_server(serve, path=path)
        relay = BrowserMetricsRelay(socket_path=path)
        try:
            assert "collection_success 1" in await relay.collect()
            assert requests[0].startswith("GET /metrics HTTP/1.1")
        finally:
            server.close()
            await server.wait_closed()
        assert "collection_success 0" in await relay.collect()
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_agent_endpoint_exports_snapshot_and_survives_browser_failure(
    monkeypatch,
):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    from agent import app as agent_app

    from common.metrics import MetricsCollector

    monkeypatch.setenv("BROWSER_CONTROL_SOCKET", "/run/browser-control/test.sock")
    local_metrics = MetricsCollector()
    local_metrics.gauge("agent_test_metric", "Agent metric preserved").set(value=3)
    monkeypatch.setattr(agent_app, "METRICS", local_metrics)
    app = agent_app.create_app()
    monkeypatch.setattr(app.state.job_store, "list_active_jobs", lambda **kw: [])
    relay = app.state.browser_metrics_relay
    assert relay.socket_path == "/run/browser-control/test.sock"
    monkeypatch.setattr(
        relay, "_fetch", AsyncMock(return_value=snapshot_body().removesuffix("# EOF\n"))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent"
    ) as client:
        first = await client.get("/metrics")
        assert first.status_code == 200
        assert "agent_test_metric 3" in first.text
        assert first.text.count("# EOF") == 1
        parse_samples(first.text)
        monkeypatch.setattr(
            relay,
            "_fetch",
            AsyncMock(side_effect=httpx.ConnectError("private transport detail")),
        )
        failed = await client.get("/metrics")
    assert failed.status_code == 200
    assert "collection_success 0" in failed.text
    assert "private" not in failed.text
    assert PREFIX + "active_sessions" not in failed.text
    assert "agent_test_metric 3" in failed.text
