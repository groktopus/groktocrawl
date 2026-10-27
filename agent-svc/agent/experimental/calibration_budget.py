"""Offline-tested dispatch boundary for an isolated W12.4 live calibration.

Not wired into application routes. Limits count HTTP dispatches at this boundary,
not opaque retries/fanout inside another service. Admission must verify those
backend contracts separately before this boundary may be used for live work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

Kind = Literal["search", "acquire", "model"]

_SCREENING_MAXIMA = {
    "attempts": 6,
    "search_per_attempt": 2,
    "acquire_per_attempt": 4,
    "model_per_attempt": 3,
    "output_tokens_per_dispatch": 256,
    "model_input_bytes_per_dispatch": 12000,
    "response_bytes_per_dispatch": 1024 * 1024,
    "request_seconds": 15,
    "attempt_seconds": 45,
    "experiment_seconds": 300,
}


class CalibrationStoppedError(RuntimeError):
    """No further external dispatch is permitted in this experiment."""


@dataclass(frozen=True)
class ScreeningCaps:
    """Six attempts: two public cases, three arms, one repetition."""

    attempts: int = 6
    search_per_attempt: int = 2
    acquire_per_attempt: int = 4
    model_per_attempt: int = 3
    output_tokens_per_dispatch: int = 256
    model_input_bytes_per_dispatch: int = 12000
    response_bytes_per_dispatch: int = 1024 * 1024
    request_seconds: float = 15
    attempt_seconds: float = 45
    experiment_seconds: float = 300

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("caps must be finite positive numbers")
            if not math.isfinite(value) or value <= 0:
                raise ValueError("caps must be finite positive numbers")
            if value > _SCREENING_MAXIMA[name]:
                raise ValueError(
                    "caps may be reduced, never raised above the frozen screen"
                )
        for name in (
            "attempts",
            "search_per_attempt",
            "acquire_per_attempt",
            "model_per_attempt",
            "output_tokens_per_dispatch",
            "model_input_bytes_per_dispatch",
            "response_bytes_per_dispatch",
        ):
            if not isinstance(getattr(self, name), int):
                raise ValueError("count and byte caps must be integers")


class ExclusiveJournal:
    """New local JSONL artifact only; never resume/replay an existing journal."""

    def __init__(self, path: Path) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._file = os.fdopen(descriptor, "w", encoding="utf-8")

    def __call__(self, event: dict[str, Any]) -> None:
        self._file.write(json.dumps(event, sort_keys=True) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()


@dataclass
class CalibrationLedger:
    """Reserve before dispatch; ambiguous operations latch a global stop."""

    experiment_id: str
    journal: Callable[[dict[str, Any]], None]
    caps: ScreeningCaps = field(default_factory=ScreeningCaps)
    clock: Callable[[], float] = time.monotonic
    events: list[dict[str, Any]] = field(default_factory=list, init=False)
    halted: str | None = field(default=None, init=False)
    _attempt: str | None = field(default=None, init=False)
    _seen_attempts: set[str] = field(default_factory=set, init=False)
    _counts: dict[str, int] = field(default_factory=dict, init=False)
    _pending: set[str] = field(default_factory=set, init=False)
    _started: float = field(init=False)
    _attempt_started: float = field(init=False)

    def __post_init__(self) -> None:
        self._started = self.clock()
        self._attempt_started = self._started

    def _record(self, **fields: Any) -> None:
        event = {
            "schema_version": "w12.4-calibration-dispatch/1",
            "experiment_id": self.experiment_id,
            "attempt_id": self._attempt,
            "sequence": len(self.events) + 1,
            "elapsed_seconds": self.clock() - self._started,
            "accounting_scope": "service_http_boundary",
            **fields,
        }
        try:
            self.journal(event)
        except Exception as exc:
            self.halted = "journal_failed"
            raise CalibrationStoppedError(
                "journal failed; no further dispatch"
            ) from exc
        self.events.append(event)

    def _stop(self, reason: str) -> None:
        self.halted = reason
        self._record(state="stopped", reason=reason)
        raise CalibrationStoppedError(reason)

    def start_attempt(self, attempt_id: str) -> None:
        if self.halted:
            raise CalibrationStoppedError(self.halted)
        if self._attempt is not None or attempt_id in self._seen_attempts:
            self._stop("attempt_overlap_or_replay")
        if len(self._seen_attempts) >= self.caps.attempts:
            self._stop("attempt_cap")
        if self.clock() - self._started >= self.caps.experiment_seconds:
            self._stop("experiment_deadline")
        self._attempt = attempt_id
        self._seen_attempts.add(attempt_id)
        self._counts = {"search": 0, "acquire": 0, "model": 0}
        self._attempt_started = self.clock()
        self._record(state="attempt_started")

    def remaining_seconds(self) -> float:
        return min(
            self.caps.experiment_seconds - (self.clock() - self._started),
            self.caps.attempt_seconds - (self.clock() - self._attempt_started),
            self.caps.request_seconds,
        )

    def reserve(self, kind: Kind, request: httpx.Request) -> str:
        if self.halted:
            raise CalibrationStoppedError(self.halted)
        if self._attempt is None:
            self._stop("no_active_attempt")
        if self.remaining_seconds() <= 0:
            self._stop("deadline")
        if self._counts[kind] >= getattr(self.caps, f"{kind}_per_attempt"):
            self._stop(f"{kind}_cap")
        operation_id = str(uuid.uuid4())
        self._record(
            state="dispatch_reserved",
            operation_id=operation_id,
            kind=kind,
            request_sha256=hashlib.sha256(request.content).hexdigest(),
            # Do not retain headers, credentials, questions or source URLs.
            endpoint_sha256=hashlib.sha256(str(request.url).encode()).hexdigest(),
            output_token_reservation=(
                self.caps.output_tokens_per_dispatch if kind == "model" else 0
            ),
            input_bytes=len(request.content),
        )
        self._counts[kind] += 1
        self._pending.add(operation_id)
        return operation_id

    def complete(self, operation_id: str, **fields: Any) -> None:
        self._record(state="response_complete", operation_id=operation_id, **fields)
        self._pending.remove(operation_id)

    def unknown(self, operation_id: str, reason: str) -> None:
        self.halted = "outcome_unknown"
        self._record(
            state="outcome_unknown",
            operation_id=operation_id,
            reason=reason,
            server_cancellation_confirmed=False,
        )
        # Keep it pending. A caller catching the exception cannot free the budget.

    def finish_attempt(self, status: str) -> None:
        if self._attempt is None:
            self._stop("no_active_attempt")
        if self._pending:
            self._stop("outcome_unknown")
        if self.halted:
            self._record(state="attempt_finished", status="failed", reason=self.halted)
            self._attempt = None
            raise CalibrationStoppedError(self.halted)
        self._record(state="attempt_finished", status=status)
        self._attempt = None


def inspect_journal(path: Path) -> dict[str, Any]:
    """Read-only recovery diagnosis, never authorize another dispatch."""
    events = [json.loads(line) for line in path.read_text().splitlines()]
    pending: set[str] = set()
    for event in events:
        if event["state"] == "dispatch_reserved":
            pending.add(event["operation_id"])
        elif event["state"] == "response_complete":
            pending.discard(event["operation_id"])
    return {
        "events": len(events),
        "unresolved_operation_ids": sorted(pending),
        "replay_authorized": False,
        "status": "outcome_unknown" if pending else "no_unresolved_dispatch_in_journal",
    }


class BudgetedTransport(httpx.AsyncBaseTransport):
    """Count every actual HTTP send, including any caller retries.

    The fixed endpoint comes from the existing configured service client, never
    from a question. Redirects to another endpoint fail before a second send.
    Model output caps and lightweight-only acquisition are request-local bounds;
    no process settings or service defaults are changed.
    """

    def __init__(
        self,
        ledger: CalibrationLedger,
        kind: Kind,
        endpoint: str,
        inner: httpx.AsyncBaseTransport,
        *,
        model_alias: str = "free",
        blocked_source_sha256: frozenset[str] = frozenset(),
    ) -> None:
        self.ledger = ledger
        self.kind = kind
        self.endpoint = httpx.URL(endpoint)
        self.inner = inner
        self.model_alias = model_alias
        self.blocked_source_sha256 = blocked_source_sha256

    def _bounded_request(self, request: httpx.Request) -> httpx.Request:
        endpoint = request.url.copy_with(query=None)
        if endpoint != self.endpoint.copy_with(query=None):
            self.ledger._stop("unexpected_endpoint")
        if self.kind == "search":
            if request.method not in {"GET", "POST"}:
                self.ledger._stop("unexpected_method")
            return request
        if request.method != "POST":
            self.ledger._stop("unexpected_method")
        try:
            body = json.loads(request.content)
        except (ValueError, UnicodeDecodeError) as exc:
            self.ledger._stop("invalid_request_json")
            raise AssertionError from exc  # _stop always raises
        if not isinstance(body, dict):
            self.ledger._stop("invalid_request_shape")
        if self.kind == "acquire":
            if (
                hashlib.sha256(str(body.get("url", "")).encode()).hexdigest()
                in self.blocked_source_sha256
            ):
                self.ledger._stop("unresolved_previous_source_operation")
            options = body.get("scrape_options", {"formats": ["markdown"]})
            if set(body) - {"url", "lightweight_only", "scrape_options"} or options != {
                "formats": ["markdown"]
            }:
                self.ledger._stop("unsupported_acquisition_options")
            if (
                body.get("lightweight_only") is not True
                or body.get("force_browser")
                or body.get("ignore_robots_txt")
            ):
                self.ledger._stop("acquisition_contract_not_bounded")
            if self.endpoint.path != "/scrape":
                self.ledger._stop("acquisition_endpoint_not_supported")
            return request
        if set(body) - {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "response_format",
            "num_retries",
            "max_retries",
            "disable_fallbacks",
        }:
            self.ledger._stop("unsupported_model_request_options")
        if body.get("temperature", 0.3) != 0.3:
            self.ledger._stop("model_sampling_changed")
        if body.get("model") != self.model_alias or body.get("stream"):
            self.ledger._stop("model_route_or_stream_changed")
        # No implicit fallback, extra reasoning budget or model parameter aliases.
        if any(
            k in body
            for k in (
                "max_completion_tokens",
                "enable_thinking",
                "chat_template_kwargs",
            )
        ):
            self.ledger._stop("unsupported_model_budget_parameter")
        body["max_tokens"] = self.ledger.caps.output_tokens_per_dispatch
        body["num_retries"] = 0
        body["max_retries"] = 0
        body["disable_fallbacks"] = True
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > self.ledger.caps.model_input_bytes_per_dispatch:
            self.ledger._stop("model_input_byte_cap")
        headers = request.headers.copy()
        headers.pop("content-length", None)
        return httpx.Request(
            request.method,
            request.url,
            headers=headers,
            content=encoded,
            extensions=request.extensions,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        bounded = self._bounded_request(request)
        operation_id = self.ledger.reserve(self.kind, bounded)
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(self.ledger.remaining_seconds()):
                response = await self.inner.handle_async_request(bounded)
                chunks: list[bytes] = []
                length = 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > self.ledger.caps.response_bytes_per_dispatch:
                        raise CalibrationStoppedError("response_byte_cap")
                    chunks.append(chunk)
                content = b"".join(chunks)
        except BaseException as exc:
            self.ledger.unknown(operation_id, type(exc).__name__)
            raise
        finally:
            if response is not None:
                try:
                    await response.aclose()
                except BaseException as exc:
                    if self.ledger.halted is None:
                        self.ledger.unknown(
                            operation_id, f"response_close_{type(exc).__name__}"
                        )
                    raise
        receipts = {
            key: response.headers.get(key)
            for key in (
                "x-request-id",
                "x-litellm-call-id",
                "x-litellm-attempted-retries",
                "x-litellm-attempted-fallbacks",
            )
        }
        usage: dict[str, Any] | None = None
        if self.kind == "model":
            if any(
                receipts[key] != "0"
                for key in (
                    "x-litellm-attempted-retries",
                    "x-litellm-attempted-fallbacks",
                )
            ):
                self.ledger.complete(
                    operation_id,
                    status_code=response.status_code,
                    usage=None,
                    accounting_complete=False,
                    boundary_accounting_complete=False,
                    receipts=receipts,
                )
                self.ledger._stop("gateway_attempt_accounting_unavailable_or_changed")

            try:
                payload = json.loads(content)
                raw = payload.get("usage", {})
                if not all(
                    type(raw.get(k)) is int and raw[k] >= 0
                    for k in ("prompt_tokens", "completion_tokens")
                ):
                    raise ValueError("missing token usage")
                usage = {k: raw[k] for k in ("prompt_tokens", "completion_tokens")}
            except (ValueError, AttributeError, TypeError):
                self.ledger.complete(
                    operation_id,
                    status_code=response.status_code,
                    usage=None,
                    accounting_complete=False,
                    boundary_accounting_complete=False,
                    receipts=receipts,
                )
                self.ledger._stop("model_usage_unavailable")
        self.ledger.complete(
            operation_id,
            status_code=response.status_code,
            usage=usage,
            response_sha256=hashlib.sha256(content).hexdigest(),
            receipts=receipts,
            accounting_complete=False,
            boundary_accounting_complete=self.kind != "model" or usage is not None,
        )
        if (
            usage
            and usage["completion_tokens"] > self.ledger.caps.output_tokens_per_dispatch
        ):
            self.ledger._stop("provider_exceeded_output_cap")
        response_headers = response.headers.copy()
        response_headers.pop("content-encoding", None)
        response_headers.pop("content-length", None)
        return httpx.Response(
            response.status_code,
            headers=response_headers,
            content=content,
            request=bounded,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def install_budget_boundary(
    client: httpx.AsyncClient,
    ledger: CalibrationLedger,
    kind: Kind,
    endpoint: str,
    *,
    model_alias: str = "free",
    blocked_source_sha256: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Wrap the fresh configured client's default AND proxy-mounted transports.

    Replacing an entire client with an explicit transport can discard environment
    proxy routing. Preserve those mounts instead. This private httpx interface is
    intentionally version-gated by structure; unknown transports fail closed.
    Call before any request, never against a shared application-process client.
    """
    if ledger.events or client.is_closed:
        raise CalibrationStoppedError("install only before the first attempt")
    mounts = client._mounts
    transports = {id(client._transport): client._transport}
    transports.update({id(t): t for t in mounts.values() if t is not None})
    for inner in transports.values():
        if not isinstance(inner, httpx.AsyncHTTPTransport):
            raise CalibrationStoppedError("unsupported configured HTTP transport")
        pool = getattr(inner, "_pool", None)
        if getattr(pool, "_retries", None) != 0:
            raise CalibrationStoppedError(
                "configured HTTP transport retries are not zero"
            )
    wrapped = {
        key: BudgetedTransport(
            ledger,
            kind,
            endpoint,
            inner,
            model_alias=model_alias,
            blocked_source_sha256=blocked_source_sha256,
        )
        for key, inner in transports.items()
    }
    client._transport = wrapped[id(client._transport)]
    client._mounts = {
        pattern: wrapped[id(inner)] if inner is not None else None
        for pattern, inner in mounts.items()
    }
    return {
        "kind": kind,
        "wrapped_transports": len(wrapped),
        "proxy_mounts_preserved": True,
        "http_transport_retries": 0,
        "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "httpx_version": httpx.__version__,
    }
