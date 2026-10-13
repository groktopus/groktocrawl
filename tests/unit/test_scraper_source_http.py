from __future__ import annotations

import ast
import asyncio
import errno
import os
import socket
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from curl_cffi.const import CurlOpt

_SCRAPER_SVC = Path(__file__).parents[2] / "scraper-svc"
if str(_SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(_SCRAPER_SVC))

from scraper import source_http as source_http_module
from scraper.llm_control_entrypoint import bind_control_listener
from scraper.source_http import (
    CAPTURE_EGRESS_PROXY_ENV,
    MODEL_EGRESS_PROXY_ENV,
    SourceDestinationPolicyUnavailableError,
    SourceTransportConfigurationError,
    capture_gateway_hostname_is_public,
    source_curl_options,
    source_httpx_client,
    source_httpx_sync_client,
    trusted_control_base_url,
    trusted_control_httpx_client,
    trusted_llm_endpoint,
    trusted_llm_headers,
    trusted_llm_httpx_client,
    trusted_llm_upstream_client,
)

from tests.outcome_governance import governed_skip


def _skip_if_socket_binding_is_restricted(error: OSError) -> None:
    if error.errno not in {errno.EACCES, errno.EPERM}:
        raise error
    governed_skip(
        "sandbox does not permit local Unix socket binding",
        owner="repository-maintainer",
        issue="#436",
        classification="retained",
        environment="restricted test sandbox denies AF_UNIX bind",
    )


def test_source_httpx_client_preserves_default_constructor(monkeypatch):
    monkeypatch.delenv(CAPTURE_EGRESS_PROXY_ENV, raising=False)
    constructor = Mock(return_value="client")
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    assert source_httpx_client(timeout=12) == "client"
    constructor.assert_called_once_with(timeout=12)


@pytest.mark.asyncio
async def test_capture_gateway_hostname_oracle_uses_fixed_ip_and_no_env_proxy(
    monkeypatch,
):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://172.31.254.2:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")
    observed = {}

    class Response:
        status_code = 200

        async def aiter_bytes(self):
            yield b'{"status":"public"}'

    class ResponseContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            observed["method"] = method
            observed["url"] = url
            observed["request"] = kwargs
            return ResponseContext()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await capture_gateway_hostname_is_public("example.com") is True
    assert observed["url"] == ("http://172.31.254.2:8080/_control/public-dns-check")
    assert observed["client_kwargs"]["trust_env"] is False
    assert observed["client_kwargs"]["follow_redirects"] is False
    assert observed["request"]["headers"]["Host"] == "capture-egress.internal:8080"
    assert observed["method"] == "POST"
    assert observed["request"]["content"] == '{"hostname":"example.com"}'


@pytest.mark.asyncio
async def test_capture_gateway_oracle_normalizes_idna_and_trailing_dot(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://172.31.254.2:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")
    observed = {}

    class Response:
        status_code = 200

        async def aiter_bytes(self):
            yield b'{"status":"public"}'

    class ResponseContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url, **kwargs):
            observed["body"] = kwargs["content"]
            return ResponseContext()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await capture_gateway_hostname_is_public("BÜCHER.example.") is True
    assert observed["body"] == '{"hostname":"xn--bcher-kva.example"}'


