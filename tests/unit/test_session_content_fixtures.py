"""Ensure session isolation inputs survive real extraction and refusal gates."""

import importlib.util
from pathlib import Path

import httpx
import pytest
from agent.barrier_guard import is_barrier_flagged
from scraper.fetch_quality import _add_quality, _quality_acceptable, html_to_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/content/multi-sentence", "/content/near-duplicate"])
async def test_session_isolation_fixture_contains_usable_content(path):
    source = Path(__file__).parents[2] / "test-site/test_site/app.py"
    spec = importlib.util.spec_from_file_location("session_content_site", source)
    assert spec is not None and spec.loader is not None
    site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=site.app), base_url="http://test-site:8000"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 200
    markdown = html_to_markdown(response.text)
    assert len(markdown) >= 200
    data = _add_quality(
        {"url": str(response.url), "markdown": markdown}, html=response.text
    )
    assert _quality_acceptable(data)
    assert not is_barrier_flagged({"data": data, "warning": data.get("warning")})
