"""Model-only egress proxy with fixed authorities and numeric peer pinning."""

from __future__ import annotations

import asyncio
import ipaddress
import os
from urllib.parse import urlsplit

from common.capture_destination import (
    DestinationDeniedError,
    DestinationError,
    parse_authority,
    resolve_and_connect_model_target,
)

from .proxy import CaptureEgressProxy, ProxyConfig, configure_model_proxy_logging


def _configured_authorities() -> frozenset[str]:
    authorities: set[str] = set()
    for variable in ("LLM_BASE_URL", "CAPTCHA_VISION_BASE_URL"):
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            authority = parse_authority(
                f"[{parsed.hostname}]:{port}"
                if ":" in parsed.hostname
                else f"{parsed.hostname}:{port}"
            )
        except (ValueError, DestinationError) as exc:
            raise ValueError(f"invalid model target in {variable}") from exc
        authorities.add(f"{authority.host}:{authority.port}")
    if not authorities:
        raise ValueError("at least one fixed model target must be configured")
    return frozenset(authorities)


def _private_host_grants() -> frozenset[str]:
    raw = os.environ.get("LLM_GATEWAY_PRIVATE_HOSTS", "")
    grants: set[str] = set()
    for item in raw.split(","):
        host = item.strip().lower().rstrip(".")
        if not host:
            continue
        if ":" in host or "/" in host or "@" in host:
            raise ValueError("private model host grants must be hostnames")
        try:
            parsed = parse_authority(f"{host}:443")
        except DestinationError as exc:
            raise ValueError("invalid private model host grant") from exc
        grants.add(parsed.host)
    return frozenset(grants)


def make_model_proxy(
    authorities: frozenset[str],
    private_host_grants: frozenset[str],
    *,
    connector=resolve_and_connect_model_target,
) -> CaptureEgressProxy:
    parsed_authorities = frozenset(
        (parse_authority(value).host, parse_authority(value).port)
        for value in authorities
    )
    ports = frozenset(port for _, port in parsed_authorities)

    async def connect(raw_authority: str):
        try:
            parsed = parse_authority(raw_authority)
        except DestinationError as exc:
            raise DestinationDeniedError() from exc
        if (parsed.host, parsed.port) not in parsed_authorities:
            raise DestinationDeniedError()
        return await connector(
            raw_authority, private_host_grants=private_host_grants
        )

    return CaptureEgressProxy(
        ProxyConfig(
            allowed_ports=ports,
            max_connections=16,
            max_body_bytes=8 * 1024 * 1024,
            max_transfer_bytes=32 * 1024 * 1024,
            max_connection_seconds=150,
            relay_idle_timeout=60,
        ),
        destination_connector=connect,
        trace_http_events=True,
    )


async def serve() -> None:
    configure_model_proxy_logging()
    proxy = make_model_proxy(_configured_authorities(), _private_host_grants())
    bind_host = os.environ.get("MODEL_PROXY_BIND_HOST", "0.0.0.0")
    try:
        ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise ValueError("model proxy bind host must be a numeric IP") from exc
    server = await asyncio.start_server(
        proxy.handle_client,
        host=bind_host,
        port=8080,
        limit=proxy.config.max_header_bytes,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