@pytest.mark.asyncio
async def test_capture_gateway_oracle_bounds_response_body(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://172.31.254.2:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")

    class Response:
        status_code = 200

        async def aiter_bytes(self):
            yield b"x" * 1025

    class ResponseContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(
        SourceDestinationPolicyUnavailableError, match="response-too-large"
    ):
        await capture_gateway_hostname_is_public("example.com")


@pytest.mark.asyncio
async def test_capture_gateway_oracle_distinguishes_outage_from_policy_denial(
    monkeypatch,
):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://172.31.254.2:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")

    class Response:
        status_code = 503

    class ResponseContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(SourceTransportConfigurationError, match="policy-unavailable"):
        await capture_gateway_hostname_is_public("example.com")


@pytest.mark.asyncio
async def test_capture_gateway_oracle_uses_total_deadline(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://172.31.254.2:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")
    real_timeout = asyncio.timeout

    class Response:
        status_code = 200

        async def aiter_bytes(self):
            await asyncio.sleep(0.05)
            yield b'{"status":"public"}'

    class ResponseContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setattr(
        source_http_module.asyncio, "timeout", lambda _seconds: real_timeout(0.001)
    )
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(SourceTransportConfigurationError, match="policy-unavailable"):
        await capture_gateway_hostname_is_public("example.com")


@pytest.mark.asyncio
async def test_capture_gateway_hostname_oracle_rejects_unavailable_or_mismatched_peer(
    monkeypatch,
):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://example.internal:8080")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_IP", "172.31.254.2")
    monkeypatch.setenv("SCRAPER_CAPTURE_GATEWAY_PORT", "8080")
    with pytest.raises(SourceTransportConfigurationError, match="policy-peer-invalid"):
        await capture_gateway_hostname_is_public("example.com")


def test_model_control_listener_is_prebound_private_and_inheritable():
    with tempfile.TemporaryDirectory(prefix="mc-", dir="/tmp") as raw_directory:
        directory = Path(raw_directory)
        directory.chmod(0o700)
        os.chown(directory, -1, os.getgid())
        path = directory / "control.sock"
        directory_info = directory.lstat()

        try:
            listener = bind_control_listener(
                path,
                expected_directory_uid=directory_info.st_uid,
                expected_directory_gid=directory_info.st_gid,
                expected_directory_mode=0o700,
            )
        except OSError as exc:
            _skip_if_socket_binding_is_restricted(exc)
        try:
            info = path.lstat()
            assert stat.S_ISSOCK(info.st_mode)
            assert stat.S_IMODE(info.st_mode) == 0o660
            assert listener.get_inheritable()
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.settimeout(1)
                probe.connect(str(path))
            except OSError as exc:
                _skip_if_socket_binding_is_restricted(exc)
            finally:
                probe.close()
        finally:
            listener.close()


def test_model_control_listener_replaces_only_owned_stale_socket():
    with tempfile.TemporaryDirectory(prefix="mc-", dir="/tmp") as raw_directory:
        directory = Path(raw_directory)
        directory.chmod(0o700)
        os.chown(directory, -1, os.getgid())
        path = directory / "control.sock"
        directory_info = directory.lstat()
        stale = socket.socket(socket.AF_UNIX)
        try:
            stale.bind(str(path))
        except OSError as exc:
            stale.close()
            _skip_if_socket_binding_is_restricted(exc)
        stale.close()

        listener = bind_control_listener(
            path,
            expected_directory_uid=directory_info.st_uid,
            expected_directory_gid=directory_info.st_gid,
            expected_directory_mode=0o700,
        )
        try:
            assert stat.S_ISSOCK(path.lstat().st_mode)
            assert stat.S_IMODE(path.lstat().st_mode) == 0o660
        finally:
            listener.close()


@pytest.mark.parametrize("occupied_kind", ["symlink", "regular"])
def test_model_control_listener_rejects_unsafe_occupied_path(tmp_path, occupied_kind):
    directory = tmp_path / "model-control"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    path = directory / "control.sock"
    if occupied_kind == "symlink":
        target = directory / "target"
        target.write_text("fixture")
        path.symlink_to(target)
    else:
        path.write_text("fixture")

    with pytest.raises(RuntimeError, match="path is occupied"):
        bind_control_listener(
            path,
            expected_directory_uid=os.getuid(),
            expected_directory_gid=os.getgid(),
            expected_directory_mode=0o700,
        )


def test_model_control_listener_rejects_wrong_parent_mode(tmp_path):
    directory = tmp_path / "model-control"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    with pytest.raises(RuntimeError, match="directory is invalid"):
        bind_control_listener(
            directory / "control.sock",
            expected_directory_uid=os.getuid(),
            expected_directory_gid=os.getgid(),
            expected_directory_mode=0o2710,
        )


def test_protected_source_httpx_forces_gateway_and_disables_environment(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080/")
    constructor = Mock(return_value="client")
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    source_httpx_client(timeout=12, proxy="http://caller-proxy.example:3128")

    constructor.assert_called_once_with(
        timeout=12,
        proxy="http://egress-gateway.example:8080",
        trust_env=False,
    )


def test_private_model_upstream_requires_isolated_gateway(monkeypatch):
    monkeypatch.setenv(MODEL_EGRESS_PROXY_ENV, "http://model-egress:8080")
    constructor = Mock(return_value="model")
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    assert (
        trusted_llm_upstream_client(
            "http://llm-svc:4001/v1/chat/completions", timeout=10
        )
        == "model"
    )
    constructor.assert_called_once_with(
        timeout=10,
        follow_redirects=False,
        proxy="http://model-egress:8080",
        trust_env=False,
    )


def test_private_model_upstream_fails_closed_without_gateway(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://source-egress:8080")
    monkeypatch.delenv(MODEL_EGRESS_PROXY_ENV, raising=False)
    constructor = Mock()
    monkeypatch.setattr(httpx, "AsyncClient", constructor)
    with pytest.raises(SourceTransportConfigurationError, match="model-egress-gateway"):
        trusted_llm_upstream_client("http://llm-svc:4001/v1/chat/completions")
    constructor.assert_not_called()


def test_model_gateway_cannot_fall_back_to_source_gateway(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://source-egress:8080")
    monkeypatch.setenv(MODEL_EGRESS_PROXY_ENV, "http://user:secret@source-egress:8080")
    constructor = Mock()
    monkeypatch.setattr(httpx, "AsyncClient", constructor)
    with pytest.raises(
        SourceTransportConfigurationError, match="model-egress-proxy-invalid"
    ):
        trusted_llm_upstream_client("https://model.example/v1/chat/completions")
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "https://egress.example:8080",
        "http://user:secret@egress.example:8080",
        "http://egress.example:99999",
        "http://egress.example/path",
        "http://[broken:8080",
    ],
)
def test_invalid_gateway_configuration_fails_before_client_creation(monkeypatch, value):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, value)
    constructor = Mock()
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    with pytest.raises(SourceTransportConfigurationError):
        source_httpx_client(timeout=2)
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "bypass", [{"transport": object()}, {"mounts": {"all://": object()}}]
)
def test_protected_source_httpx_rejects_custom_transport_bypass(monkeypatch, bypass):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    constructor = Mock()
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    with pytest.raises(
        SourceTransportConfigurationError, match="custom-source-transport"
    ):
        source_httpx_client(**bypass)
    constructor.assert_not_called()


def test_sync_substack_source_probe_uses_gateway(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    constructor = Mock(return_value="client")
    monkeypatch.setattr(httpx, "Client", constructor)

    source_httpx_sync_client(follow_redirects=True)

    constructor.assert_called_once_with(
        follow_redirects=True,
        proxy="http://egress-gateway.example:8080",
        trust_env=False,
    )


def test_curl_options_override_residential_proxy_only_when_protected(monkeypatch):
    monkeypatch.delenv(CAPTURE_EGRESS_PROXY_ENV, raising=False)
    assert source_curl_options(compatibility_proxy="http://residential.example") == {
        "proxy": "http://residential.example"
    }

    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    assert source_curl_options(compatibility_proxy="http://residential.example") == {
        "proxy": "http://egress-gateway.example:8080",
        "trust_env": False,
        "curl_options": {CurlOpt.NOPROXY: ""},
    }


def test_fixed_control_client_does_not_inherit_source_proxy(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    constructor = Mock(return_value="control")
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    assert trusted_control_httpx_client(service="browser", timeout=7) == "control"
    assert constructor.call_args.kwargs["timeout"] == 7
    assert constructor.call_args.kwargs["trust_env"] is False
    assert isinstance(
        constructor.call_args.kwargs["transport"], httpx.AsyncHTTPTransport
    )


def test_protected_llm_is_a_fixed_keyless_socket_capability(monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    monkeypatch.setenv("LLM_CONTROL_SOCKET", "/run/scraper-llm/control.sock")
    transport = Mock(return_value="fixed-uds-transport")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    constructor = Mock(return_value="model")
    monkeypatch.setattr(httpx, "AsyncClient", constructor)

    assert trusted_llm_httpx_client("recovery", timeout=11) == "model"
    kwargs = constructor.call_args.kwargs
    assert kwargs["trust_env"] is False
    assert kwargs["base_url"] == "http://llm-control"
    assert kwargs["timeout"] == 11
    assert kwargs["transport"] == "fixed-uds-transport"
    transport.assert_called_once_with(uds="/run/scraper-llm/control.sock", retries=0)
    assert trusted_llm_endpoint("recovery", "http://ignored.invalid/v1") == (
        "/recovery/chat/completions"
    )
    assert trusted_llm_headers("recovery", "must-not-leak") == {
        "Content-Type": "application/json"
    }


@pytest.mark.parametrize(
    ("service", "value", "expected"),
    [
        (
            "browser",
            "http://candidate-browser-controller:8012",
            "http://candidate-browser-controller:8012",
        ),
        (
            "flare",
            "http://candidate-flare-control:8191/v1",
            "http://candidate-flare-control:8191/v1",
        ),
    ],
)
def test_protected_control_urls_are_fixed(service, value, expected, monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    assert trusted_control_base_url(value, service) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://candidate-valkey:6379",
        "http://candidate-browser-controller:8012/other",
        "http://user@candidate-browser-controller:8012",
        "https://candidate-flare-control:8191/v1",
    ],
)
def test_protected_control_url_rejects_nonfixed_authorities(value, monkeypatch):
    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    service = "flare" if "flare" in value else "browser"
    with pytest.raises(SourceTransportConfigurationError, match="trusted-control-url"):
        trusted_control_base_url(value, service)


def test_protected_tier3_delegates_to_isolated_browser_service(monkeypatch):
    from scraper import fetch_tiers

    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    monkeypatch.setattr(
        fetch_tiers, "FLARE_SOLVERR_URL", "http://candidate-flare-control:8191/v1"
    )
    isolated_fetch = AsyncMock(
        return_value={"markdown": "isolated", "source": "browser-svc"}
    )
    browser_fetch = AsyncMock(side_effect=AssertionError("browser must not launch"))
    monkeypatch.setattr(fetch_tiers, "_fetch_via_browser_svc", isolated_fetch)
    monkeypatch.setattr(fetch_tiers, "_playwright_fetch_with_proxy", browser_fetch)

    assert asyncio.run(fetch_tiers.fetch_via_playwright("https://source.example")) == {
        "markdown": "isolated",
        "source": "browser-svc",
    }
    isolated_fetch.assert_awaited_once_with("https://source.example")
    browser_fetch.assert_not_awaited()


def test_protected_flaresolverr_uses_separate_control_client(monkeypatch):
    from types import SimpleNamespace

    from scraper import fetch_tiers

    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    monkeypatch.setattr(
        fetch_tiers, "FLARE_SOLVERR_URL", "http://candidate-flare-control:8191/v1"
    )
    response = Mock(status_code=200)
    response.json.return_value = {
        "solution": {"status": 200, "response": "<html>fixture</html>"}
    }
    client = Mock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)
    constructor = Mock(return_value=client)
    monkeypatch.setattr(fetch_tiers, "trusted_control_httpx_client", constructor)
    monkeypatch.setattr(fetch_tiers, "html_to_markdown", Mock(return_value="x" * 80))
    monkeypatch.setattr(
        fetch_tiers,
        "_classify_barrier",
        Mock(return_value=SimpleNamespace(detected=False, confidence=0.0)),
    )

    result = asyncio.run(fetch_tiers.fetch_via_flaresolverr("https://source.example"))

    assert result["source"] == "flare-solverr"
    assert constructor.call_args.kwargs == {"service": "flare", "timeout": 60}
    client.post.assert_awaited_once()


def test_candidate_scraper_is_not_attached_to_direct_egress_network():
    import yaml

    compose_path = Path(__file__).parents[2] / "compose.experimental-candidate.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    ci_overlay = yaml.safe_load(
        (compose_path.parent / "compose.protected-flare-ci.yml").read_text(
            encoding="utf-8"
        )
    )
    scraper = compose["services"]["candidate-scraper"]
    assert scraper["group_add"] == ["20000"]
    networks = scraper["networks"]
    if isinstance(networks, dict):
        network_names = set(networks)
    else:
        network_names = set(networks)

    assert "candidate_egress" not in network_names
    assert network_names == {"candidate_capture"}
    assert compose["networks"]["candidate_capture"]["internal"] is True
    assert "candidate_private" not in network_names
    assert "candidate_browser_capture" not in network_names
    assert "candidate_flare_capture" not in network_names
    assert "candidate_model_capture" not in network_names
    assert "candidate_model_gateway" not in network_names
    services = compose["services"]
    assert set(services["candidate-capture-egress"]["networks"]) == {
        "candidate_egress",
        "candidate_capture",
        "candidate_browser_capture",
        "candidate_flare_capture",
    }
    assert services["candidate-scraper-ingress"]["networks"] == ["candidate_private"]
    assert services["candidate-agent"]["environment"]["SCRAPER_URL"] == (
        "http://candidate-scraper-ingress:8010"
    )
    assert "candidate_browser_renderer_socket:/run/browser:ro" not in scraper["volumes"]
    assert "candidate_flare_socket:/run/flaresolverr:ro" not in scraper["volumes"]
    assert "candidate_scraper_state_socket:/run/scraper-state:ro" in scraper["volumes"]
    assert "candidate_flare_control_socket:/run/flare-control:ro" in scraper["volumes"]
    assert services["candidate-flare-control"]["network_mode"] == "none"
    model_control = services["candidate-scraper-model-control"]
    assert model_control["command"] == [
        "python",
        "-m",
        "scraper.llm_control_entrypoint",
    ]
    assert set(model_control["networks"]) == {"candidate_model_gateway"}
    model_gateway = services["candidate-model-egress"]
    assert set(model_gateway["networks"]) == {
        "candidate_model_gateway",
        "candidate_model_upstream",
    }
    assert "candidate_egress" not in model_gateway["networks"]
    assert "LLM_API_KEY" not in model_gateway["environment"]
    assert "LLM_GATEWAY_PRIVATE_HOSTS" in model_gateway["environment"]
    assert compose["networks"]["candidate_model_gateway"]["internal"] is True
    assert compose["networks"]["candidate_model_upstream"].get("internal") is not True
    assert "candidate-scraper" not in ci_overlay["services"]
    assert "extra_hosts" not in ci_overlay["services"]["candidate-capture-egress"]
    assert ci_overlay["services"]["candidate-flare-test-origin"]["networks"][
        "candidate_flare_fixture"
    ]["aliases"] == ["flare-origin.test"]
    assert model_control["environment"]["MODEL_EGRESS_PROXY_URL"] == (
        "http://172.31.254.34:8080"
    )
    assert "LLM_BASE_URL" not in scraper["environment"]
    assert "LLM_API_KEY" not in scraper["environment"]
    assert "CAPTCHA_VISION_API_KEY" not in scraper["environment"]
    assert scraper["environment"]["LLM_CONTROL_SOCKET"] == (
        "/run/scraper-llm/control.sock"
    )
    assert "LLM_API_KEY" in model_control["environment"]
    assert (
        scraper["environment"]["SCRAPER_CAPTURE_EGRESS_PROXY_URL"]
        == "http://172.31.254.2:8080"
    )


def test_adapter_browser_fallbacks_remain_available_in_protected_mode(monkeypatch):
    from scraper.adapters import bluesky, shopify, substack, youtube
    from scraper.adapters.base import AdapterContext

    monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, "http://egress-gateway.example:8080")
    context = AdapterContext(
        config={"BROWSER_SVC_URL": "http://candidate-browser-controller:8012"}
    )
    response = Mock(status_code=200)
    response.json.return_value = {
        "id": "session-1",
        "success": True,
        "result": {"script_result": "x" * 250},
    }
    client = Mock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)
    for module in (bluesky, shopify, substack, youtube):
        monkeypatch.setattr(
            module,
            "trusted_control_httpx_client",
            Mock(return_value=client),
        )

    assert asyncio.run(bluesky._fetch_via_browser("https://source.example", context))
    assert asyncio.run(shopify._fetch_via_browser("https://source.example", context))
    assert asyncio.run(substack._fetch_via_browser("https://source.example", context))
    assert asyncio.run(
        youtube._fetch_via_browser("https://source.example", "video", context)
    )
    assert client.post.await_count > 0


