#!/usr/bin/env python3
"""Offline installed search pipeline probe; all provider HTTP is mocked."""

import asyncio
import json
import socket

blocked_connects = 0


def deny_connect(*args, **kwargs):
    global blocked_connects
    blocked_connects += 1
    raise RuntimeError("network disabled for offline search probe")


socket.socket.connect = deny_connect

import httpx
from engines.brave import BraveAdapter
from slopsearx.service import AppContext, SearchRequest, SearchService


async def check(status):
    sends = []

    def send(request):
        sends.append(str(request.url))
        return httpx.Response(
            status,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://public.example/fixture",
                            "title": "Synthetic",
                            "description": "Synthetic evidence",
                        }
                    ]
                }
            },
        )

    transport = httpx.MockTransport(send)
    engine = BraveAdapter({"api_key": "synthetic-no-credential", "max_results": 2})
    engine.set_http_transport(transport)
    service = SearchService(AppContext(active_engines={"brave": engine}))
    request = SearchRequest(
        query="synthetic probe", engines=["brave"], generate_suggestions=False
    )
    scope = await service.resolve_scope(request)
    await service.search(request, resolved_scope=scope)
    await engine.shutdown()
    await transport.aclose()
    return {
        "fixture_status": status,
        "selected_engines": scope.selected_engines,
        "provider_http_dispatches": len(sends),
    }


async def main():
    results = [await check(status) for status in (200, 403, 429, 500)]
    print(
        json.dumps(
            {
                "schema_version": "w12.4-installed-search-offline-probe/1",
                "results": results,
                "real_socket_connects_denied": blocked_connects,
                "real_provider_calls": 0,
                "passed": all(
                    x["selected_engines"] == ["brave"]
                    and x["provider_http_dispatches"] == 1
                    for x in results
                ),
            }
        )
    )


asyncio.run(main())
