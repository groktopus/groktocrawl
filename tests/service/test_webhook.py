"""Tests for agent-svc/agent/webhook.py — webhook delivery with retry and signing.

Covers:
- UUID v4 webhookId format (VAL-PARITY-011)
- Metadata echo (VAL-PARITY-009)
- ``type`` field in payload body
- ``success`` / ``error`` fields
- ``data`` array format for per-page events
- Events filter
- HMAC signature (VAL-PARITY-010)
- Retry logic
- Graceful failure on unreachable URL (VAL-PARITY-032)
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

# Captured before the autouse _public_dns fixture patches the resolver, so
# tests that need the real DNS behavior can restore it.
from common import url as _common_url

_REAL_RESOLVE = _common_url._resolve_to_ips_with_transient


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Make DNS resolution hermetic: every hostname resolves to a public IP.

    Webhook destination validation (issue #469) resolves hostnames via
    ``common.url._resolve_to_ips_with_transient``. Real lookups would be
    slow and nondeterministic in CI, so tests stub them to a public
    address.
    """
    from ipaddress import ip_address

    monkeypatch.setattr(
        "common.url._resolve_to_ips_with_transient",
        lambda hostname: ([ip_address("93.184.216.34")], False),
    )


class TestSignBody:
    def test_signs_body_correctly(self):
        from agent.webhook import _sign_body

        sig = _sign_body(b'{"type":"completed","id":"123"}', "mysecret")
        assert isinstance(sig, str)
        assert len(sig) == 64  # sha256 hexdigest
        # Deterministic: same input => same output
        assert sig == _sign_body(b'{"type":"completed","id":"123"}', "mysecret")


class TestWebhookIdUuid:
    """Test that webhookId uses UUID v4 format (VAL-PARITY-011)."""

    def test_generates_valid_uuid_v4(self):
        from agent.webhook import _next_webhook_id

        whid = _next_webhook_id()
        # Should be a valid UUID v4 string
        parsed = uuid.UUID(whid)
        assert parsed.version == 4

    def test_each_call_returns_unique_value(self):
        from agent.webhook import _next_webhook_id

        ids = {_next_webhook_id() for _ in range(100)}
        assert len(ids) == 100  # All unique
        for whid in ids:
            assert uuid.UUID(whid).version == 4


class TestBuildWebhookIntent:
    @pytest.mark.parametrize("config", [None, {}, {"url": ""}])
    def test_missing_destination_does_not_create_intent(self, config):
        from agent.webhook import build_webhook_intent

        assert build_webhook_intent(config, "completed", "job-1") is None


