"""Webhook delivery for async job endpoints.

Called from worker functions after store.complete_job() / store.fail_job().
Retries with exponential backoff, optional HMAC signing.
Each event includes a unique UUID v4 ``webhookId`` for receiver-side
deduplication (VAL-PARITY-011).

Payload format (per validation contract):
    - ``type`` — the event type (e.g. ``"crawl.started"``)
    - ``id`` — the job ID
    - ``webhookId`` — unique UUID v4 per delivery
    - ``success`` — boolean, ``True`` for normal events
    - ``error`` — ``None`` for success, error string for failures
    - ``data`` — list for crawl events (``[]`` or ``[{...}]``),
                 dict for other job types
    - ``metadata`` — echo of ``webhook.metadata`` (VAL-PARITY-009)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from urllib.parse import urlparse

import httpx

from common.url import (
    WebhookDestinationDNSRetryableError,
    WebhookDestinationValidationError,
    validate_outbound_webhook_url,
)

from .settings import load_settings

logger = logging.getLogger(__name__)

_webhook_settings = load_settings()
MAX_RETRIES = 3
TIMEOUT_SECONDS = 5


def _next_webhook_id() -> str:
    """Generate a unique UUID v4 for webhookId (VAL-PARITY-011).

    Each call returns a new UUID v4 string, ensuring uniqueness
    across all deliveries regardless of job or event type.

    Returns:
        A UUID v4 string like ``"f47ac10b-58cc-4372-a567-0e02b2c3d479"``.
    """
    return str(uuid.uuid4())


def _sign_body(body: bytes, secret: str) -> str:
    """HMAC-SHA256 sign the request body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _redact_webhook_url(url: str) -> str:
    """Mask the path, query, and credentials of a webhook URL for logs.

    Webhook URLs commonly embed secret tokens in the path or query (e.g.
    Slack/Discord hooks) and may carry userinfo credentials, so logs must
    only expose ``scheme://host[:port]``.
    """
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return "<redacted>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}/***"
    except Exception:
        return "<redacted>"


async def ensure_deliverable_webhook_destination(
    url: str,
    *,
    context: str = "",
    max_retries: int = MAX_RETRIES,
) -> bool:
    """Validate a webhook destination, retrying transient DNS failures.

    Shared by the job delivery path (``deliver_webhook``) and the monitor
    notify paths (``agent.monitor``) so every outbound webhook POST is
    gated by the same policy (issue #469): public HTTP(S) destinations
    only, transient DNS failures retried with exponential backoff, and
    permanent rejections skipped without attempting a request.

    Args:
        url: The webhook destination URL.
        context: Optional label (e.g. ``"job abc"`` / ``"monitor m1"``)
            included in log messages.
        max_retries: Number of validation attempts for transient DNS
            failures (backoff ``2**attempt`` seconds between attempts).

    Returns:
        ``True`` when the destination passed validation and may be posted
        to; ``False`` when it was permanently rejected or validation gave
        up after exhausting retries on transient DNS failures. Never
        raises.
    """
    prefix = f"{context}: " if context else ""
    for attempt in range(1, max_retries + 1):
        try:
            await asyncio.to_thread(validate_outbound_webhook_url, url)
            return True
        except WebhookDestinationDNSRetryableError:
            if attempt < max_retries:
                logger.warning(
                    "%sWebhook attempt %d/%d: DNS resolution temporarily "
                    "failed (url=%s)",
                    prefix,
                    attempt,
                    max_retries,
                    _redact_webhook_url(url),
                )
                await asyncio.sleep(2**attempt)
            else:
                logger.error(
                    "%sWebhook delivery failed after %d attempts: DNS "
                    "resolution temporarily failed (url=%s)",
                    prefix,
                    max_retries,
                    _redact_webhook_url(url),
                )
        except WebhookDestinationValidationError as e:
            logger.warning(
                "%sWebhook skipped: %s (url=%s)",
                prefix,
                e,
                _redact_webhook_url(url),
            )
            return False
    return False


