#!/usr/bin/env python3
"""Verify an isolated replacement candidate and write a sanitized receipt."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _run_bytes(command: list[str], *, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(command, check=True, capture_output=True, env=env)
    return result.stdout


def _sse_payload(response: httpx.Response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return response.json()


def _json_records(value: str) -> list[dict[str, Any]]:
    """Accept Compose's array or newline-delimited JSON output variants."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in value.splitlines() if line.strip()]
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise RuntimeError("unexpected Docker Compose JSON output")


def _compose_service_receipts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts = [
        {
            "service": record.get("Service"),
            "image": record.get("Image"),
            "state": record.get("State"),
            "health": record.get("Health"),
            "ports": record.get("Ports", ""),
        }
        for record in records
    ]
    unhealthy = [
        receipt["service"]
        for receipt in receipts
        if receipt["state"] != "running" or receipt["health"] != "healthy"
    ]
    if unhealthy:
        raise RuntimeError(f"candidate services are not healthy: {sorted(unhealthy)}")
    return sorted(receipts, key=lambda receipt: str(receipt["service"]))


def _compose_image_receipts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts = [
        {
            "repository": record.get("Repository"),
            "tag": record.get("Tag", ""),
            "id": record.get("ID"),
            "platform": record.get("Platform"),
            "size_bytes": record.get("Size"),
        }
        for record in records
    ]
    return sorted(
        receipts,
        key=lambda receipt: (str(receipt["repository"]), str(receipt["tag"])),
    )


async def _mcp_call(
    client: httpx.AsyncClient,
    session: str,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Session-Id": session,
        },
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    response.raise_for_status()
    payload = _sse_payload(response)
    if payload.get("error") or payload.get("result", {}).get("isError"):
        raise RuntimeError(f"MCP tool {name} failed")
    return payload