class TestDeliverWebhook:
    @pytest.mark.asyncio
    async def test_no_webhook_config_does_nothing(self):
        from agent.webhook import deliver_webhook

        # Should not raise
        await deliver_webhook(None, "crawl.started", "job-1")

    @pytest.mark.asyncio
    async def test_no_url_in_config_does_nothing(self):
        from agent.webhook import deliver_webhook

        await deliver_webhook({}, "crawl.started", "job-1")
        await deliver_webhook({"events": ["crawl.completed"]}, "crawl.started", "job-1")

    @pytest.mark.asyncio
    async def test_respects_events_filter(self):
        """Events filter: if events=['crawl.completed'], only crawl.completed fires."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client:
            cfg = {"url": "https://hook.example.com", "events": ["crawl.completed"]}
            await deliver_webhook(cfg, "crawl.started", "job-1", data=[])
            mock_client.assert_not_called()  # 'crawl.started' not in events filter

    @pytest.mark.asyncio
    async def test_respects_events_filter_allows_matching(self):
        """Events filter: matching event should be delivered."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            cfg = {"url": "https://hook.example.com", "events": ["crawl.completed"]}
            await deliver_webhook(cfg, "crawl.completed", "job-1", data=[])

            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_payload_has_type_field(self):
        """Payload uses ``type`` field instead of ``event`` (VAL-PARITY-005)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-123",
                data=[],
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["type"] == "crawl.started"
            assert "event" not in body  # Should use 'type' not 'event'

    @pytest.mark.asyncio
    async def test_payload_includes_success_and_error(self):
        """Payload includes success and error fields (VAL-PARITY-005)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-123",
                data=[],
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["success"] is True
            assert body["error"] is None

    @pytest.mark.asyncio
    async def test_payload_includes_error_for_failure_events(self):
        """Payload includes error message for failure events."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.failed",
                "job-123",
                data=[],
                success=False,
                error="Something went wrong",
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["success"] is False
            assert body["error"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_payload_data_is_list(self):
        """Data field is always a list (empty [] for lifecycle, [page] for per-page)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            # Lifecycle event: data is empty list
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-123",
                data=[],
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert isinstance(body["data"], list)
            assert body["data"] == []

    @pytest.mark.asyncio
    async def test_payload_data_with_page_list(self):
        """Per-page webhook: data is a list with one page document."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            page_data = {
                "url": "https://example.com/page1",
                "markdown": "# Page 1",
                "metadata": {"title": "Page 1", "status_code": 200},
            }
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.page",
                "job-123",
                data=[page_data],
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert isinstance(body["data"], list)
            assert len(body["data"]) == 1
            assert body["data"][0]["url"] == "https://example.com/page1"
            assert body["data"][0]["markdown"] == "# Page 1"

    @pytest.mark.asyncio
    async def test_metadata_echoed_back(self):
        """Metadata from webhook_config is echoed verbatim (VAL-PARITY-009)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            metadata = {"customer_id": "123", "campaign": "Q4"}
            cfg = {
                "url": "https://hook.example.com",
                "metadata": metadata,
            }
            await deliver_webhook(cfg, "crawl.started", "job-123", data=[])

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["metadata"] == metadata

    @pytest.mark.asyncio
    async def test_metadata_is_empty_dict_when_not_set(self):
        """Metadata defaults to empty dict when not provided in webhook_config."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            cfg = {"url": "https://hook.example.com"}
            await deliver_webhook(cfg, "crawl.started", "job-123", data=[])

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["metadata"] == {}

    @pytest.mark.asyncio
    async def test_outbox_delivery_is_enqueued_and_tracked(self, monkeypatch):
        """Job deliveries persist their intent before scheduling the send."""
        from agent import webhook

        job_id = "job-outbox-test"
        persisted = {
            "webhookId": "persisted-id",
            "url": "https://hook.example.com",
            "event": "completed",
            "body": '{"type":"completed"}',
            "headers": {"Content-Type": "application/json"},
        }

        class Store:
            def __init__(self):
                self.calls = []

            def enqueue_webhook(self, actual_job_id, fingerprint, event):
                self.calls.append((actual_job_id, fingerprint, event))
                return persisted

        class Tracker:
            def __init__(self):
                self.pending = []

            def create_background_task(self, coroutine):
                self.pending.append(coroutine)

        store = Store()
        tracker = Tracker()
        delivered = []

        async def deliver(actual_store, actual_job_id, event):
            delivered.append((actual_store, actual_job_id, event))

        monkeypatch.setattr(webhook, "_deliver_outbox_event", deliver)
        token = webhook._WEBHOOK_STORE_CONTEXT.set(store)
        try:
            await webhook.deliver_webhook(
                {"url": "https://hook.example.com"},
                "completed",
                job_id,
                {"result": "saved"},
                task_tracker=tracker,
            )
        finally:
            webhook._WEBHOOK_STORE_CONTEXT.reset(token)

        assert len(store.calls) == 1
        assert store.calls[0][0] == job_id
        assert store.calls[0][1]
        assert len(tracker.pending) == 1
        assert delivered == []
        await tracker.pending.pop()
        assert delivered == [(store, job_id, persisted)]

    @pytest.mark.asyncio
    async def test_outbox_claim_conflict_does_not_send_or_finish(self, monkeypatch):
        """A delivery that lost its lease cannot send or alter the outbox row."""
        from agent import webhook

        class Store:
            def __init__(self):
                self.finished = []

            def claim_webhook(self, *_args, **_kwargs):
                return None

            def finish_webhook(self, *args):
                self.finished.append(args)

        async def unexpected_send(*_args):
            pytest.fail("an unclaimed webhook must not be sent")

        store = Store()
        monkeypatch.setattr(webhook, "_send_outbox_event", unexpected_send)
        await webhook._deliver_outbox_event(
            store, "job-lease-conflict", {"webhookId": "already-owned"}
        )
        assert store.finished == []

    @pytest.mark.asyncio
    async def test_outbox_delivery_records_nonterminal_send_result(self, monkeypatch):
        """A retryable or rejected send is retained with its delivery status."""
        from agent import webhook

        event = {"webhookId": "event-id", "url": "https://hook.example.com"}
        calls = []

        class Store:
            def claim_webhook(self, job_id, webhook_id, owner):
                calls.append(("claim", job_id, webhook_id, owner))
                return event

            def finish_webhook(self, *args):
                calls.append(("finish", *args))

        async def retry_later(claimed, job_id):
            assert claimed is event
            assert job_id == "job-retry-later"
            return "pending"

        monkeypatch.setattr(webhook, "_send_outbox_event", retry_later)
        await webhook._deliver_outbox_event(
            Store(), "job-retry-later", {"webhookId": "event-id"}
        )

        claim, finish = calls
        assert claim[:3] == ("claim", "job-retry-later", "event-id")
        assert finish[:4] == ("finish", "job-retry-later", "event-id", claim[3])
        assert finish[4:] == ("pending", "pending")

    @pytest.mark.asyncio
    async def test_pending_replay_schedules_each_event_with_its_job_id(
        self, monkeypatch
    ):
        """Startup replay hands each persisted event to the tracked executor."""
        from agent import webhook

        class Store:
            def list_pending_webhooks(self):
                return [
                    {"job_id": "job-a", "webhookId": "event-a"},
                    {"job_id": "job-b", "webhookId": "event-b"},
                ]

        class Tracker:
            def __init__(self):
                self.tasks = []

            def create_background_task(self, coroutine):
                self.tasks.append(coroutine)

        store = Store()
        tracker = Tracker()
        scheduled = []

        async def deliver(actual_store, job_id, event):
            scheduled.append((actual_store, job_id, event))

        monkeypatch.setattr(webhook, "_deliver_outbox_event", deliver)
        count = await webhook.replay_pending_webhooks(store, tracker)
        assert count == 2
        assert scheduled == []
        for task in tracker.tasks:
            await task
        assert [(job_id, event["webhookId"]) for _, job_id, event in scheduled] == [
            ("job-a", "event-a"),
            ("job-b", "event-b"),
        ]

    @pytest.mark.asyncio
    async def test_metadata_echoed_on_all_event_types(self):
        """Metadata is echoed on all event types (started, page, completed)."""
        from agent.webhook import deliver_webhook

        metadata = {"session": "test-123"}
        cfg = {"url": "https://hook.example.com", "metadata": metadata}

        captured_bodies = []

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **post_kw):
                captured_bodies.append(json.loads(post_kw["content"].decode()))
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            # Fire all three event types
            await deliver_webhook(cfg, "crawl.started", "job-1", data=[])
            await deliver_webhook(
                cfg, "crawl.page", "job-1", data=[{"url": "https://example.com/p1"}]
            )
            await deliver_webhook(cfg, "crawl.completed", "job-1", data=[])

            assert len(captured_bodies) == 3
            for body in captured_bodies:
                assert body["metadata"] == metadata

    @pytest.mark.asyncio
    async def test_delivers_successfully(self):
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.completed",
                "job-123",
                data=[],
            )

            mock_client.post.assert_called_once()
            args, kwargs = mock_client.post.call_args
            assert args[0] == "https://hook.example.com"
            body = json.loads(kwargs["content"].decode())
            assert body["type"] == "crawl.completed"
            assert body["id"] == "job-123"

    @pytest.mark.asyncio
    async def test_adds_signature_when_secret_set(self):
        """HMAC signature header present when WEBHOOK_SECRET is set (VAL-PARITY-010)."""
        from agent.webhook import deliver_webhook

        with (
            patch("agent.webhook.httpx.AsyncClient") as mock_client_cls,
            patch("agent.webhook._webhook_settings.webhook_secret", "my-secret"),
        ):
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-1",
                data=[],
            )

            _args, kwargs = mock_client.post.call_args
            assert "X-Webhook-Signature" in kwargs["headers"]
            assert kwargs["headers"]["X-Webhook-Signature"].startswith("sha256=")

    @pytest.mark.asyncio
    async def test_signature_is_verifiable(self):
        """Signature can be verified by computing HMAC-SHA256 of body."""
        from agent.webhook import _sign_body, deliver_webhook

        secret = "test-secret-123"

        with (
            patch("agent.webhook.httpx.AsyncClient") as mock_client_cls,
            patch("agent.webhook._webhook_settings.webhook_secret", secret),
        ):
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            captured_body = None
            captured_signature = None

            async def _post(*a, **kw):
                nonlocal captured_body, captured_signature
                captured_body = kw["content"]
                captured_signature = kw["headers"]["X-Webhook-Signature"]
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-1",
                data=[],
            )

            # Verify the signature matches
            expected_sig = _sign_body(captured_body, secret)
            assert captured_signature == f"sha256={expected_sig}"

    @pytest.mark.asyncio
    async def test_retries_on_5xx(self):
        """Retries on server errors (VAL-PARITY-031)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 500

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-1",
                data=[],
            )
            assert mock_client.post.call_count > 1

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        import httpx
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                raise httpx.TimeoutException("timeout")

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-1",
                data=[],
            )
            assert mock_client.post.call_count > 1

    @pytest.mark.asyncio
    async def test_delivery_failure_logged_does_not_raise(self):
        """Webhook delivery failure is logged but does not raise (VAL-PARITY-032)."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                raise Exception("Connection refused")

            mock_client.post = MagicMock(side_effect=_post)

            # Should not raise
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.started",
                "job-1",
                data=[],
            )

    @pytest.mark.asyncio
    async def test_payload_includes_webhook_id(self):
        """Each webhook delivery includes unique webhookId."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.page",
                "job-123",
                data=[{"url": "https://example.com/page"}],
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert "webhookId" in body
            # Verify UUID v4 format
            parsed = uuid.UUID(body["webhookId"])
            assert parsed.version == 4

    @pytest.mark.asyncio
    async def test_multiple_webhooks_have_unique_ids(self):
        """Multiple webhooks from same job have unique webhookIds."""
        from agent.webhook import deliver_webhook

        webhook_ids = []
        cfg = {"url": "https://hook.example.com"}

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **post_kw):
                body = json.loads(post_kw["content"].decode())
                webhook_ids.append(body["webhookId"])
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(cfg, "crawl.started", "job-1", data=[])
            await deliver_webhook(
                cfg, "crawl.page", "job-1", data=[{"url": "https://example.com/p1"}]
            )
            await deliver_webhook(
                cfg, "crawl.page", "job-1", data=[{"url": "https://example.com/p2"}]
            )
            await deliver_webhook(cfg, "crawl.completed", "job-1", data=[])

            # All webhookIds are unique
            assert len(set(webhook_ids)) == 4
            # All are valid UUID v4
            for whid in webhook_ids:
                parsed = uuid.UUID(whid)
                assert parsed.version == 4

    @pytest.mark.asyncio
    async def test_non_crawl_events_still_work(self):
        """Non-crawl events (completed, failed) still use the new payload format."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            async def _post(*a, **kw):
                return mock_resp

            mock_client.post = MagicMock(side_effect=_post)

            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "completed",
                "job-1",
                data={"pages": [{"url": "https://example.com"}]},
            )

            _args, kwargs = mock_client.post.call_args
            body = json.loads(kwargs["content"].decode())
            assert body["type"] == "completed"
            assert body["success"] is True
            assert body["error"] is None
            assert body["data"] == {"pages": [{"url": "https://example.com"}]}


class TestWebhookSsrfGuard:
    """SSRF guard for webhook destinations (issue #469)."""

    def _mock_client_cls(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client_cls = MagicMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        async def _post(*a, **kw):
            return mock_resp

        mock_client.post = MagicMock(side_effect=_post)
        return mock_client_cls, mock_client

    @pytest.mark.asyncio
    async def test_rejects_loopback_destination(self):
        """A loopback webhook destination is never posted to."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "http://127.0.0.1:8080/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_rfc1918_destination(self):
        """A private RFC 1918 webhook destination is never posted to."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "http://10.0.0.5/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_link_local_destination(self):
        """A link-local webhook destination is never posted to."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "http://169.254.169.254/latest/meta-data/"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_ipv4_mapped_loopback_destination(self):
        """An IPv4-mapped IPv6 loopback destination is never posted to."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "http://[::ffff:127.0.0.1]:8080/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self):
        """Non-HTTP(S) webhook destinations are never posted to."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "ftp://example.com/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_hostname_resolving_to_private_ip(self, monkeypatch):
        """A public-looking hostname resolving to a private IP is rejected."""
        from ipaddress import ip_address

        from agent.webhook import deliver_webhook

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient",
            lambda hostname: ([ip_address("192.168.1.99")], False),
        )
        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "https://public.example.com/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_fails_closed_on_dns_failure(self, monkeypatch):
        """An unresolvable webhook hostname is rejected (fail closed)."""
        from agent.webhook import deliver_webhook

        monkeypatch.setattr(
            "common.url._resolve_to_ips_with_transient", lambda hostname: ([], False)
        )
        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "https://unresolvable.example.invalid/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_destination_does_not_crash_delivery(self):
        """Malformed destinations are skipped without leaking parser errors."""
        from agent.webhook import deliver_webhook

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            # Malformed IPv6 brackets: must not raise out of deliver_webhook
            await deliver_webhook(
                {"url": "http://[::1/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_overlong_label_destination_does_not_crash_delivery(
        self, monkeypatch
    ):
        """A >63-char hostname label is skipped without leaking UnicodeError."""
        from agent.webhook import deliver_webhook

        # Restore real resolution so the IDNA label-too-long path is exercised
        monkeypatch.setattr("common.url._resolve_to_ips_with_transient", _REAL_RESOLVE)
        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": f"http://{'a' * 64}.example.com/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_transient_dns_failure_retries_validation(self, monkeypatch):
        """A transient DNS failure retries instead of dropping the webhook."""
        from agent.webhook import deliver_webhook

        calls = {"n": 0}

        def _flaky_resolve(hostname):
            calls["n"] += 1
            if calls["n"] == 1:
                # First attempt: transient resolver failure
                return ([], True)
            from ipaddress import ip_address

            return ([ip_address("93.184.216.34")], False)

        monkeypatch.setattr("common.url._resolve_to_ips_with_transient", _flaky_resolve)

        mock_client_cls, mock_client = self._mock_client_cls()
        with patch("agent.webhook.httpx.AsyncClient", mock_client_cls):
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.completed",
                "job-1",
                data=[],
            )
        # Transient failure was retried; the second attempt delivered
        assert calls["n"] >= 2
        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://hook.example.com"

    @pytest.mark.asyncio
    async def test_persistent_transient_dns_failure_gives_up_after_retries(
        self, monkeypatch
    ):
        """A persistent transient DNS failure retries, then gives up silently."""
        from agent.webhook import deliver_webhook

        calls = {"n": 0}

        def _always_fail(hostname):
            calls["n"] += 1
            return ([], True)

        monkeypatch.setattr("common.url._resolve_to_ips_with_transient", _always_fail)

        with patch("agent.webhook.httpx.AsyncClient") as mock_client_cls:
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.completed",
                "job-1",
                data=[],
            )
        # One validation per attempt; no HTTP request was ever made
        assert calls["n"] == 3
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_public_ip_literal_still_delivers(self):
        """A public IP-literal webhook destination still delivers."""
        from agent.webhook import deliver_webhook

        mock_client_cls, mock_client = self._mock_client_cls()
        with patch("agent.webhook.httpx.AsyncClient", mock_client_cls):
            await deliver_webhook(
                {"url": "http://93.184.216.34/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
        mock_client.post.assert_called_once()
        args, _kwargs = mock_client.post.call_args
        assert args[0] == "http://93.184.216.34/hook"

    @pytest.mark.asyncio
    async def test_redirects_disabled(self):
        """Redirects are disabled on the webhook HTTP client."""
        from agent.webhook import deliver_webhook

        mock_client_cls, _ = self._mock_client_cls()
        with patch("agent.webhook.httpx.AsyncClient", mock_client_cls):
            await deliver_webhook(
                {"url": "https://hook.example.com"},
                "crawl.completed",
                "job-1",
                data=[],
            )
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["follow_redirects"] is False

    @pytest.mark.asyncio
    async def test_rejection_logs_redact_path_tokens(self, caplog):
        """Rejected webhook URLs are logged without their path/query tokens."""
        import logging

        from agent.webhook import deliver_webhook

        with (
            patch("agent.webhook.httpx.AsyncClient") as mock_client_cls,
            caplog.at_level(logging.WARNING, logger="agent.webhook"),
        ):
            await deliver_webhook(
                {"url": "http://127.0.0.1:8080/services/T0000/secret-token/hook"},
                "crawl.completed",
                "job-1",
                data=[],
            )
            mock_client_cls.assert_not_called()

        assert "secret-token" not in caplog.text
        assert "services" not in caplog.text
        assert "127.0.0.1:8080" in caplog.text  # host is still useful in logs

    def test_redact_webhook_url_masks_path_and_query(self):
        from agent.webhook import _redact_webhook_url

        redacted = _redact_webhook_url(
            "https://hooks.slack.com/services/T0000/B0000/token123?x=1"
        )
        assert "token123" not in redacted
        assert "hooks.slack.com" in redacted
        assert redacted.endswith("/***")
        assert _redact_webhook_url("not-a-url") == "<redacted>"

    def test_redact_webhook_url_masks_userinfo(self):
        from agent.webhook import _redact_webhook_url

        redacted = _redact_webhook_url(
            "https://user:supersecret@hooks.example.com:8443/path/token"
        )
        assert "user" not in redacted
        assert "supersecret" not in redacted
        assert "token" not in redacted
        assert "hooks.example.com:8443" in redacted