def test_all_adapter_http_clients_use_the_source_factory():
    adapters_dir = Path(__file__).parents[2] / "scraper-svc" / "scraper" / "adapters"
    for path in adapters_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and node.func.attr in {"AsyncClient", "Client", "get", "post", "request"}
        ]
        assert not calls, f"direct source HTTP client remains in {path.name}"
    helper = (adapters_dir / "_helpers.py").read_text()
    assert "source_httpx_client(" in helper


def test_all_scraper_httpx_clients_are_classified_by_the_factory():
    scraper_dir = Path(__file__).parents[2] / "scraper-svc" / "scraper"
    for path in scraper_dir.rglob("*.py"):
        if path.name == "source_http.py":
            continue
        tree = ast.parse(path.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and node.func.attr in {"AsyncClient", "Client", "get", "post", "request"}
        ]
        assert not calls, (
            f"unclassified HTTPX client remains in {path.relative_to(scraper_dir)}"
        )


def test_protected_curl_ignores_no_proxy_with_local_proxy_fixture(monkeypatch):
    from curl_cffi import requests as curl_requests

    async def scenario():
        proxy_requests = []
        origin_requests = []

        async def respond(reader, writer, captured, body):
            request = await reader.readuntil(b"\r\n\r\n")
            captured.append(request)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async def proxy_handler(reader, writer):
            await respond(reader, writer, proxy_requests, b"gateway")

        async def origin_handler(reader, writer):
            await respond(reader, writer, origin_requests, b"origin")

        origin = await asyncio.start_server(origin_handler, "127.0.0.1", 0)
        proxy = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
        origin_port = origin.sockets[0].getsockname()[1]
        proxy_port = proxy.sockets[0].getsockname()[1]
        monkeypatch.setenv(CAPTURE_EGRESS_PROXY_ENV, f"http://127.0.0.1:{proxy_port}")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        try:
            async with curl_requests.AsyncSession(
                timeout=3,
                **source_curl_options(compatibility_proxy=None),
            ) as client:
                response = await client.get(f"http://127.0.0.1:{origin_port}/proof")
            assert response.text == "gateway"
            assert len(proxy_requests) == 1
            assert proxy_requests[0].startswith(
                f"GET http://127.0.0.1:{origin_port}/proof ".encode()
            )
            assert origin_requests == []
        finally:
            proxy.close()
            origin.close()
            await proxy.wait_closed()
            await origin.wait_closed()

    asyncio.run(scenario())
