"""Focused CAPTCHA classification and recovery contract tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

SCRAPER_SVC = Path(__file__).resolve().parents[2] / "scraper-svc"
if str(SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(SCRAPER_SVC))


def test_recaptcha_widget_is_definitive_not_a_point_seven_fallthrough():
    from scraper.barrier import _classify_barrier

    result = _classify_barrier(
        "",
        "https://example.test",
        "",
        '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>',
    )

    assert result.detected
    assert result.barrier_type == "captcha"
    assert result.provider == "recaptcha"
    assert result.confidence > 0.7


def test_turnstile_widget_is_identified_by_provider():
    from scraper.barrier import _classify_barrier

    result = _classify_barrier(
        "",
        "https://example.test",
        "",
        '<div class="cf-turnstile" data-sitekey="key"></div>',
    )

    assert result.detected
    assert result.provider == "turnstile"


def test_captcha_prose_is_not_a_widget():
    from scraper.barrier import _classify_barrier

    result = _classify_barrier(
        "",
        "https://example.test",
        "This article explains how CAPTCHA systems work. " * 10,
    )

    assert result.barrier_type != "captcha"
    assert result.provider is None


def test_generic_iframe_needs_captcha_structure_not_an_unrelated_response_word():
    from scraper.barrier import _classify_barrier

    result = _classify_barrier(
        "",
        "https://example.test",
        "",
        '<iframe src="/feedback?response=thanks"></iframe>',
    )

    assert result.barrier_type != "captcha"
    assert result.provider is None


def test_tile_response_requires_unique_in_range_indices():
    from scraper.captcha import parse_tile_response

    assert parse_tile_response('{"tiles": [0, 4, 8], "submit": true}', 9) == [0, 4, 8]
    assert parse_tile_response('{"tiles": [0, 0], "submit": true}', 9) is None
    assert parse_tile_response('{"tiles": [9], "submit": true}', 9) is None
    assert parse_tile_response('{"tiles": ["0"], "submit": true}', 9) is None


def test_stable_fingerprint_seed_is_domain_scoped():
    from scraper.stealth import fingerprint_seed

    assert fingerprint_seed("https://www.example.com/a") == fingerprint_seed(
        "https://example.com/b"
    )
    assert fingerprint_seed("https://example.com") != fingerprint_seed(
        "https://other.test"
    )
    assert fingerprint_seed("https://example.com").isdigit()
    assert 0 <= int(fingerprint_seed("https://example.com")) < 2**31


@pytest.mark.asyncio
async def test_vision_unsupported_disables_future_calls(monkeypatch):
    import scraper.captcha as captcha

    captcha._vision_unavailable = False
    captcha._settings = captcha._settings.model_copy(
        update={
            "captcha_vision_base_url": "https://vision.example/v1",
            "captcha_vision_api_key": "test-key",
            "captcha_vision_model": "test-model",
        }
    )
    calls = 0

    class Response:
        status_code = 400

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(captcha.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert await captcha.ask_vision_for_tiles("prompt", b"image", 9) is None
    assert await captcha.ask_vision_for_tiles("prompt", b"image", 9) is None
    assert calls == 1


@pytest.mark.asyncio
async def test_vision_transient_failure_does_not_disable_future_calls(monkeypatch):
    import scraper.captcha as captcha

    captcha._vision_unavailable = False
    captcha._settings = captcha._settings.model_copy(
        update={
            "captcha_vision_base_url": "https://vision.example/v1",
            "captcha_vision_api_key": "test-key",
            "captcha_vision_model": "test-model",
        }
    )

    class Response:
        status_code = 429

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(captcha.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert await captcha.ask_vision_for_tiles("prompt", b"image", 9) is None
    assert captcha._vision_unavailable is False


@pytest.mark.asyncio
async def test_malformed_vision_success_does_not_disable_future_calls(monkeypatch):
    import scraper.captcha as captcha

    captcha._vision_unavailable = False
    captcha._settings = captcha._settings.model_copy(
        update={
            "captcha_vision_base_url": "https://vision.example/v1",
            "captcha_vision_api_key": "test-key",
            "captcha_vision_model": "test-model",
        }
    )
    calls = 0

    class Response:
        status_code = 200

        def json(self):
            return {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(captcha.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert await captcha.ask_vision_for_tiles("prompt", b"image", 9) is None
    assert await captcha.ask_vision_for_tiles("prompt", b"image", 9) is None
    assert calls == 2
    assert captcha._vision_unavailable is False


@pytest.mark.asyncio
async def test_solved_check_skips_absent_checkbox_without_waiting():
    import scraper.captcha as captcha

    class Missing:
        first = None

        def __init__(self):
            self.first = self

        async def count(self):
            return 0

        async def input_value(self):
            return ""

        async def get_attribute(self, _name):
            raise AssertionError("absent checkbox must not be awaited")

        async def is_checked(self):
            raise AssertionError("absent checkbox must not be awaited")

    class Page:
        def locator(self, _selector):
            return Missing()

    class Frame:
        def locator(self, _selector):
            return Missing()

    assert await captcha._is_solved(Page(), "hcaptcha", [Frame()]) is False


@pytest.mark.asyncio
async def test_frame_checkbox_token_marks_captcha_solved(monkeypatch):
    import scraper.captcha as captcha

    class Locator:
        first = None

        def __init__(self, value=""):
            self.value = value
            self.first = self
            self.clicked = False

        async def count(self):
            return 1

        async def input_value(self):
            return self.value

        async def get_attribute(self, _name):
            return None

        async def click(self, **_kwargs):
            self.clicked = True
            token.value = "solved-token"

    token = Locator()

    class Frame:
        url = "https://www.google.com/recaptcha/api2/anchor"

        def locator(self, selector):
            return Locator() if "recaptcha-anchor" in selector else token

    class Page:
        url = "https://example.test"
        frames = [Frame()]

        async def content(self):
            return '<textarea name="g-recaptcha-response"></textarea><iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>'

        async def wait_for_timeout(self, _timeout):
            return None

        def locator(self, _selector):
            return token

    monkeypatch.setattr(captcha, "_record_attempt", lambda *_args: None)
    unresolved, attempts = await captcha.resolve_captcha(Page(), "https://example.test")
    assert unresolved is None
    assert attempts == ["passive_wait", "checkbox"]


@pytest.mark.asyncio
async def test_frame_vision_stops_after_two_rounds(monkeypatch):
    import scraper.captcha as captcha
    from scraper.barrier import BarrierInfo

    calls = []

    class Tiles:
        async def count(self):
            return 9

        def nth(self, index):
            class Tile:
                async def click(self):
                    calls.append(index)

            return Tile()

    class Grid:
        first = None

        def __init__(self):
            self.first = self

        def locator(self, selector):
            return Tiles() if "tile" in selector else self

        async def inner_text(self):
            return "Select buses"

        async def screenshot(self):
            return b"image"

        async def click(self, **_kwargs):
            return None

    class Frame:
        url = "https://www.google.com/recaptcha/api2/bframe"

        def locator(self, _selector):
            return Grid()

    class Page:
        url = "https://example.test"
        frames = [Frame()]

        async def content(self):
            return (
                '<iframe src="https://www.google.com/recaptcha/api2/bframe"></iframe>'
            )

        async def wait_for_timeout(self, _timeout):
            return None

        def locator(self, _selector):
            class Empty:
                async def count(self):
                    return 0

            return Empty()

    answers = iter([(None, "failure"), ([1], "success")])

    async def vision(*_args):
        return next(answers)

    monkeypatch.setattr(captcha, "_vision_request", vision)
    monkeypatch.setattr(captcha, "_record_attempt", lambda *_args: None)
    unresolved, attempts = await captcha.resolve_captcha(Page(), "https://example.test")
    assert isinstance(unresolved, BarrierInfo)
    assert attempts.count("vision_grid") == 2
    assert calls == [1]


@pytest.mark.asyncio
async def test_grid_uses_bframe_when_anchor_precedes_it(monkeypatch):
    import scraper.captcha as captcha

    seen = []

    class EmptyGrid:
        first = None

        def __init__(self):
            self.first = self

        def locator(self, _selector):
            return self

        async def count(self):
            return 0

    class Grid(EmptyGrid):
        async def count(self):
            return 9

        async def inner_text(self):
            return "Select fixture tiles"

        async def screenshot(self):
            return b"fixture"

        def nth(self, _index):
            return self

        async def click(self, **_kwargs):
            return None

    class Anchor:
        url = "https://www.google.com/recaptcha/api2/anchor"

        def locator(self, _selector):
            return EmptyGrid()

    class Bframe:
        url = "https://www.google.com/recaptcha/api2/bframe"

        def locator(self, _selector):
            seen.append("bframe")
            return Grid()

    class Page:
        url = "https://example.test"

        def __init__(self):
            self.frame_reads = 0

        @property
        def frames(self):
            self.frame_reads += 1
            if self.frame_reads == 1:
                return [Anchor()]
            return [Anchor(), Bframe()]

        async def content(self):
            return (
                '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>'
            )

        async def wait_for_timeout(self, _timeout):
            return None

        def locator(self, _selector):
            return EmptyGrid()

    async def vision(*_args):
        return None, "unavailable"

    monkeypatch.setattr(captcha, "_vision_request", vision)
    monkeypatch.setattr(captcha, "_record_attempt", lambda *_args: None)
    _unresolved, attempts = await captcha.resolve_captcha(
        Page(), "https://example.test"
    )
    assert attempts[-1] == "vision_grid"
    assert seen


@pytest.mark.asyncio
async def test_force_browser_only_routes_turnstile_to_flaresolverr(monkeypatch):
    import scraper.fetch as fetch

    monkeypatch.setattr(
        fetch._settings, "scraper_private_url_allowlist", "example.test"
    )

    async def allow(*_args, **_kwargs):
        return True, None

    async def playwright(_url):
        return {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"provider": "turnstile"},
        }

    async def flaresolverr(_url):
        return {"markdown": "resolved content " * 40, "url": "https://example.test"}

    monkeypatch.setattr(fetch, "_politeness_check_and_delay", allow)
    monkeypatch.setattr(fetch, "fetch_via_playwright", playwright)
    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", flaresolverr)

    async def accept(result, *_args):
        return result

    monkeypatch.setattr(fetch, "_maybe_degrade", accept)
    result = await fetch.smart_scrape("https://example.test", force_browser=True)
    assert result["markdown"].startswith("resolved")

    async def recaptcha(_url):
        return {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"provider": "recaptcha"},
        }

    monkeypatch.setattr(fetch, "fetch_via_playwright", recaptcha)
    result = await fetch.smart_scrape("https://example.test", force_browser=True)
    assert result["error_code"] == "CAPTCHA_UNRESOLVED"


@pytest.mark.asyncio
async def test_unresolved_turnstile_survives_failed_flaresolverr_without_cache(
    monkeypatch,
):
    import scraper.fetch as fetch

    monkeypatch.setattr(
        fetch._settings, "scraper_private_url_allowlist", "example.test"
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def allow(*_args, **_kwargs):
        return True, None

    async def playwright(_url):
        return {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"provider": "turnstile"},
        }

    async def no_flaresolverr(_url):
        return None

    async def no_cache(_url):
        return None

    async def shielded(*_args):
        return {
            "redirect_url": "https://example.test",
            "shielded": True,
            "is_binary": False,
        }

    writes = []

    async def cache(*_args, **_kwargs):
        writes.append(True)

    monkeypatch.setattr(
        fetch.curl_requests, "AsyncSession", lambda **_kwargs: Session()
    )
    monkeypatch.setattr(fetch, "_politeness_check_and_delay", allow)
    monkeypatch.setattr(fetch, "_check_cache", no_cache)
    monkeypatch.setattr(fetch, "_head_probe", shielded)
    monkeypatch.setattr(fetch, "fetch_via_playwright", playwright)
    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", no_flaresolverr)
    monkeypatch.setattr(fetch, "_set_cache", cache)
    result = await fetch.smart_scrape("https://example.test")
    assert result["error_code"] == "CAPTCHA_UNRESOLVED"
    assert not writes


@pytest.mark.asyncio
async def test_tier3_stores_cookies_before_closing_browser(monkeypatch):
    import scraper.fetch_tiers as tiers

    events = []

    class Page:
        url = "https://example.test"

        async def goto(self, *_args, **_kwargs):
            return None

        async def title(self):
            return "Article"

        async def content(self):
            return (
                "<html><body><article>"
                + ("content " * 100)
                + "</article></body></html>"
            )

    class Context:
        async def new_page(self):
            return Page()

    class Browser:
        async def close(self):
            events.append("close")

    class PlaywrightContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def create_browser(*_args):
        return Browser(), False

    async def create_context(*_args, **_kwargs):
        return Context()

    async def store(*_args):
        events.append("store")

    async def inject(*_args):
        return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: PlaywrightContext()),
    )
    monkeypatch.setattr("scraper.stealth.create_stealth_browser", create_browser)
    monkeypatch.setattr("scraper.stealth.create_stealth_context", create_context)
    monkeypatch.setattr(tiers, "_is_private_url", lambda _url: (False, ""))
    monkeypatch.setattr("scraper.cookie_store.inject_cookies", inject)
    monkeypatch.setattr("scraper.cookie_store.store_cookies", store)
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "content " * 100)

    result = await tiers._playwright_fetch_with_proxy("https://example.test", None)
    assert result and result["source"] == "playwright"
    assert events == ["store", "close"]


@pytest.mark.asyncio
async def test_public_scrape_maps_unresolved_captcha_to_typed_error(monkeypatch):
    import scraper.app as app
    from scraper.exceptions import CaptchaError

    async def unresolved(*_args, **_kwargs):
        return {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"provider": "recaptcha", "attempted_strategies": ["checkbox"]},
        }

    monkeypatch.setattr(app, "smart_scrape", unresolved)
    with pytest.raises(CaptchaError) as error:
        await app.scrape(app.ScrapeRequest(url="https://example.test"))
    assert error.value.error_code == "CAPTCHA_UNRESOLVED"
    assert error.value.details["provider"] == "recaptcha"


@pytest.mark.asyncio
async def test_agent_scraper_client_preserves_unresolved_captcha(monkeypatch):
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper.test")
    calls = 0

    async def unresolved(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "success": False,
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "details": {"provider": "recaptcha"},
        }

    monkeypatch.setattr(client, "scrape", unresolved)
    result = await client.scrape_with_fallback("https://example.test")
    await client.close()

    assert result["error_code"] == "CAPTCHA_UNRESOLVED"
    assert calls == 1


@pytest.mark.asyncio
async def test_agent_scraper_client_accepts_binary_download_without_browser_fallback(
    monkeypatch,
):
    from agent.scraper_client import ScraperClient

    client = ScraperClient("http://scraper.test")
    calls = 0
    download = {
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "size": 128,
    }

    async def binary_result(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"success": True, "data": {"markdown": "", "download": download}}

    monkeypatch.setattr(client, "scrape", binary_result)
    result = await client.scrape_with_fallback("https://example.test/document.pdf")
    await client.close()

    assert result["data"]["download"] == download
    assert calls == 1


@pytest.mark.asyncio
async def test_adapter_identifier_dispatches_before_url_guard(monkeypatch):
    import scraper.fetch as fetch

    class AdapterResult:
        source = "nvd"

        @staticmethod
        def to_dict():
            return {"markdown": "CVE content", "source": "nvd", "url": "cve:CVE-1"}

    class Registry:
        _entries = [object()]

        @staticmethod
        async def dispatch(url, _ctx):
            assert url == "cve:CVE-1"
            return AdapterResult()

    def unexpected_guard(_url):
        raise AssertionError("adapter identifiers must not reach URL validation")

    monkeypatch.setattr(fetch, "get_registry", lambda: Registry())
    monkeypatch.setattr(fetch, "_is_private_url", unexpected_guard)

    result = await fetch.smart_scrape("cve:CVE-1")

    assert result["source"] == "nvd"
    assert result["markdown"] == "CVE content"


@pytest.mark.asyncio
async def test_smart_scrape_blocks_private_url_before_any_fetch_tier(monkeypatch):
    import scraper.fetch as fetch

    async def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("fetch tier must not run for a private destination")

    monkeypatch.setattr(fetch._settings, "scraper_private_url_allowlist", "")
    monkeypatch.setattr(fetch, "_is_private_url", lambda _url: (True, "blocked"))
    monkeypatch.setattr(fetch, "fetch_via_playwright", unexpected_fetch)

    result = await fetch.smart_scrape("http://127.0.0.1/private", force_browser=True)

    assert result["error_code"] == "PRIVATE_URL_BLOCKED"
    assert result["markdown"] == ""


def test_private_url_allowlist_matches_exact_hostname_only(monkeypatch):
    import scraper.fetch as fetch

    monkeypatch.setattr(
        fetch._settings,
        "scraper_private_url_allowlist",
        "test-site, tier3-fixture",
    )

    assert fetch._private_url_allowlisted("http://test-site:8000/pricing") is True
    assert fetch._private_url_allowlisted("http://tier3-fixture:8000/") is True
    assert fetch._private_url_allowlisted("http://evil.test-site/") is False


@pytest.mark.asyncio
async def test_agent_scrape_route_raises_typed_captcha_error():
    from agent.exceptions import CaptchaError
    from agent.models import ScrapeRequest
    from agent.routes.scrape import scrape

    class Client:
        async def scrape(self, *_args, **_kwargs):
            return {
                "success": False,
                "error": "CAPTCHA challenge could not be resolved",
                "error_code": "CAPTCHA_UNRESOLVED",
                "details": {"provider": "hcaptcha"},
            }

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(scraper_client=Client()))
    )
    with pytest.raises(CaptchaError) as error:
        await scrape(request, ScrapeRequest(url="https://example.test"))

    assert error.value.error_code == "CAPTCHA_UNRESOLVED"
    assert error.value.details["provider"] == "hcaptcha"


@pytest.mark.asyncio
async def test_batch_scrape_preserves_captcha_error_code(monkeypatch):
    import agent.worker as worker

    captured = {}

    class Store:
        def __init__(self, *_args):
            pass

        def get_job(self, _job_id):
            return None

        def update_job_progress(self, *_args, **_kwargs):
            pass

        def get_completed(self, _job_id):
            return 0

    class Client:
        def __init__(self, *_args):
            pass

        async def scrape(self, _url):
            return {
                "success": False,
                "error": "CAPTCHA challenge could not be resolved",
                "error_code": "CAPTCHA_UNRESOLVED",
            }

        async def close(self):
            pass

    async def run(_job_id, _kind, _store, _webhook, work_fn, cleanup_fn):
        captured.update(await work_fn())
        await cleanup_fn()

    settings = types.SimpleNamespace(
        valkey_host="valkey", valkey_port=6379, valkey_db=0
    )
    monkeypatch.setattr(worker, "_get_worker_settings", lambda: settings)
    monkeypatch.setattr(worker, "JobStore", Store)
    monkeypatch.setattr(worker, "ScraperClient", Client)
    monkeypatch.setattr(worker, "_run_job_with_observability", run)

    await worker._process_batch_scrape_async(
        "job-1", ["https://example.test"], "http://scraper.test"
    )

    assert captured["errors"][0]["error_code"] == "CAPTCHA_UNRESOLVED"
    assert captured["errors"][0]["error_type"] == "captcha_unresolved"
