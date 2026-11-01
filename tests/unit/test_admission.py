"""Unit tests for the shared weighted admission controller (ADR-0051)."""

import asyncio

import pytest

from common.admission import (
    DEFAULT_WEIGHTS,
    RESOURCE_CLASSES,
    AdmissionController,
    AdmissionRejectedError,
)
from common.metrics import METRICS


def _metrics_text() -> str:
    return METRICS.generate_openmetrics()


class TestAdmissionController:
    @pytest.mark.asyncio
    async def test_fast_path_acquire_and_release(self):
        controller = AdmissionController(limits={"lightweight_fetch": 2})
        await controller.acquire("lightweight_fetch")
        assert controller.active("lightweight_fetch") == 1
        controller.release("lightweight_fetch")
        assert controller.active("lightweight_fetch") == 0

    @pytest.mark.asyncio
    async def test_fifo_waiters_are_drained_on_release(self):
        controller = AdmissionController(limits={"llm": 4})
        await controller.acquire("llm", weight=4)  # saturate the budget
        assert controller.active("llm") == 4

        waiter = asyncio.create_task(controller.acquire("llm", weight=4))
        await asyncio.sleep(0.01)
        assert controller.queue_depth("llm") == 1

        controller.release("llm", weight=4)
        await asyncio.wait_for(waiter, timeout=1.0)
        assert controller.active("llm") == 4

    @pytest.mark.asyncio
    async def test_weight_exceeding_budget_is_rejected(self):
        controller = AdmissionController(limits={"browser": 4})
        assert DEFAULT_WEIGHTS["browser"] == 8
        with pytest.raises(AdmissionRejectedError):
            await controller.acquire("browser", weight=8)
        assert 'admission_rejected_total{class="browser"} 1.0' in _metrics_text()

    @pytest.mark.asyncio
    async def test_queue_overflow_is_rejected(self):
        controller = AdmissionController(
            limits={"lightweight_fetch": 1}, queue_capacity=1
        )
        await controller.acquire("lightweight_fetch")  # active=1, queue empty

        second = asyncio.create_task(controller.acquire("lightweight_fetch"))
        await asyncio.sleep(0.01)  # second is now queued
        assert controller.queue_depth("lightweight_fetch") == 1

        with pytest.raises(AdmissionRejectedError):
            await controller.acquire("lightweight_fetch")

        controller.release("lightweight_fetch")
        await asyncio.wait_for(second, timeout=1.0)
        controller.release("lightweight_fetch")
        assert controller.active("lightweight_fetch") == 0

    @pytest.mark.asyncio
    async def test_timeout_is_rejected(self):
        controller = AdmissionController(limits={"llm": 4})
        await controller.acquire("llm", weight=4)

        with pytest.raises(AdmissionRejectedError):
            await controller.acquire("llm", weight=4, timeout=0.05)
        assert 'admission_rejected_total{class="llm"} 1.0' in _metrics_text()

    @pytest.mark.asyncio
    async def test_cancellation_records_cancelled(self):
        controller = AdmissionController(limits={"llm": 4})
        await controller.acquire("llm", weight=4)

        waiter = asyncio.create_task(controller.acquire("llm", weight=4))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert 'admission_cancelled_total{class="llm"} 1.0' in _metrics_text()

        controller.release("llm", weight=4)
        assert controller.active("llm") == 0
        assert controller.queue_depth("llm") == 0

    @pytest.mark.asyncio
    async def test_granted_waiter_then_cancelled_refunds_weight(self):
        controller = AdmissionController(limits={"llm": 4})
        await controller.acquire("llm", weight=4)  # saturate the budget
        assert controller.active("llm") == 4

        waiter = asyncio.create_task(controller.acquire("llm", weight=4))
        await asyncio.sleep(0.01)
        assert controller.queue_depth("llm") == 1

        # Release grants the waiter synchronously (future resolved, waiter
        # popped, _active incremented), then cancel before it resumes so the
        # CancelledError is delivered at ``await future`` even though the
        # future already holds a result.
        controller.release("llm", weight=4)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        # The granted weight was refunded: _active returns to its prior value
        # and a subsequent acquire succeeds immediately instead of leaking.
        assert controller.active("llm") == 0
        assert controller.queue_depth("llm") == 0
        await controller.acquire("llm", weight=4)
        assert controller.active("llm") == 4
        controller.release("llm", weight=4)

    @pytest.mark.asyncio
    async def test_resource_context_manager_releases(self):
        controller = AdmissionController(limits={"lightweight_fetch": 2})
        async with controller.resource("lightweight_fetch"):
            assert controller.active("lightweight_fetch") == 1
        assert controller.active("lightweight_fetch") == 0

    @pytest.mark.asyncio
    async def test_metrics_are_exported(self):
        controller = AdmissionController(limits={"lightweight_fetch": 1})
        await controller.acquire("lightweight_fetch")
        controller.release("lightweight_fetch")

        text = _metrics_text()
        assert "# TYPE admission_active gauge" in text
        assert "# TYPE admission_queue_depth gauge" in text
        assert "# TYPE admission_wait_seconds histogram" in text
        assert "# TYPE admission_rejected_total counter" in text
        assert "# TYPE admission_cancelled_total counter" in text
        assert 'admission_active{class="lightweight_fetch"} 0.0' in text

    @pytest.mark.asyncio
    async def test_unknown_class_raises_value_error(self):
        controller = AdmissionController(limits={"llm": 4})
        with pytest.raises(ValueError):
            await controller.acquire("not_a_class")


class TestResourceClasses:
    def test_resource_classes_tuple(self):
        assert RESOURCE_CLASSES == ("lightweight_fetch", "browser", "llm")

    def test_default_weights(self):
        assert DEFAULT_WEIGHTS == {"lightweight_fetch": 1, "llm": 4, "browser": 8}


class TestAgentAdmissionSingleton:
    def test_get_admission_reads_settings(self, monkeypatch):
        from agent.admission import get_admission, reset_admission
        from agent.settings import load_settings

        load_settings.cache_clear()
        monkeypatch.setenv("ADMISSION_LIGHT_FETCH_LIMIT", "64")
        monkeypatch.setenv("ADMISSION_BROWSER_LIMIT", "32")
        monkeypatch.setenv("ADMISSION_LLM_LIMIT", "32")
        reset_admission()
        controller = get_admission()
        assert controller.budget_for("lightweight_fetch") == 64
        assert controller.budget_for("browser") == 32
        assert controller.budget_for("llm") == 32
        reset_admission()
        load_settings.cache_clear()

    def test_admission_budgets_default_to_unlimited(self, monkeypatch):
        """ADR-0077: unset admission budgets no longer throttle by default."""
        from agent.admission import get_admission, reset_admission
        from agent.settings import load_settings

        for key in (
            "ADMISSION_LIGHT_FETCH_LIMIT",
            "ADMISSION_BROWSER_LIMIT",
            "ADMISSION_LLM_LIMIT",
        ):
            monkeypatch.delenv(key, raising=False)
        load_settings.cache_clear()
        reset_admission()
        controller = get_admission()
        assert controller.budget_for("lightweight_fetch") == 0
        assert controller.budget_for("browser") == 0
        assert controller.budget_for("llm") == 0
        reset_admission()
        load_settings.cache_clear()
