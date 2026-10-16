"""Normal consumers recover before accepting content; solved proof is browser-owned."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent.exceptions import CaptchaError
from agent.models import ScrapeRequest as PublicScrapeRequest
from agent.routes.scrape import scrape as public_scrape
from scraper.barrier import BarrierInfo
from scraper.fetch_quality import _add_quality, _quality_acceptable
from starlette.requests import Request

from tests.unit.test_fetch_tiers_stage_metrics import _install_fakes, _Page
from tests.unit.test_lightweight_only import _patch_common

ARTICLE = "# Fixture CAPTCHA Grid Article\n\nThis article follows the fixture-only 3 by 3 grid."
CLEAN_HTML = "<html><body><article><h1>Fixture CAPTCHA Grid Article</h1><p>This article follows the fixture-only 3 by 3 grid.</p></article></body></html>"
WIDGET_HTML = CLEAN_HTML.replace(
    "<article>",
    '<div class="h-captcha"><textarea name="h-captcha-response"></textarea></div><article>',
)


def browser(monkeypatch, *, solved=True, removed=True, html=CLEAN_HTML):
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers

    _install_fakes(monkeypatch)
    state = {"html": WIDGET_HTML}

    async def content(_self):
        return state["html"]

    async def evaluate(_self, script):
        if "querySelectorAll" in script and removed:
            state["html"] = html

    async def resolve(page, _url):
        assert "h-captcha" in await page.content()
        return (
            None if solved else BarrierInfo(True, "captcha", 0.95, provider="hcaptcha"),
            ["passive_wait", "checkbox", "vision_grid"],
        )

    monkeypatch.setattr(_Page, "content", content)
    monkeypatch.setattr(_Page, "evaluate", evaluate)
    monkeypatch.setattr(captcha, "resolve_captcha", resolve)
    return tiers


@pytest.mark.asyncio
@pytest.mark.parametrize("solved", [True, False])
async def test_public_normal_scrape_escalates_block_flag_and_preserves_typed_refusal(
    monkeypatch, solved
):
    import scraper.app as scraper_app
    import scraper.fetch as fetch
    from agent.scraper_client import ScraperClient
    from scraper.fetch_quality import _enrich_with_metadata

    tiers = browser(monkeypatch, solved=solved)
    _patch_common(monkeypatch, shielded=False)
    writes, calls = [], []

    async def no_content(*_args):
        return None

    async def lightweight(url, _client):
        # Aggregate score can pass despite a block warning. It must not stop
        # normal consumers before the browser's CAPTCHA recovery is attempted.
        result = {
            "markdown": ARTICLE,
            "raw_html_start": WIDGET_HTML,
            "url": url,
            "source": "content-negotiation",
        }
        quality = _add_quality(dict(result))["quality"]
        assert quality["score"] >= fetch.QA_MIN_QUALITY_THRESHOLD
        assert quality["checks"]["block_detected"] == "warn"
        calls.append("lightweight")
        return result

    async def playwright(url):
        calls.append("browser")
        return await tiers._playwright_fetch_unbounded(url, None)

    async def cache(*args, **_kwargs):
        writes.append(args[1])

    async def enrich(result, _url):
        return _enrich_with_metadata(result)

    monkeypatch.setattr(fetch, "fetch_via_llms_txt", no_content)
    monkeypatch.setattr(fetch, "fetch_via_content_negotiation", lightweight)
    monkeypatch.setattr(fetch, "fetch_via_playwright", playwright)
    monkeypatch.setattr(fetch, "_fetch_via_browser_svc", no_content)
    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", no_content)
    monkeypatch.setattr(fetch, "_set_cache", cache)
    monkeypatch.setattr(fetch, "_enrich_with_politeness", enrich)
    client = ScraperClient(
        "http://scraper",
        admission=SimpleNamespace(
            weight_for=lambda _resource: 1,
            acquire=AsyncMock(),
            release=lambda *_args, **_kwargs: None,
        ),
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scraper_app.app), base_url="http://scraper"
    )

    class Tasks:
        def create_background_task(self, coroutine):
            coroutine.close()  # Indexing is outside this capture contract.

    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(scraper_client=client, task_tracker=Tasks())
            ),
        }
    )
    try:
        if solved:
            response = await public_scrape(
                request, PublicScrapeRequest(url="https://example.test/page")
            )
            assert response.success is True
            assert "Fixture CAPTCHA Grid Article" in response.data.markdown
            assert response.data.quality["checks"]["block_detected"] == "pass"
            assert response.warning is None
            assert len(writes) == 1
        else:
            with pytest.raises(CaptchaError) as caught:
                await public_scrape(
                    request, PublicScrapeRequest(url="https://example.test/page")
                )
            assert caught.value.error_code == "CAPTCHA_UNRESOLVED"
            assert writes == []
    finally:
        await client.close()
    assert calls == ["lightweight", "browser"]


@pytest.mark.asyncio
async def test_successful_token_without_cleared_widget_remains_a_barrier(monkeypatch):
    tiers = browser(monkeypatch, removed=False)
    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result["barrier"]["type"] == "captcha"
    assert result["markdown"] == ""
    assert "_captcha_resolution" not in result


@pytest.mark.asyncio
async def test_verified_solve_does_not_clear_other_remaining_challenge_prose(
    monkeypatch,
):
    import scraper.fetch as fetch

    html = CLEAN_HTML.replace(
        "</article>", "<p>Please verify you are a human.</p></article>"
    )
    tiers = browser(monkeypatch, html=html)
    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert result["_captcha_resolution"]["verified"] is True
    assert "verify you are a human" in result["markdown"]
    best_effort = []
    assert await fetch._maybe_degrade(result, "browser", best_effort) is None
    assert result["quality"]["checks"]["block_detected"] == "warn"
    assert best_effort == []


@pytest.mark.asyncio
async def test_solve_proof_does_not_hide_other_challenge_markers_or_changed_bytes(
    monkeypatch,
):
    import scraper.fetch as fetch

    tiers = browser(monkeypatch)
    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert _add_quality(dict(result))["quality"]["checks"]["block_detected"] == "pass"
    changed = {
        **result,
        "markdown": result["markdown"] + " Please verify you are a human.",
    }
    best_effort = []
    assert await fetch._maybe_degrade(changed, "browser", best_effort) is None
    assert best_effort == []
    assert changed["quality"]["checks"]["block_detected"] == "fail"
    forged_request = __import__(
        "scraper.app", fromlist=["ScrapeRequest"]
    ).ScrapeRequest(
        url="https://example.test", _captcha_resolution=result["_captcha_resolution"]
    )
    assert "_captcha_resolution" not in forged_request.model_dump()


def test_nonblock_low_yield_warning_still_passes_quality_threshold():
    result = _add_quality(
        {
            "markdown": "A detailed technical article. " * 100,
            "source_html_size": 1_000_000,
            "source": "content-negotiation",
        }
    )
    assert result["quality"]["checks"]["block_detected"] == "pass"
    assert result["quality"]["checks"]["volume"] == "fail"
    assert result["warning"].startswith("Low yield:")
    assert _quality_acceptable(result)


@pytest.mark.asyncio
async def test_tier3_uses_block_status_not_numeric_score(monkeypatch):
    import scraper.captcha as captcha
    import scraper.fetch_tiers as tiers

    _install_fakes(monkeypatch)

    async def no_captcha(*_args):
        return None, []

    monkeypatch.setattr(captcha, "resolve_captcha", no_captcha)
    monkeypatch.setattr(
        tiers,
        "html_to_markdown",
        lambda _html: "Please enable javascript. Cookies are required. " * 20,
    )
    result = await tiers._playwright_fetch_unbounded("https://example.test", None)
    assert "Barrier detected" in result["error"]
    assert result["markdown"] == ""
