"""Standalone existing-container workers for the frozen calibration profile.

Never imported by an API route. The source worker deliberately uses one protected,
non-redirecting generic GET and a fresh robots GET, with no adapter/cache/recovery.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("public HTTP URL required")
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    port = parsed.port
    if port is not None and (parsed.scheme.lower(), port) not in {
        ("http", 80),
        ("https", 443),
    }:
        host += f":{port}"
    return urlunsplit(
        (parsed.scheme.lower(), host, parsed.path or "/", parsed.query, "")
    )


def unresolved_source_key(url: str) -> str:
    parsed = urlsplit(canonical_url(url))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, unquote(parsed.path).rstrip("/") or "/", "", "")
    )


def _hash_files(paths: list[str]) -> dict[str, str]:
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def agent_routes(settings: Any) -> dict[str, str]:
    """Bind the actual caller endpoints and environment proxy routing, without disclosure."""
    import httpx
    from agent.llm import LLMClient

    for value in (settings.llm_base_url, settings.searxng_url):
        parsed = httpx.URL(value)
        if parsed.username or parsed.password:
            raise ValueError("embedded endpoint credentials refused")
    model = LLMClient.__new__(LLMClient)
    model.base_url = settings.llm_base_url
    routes = {
        "completion": model._completion_url(),
        "search": f"{settings.searxng_url.rstrip('/')}/search",
        "proxy_environment": json.dumps(
            {
                key: os.getenv(key, "")
                for key in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "NO_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                    "no_proxy",
                )
            },
            sort_keys=True,
        ),
    }
    return {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in routes.items()
    }


def describe(role: str) -> dict[str, Any]:
    if role == "search":
        from slopsearx.config import load_config

        cfg = load_config()
        brave = cfg.engines.get("brave")
        return {
            "role": role,
            "enabled": bool(brave and brave.enabled),
            "max_results": brave.max_results if brave else None,
            "timeout_ms": brave.timeout_ms if brave else None,
            "api_key_configured": bool(
                brave and (brave.api_key or os.getenv("ENGINE_BRAVE_API_KEY"))
            ),
            "suggestions": cfg.enable_suggestions,
            "jev_or_rerank_env": any(
                bool(v) for k, v in os.environ.items() if "JEV" in k or "RERANK" in k
            ),
            "source_sha256": _hash_files(
                [
                    "/app/engines/brave.py",
                    "/app/slopsearx/service.py",
                    "/app/slopsearx/adapter.py",
                    "/app/slopsearx/config.py",
                ]
            ),
        }
    if role == "source":
        from scraper import source_http
        from scraper.settings import load_settings

        settings = load_settings()
        return {
            "role": role,
            "protected_source": source_http.protected_source_mode(),
            "compatibility_proxy_configured": bool(settings.scraper_proxy_url),
            "environment_proxy_configured": any(
                bool(os.getenv(k))
                for k in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                )
            ),
            "politeness_enabled": settings.politeness_enabled,
            "distributed_politeness": settings.distributed_politeness,
            "source_sha256": _hash_files(
                [
                    inspect.getfile(source_http),
                    "/app/scraper/politeness.py",
                    "/app/scraper/fetch.py",
                    "/app/scraper/fetch_quality.py",
                    "/app/scraper/extract.py",
                    "/app/scraper/fetch_tiers.py",
                    "/app/common/url.py",
                    "/app/scraper/proxy.py",
                ]
            ),
        }
    if role == "gateway":
        import litellm.router
        import yaml
        from litellm.llms.openai.openai import OpenAIChatCompletion

        cfg = yaml.safe_load(Path("/app/config.yaml").read_text())
        settings = cfg.get("litellm_settings", {})
        entries = [
            x for x in cfg.get("model_list", []) if x.get("model_name") == "free"
        ]
        return {
            "role": role,
            "version": importlib.metadata.version("litellm"),
            "drop_params": settings.get("drop_params", False),
            "callbacks_observability_only": all(
                x == "prometheus" for x in settings.get("callbacks", [])
            ),
            "pre_call_rules_configured": bool(settings.get("pre_call_rules")),
            "retry_policy_configured": any(
                cfg.get(s, {}).get(k) is not None
                for s in ("router_settings", "litellm_settings")
                for k in ("retry_policy", "model_group_retry_policy")
            ),
            "free_models": [x.get("litellm_params", {}).get("model") for x in entries],
            "declared_costs": [
                {
                    k: v
                    for k, v in x.get("model_info", {}).items()
                    if k in ("input_cost_per_token", "output_cost_per_token")
                }
                for x in entries
            ],
            "source_sha256": {
                "router_retries": hashlib.sha256(
                    inspect.getsource(
                        litellm.router.Router.async_function_with_retries
                    ).encode()
                ).hexdigest(),
                "proxy_request_route": hashlib.sha256(
                    Path(
                        inspect.getfile(
                            __import__(
                                "litellm.proxy.route_llm_request",
                                fromlist=["route_request"],
                            )
                        )
                    ).read_bytes()
                ).hexdigest(),
                "async_provider": hashlib.sha256(
                    inspect.getsource(OpenAIChatCompletion.acompletion).encode()
                ).hexdigest(),
            },
        }
    from agent.settings import load_settings

    s = load_settings()
    return {
        "role": "agent",
        "route_sha256": agent_routes(s),
        "model": s.llm_model,
        "jev_enabled": bool(s.typesafe_api_key),
        "thinking": s.llm_enable_thinking,
        "token_parameter": s.llm_token_parameter,
        "source_sha256": _hash_files(
            [
                "/app/agent/llm.py",
                "/app/agent/searxng_client.py",
                "/app/agent/settings.py",
            ]
        ),
    }


async def service_request(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    from agent.settings import load_settings

    s = load_settings()
    if kind == "search":
        # Explicit engine bypasses specialist routing. Frozen configuration must
        # separately show no reranker or suggestions before any case is admitted.
        from agent.searxng_client import SearXNGClient

        client = SearXNGClient(s.searxng_url, max_searches=2)
        try:
            params = dict(body)
            params.update(engines="brave", format="json", language="en", pageno=1)
            response = await client._client.get(
                f"{s.searxng_url.rstrip('/')}/search", params=params
            )
            return {
                "status_code": response.status_code,
                "body": response.json(),
                "headers": {},
                "lower_call_upper": {
                    "search_service_http": 1,
                    "brave_api_http": 1,
                    "search_model_http": 0,
                    "suggest_http": 0,
                },
            }
        finally:
            await client.close()
    from agent.experimental.calibration_budget import (
        CalibrationLedger,
        ScreeningCaps,
        install_budget_boundary,
    )
    from agent.llm import LLMClient

    if (
        s.llm_model != "free"
        or s.typesafe_api_key
        or s.llm_enable_thinking
        or s.llm_token_parameter != "max_tokens"
    ):
        raise ValueError("model configuration drift")
    events: list[dict[str, Any]] = []
    book = CalibrationLedger(
        "isolated-model-operation",
        events.append,
        ScreeningCaps(attempts=1, model_per_attempt=1),
    )
    model_client = LLMClient(s.llm_base_url, s.llm_api_key, s.llm_model)
    install_budget_boundary(
        model_client._client, book, "model", model_client._completion_url()
    )
    book.start_attempt("one")
    try:
        response = await model_client._client.post(
            model_client._completion_url(),
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            json=body,
        )
        payload = response.json()
        book.finish_attempt("completed")
        headers = {
            k: response.headers[k]
            for k in (
                "x-litellm-attempted-retries",
                "x-litellm-attempted-fallbacks",
                "x-litellm-call-id",
            )
            if k in response.headers
        }
        return {
            "status_code": response.status_code,
            "body": payload,
            "headers": headers,
            "events": events,
            "lower_call_upper": {"model_gateway_http": 1, "model_provider_http": 1},
        }
    finally:
        await model_client.close()


class BoundedSourceReads:
    """One robots and one page request; latch outside caller error handlers."""

    def __init__(
        self,
        client_factory,
        *,
        response_limit: int = 1024 * 1024,
        destination_check=None,
    ) -> None:
        self.client_factory = client_factory
        self.destination_check = destination_check
        self.response_limit = response_limit
        self.events: list[dict[str, Any]] = []
        self.halted = False
        self.count = 0

    async def get(self, url: str, *, role: str) -> tuple[int, bytes, dict[str, str]]:
        if self.halted or self.count >= 2:
            self.halted = True
            raise RuntimeError("source dispatch cap")
        if self.destination_check is not None:
            await self.destination_check(url)
        self.count += 1
        event: dict[str, Any] = {
            "role": role,
            "url_sha256": hashlib.sha256(canonical_url(url).encode()).hexdigest(),
            "state": "dispatch_reserved",
        }
        self.events.append(event)
        try:
            async with self.client_factory(
                timeout=10, follow_redirects=False
            ) as client:
                if client.follow_redirects:
                    raise RuntimeError("source redirects must remain disabled")
                async with client.stream(
                    "GET",
                    url,
                    headers={
                        "User-Agent": "GroktoCrawl",
                        "Accept": "text/markdown, text/html;q=0.9",
                    },
                ) as response:
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > self.response_limit:
                            raise RuntimeError("source response byte cap")
                        chunks.append(chunk)
                    event.update(
                        state="response_complete",
                        status_code=response.status_code,
                        bytes=length,
                    )
                    if 300 <= response.status_code < 400:
                        self.halted = True
                        raise ValueError("source redirect refused")
                    return (
                        response.status_code,
                        b"".join(chunks),
                        dict(response.headers),
                    )
        except BaseException:
            self.halted = True
            if event["state"] != "response_complete":
                event.update(
                    state="outcome_unknown", server_cancellation_confirmed=False
                )
            raise


async def acquire(body: dict[str, Any]) -> dict[str, Any]:
    from scraper import fetch, source_http
    from scraper.fetch_quality import _add_quality, html_to_markdown
    from scraper.politeness import PolitenessManager, _DomainState

    url = canonical_url(body["url"])
    if (
        hashlib.sha256(unresolved_source_key(url).encode()).hexdigest()
        in body["blocked_source_sha256"]
    ):
        raise ValueError("unresolved source forbidden")
    counts = {
        "dns_control_http": 0,
        "source_http": 1,
        "robots_http": 1,
        "source_model_http": 0,
        "browser_http": 0,
        "source_cache_reads": 0,
    }

    def configured_factory(**kwargs):
        compatibility_proxy = fetch._get_httpx_proxies()
        if compatibility_proxy:
            kwargs["proxy"] = compatibility_proxy
        return source_http.source_httpx_client(**kwargs)

    async def destination_check(target):
        if source_http.protected_source_mode():
            counts["dns_control_http"] += 1
        private, _reason = await fetch._is_private_url(target)
        if private:
            raise ValueError("public destination validation refused")

    reads = BoundedSourceReads(configured_factory, destination_check=destination_check)
    manager = PolitenessManager()
    parsed = urlsplit(url)
    domain = manager._domain_from_url(url)
    parsed_origin = urlsplit(url)
    robots_url = urlunsplit(
        (parsed_origin.scheme, parsed_origin.netloc, "/robots.txt", "", "")
    )
    try:
        status, robots_body, _ = await reads.get(robots_url, role="robots")
        counts["robots_http"] = 1
        if status not in (200, 404, 410):
            return {
                "success": False,
                "error": "robots unavailable or refused",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        state = _DomainState()
        if status == 200:
            manager._parse_robots_txt(
                robots_body.decode("utf-8", errors="replace"), state
            )
        state.robots_cached_at = time.time()
        # Mandatory robots even when the deployment's optional politeness layer
        # is disabled. No policy/default is changed; this caller is stricter.
        if any(
            pattern.search(parsed.path or "/")
            for pattern in state.robots_disallowed_paths
        ):
            return {
                "success": False,
                "error": "robots disallow",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        manager._domains[domain] = state
        # Preserve configured shared origin pacing. Robots are already loaded,
        # so check cannot initiate an uninstrumented robots request.
        gate = await manager.check(url)
        if reads.halted or gate.action == "blocked":
            return {
                "success": False,
                "error": "politeness refused",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        if gate.delay_seconds:
            await asyncio.sleep(gate.delay_seconds)
        status, raw, headers = await reads.get(url, role="page")
        counts["source_http"] = 1
        if status != 200:
            return {
                "success": False,
                "error": f"source HTTP {status}",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        content_type = headers.get("content-type", "")
        if not any(
            t in content_type for t in ("text/html", "text/plain", "text/markdown")
        ):
            return {
                "success": False,
                "error": "non-text source refused",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        text = raw.decode("utf-8", errors="replace")
        markdown = html_to_markdown(text) if "html" in content_type else text
        result = _add_quality(
            {
                "markdown": markdown,
                "source": "isolated-protected-generic-get",
                "url": url,
                "raw_html_start": text[:10000] if "html" in content_type else "",
            }
        )
        checks = (result.get("quality") or {}).get("checks", {})
        if result.get("barrier") or checks.get("block_detected") in ("warn", "fail"):
            return {
                "success": False,
                "error": "barrier refused",
                "lower_call_upper": counts,
                "events": reads.events,
            }
        manager.record_request(url)
        return {
            "success": True,
            "data": result,
            "lower_call_upper": counts,
            "events": reads.events,
            "captured_at": time.time(),
            "raw_source": text,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
        }
    except ValueError:
        if any(event["state"] == "outcome_unknown" for event in reads.events):
            raise
        return {
            "success": False,
            "error": "source redirect or destination refused",
            "lower_call_upper": counts,
            "events": reads.events,
        }
    except BaseException:
        # Do not turn an unknown/failed robots operation into page permission.
        raise


async def operation(packet: dict[str, Any]) -> dict[str, Any]:
    kind = packet["kind"]
    if kind == "describe":
        return describe(packet["role"])
    if kind == "acquire":
        return await acquire(packet["body"])
    return await service_request(kind, packet["body"])


def main() -> None:
    import sys

    packet = json.load(sys.stdin)
    try:
        result = asyncio.run(asyncio.wait_for(operation(packet), 14))
        output = {
            "operation_id": packet["operation_id"],
            "state": "terminal",
            "result": result,
        }
    except BaseException as exc:
        output = {
            "operation_id": packet.get("operation_id"),
            "state": "outcome_unknown",
            "error_type": type(exc).__name__,
        }
    sys.stdout.write("W12_RECEIPT:" + json.dumps(output) + "\n")
    sys.stdout.flush()