def _mcp_result(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            parsed = json.loads(item["text"])
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError("MCP tool returned no JSON object")


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    api_headers = {"Authorization": f"Bearer {args.api_key}"}
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=api_headers, timeout=args.timeout
    ) as client:
        health_response = await client.get("/health")
        health_response.raise_for_status()
        health = health_response.json()
        if health.get("status") != "ok":
            raise RuntimeError(f"candidate health is {health.get('status')!r}")
        runtime = health.get("runtime", {})
        expected_revision = _run(["git", "rev-parse", "HEAD"])
        if runtime.get("revision") != expected_revision:
            raise RuntimeError(
                "candidate runtime revision does not match the checked-out source"
            )
        if not str(runtime.get("model", "")).strip():
            raise RuntimeError("candidate runtime does not report its model alias")

        capability_response = await client.get("/experimental/research/v1/capabilities")
        capability_response.raise_for_status()
        capabilities = capability_response.json()
        if (
            capabilities.get("implementation_stage")
            != "postgres_artifact_authority_adapter"
        ):
            raise RuntimeError("PostgreSQL artifact authority is not active")

        admitted_response = await client.post(
            "/experimental/research/v1/runs",
            headers={**api_headers, "Idempotency-Key": f"candidate-{time.time_ns()}"},
            json={
                "objective": "Verify the isolated replacement candidate artifact journey."
            },
        )
        admitted_response.raise_for_status()
        admitted = admitted_response.json()
        run_id = admitted["run_id"]

        deadline = time.monotonic() + args.timeout
        run: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status_response = await client.get(
                f"/experimental/research/v1/runs/{run_id}"
            )
            status_response.raise_for_status()
            run = status_response.json()
            if run["state"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.25)
        if run.get("state") != "completed":
            raise RuntimeError(f"research journey ended in {run.get('state')!r}")

        events_response = await client.get(
            f"/experimental/research/v1/runs/{run_id}/events"
        )
        events_response.raise_for_status()
        event_names = [
            line[6:].strip()
            for line in events_response.text.splitlines()
            if line.startswith("event:")
        ]
        if "done" not in event_names:
            raise RuntimeError("SSE replay did not contain the terminal event")

        result = run.get("result") or {}
        artifact_set_id = result.get("artifact_set_id")
        manifest_path = result.get("manifest_url")
        artifacts = result.get("artifacts") or {}
        if (
            not artifact_set_id
            or not manifest_path
            or set(artifacts) != {"summary", "analysis", "dossier"}
        ):
            raise RuntimeError("completed run did not expose the complete artifact set")
        manifest_response = await client.get(manifest_path)
        manifest_response.raise_for_status()
        artifact_digests: dict[str, str] = {}
        for layer, path in artifacts.items():
            artifact_response = await client.get(path)
            artifact_response.raise_for_status()
            artifact_digests[layer] = hashlib.sha256(
                artifact_response.content
            ).hexdigest()

        search_response = await client.post(
            "/v2/search",
            json={
                "query": "agentic engineering software factory enterprise",
                "limit": 3,
            },
        )
        search_response.raise_for_status()
        search_payload = search_response.json()
        search_result_count = len(search_payload.get("data", {}).get("web", []))
        if not search_payload.get("success") or search_result_count == 0:
            raise RuntimeError("HTTP search returned no usable results")

        # Basic service health and retrieval can remain green while the
        # configured model alias is unavailable. Exercise the same public
        # model-backed path used by research, once for text and once for
        # schema-constrained output. Keep the receipt to status and shape;
        # provider bodies and credentials are intentionally never persisted.
        model_probes: dict[str, dict[str, Any]] = {}
        for probe_name, output_schema in (
            ("text", None),
            (
                "structured",
                {
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                    "additionalProperties": False,
                },
            ),
        ):
            started = time.monotonic()
            payload: dict[str, Any] = {
                "query": "What is retrieval augmented generation?",
                "limit": 1,
                "search_type": "rich",
                "system_prompt": "Answer in one short sentence.",
            }
            if output_schema is not None:
                payload["output_schema"] = output_schema
            response = await client.post("/v2/search", json=payload)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"model_provider/{probe_name} readiness failed "
                    f"with HTTP {response.status_code // 100}xx"
                )
            body = response.json()
            if not body.get("success"):
                raise RuntimeError(f"model_provider/{probe_name} readiness failed")
            if output_schema is not None and not isinstance(body.get("output"), dict):
                raise RuntimeError(
                    "model_provider/structured readiness returned no structured output"
                )
            model_probes[probe_name] = {
                "status": "ready",
                "http_class": "2xx",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }

    cli_env = {**os.environ, "GROKTOCRAWL_API_KEY": args.api_key}
    cli_search = json.loads(
        _run(
            [
                str(Path(__file__).parents[1] / "groktocrawl"),
                "--server",
                args.base_url,
                "--json",
                "search",
                "agentic engineering software factory enterprise",
                "--limit",
                "3",
            ],
            env=cli_env,
        )
    )
    cli_result_count = len(cli_search.get("results", []))
    if cli_result_count == 0:
        raise RuntimeError("CLI search returned no usable results")
    cli_status = json.loads(
        _run(
            [
                str(Path(__file__).parents[1] / "groktocrawl"),
                "--server",
                args.base_url,
                "--json",
                "research",
                "status",
                run_id,
            ],
            env=cli_env,
        )
    )
    cli_manifest = json.loads(
        _run(
            [
                str(Path(__file__).parents[1] / "groktocrawl"),
                "--server",
                args.base_url,
                "--json",
                "research",
                "show",
                artifact_set_id,
            ],
            env=cli_env,
        )
    )
    summary_artifact_id = str(artifacts["summary"]).rstrip("/").rsplit("/", 1)[-1]
    cli_summary = _run_bytes(
        [
            str(Path(__file__).parents[1] / "groktocrawl"),
            "--server",
            args.base_url,
            "research",
            "download",
            summary_artifact_id,
        ],
        env=cli_env,
    )
    if (
        cli_status.get("state") != "completed"
        or cli_manifest.get("artifact_set_id") != artifact_set_id
        or hashlib.sha256(cli_summary).hexdigest() != artifact_digests["summary"]
    ):
        raise RuntimeError("CLI research artifact journey did not reproduce HTTP state")

    async with httpx.AsyncClient(
        base_url=args.mcp_url,
        headers=api_headers,
        timeout=args.timeout,
    ) as mcp:
        initialized = await mcp.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "candidate-verifier", "version": "1"},
                },
            },
        )
        initialized.raise_for_status()
        session = initialized.headers.get("mcp-session-id")
        if not session:
            raise RuntimeError("MCP initialize returned no session")
        mcp_capabilities = _mcp_result(
            await _mcp_call(mcp, session, "research_capabilities")
        )
        mcp_status = _mcp_result(
            await _mcp_call(mcp, session, "research_status", {"run_id": run_id})
        )
        mcp_manifest = _mcp_result(
            await _mcp_call(
                mcp,
                session,
                "research_show",
                {"artifact_set_id": artifact_set_id},
            )
        )
        mcp_summary = _mcp_result(
            await _mcp_call(
                mcp,
                session,
                "research_artifact",
                {"artifact_id": summary_artifact_id},
            )
        )
        decoded_mcp_summary = base64.b64decode(
            mcp_summary.get("content_base64", ""), validate=True
        )
        if (
            mcp_status.get("state") != "completed"
            or mcp_manifest.get("artifact_set_id") != artifact_set_id
            or hashlib.sha256(decoded_mcp_summary).hexdigest()
            != artifact_digests["summary"]
        ):
            raise RuntimeError(
                "MCP research artifact journey did not reproduce HTTP state"
            )

    compose_files = args.compose_file or ["compose.experimental-candidate.yml"]
    compose = ["docker", "compose", "--env-file", args.env_file]
    for compose_file in compose_files:
        compose.extend(("-f", compose_file))
    schema_version = _run(
        [
            *compose,
            "exec",
            "-T",
            "candidate-postgres",
            "psql",
            "-U",
            "groktocrawl_x",
            "-d",
            "groktocrawl_x",
            "-Atc",
            "SELECT version FROM research_staging.schema_version",
        ]
    )
    pgvector_version = _run(
        [
            *compose,
            "exec",
            "-T",
            "candidate-postgres",
            "psql",
            "-U",
            "groktocrawl_x",
            "-d",
            "groktocrawl_x",
            "-Atc",
            "SELECT extversion FROM pg_extension WHERE extname='vector'",
        ]
    )
    if schema_version != "14" or not pgvector_version:
        raise RuntimeError("PostgreSQL schema or pgvector extension is unavailable")
    semantic_health = json.loads(
        _run(
            [
                *compose,
                "exec",
                "-T",
                "candidate-semantic",
                "python",
                "-c",
                (
                    "import json,urllib.request;"
                    "print(json.dumps(json.load(urllib.request.urlopen("
                    "'http://127.0.0.1:8003/ready',timeout=5))))"
                ),
            ]
        )
    )
    if (
        semantic_health.get("vector_store") != "pgvector"
        or semantic_health.get("pgvector") != "ready"
        or semantic_health.get("qdrant") != "rollback_ready"
    ):
        raise RuntimeError("semantic serving or rollback readiness is unavailable")

    compose_services = _compose_service_receipts(
        _json_records(_run([*compose, "ps", "--format", "json"]))
    )
    compose_images = _compose_image_receipts(
        _json_records(_run([*compose, "images", "--format", "json"]))
    )

    return {
        "schema_version": "experimental-candidate-verification/1",
        "verified_at_unix": int(time.time()),
        "git_revision": expected_revision,
        "source": {
            "compose_sha256": hashlib.sha256(
                b"".join(
                    len(content).to_bytes(8, "big") + content
                    for content in (Path(file).read_bytes() for file in compose_files)
                )
            ).hexdigest(),
            "uv_lock_sha256": hashlib.sha256(
                (Path(__file__).parents[1] / "uv.lock").read_bytes()
            ).hexdigest(),
        },
        "service_health": health,
        "runtime": runtime,
        "research": {
            "run_id": run_id,
            "research_id": run["research_id"],
            "state": run["state"],
            "sse_events": event_names,
            "manifest_sha256": hashlib.sha256(manifest_response.content).hexdigest(),
            "artifact_sha256": artifact_digests,
        },
        "postgres": {
            "research_schema_version": int(schema_version),
            "pgvector_version": pgvector_version,
        },
        "semantic": semantic_health,
        "search": {"result_count": search_result_count},
        "model_provider": model_probes,
        "cli": {
            "search_result_count": cli_result_count,
            "research_state": cli_status["state"],
            "artifact_set_match": True,
            "summary_digest_match": True,
        },
        "mcp": {
            "research_capabilities": bool(mcp_capabilities.get("protocol_version")),
            "research_state": mcp_status["state"],
            "artifact_set_match": True,
            "summary_digest_match": True,
        },
        "compose": {
            "version": _run(["docker", "compose", "version", "--short"]),
            "services": compose_services,
            "images": compose_images,
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--base-url", default="http://127.0.0.1:18080")
    value.add_argument("--mcp-url", default="http://127.0.0.1:18002")
    value.add_argument("--api-key", default=os.environ.get("CANDIDATE_API_KEY", ""))
    value.add_argument("--env-file", required=True)
    value.add_argument("--compose-file", action="append")
    value.add_argument("--timeout", type=float, default=300)
    value.add_argument("--output", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    if not args.api_key:
        raise SystemExit("CANDIDATE_API_KEY or --api-key is required")
    receipt = asyncio.run(verify(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