async def deliver_webhook(
    webhook_config: dict | None,
    event: str,
    job_id: str,
    data: dict | list | None = None,
    task_tracker: object = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    """POST a job event to the configured webhook URL with retry logic.

    Each delivery includes a unique UUID v4 ``webhookId`` field for
    receiver-side deduplication (VAL-PARITY-011).

    Payload format:
        ``{"type": event, "id": job_id, "webhookId": "<uuid>",
          "success": success, "error": error, "data": data,
          "metadata": {metadata_echo}}``

    Args:
        webhook_config: Dict with 'url', optional 'events' list, and
                        optional 'metadata' dict (echoed in every event).
                        Example: ``{"url": "https://example.com/hook",
                                   "events": ["crawl.completed"],
                                   "metadata": {"customer_id": "123"}}``
        event: The event type (e.g. ``"completed"``, ``"failed"``,
               ``"crawl.started"``, ``"crawl.page"``).
        job_id: The job identifier.
        data: Payload to include in the body. For crawl lifecycle events
              (started, completed, failed), pass ``[]``. For per-page
              events, pass ``[{url, markdown, ...}]``.
        task_tracker: Optional ``TaskTracker`` instance. If provided, the
                      webhook delivery is spawned as a tracked background
                      task instead of executing inline.
        success: Whether the event indicates success (default ``True``).
        error: Error message for failure events (default ``None``).
    """
    if not webhook_config:
        return

    url = webhook_config.get("url")
    if not url:
        return

    # Respect events filter — if events list provided, only fire for matching events
    events_filter = webhook_config.get("events")
    if events_filter and event not in events_filter:
        return

    webhook_id = _next_webhook_id()

    payload = {
        "type": event,
        "id": job_id,
        "webhookId": webhook_id,
        "success": success,
        "error": error,
        "data": data or ([] if data is None else data),
        "metadata": webhook_config.get("metadata", {}) if webhook_config else {},
    }
    body = json.dumps(payload).encode()

    headers = {"Content-Type": "application/json"}
    if _webhook_settings.webhook_secret:
        headers["X-Webhook-Signature"] = (
            f"sha256={_sign_body(body, _webhook_settings.webhook_secret)}"
        )

    async def _do_deliver() -> None:
        # SSRF guard (issue #469): skip permanently rejected destinations;
        # transient DNS failures are retried inside the shared validator.
        if not await ensure_deliverable_webhook_destination(
            url, context=f"job {job_id}"
        ):
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Redirects are disabled so a validated public destination
                # can never be redirected to a restricted host (issue #469).
                async with httpx.AsyncClient(
                    timeout=TIMEOUT_SECONDS, follow_redirects=False
                ) as client:
                    resp = await client.post(url, content=body, headers=headers)
                    if resp.status_code < 500:
                        logger.info(
                            "Webhook delivered %s for job %s (webhookId=%s, status %d)",
                            event,
                            job_id,
                            webhook_id,
                            resp.status_code,
                        )
                        return
                    logger.warning(
                        "Webhook attempt %d/%d returned %d for job %s (webhookId=%s)",
                        attempt,
                        MAX_RETRIES,
                        resp.status_code,
                        job_id,
                        webhook_id,
                    )
            except httpx.TimeoutException:
                logger.warning(
                    "Webhook attempt %d/%d timed out for job %s (webhookId=%s)",
                    attempt,
                    MAX_RETRIES,
                    job_id,
                    webhook_id,
                )
            except Exception as e:
                logger.warning(
                    "Webhook attempt %d/%d failed for job %s: %s (webhookId=%s)",
                    attempt,
                    MAX_RETRIES,
                    job_id,
                    e,
                    webhook_id,
                )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(2**attempt)  # 2s, 4s

        logger.error(
            "Webhook delivery failed for job %s after %d attempts (webhookId=%s)",
            job_id,
            MAX_RETRIES,
            webhook_id,
        )

    if task_tracker is not None and hasattr(task_tracker, "create_background_task"):
        task_tracker.create_background_task(_do_deliver())
    else:
        await _do_deliver()


async def deliver_recorded_webhook(
    url: str, body: bytes, headers: dict[str, str] | None = None, *, context: str = ""
) -> str:
    """One validated signed attempt for a durable same-ID outbox.

    The caller persists exact body bytes and owns retry count/backoff. A timeout
    is retryable with that same event ID; receiving systems must deduplicate it.
    No destination, body, response content or credentials are logged here.
    """
    if not isinstance(body, bytes) or len(body) > 1_048_576:
        return "rejected"
    try:
        async with asyncio.timeout(15):
            await asyncio.to_thread(validate_outbound_webhook_url, url)
            actual_headers = {"Content-Type": "application/json", **(headers or {})}
            if headers is None and _webhook_settings.webhook_secret:
                actual_headers["X-Webhook-Signature"] = (
                    f"sha256={_sign_body(body, _webhook_settings.webhook_secret)}"
                )
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST", url, content=body, headers=actual_headers
                ) as response:
                    status = response.status_code
            if 200 <= status < 300:
                return "delivered"
            return "retry" if status >= 500 or status == 429 else "rejected"
    except WebhookDestinationDNSRetryableError:
        return "retry"
    except WebhookDestinationValidationError:
        return "rejected"
    except (httpx.HTTPError, TimeoutError):
        return "retry"
