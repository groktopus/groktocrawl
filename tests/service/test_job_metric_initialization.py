"""First terminal job events must have a prior zero-valued series."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.metrics import MetricsCollector


def test_app_creation_exposes_job_zero_samples(monkeypatch):
    from agent import app, workload_metrics

    collector = MetricsCollector()
    monkeypatch.setattr(workload_metrics, "METRICS", collector)
    for name in ("ScraperClient", "SearXNGClient", "LLMClient"):
        monkeypatch.setattr(app, name, MagicMock())
    app.create_app()
    assert 'jobs_failed_total{type="agent"} 0.0' in collector.generate_openmetrics()


@pytest.mark.asyncio
async def test_first_worker_failure_increments_preinitialized_counter(monkeypatch):
    from agent import worker, workload_metrics
    from agent.exceptions import CitationValidationError

    collector = MetricsCollector()
    monkeypatch.setattr(worker, "METRICS", collector)
    monkeypatch.setattr(workload_metrics, "METRICS", collector)
    monkeypatch.setattr(worker, "deliver_webhook", AsyncMock())
    workload_metrics.initialize_job_metrics()
    before = collector.generate_openmetrics()
    for kind in (
        "agent",
        "crawl",
        "batch_scrape",
        "extract",
        "llmstxt",
        "plan_execute",
    ):
        for name in ("submitted", "completed", "failed"):
            assert f'jobs_{name}_total{{type="{kind}"}} 0.0' in before

    store = MagicMock()
    await worker._run_job_with_observability(
        "failed-citation-job",
        "agent",
        store,
        None,
        AsyncMock(side_effect=CitationValidationError()),
    )
    workload_metrics.initialize_job_metrics()
    after = collector.generate_openmetrics()
    assert 'jobs_failed_total{type="agent"} 1.0' in after
    assert 'jobs_submitted_total{type="agent"} 1.0' in after
    assert 'jobs_completed_total{type="agent"} 0.0' in after
    assert 'jobs_failed_total{type="crawl"} 0.0' in after
    store.fail_job.assert_called_once()
