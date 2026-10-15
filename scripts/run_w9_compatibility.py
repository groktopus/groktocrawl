#!/usr/bin/env python3
"""Run one arm of the frozen W9 incumbent compatibility comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURE_URL = "https://docs.python.org/3/tutorial/introduction.html"
QUERY = "agentic engineering software factory enterprise"
QUESTION = (
    "What distinguishes an agentic engineering software factory from ordinary "
    "CI automation?"
)
TERMINAL = {"completed", "failed", "cancelled"}


def digest(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def timed(call: Callable[[], httpx.Response]) -> tuple[httpx.Response, float]:
    started = time.monotonic()
    response = call()
    return response, round((time.monotonic() - started) * 1000, 3)


def require(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("expected a JSON object")
    return value


def poll_job(
    client: httpx.Client, path: str, timeout: float
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    deadline = started + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = require(client.get(path))
        state = str(latest.get("status", latest.get("state", "")))
        if state in TERMINAL:
            return latest, round((time.monotonic() - started) * 1000, 3)
        time.sleep(0.5)
    raise TimeoutError(f"job did not finish: {path}")


def sse_summary(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            events.append(parsed)
    types = [str(item.get("type", item.get("event", "unknown"))) for item in events]
    if "done" not in types:
        raise RuntimeError(f"SSE stream has no done event: {types}")
    return {
        "event_types": types,
        "event_count": len(events),
        "body_sha256": digest(response.content),
    }


def command_json(
    command: list[str], env: dict[str, str]
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, env=env
    )
    elapsed = round((time.monotonic() - started) * 1000, 3)
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("CLI returned no JSON object")
    return value, elapsed


def mcp_payload(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            value = json.loads(line[5:].strip())
            if value.get("error") or value.get("result", {}).get("isError"):
                raise RuntimeError("MCP returned an error")
            return value
    value = response.json()
    if value.get("error"):
        raise RuntimeError("MCP returned an error")
    return value


def mcp_text(payload: dict[str, Any]) -> dict[str, Any]:
    for item in payload.get("result", {}).get("content", []):
        if item.get("type") == "text":
            value = json.loads(item["text"])
            if isinstance(value, dict):
                return value
    raise RuntimeError("MCP returned no JSON tool content")


def mcp_call(
    client: httpx.Client, session: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    return mcp_text(
        mcp_payload(
            client.post(
                "/mcp",
                headers={"MCP-Session-Id": session},
                json={
                    "jsonrpc": "2.0",
                    "id": time.time_ns(),
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
        )
    )


def create_webhook(client: httpx.Client) -> str:
    response = client.post("https://webhook.site/token", json={"request_limit": 20})
    response.raise_for_status()
    token = response.json().get("uuid")
    if not token:
        raise RuntimeError("webhook token creation failed")
    return str(token)


def webhook_receipt(
    client: httpx.Client, token: str, job_id: str, timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"https://webhook.site/token/{token}/requests", params={"sorting": "newest"}
        )
        response.raise_for_status()
        items = response.json().get("data", [])
        for item in items:
            raw = item.get("content", "")
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if str(payload.get("id", payload.get("jobId", ""))) == job_id:
                return {
                    "received": True,
                    "method": item.get("method"),
                    "event": payload.get("type", payload.get("event")),
                    "job_id_match": True,
                    "payload_sha256": digest(raw),
                }
        time.sleep(1)
    raise TimeoutError("matching webhook was not observed")


def run_trial(args: argparse.Namespace, repetition: int) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    webhook_client = httpx.Client(timeout=args.timeout)
    token = create_webhook(webhook_client)
    callback = f"https://webhook.site/{token}"
    try:
        with httpx.Client(
            base_url=args.base_url, headers=headers, timeout=args.timeout
        ) as client:
            health_response, health_ms = timed(lambda: client.get("/health"))
            health = require(health_response)

            scrape_response, scrape_ms = timed(
                lambda: client.post(
                    "/v2/scrape", json={"url": FIXTURE_URL, "formats": ["markdown"]}
                )
            )
            scrape = require(scrape_response)
            markdown = str((scrape.get("data") or {}).get("markdown", ""))
            if not scrape.get("success") or not markdown:
                raise RuntimeError("scrape returned no markdown")

            crawl_response, crawl_admit_ms = timed(
                lambda: client.post(
                    "/v2/crawl",
                    json={
                        "url": FIXTURE_URL,
                        "limit": 2,
                        "maxDepth": 1,
                        "sitemap": "skip",
                        "webhook": {"url": callback, "events": ["crawl.completed"]},
                    },
                )
            )
            crawl_admitted = require(crawl_response)
            crawl_id = str(crawl_admitted["id"])
            crawl, crawl_poll_ms = poll_job(
                client, f"/v2/crawl/{crawl_id}", args.timeout
            )
            if crawl.get("status") != "completed":
                raise RuntimeError(f"crawl ended in {crawl.get('status')}")
            webhook = webhook_receipt(webhook_client, token, crawl_id, args.timeout)

            search_response, search_ms = timed(
                lambda: client.post(
                    "/v2/search",
                    json={"query": QUERY, "limit": 3, "search_type": "fast"},
                )
            )
            search = require(search_response)
            search_results = (search.get("data") or {}).get("web", [])
            if not search.get("success") or not search_results:
                raise RuntimeError("search returned no results")

            answer_response, answer_ms = timed(
                lambda: client.post(
                    "/v2/answer", json={"query": QUESTION, "num_sources": 3}
                )
            )
            answer = require(answer_response)
            if not answer.get("success") or not answer.get("answer"):
                raise RuntimeError("answer returned no output")

            answer_stream_response, answer_stream_ms = timed(
                lambda: client.post(
                    "/v2/answer",
                    json={"query": QUESTION, "num_sources": 3, "stream": True},
                )
            )
            answer_stream = sse_summary(answer_stream_response)

            agent_response, agent_admit_ms = timed(
                lambda: client.post(
                    "/v2/agent",
                    json={
                        "prompt": QUESTION,
                        "search_type": "focused",
                        "max_credits": 3,
                    },
                )
            )
            agent_admitted = require(agent_response)
            agent_id = str(agent_admitted["id"])
            agent, agent_poll_ms = poll_job(
                client, f"/v2/agent/{agent_id}", args.timeout
            )
            if agent.get("status") != "completed":
                raise RuntimeError(f"agent ended in {agent.get('status')}")

            agent_stream_response, agent_stream_ms = timed(
                lambda: client.post(
                    "/v2/agent",
                    json={
                        "prompt": QUESTION,
                        "search_type": "focused",
                        "max_credits": 3,
                        "stream": True,
                    },
                )
            )
            agent_stream = sse_summary(agent_stream_response)

        cli_env = {**os.environ}
        if args.api_key:
            cli_env["GROKTOCRAWL_API_KEY"] = args.api_key
        cli = str(Path(args.cli).resolve())
        cli_scrape, cli_scrape_ms = command_json(
            [cli, "--server", args.base_url, "--json", "scrape", FIXTURE_URL], cli_env
        )
        cli_search, cli_search_ms = command_json(
            [cli, "--server", args.base_url, "--json", "search", QUERY, "--limit", "3"],
            cli_env,
        )
        if not (cli_scrape.get("markdown") and cli_search.get("results")):
            raise RuntimeError("CLI compatibility journey failed")

        mcp_headers = {
            **headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if args.mcp_host_header:
            mcp_headers["Host"] = args.mcp_host_header
        with httpx.Client(
            base_url=args.mcp_url, headers=mcp_headers, timeout=args.timeout
        ) as mcp:
            initialized = mcp.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "w9-compatibility", "version": "1"},
                    },
                },
            )
            mcp_payload(initialized)
            session = initialized.headers.get("mcp-session-id")
            if not session:
                raise RuntimeError("MCP returned no session")
            mcp_scrape = mcp_call(mcp, session, "scrape", {"url": FIXTURE_URL})
            mcp_search = mcp_call(mcp, session, "search", {"query": QUERY, "limit": 3})
            if not (
                mcp_scrape.get("success") and (mcp_search.get("data") or {}).get("web")
            ):
                raise RuntimeError("MCP compatibility journey failed")

        pages = crawl.get("data") or []
        return {
            "repetition": repetition,
            "outcome": "completed",
            "health": {"status": health.get("status"), "latency_ms": health_ms},
            "scrape": {
                "latency_ms": scrape_ms,
                "markdown_bytes": len(markdown.encode()),
                "sha256": digest(markdown),
            },
            "crawl": {
                "admit_ms": crawl_admit_ms,
                "terminal_ms": crawl_poll_ms,
                "status": crawl["status"],
                "page_count": len(pages),
            },
            "webhook": webhook,
            "search": {"latency_ms": search_ms, "result_count": len(search_results)},
            "answer": {
                "latency_ms": answer_ms,
                "source_count": len(answer.get("sources", [])),
                "citation_count": len(answer.get("citations", [])),
                "answer_sha256": digest(str(answer["answer"])),
            },
            "answer_sse": {"latency_ms": answer_stream_ms, **answer_stream},
            "agent": {
                "admit_ms": agent_admit_ms,
                "terminal_ms": agent_poll_ms,
                "status": agent["status"],
                "result_sha256": digest(json.dumps(agent.get("data"), sort_keys=True)),
            },
            "agent_sse": {"latency_ms": agent_stream_ms, **agent_stream},
            "cli": {
                "scrape_ms": cli_scrape_ms,
                "search_ms": cli_search_ms,
                "search_result_count": len(cli_search["results"]),
            },
            "mcp": {
                "scrape_success": True,
                "search_result_count": len(
                    (mcp_search.get("data") or {}).get("web", [])
                ),
            },
        }
    finally:
        try:
            webhook_client.delete(f"https://webhook.site/token/{token}")
        finally:
            webhook_client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("incumbent", "candidate"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--mcp-host-header")
    parser.add_argument("--api-key", default=os.environ.get("GROKTOCRAWL_API_KEY", ""))
    parser.add_argument("--cli", default="./groktocrawl")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    trials: list[dict[str, Any]] = []
    for repetition in range(args.repetitions):
        try:
            trials.append(run_trial(args, repetition))
        except Exception as error:
            trials.append(
                {
                    "repetition": repetition,
                    "outcome": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    receipt = {
        "schema_version": "w9-compatibility-arm/1",
        "arm": args.arm,
        "executed_at_unix": int(time.time()),
        "fixture_url": FIXTURE_URL,
        "query": QUERY,
        "question_sha256": digest(QUESTION),
        "repetitions": args.repetitions,
        "trials": trials,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0 if all(item["outcome"] == "completed" for item in trials) else 1


if __name__ == "__main__":
    raise SystemExit(main())
