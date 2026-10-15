"""Opt-in SlopSearX provenance boundary selected by ADR-0082.

This module deliberately does not expose SlopSearX workflow state or transfer
research completion, verification, synthesis, or publication authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

PROFILE = "retrieval-provenance-v1"
HANDOFF_CONTRACT = "slopsearx.retrieval_handoff"
HANDOFF_VERSION = 1
REFERENCE_SCHEMA = "groktocrawl.slopsearx_provenance_reference/1"
MAX_REFERENCE_BYTES = 8192
REQUIRED_GRANT = "retrieval_receipts"
FORBIDDEN_GRANTS = frozenset(
    {"jobs", "research", "staged_search", "saved_searches", "dependency_dossier"}
)


class ProvenanceUnavailableError(RuntimeError):
    """The opt-in provenance dependency cannot prove a safe stable reference."""


class ToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _decode_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    if "text/event-stream" not in response.headers.get("content-type", "").casefold():
        value = response.json()
        if isinstance(value, dict):
            return value
        raise ProvenanceUnavailableError("MCP response was not an object")
    data: list[str] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data.append(line.removeprefix("data:").lstrip())
        elif not line and data:
            break
    if not data:
        raise ProvenanceUnavailableError("MCP response contained no data event")
    value = json.loads("\n".join(data))
    if not isinstance(value, dict):
        raise ProvenanceUnavailableError("MCP data event was not an object")
    return value


def _tool_payload(envelope: dict[str, Any]) -> Any:
    if "error" in envelope:
        error = envelope["error"]
        code = error.get("code", "transport_error") if isinstance(error, dict) else "transport_error"
        raise ProvenanceUnavailableError(f"MCP request failed: {code}")
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise ProvenanceUnavailableError("MCP tool response lacked a result")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return result


class McpProvenanceTransport:
    """Small lazy async MCP transport scoped to the provenance credential."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("provenance MCP endpoint must be absolute HTTP(S)")
        if not token:
            raise ValueError("provenance MCP token is required")
        self._endpoint = endpoint
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self._session_id: str | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self._session_id:
            headers.update(
                {
                    "Mcp-Session-Id": self._session_id,
                    "MCP-Protocol-Version": "2025-11-25",
                }
            )
        try:
            response = await self._client.post(self._endpoint, json=body, headers=headers)
            response.raise_for_status()
            envelope = _decode_response(response)
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise ProvenanceUnavailableError(
                f"MCP transport failed: {type(error).__name__}"
            ) from error
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return envelope

    async def _initialize(self) -> None:
        if self._session_id:
            return
        envelope = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "groktocrawl-provenance", "version": "1"},
                },
            }
        )
        self._next_id += 1
        if not self._session_id or not isinstance(envelope.get("result"), dict):
            raise ProvenanceUnavailableError("MCP initialization failed")
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            await self._initialize()
            envelope = await self._post(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
            self._next_id += 1
            return _tool_payload(envelope)


@dataclass(frozen=True)
class CaptureObservation:
    status: str
    final_url: str | None = None
    content_sha256: str | None = None
    capture_ref: str | None = None
    captured_at: str | None = None
    failure_code: str | None = None

    def receipt_fields(self) -> dict[str, Any]:
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("capture status must be succeeded or failed")
        fields = {key: value for key, value in asdict(self).items() if value is not None}
        if self.status == "succeeded" and not {
            "final_url",
            "content_sha256",
            "capture_ref",
            "captured_at",
        } <= fields.keys():
            raise ValueError("successful capture observation is incomplete")
        if self.status == "failed" and "failure_code" not in fields:
            raise ValueError("failed capture observation requires a failure code")
        return fields


@dataclass(frozen=True)
class ProvenanceReference:
    """Stable internal reference; deliberately contains no remote job state."""

    schema_version: str
    contract: str
    contract_version: int
    snapshot_id: str
    result_id: str
    receipt_id: str
    manifest_sha256: str
    observations_verified: bool = False
    publishable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def reference_document(reference: ProvenanceReference) -> tuple[bytes, str]:
    value = reference.as_dict()
    if value.get("schema_version") != REFERENCE_SCHEMA:
        raise ValueError("unsupported provenance reference schema")
    if value.get("observations_verified") is not False or value.get("publishable") is not False:
        raise ValueError("provenance reference cannot grant verification or publication")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_REFERENCE_BYTES:
        raise ValueError("provenance reference byte limit exceeded")
    return raw, hashlib.sha256(raw).hexdigest()


def receipt_idempotency_key(
    *, owner_id: str, result_id: str, observation: CaptureObservation
) -> str:
    """Return a stable key across timeout, process restart, and replay."""
    payload = json.dumps(
        {
            "owner_id": owner_id,
            "result_id": result_id,
            "observation": observation.receipt_fields(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"gtcx-prov-{hashlib.sha256(payload).hexdigest()[:40]}"


def _object(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceUnavailableError(f"{operation} returned no object")
    if "error" in value:
        error = value["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise ProvenanceUnavailableError(f"{operation} failed: {code}")
    return value


class SlopSearXProvenanceAdapter:
    """Least-privilege adapter for handoffs, receipts, and manifest references."""

    def __init__(self, caller: ToolCaller, *, profile: str = PROFILE) -> None:
        if profile != PROFILE:
            raise ValueError(f"unsupported SlopSearX provenance profile: {profile}")
        self._caller = caller

    async def check_profile(self) -> dict[str, Any]:
        capabilities = _object(
            await self._caller.call_tool("slopsearx_get_service_status", {}),
            "capability check",
        )
        grants = capabilities.get("grants") or {}
        enabled = set(grants.get("enabled") or [])
        if REQUIRED_GRANT not in enabled or enabled & FORBIDDEN_GRANTS:
            raise ProvenanceUnavailableError("least-privilege provenance profile is not active")
        workflow = (capabilities.get("workflow_health") or {}).get("retrieval_receipt") or {}
        if workflow.get("status") != "available":
            raise ProvenanceUnavailableError("retrieval receipt workflow is unavailable")
        return {
            "profile": PROFILE,
            "status": "available",
            "snapshot_ttl_seconds": (capabilities.get("policy_bounds") or {}).get(
                "snapshot_ttl_seconds"
            ),
        }

    async def record(
        self,
        *,
        owner_id: str,
        result_id: str,
        retriever: str,
        observation: CaptureObservation,
    ) -> ProvenanceReference:
        if not owner_id or not result_id or not retriever:
            raise ValueError("owner, result, and retriever identities are required")
        expanded = _object(
            await self._caller.call_tool(
                "slopsearx_read_result", {"result_id": result_id}
            ),
            "read result",
        )
        handoff = expanded.get("retrieval")
        if (
            not isinstance(handoff, dict)
            or handoff.get("contract") != HANDOFF_CONTRACT
            or handoff.get("version") != HANDOFF_VERSION
            or handoff.get("result_id") != result_id
        ):
            raise ProvenanceUnavailableError("retrieval handoff contract mismatch")
        provenance = handoff.get("provenance") or {}
        snapshot_id = provenance.get("snapshot_cursor")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ProvenanceUnavailableError("retrieval snapshot identity is missing")

        receipt = _object(
            await self._caller.call_tool(
                "slopsearx_submit_retrieval_receipt",
                {
                    "result_id": result_id,
                    "retriever": retriever,
                    "idempotency_key": receipt_idempotency_key(
                        owner_id=owner_id,
                        result_id=result_id,
                        observation=observation,
                    ),
                    **observation.receipt_fields(),
                },
            ),
            "submit receipt",
        )
        if receipt.get("state") not in {"created", "replayed"}:
            raise ProvenanceUnavailableError("receipt was not durably accepted")
        receipt_value = receipt.get("receipt") or {}
        receipt_id = receipt_value.get("receipt_id")
        if not isinstance(receipt_id, str) or receipt_value.get("result_id") != result_id:
            raise ProvenanceUnavailableError("receipt identity is missing or mismatched")

        manifest = _object(
            await self._caller.call_tool(
                "slopsearx_export_research_manifest", {"result_ids": [result_id]}
            ),
            "export manifest",
        )
        items = manifest.get("items") or []
        if not any(item.get("result_id") == result_id for item in items):
            raise ProvenanceUnavailableError("research manifest omitted the result")
        if manifest.get("observations_verified") is not False:
            raise ProvenanceUnavailableError("manifest blurred observation and verification")
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        return ProvenanceReference(
            schema_version=REFERENCE_SCHEMA,
            contract=HANDOFF_CONTRACT,
            contract_version=HANDOFF_VERSION,
            snapshot_id=snapshot_id,
            result_id=result_id,
            receipt_id=receipt_id,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )
