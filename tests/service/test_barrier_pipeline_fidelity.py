"""Pipeline-fidelity supplements for the #586 barrier-consumer suite.

The committed consumer tests (``test_barrier_consumers.py``) prove the
underlying gate logic with locally replicated predicates and stubbed
payloads. This module drives the REAL pipeline paths over the served F1
Fastly-challenge fixture instead, per the M1 user-testing fidelity findings:

  * ``scrape_with_fallback`` accepts a clean negative-control (fixture N)
    scrape end-to-end — no spurious browser retry.
  * ``smart_scrape`` over an HTTP-served F1 (default flow) surfaces the
    block-page gate escalates to bounded browser recovery, then refuses
    unresolved content without returning challenge bytes.
  * ``smart_scrape`` over an HTTP-served F1 (forced-browser flow) keeps the
    Tier 3 barrier envelope visible in its terminal outcome (#586 polish),
    and the default flow preserves the envelope after full tier exhaustion
    too (today's bare refusal envelopes survive via the best-effort path;
    the terminal dict also merges ``barrier`` provenance for corroborated
    envelopes, mirroring the forced-browser polish; plain exhaustion stays
    bare).
  * A flagged CHILD page is attempted at most 3 times through ``CrawlEngine.run``
    (initial + bounded retries), then skipped.
  * A poisoned CRAWL-cache hit is refused end-to-end on a real engine run:
    never recorded as a page, no fresh scrape performed.
  * The scraper-svc cache-hit path re-gates a poisoned entry via
    ``_add_quality`` (block-page precedence over the low-yield warning).

Serving notes (VAL-BARR-008/015): the fixture server binds 127.0.0.1 on an
ephemeral port, so ``SCRAPER_PRIVATE_URL_ALLOWLIST=127.0.0.1`` is set for the
scraper settings singleton (exact-hostname match). The scraper cache client
is pointed at an unused loopback port where Valkey is absent — reads then miss
and writes no-op by design, keeping the tier flow deterministic without
mocking any gate logic.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

AGENT_SVC = Path(__file__).resolve().parents[2] / "agent-svc"
if str(AGENT_SVC) not in sys.path:
    sys.path.insert(0, str(AGENT_SVC))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
F1_PATH = FIXTURES / "fastly-challenge-full.html"
N_PATH = FIXTURES / "tech-article-javascript.html"

CHALLENGE_PHRASES = (
    "javascript is disabled in your browser",
    "please enable javascript to proceed",
)

# The smart_scrape tests below import ``scraper.fetch``, whose module-level
# ``from curl_cffi import requests`` requires a Fast-Tests-lane dependency the
# agent-svc container (integration lane host) does not ship. Skip-governed per
# tests/conftest.py rules; same rationale as tests/unit/test_lightweight_only.py.
_curl_cffi_available = True
try:
    import curl_cffi  # noqa: F401
except ModuleNotFoundError:
    _curl_cffi_available = False

_requires_curl_cffi = pytest.mark.skipif(
    not _curl_cffi_available,
    reason="scraper.fetch imports curl_cffi, unavailable in the agent container",
    owner="repository-maintainer",
    issue="#586",
    classification="retained",
    environment="agent-svc integration container lacks scraper-svc heavy deps",
)


def _contains_challenge(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in CHALLENGE_PHRASES)


# ── Shared fixture plumbing ─────────────────────────────────────


class _AsyncFileServer:
    """Minimal async HTTP/1.1 server serving one payload on 127.0.0.1.

    Uses asyncio.start_server directly (no uvicorn dependency) — the served
    bytes are static, and curl_cffi/httpx only need a valid response head.
    """

    def __init__(self, payload: bytes):
        self.payload = payload
        self.server: asyncio.Server | None = None
        self.port: int = 0

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.read(64 * 1024)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: " + str(len(self.payload)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.write(self.payload)
            await writer.drain()
        except Exception:  # client disconnects are expected
            pass
        finally:
            writer.close()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/challenge"


@pytest_asyncio.fixture
async def _scraper_loopback_settings(monkeypatch):
    """Point the scraper at loopback fixtures with the private-URL allowlist.

    Sets SCRAPER_PRIVATE_URL_ALLOWLIST=127.0.0.1 (required to serve the F1
    fixture over localhost, VAL-BARR-008/015 contract note) and aims the
    scrape-cache at an unbound loopback port so Valkey-backed caching degrades
    to miss/no-op deterministically. The cached settings objects are patched
    attribute-wise (repo pattern): fetch.py binds ``_settings`` at import
    time, so env vars alone do not reach it on xdist workers.
    """
    from scraper import cache as cache_mod
    from scraper import fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod._settings, "scraper_private_url_allowlist", "127.0.0.1"
    )
    monkeypatch.setattr(cache_mod._settings, "valkey_host", "127.0.0.1")

    # Fresh cache-client singleton per test; connection to the dead port
    # fails fast, after which _check_cache returns None (miss) and
    # _set_cache silently skips writes — exactly the no-cache behavior the
    # tier flow expects, with the REAL cache code path exercised.
    monkeypatch.setattr(cache_mod, "_cache_client", None)

    yield


@pytest_asyncio.fixture
async def served_challenge_url(_scraper_loopback_settings):
    """Serve the F1 Fastly challenge fixture over ephemeral loopback HTTP."""
    from scraper.fetch_quality import html_to_markdown  # noqa: F401  (import sanity)

    server = _AsyncFileServer(F1_PATH.read_bytes())
    await server.start()
    try:
        yield server.url
    finally:
        await server.stop()


class _FakeCacheClient:
    """Dict-backed stand-in for the Valkey cache client (get-only use)."""

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}

    async def get(self, key: str):
        return self._payloads.get(key)


@pytest.fixture()
def seeded_scraper_cache(monkeypatch):
    """Serve seeded payloads behind scraper.cache's REAL ``_check_cache``.

    Same seam pattern as test_cache_source_html_size_migration.py, but the
    fixture exposes the backing client so tests can assert on stored keys.
    """
    from scraper import cache as cache_mod

    client = _FakeCacheClient()

    async def _fake_get_client():
        return client

    monkeypatch.setattr(cache_mod, "_cache_client", client)
    monkeypatch.setattr(cache_mod, "_get_cache_client", _fake_get_client)

    def _seed(payload: dict):
        key = cache_mod._scrape_cache_key(payload["url"])
        import json as _json

        client._payloads[key] = _json.dumps(payload)
        return payload

    _seed.client = client  # type: ignore[attr-defined]
    return _seed


# ── Promoted scratch coverage (validator round, issue-586) ───────


class TestScrapeWithFallbackNegativeControlEndToEnd:
    @pytest.mark.asyncio
    async def test_layer5_scrape_with_fallback_accepts_clean_n_unchanged(self):
        """Fixture N flows through scrape_with_fallback untouched.

        Promoted from validator scratch (.ut-scratch-issue-586,
        VAL-BARR-007 layer-5): the negative-control article must be accepted
        as a plain success with NO spurious forced-browser retry — the
        false-positive hazard that motivates the corroboration requirement.
        """
        from agent.scraper_client import ScraperClient
        from scraper.fetch_quality import html_to_markdown

        n_markdown = html_to_markdown(N_PATH.read_text(encoding="utf-8"))
        lowered = n_markdown.lower()
        assert "javascript" in lowered, "N must naturally mention JavaScript"
        assert not any(p in lowered for p in CHALLENGE_PHRASES), (
            "N must contain no barrier phrases"
        )

        n_payload = {
            "success": True,
            "data": {
                "markdown": n_markdown,
                "source": "http-tier",
                "url": "https://blog.example.test/js-tooling",
                "quality": {
                    "score": 1.0,
                    "checks": {
                        "boilerplate": "pass",
                        "completeness": "pass",
                        "block_detected": "pass",
                        "volume": "pass",
                    },
                    "detail": "all checks passed",
                },
            },
        }

        client = ScraperClient("http://scraper")
        calls: list[dict] = []

        async def fake_scrape(url, force_browser=False, lightweight_only=False, **kw):
            calls.append({"force_browser": force_browser})
            return dict(n_payload)  # fresh copy per call

        client.scrape = fake_scrape

        result = await client.scrape_with_fallback(
            "https://blog.example.test/js-tooling"
        )

        assert result.get("success") is True
        assert result["data"]["markdown"] == n_markdown
        assert not result.get("warning")
        assert result["data"]["quality"]["checks"]["block_detected"] == "pass"
        assert len(calls) == 1, f"expected single generic-stage call, got {calls}"
        assert calls[0]["force_browser"] is False


class TestSmartScrapeOverServedFixture:
    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_default_flow_served_fixture_is_flagged_never_bare_success(
        self, served_challenge_url, monkeypatch
    ):
        """Real HTTP Tier 2 gates escalate F1 and refuse exhausted recovery.

        Browser boundaries are local stubs; HTTP acquisition, extraction,
        quality gates and recovery coordination remain the production paths.
        """
        import scraper.fetch as fetch

        browser_calls = []

        async def blocked_browser(url):
            browser_calls.append(url)
            return {
                "error": "Barrier detected: fastly",
                "barrier": {
                    "detected": True,
                    "type": "fastly",
                    "provider": "fastly",
                    "confidence": 0.95,
                },
                "markdown": "",
                "source": "barrier-detection",
                "url": url,
            }

        async def unavailable(*_args):
            return None

        monkeypatch.setattr(fetch, "fetch_via_playwright", blocked_browser)
        monkeypatch.setattr(fetch, "fetch_via_flaresolverr", unavailable)
        monkeypatch.setattr(fetch, "_fetch_via_browser_svc", unavailable)
        result = await fetch.smart_scrape(served_challenge_url)

        assert browser_calls == [served_challenge_url]
        assert result["error_code"] == "BARRIER_DETECTED"
        assert result["markdown"] == ""
        assert result["recovery"]["outcome"] == "unresolved"
        assert result["recovery"]["exhausted"] is True

    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_forced_browser_flow_preserves_tier3_barrier_provenance(
        self, served_challenge_url
    ):
        """Forced-browser flow: the Tier 3 envelope stays visible terminally.

        When Tier 3's post-extraction gate refuses F1 it returns the barrier
        envelope (fetch_tiers.py builds it for this exact fixture — pinned by
        tests/unit/test_barrier_fastly.py); later tiers yield nothing. The
        terminal failure dict must keep that barrier provenance (#586 polish)
        instead of collapsing into a bare ``source: none`` error. The tier
        boundary is patched (repo precedent:
        test_barrier_consumers.TestScraperPipelineFixtureOutcome) so the test
        stays hermetic — no real Chromium launch — while smart_scrape's
        terminal assembly runs for real.
        """
        import scraper.recovery as recovery
        from scraper.fetch import smart_scrape

        envelope = {
            "error": ("Barrier detected: barrier fastly (confidence: 0.95)"),
            "barrier": {
                "detected": True,
                "type": "fastly",
                "provider": "fastly",
                "confidence": 0.95,
                "detail": "Definitive Fastly challenge signature (/_fs-ch-)",
            },
            "markdown": "",
            "source": "barrier-detection",
            "url": served_challenge_url,
        }

        async def gated_tier3(url):
            return dict(envelope)

        async def dead_flare(_url):
            return None

        async def no_recovery(_url, _content):
            return None

        with (
            patch("scraper.fetch.fetch_via_playwright", gated_tier3),
            patch("scraper.fetch.fetch_via_flaresolverr", dead_flare),
            patch.object(recovery, "attempt_llm_recovery", no_recovery),
        ):
            result = await smart_scrape(served_challenge_url, force_browser=True)

        assert result.get("error"), f"expected terminal error payload, got {result!r}"
        envelope_visible = (
            isinstance(result.get("barrier"), dict)
            or result.get("source") == "barrier-detection"
            or "barrier" in str(result.get("error", "")).lower()
        )
        assert envelope_visible, (
            f"Tier 3 barrier provenance must survive into the terminal dict: {result!r}"
        )
        assert result.get("barrier") == envelope["barrier"]
        assert not (result.get("markdown") and not result.get("warning")), (
            "must never ship challenge content as unqualified success"
        )

    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_forced_browser_flow_bare_failure_stays_bare_without_barrier(
        self, served_challenge_url
    ):
        """Non-barrier exhaustion keeps the legacy bare terminal dict.

        The provenance merge must not grow a ``barrier`` key when no tier
        reported one — plain unextractable pages fail exactly as before.
        """
        import scraper.recovery as recovery
        from scraper.fetch import smart_scrape

        async def empty_tier3(_url):
            return None

        async def dead_flare(_url):
            return None

        async def no_recovery(_url, _content):
            return None

        with (
            patch("scraper.fetch.fetch_via_playwright", empty_tier3),
            patch("scraper.fetch.fetch_via_flaresolverr", dead_flare),
            patch.object(recovery, "attempt_llm_recovery", no_recovery),
        ):
            result = await smart_scrape(served_challenge_url, force_browser=True)

        assert result.get("error")
        assert "barrier" not in result
        assert result.get("source") == "none"

    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_default_flow_bare_envelope_survives_exhaustion(
        self, served_challenge_url
    ):
        """Default flow over the REAL refused-envelope shape stays non-bare.

        fetch_tiers' Tier 3 gate-refusal envelope carries ``markdown: ""`` and
        NO ``raw_html_start``, so smart_scrape re-classifies it as barrier
        type "empty" (conf 0.95) — the escape hatch routes the envelope into
        ``best_effort`` instead of the terminal dict. After full tier
        exhaustion the best-effort result must still be returned with its
        FULL barrier dict intact (never a bare ``source: none`` error): the
        provenance survives even though the terminal merge is not what
        carried it for this shape.
        """
        import scraper.recovery as recovery
        from scraper.fetch import smart_scrape

        envelope = {
            "error": "Barrier detected: barrier fastly (confidence: 0.95)",
            "barrier": {
                "detected": True,
                "type": "fastly",
                "provider": "fastly",
                "confidence": 0.95,
                "detail": "Definitive Fastly challenge signature (/_fs-ch-)",
            },
            "markdown": "",
            "source": "barrier-detection",
            "url": served_challenge_url,
            # Deliberately NO raw_html_start: this is the exact committed
            # producer shape from fetch_tiers._playwright_fetch_unbounded.
        }

        async def gated_tier3(url):
            return dict(envelope)

        async def dead_flare(_url):
            return None

        async def no_recovery(_url, _content):
            return None

        async def allow(*_args, **_kwargs):
            return True, None

        async def shielded_probe(_url, _client):
            return {
                "shielded": True,
                "redirect_url": _url,
                "is_binary": False,
                "is_empty": False,
                "status_code": 403,
                "content_type": "text/html",
            }

        async def no_cache(_url):
            return None

        async def no_set_cache(_url, _result, prior_entry=None):
            return None

        with (
            patch("scraper.fetch._politeness_check_and_delay", allow),
            patch("scraper.fetch._head_probe", shielded_probe),
            patch("scraper.fetch._check_cache", no_cache),
            patch("scraper.fetch._set_cache", no_set_cache),
            patch("scraper.fetch.fetch_via_playwright", gated_tier3),
            patch("scraper.fetch.fetch_via_flaresolverr", dead_flare),
            patch.object(recovery, "attempt_llm_recovery", no_recovery),
        ):
            result = await smart_scrape(served_challenge_url)

        assert result.get("barrier") == envelope["barrier"], (
            f"the refused envelope's barrier dict must survive exhaustion: {result!r}"
        )
        assert result.get("source") == "barrier-detection"
        assert result.get("error"), f"expected non-bare outcome, got {result!r}"
        assert not (result.get("markdown") and not result.get("warning")), (
            "must never ship challenge content as unqualified success"
        )

    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_default_flow_terminal_merges_corroborated_tier3_barrier(
        self, served_challenge_url
    ):
        """Terminal merge keeps corroborated envelopes out of the bare dict.

        Defensive twin of the forced-browser provenance test: when a Tier 3
        envelope's challenge is corroborated via ``raw_html_start``
        (re-classified at confidence > 0.7, type != "empty"), the envelope
        takes the gate-refusal skip-path and can never enter ``best_effort``.
        The default-flow terminal ``source: none`` dict must merge the
        ``barrier`` provenance exactly like the forced-browser fast path
        (#586), so a future producer that attaches corroboration payload to
        its refusal envelopes cannot silently regress to bare failures.
        Today no committed producer emits this shape — the test pins the
        terminal-assembly contract itself, hermetically via the patched
        tier boundary over the loopback-served F1 fixture.
        """
        import scraper.recovery as recovery
        from scraper.fetch import smart_scrape

        envelope = {
            "error": "Barrier detected: barrier fastly (confidence: 0.95)",
            "barrier": {
                "detected": True,
                "type": "fastly",
                "provider": "fastly",
                "confidence": 0.95,
                "detail": "Definitive Fastly challenge signature (/_fs-ch-)",
            },
            "markdown": "",
            "source": "barrier-detection",
            "url": served_challenge_url,
            # Corroboration payload: _classify_barrier re-runs over
            # raw_html_start at the gate below; the F1 signature trips it.
            "raw_html_start": F1_PATH.read_text(encoding="utf-8"),
        }

        async def gated_tier3(url):
            return dict(envelope)

        async def dead_flare(_url):
            return None

        async def no_recovery(_url, _content):
            return None

        async def allow(*_args, **_kwargs):
            return True, None

        async def shielded_probe(_url, _client):
            return {
                "shielded": True,
                "redirect_url": _url,
                "is_binary": False,
                "is_empty": False,
                "status_code": 403,
                "content_type": "text/html",
            }

        async def no_cache(_url):
            return None

        with (
            patch("scraper.fetch._politeness_check_and_delay", allow),
            patch("scraper.fetch._head_probe", shielded_probe),
            patch("scraper.fetch._check_cache", no_cache),
            patch("scraper.fetch.fetch_via_playwright", gated_tier3),
            patch("scraper.fetch.fetch_via_flaresolverr", dead_flare),
            patch.object(recovery, "attempt_llm_recovery", no_recovery),
        ):
            result = await smart_scrape(served_challenge_url)

        assert result.get("error"), f"expected terminal error payload, got {result!r}"
        assert result.get("barrier") == envelope["barrier"], (
            f"Tier 3 barrier provenance must survive into the default-flow "
            f"terminal dict: {result!r}"
        )
        assert result.get("source") == "none", (
            f"this shape must reach the terminal dict, not best_effort: {result!r}"
        )
        assert not (result.get("markdown") and not result.get("warning")), (
            "must never ship challenge content as unqualified success"
        )

    @_requires_curl_cffi
    @pytest.mark.asyncio
    async def test_default_flow_bare_failure_stays_bare_without_barrier(
        self, served_challenge_url
    ):
        """Non-barrier exhaustion keeps the legacy bare default-flow dict.

        The default-flow twin of the forced-browser non-regression test: a
        Tier 3 that yields nothing must not grow a ``barrier`` key on the
        terminal error — plain unextractable pages fail exactly as before.
        """
        import scraper.recovery as recovery
        from scraper.fetch import smart_scrape

        async def empty_tier3(_url):
            return None

        async def dead_flare(_url):
            return None

        async def no_recovery(_url, _content):
            return None

        async def allow(*_args, **_kwargs):
            return True, None

        async def shielded_probe(_url, _client):
            return {
                "shielded": True,
                "redirect_url": _url,
                "is_binary": False,
                "is_empty": False,
                "status_code": 403,
                "content_type": "text/html",
            }

        async def no_cache(_url):
            return None

        with (
            patch("scraper.fetch._politeness_check_and_delay", allow),
            patch("scraper.fetch._head_probe", shielded_probe),
            patch("scraper.fetch._check_cache", no_cache),
            patch("scraper.fetch.fetch_via_playwright", empty_tier3),
            patch("scraper.fetch.fetch_via_flaresolverr", dead_flare),
            patch.object(recovery, "attempt_llm_recovery", no_recovery),
        ):
            result = await smart_scrape(served_challenge_url)

        assert result.get("error")
        assert "barrier" not in result
        assert result.get("source") == "none"


# ── Crawler end-to-end gaps promoted from the validator round ────


def _flagged_success(markdown: str | None = None) -> dict:
    return {
        "success": True,
        "warning": (
            "Block-page content detected (block_detected=fail); "
            "the page may be a challenge or error interstitial."
        ),
        "data": {
            "markdown": markdown or "# JavaScript is disabled in your browser.",
            "source": "playwright",
            "quality": {
                "score": 0.38,
                "checks": {
                    "boilerplate": "warn",
                    "completeness": "warn",
                    "block_detected": "fail",
                    "volume": "pass",
                },
                "detail": "block:fail",
            },
        },
    }


class TestCrawlerChildRetryBoundThroughRun:
    @pytest.mark.asyncio
    async def test_flagged_child_attempted_at_most_three_times_then_skipped(self):
        """Bounded child retry driven through CrawlEngine.run() end-to-end.

        Promoted from validator scratch (VAL-BARR-012a): a flagged CHILD page
        gets the initial attempt plus at most 2 retries inside one run(), then
        ends skipped — recorded once as a BARRIER_DETECTED error, never as a
        page. The start URL scrapes clean so the crawl actually reaches it.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        attempts: list[str] = []

        class Scraper:
            async def scrape(self, url, **kwargs):
                attempts.append(url)
                if url.rstrip("/").endswith("/child"):
                    return _flagged_success()
                return {
                    "success": True,
                    "data": {
                        "markdown": "# Clean start\n\nStart page body.",
                        "source": "content-negotiation",
                        "quality": {
                            "score": 0.9,
                            "checks": {"block_detected": "pass"},
                        },
                    },
                }

        engine = CrawlEngine(
            Scraper(),  # type: ignore[arg-type]
            options=CrawlOptions(max_pages=5, max_depth=1, sitemap_mode="skip"),
        )
        # Seed the flagged child directly (link discovery is stubbed off).
        engine._queue.append(("https://example.com/child", 1, False))
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        assert result.completed == 1, result.pages
        child_attempts = [u for u in attempts if u.endswith("/child")]
        assert len(child_attempts) == 3, (
            f"expected initial + exactly 2 retries, got {len(child_attempts)}"
        )
        child_errors = [e for e in result.errors if e.get("url", "").endswith("/child")]
        assert len(child_errors) == 1
        assert child_errors[0].get("error_code") == "BARRIER_DETECTED"
        assert all(
            "/child" not in p.get("metadata", {}).get("sourceURL", "")
            for p in result.pages
        )
        assert all(not _contains_challenge(p.get("markdown", "")) for p in result.pages)


class _FakeCrawlCache:
    """In-memory stand-in matching CrawlCache.check_cache()'s tuple shape."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def check_cache(self, url, max_age_ms=None, min_age_ms=None):
        entry = self.store.get(url)
        return (entry is not None), entry, None

    def set(self, url, result, ttl_ms=None):
        self.store[url] = result


class TestCrawlCacheHitRefusalThroughRun:
    @pytest.mark.asyncio
    async def test_cached_flagged_payload_never_recorded_as_page(self):
        """Poisoned crawl-cache hit refused end-to-end via CrawlEngine.run().

        Promoted from validator scratch (VAL-BARR-016b): a success:true-but-
        flagged payload placed in the crawl cache short-circuits the fresh
        scrape (cache HIT), fails the per-page barrier gate, and leaves zero
        pages plus a single typed refusal.
        """
        from agent.crawler import CrawlEngine, CrawlOptions

        cache = _FakeCrawlCache()
        cache.set("https://example.com/", _flagged_success())

        calls: list[str] = []

        class Scraper:
            async def scrape(self, url, **kwargs):
                calls.append(url)
                return {
                    "success": True,
                    "data": {"markdown": "# Should never be reached", "source": "x"},
                }

        engine = CrawlEngine(
            Scraper(),  # type: ignore[arg-type]
            options=CrawlOptions(
                max_pages=5,
                max_depth=0,
                sitemap_mode="skip",
                scrape_options={"max_age": 3600000},
            ),
            crawl_cache=cache,  # type: ignore[arg-type]
        )
        with patch.object(engine, "_get_html", return_value=None):
            result = await engine.run("https://example.com/")

        assert calls == [], "poisoned cache hit must short-circuit the scrape"
        assert result.pages == [], (
            f"poisoned cached payload must never be recorded as a page: {result.pages}"
        )
        child_errors = [
            e for e in result.errors if e.get("url") == "https://example.com/"
        ]
        assert len(child_errors) == 1
        assert child_errors[0].get("error_code") == "BARRIER_DETECTED"
        assert result.completed == 0


# ── Scraper-svc cache re-gating over the served fixture ──────────


class TestScraperCacheHitBranchRegating:
    @pytest.mark.asyncio
    async def test_poisoned_hit_via_real_check_cache_regated_with_block_warning(
        self, seeded_scraper_cache
    ):
        """fetch.py's cache-hit branch re-gates through the REAL cache read.

        A poisoned entry (challenge markdown cached as a bare success) placed
        behind the genuine ``_check_cache`` seam comes back re-assessed by
        ``_add_quality``: block_detected "fail" AND the block-page warning —
        the block string now wins even when the thin output would also trip
        the volume gate (#586 warning-precedence edge).
        """
        from pathlib import Path as _Path

        from scraper.cache import _scrape_cache_key
        from scraper.fetch_quality import html_to_markdown

        poisoned_url = "https://poisoned.test/challenge"
        fixture_html = _Path(F1_PATH).read_text(encoding="utf-8")
        seeded_scraper_cache(
            {
                "markdown": html_to_markdown(fixture_html),
                "url": poisoned_url,
                "source": "playwright",
                "source_html_size": len(fixture_html),
            }
        )
        key = _scrape_cache_key(poisoned_url)
        assert key in seeded_scraper_cache.client._payloads  # type: ignore[attr-defined]

        from scraper.cache import _check_cache

        cached = await _check_cache(poisoned_url)
        assert cached is not None, "seeded entry must be served by _check_cache"

        # Now the exact regating sequence fetch.py applies on hits:
        from scraper.fetch_quality import _add_quality

        enriched = _add_quality(cached)
        checks = enriched["quality"]["checks"]
        assert checks["block_detected"] == "fail"
        warning = enriched.get("warning") or ""
        assert warning.startswith("Block-page content detected"), warning

    @pytest.mark.asyncio
    async def test_block_warning_wins_over_low_yield_on_poisoned_volume_fail_entry(
        self, seeded_scraper_cache
    ):
        """The precedence edge itself: block-page beats low-yield text.

        A poisoned entry whose markdown is BOTH challenge text and anomalously
        thin relative to its large source used to surface the low-yield
        warning string; the block-page warning must win so consumers can key
        refusal on it directly.
        """
        from scraper.fetch_quality import _add_quality

        poisoned = {
            # Challenge prose (< 2048 chars) + huge source => volume fail too.
            "markdown": "# JavaScript is disabled in your browser.\n\nPlease enable JavaScript.",
            "url": "https://x.test/poisoned-volume",
            "source": "cache",
            "source_html_size": 94978,
        }
        scored = _add_quality(poisoned)

        checks = scored["quality"]["checks"]
        assert checks["block_detected"] == "fail"
        assert checks["volume"] == "fail"
        warning = scored.get("warning") or ""
        assert warning.startswith("Block-page content detected"), (
            f"block-page warning must take precedence, got: {warning}"
        )


# ── Session deepen step end-to-end (promoted gap) ────────────────


class TestSessionDeepenStepEndToEnd:
    @pytest.mark.asyncio
    async def test_deepen_stores_no_new_refs_and_no_challenge_text(self):
        """_step_deepen refuses every flagged deep-dive source end-to-end.

        Promoted from validator scratch (VAL-BARR-019b): drives the real
        deepen step (search -> scrape-with-fallback seam -> synthesis) with
        every new-source scrape barrier-flagged. Zero new refs/urls are
        stored, and neither the returned findings nor the appended artifact
        section carries challenge markdown.
        """
        import agent.session as session_mod

        manager = session_mod.SessionManager.__new__(session_mod.SessionManager)
        manager.store = MagicMock()
        manager.store.get_ref.return_value = {
            "url": "https://clean.test/source",
            "title": "Source",
            "markdown": "# Clean source\n\nReal content.",
        }
        manager.store.get_refs.return_value = {
            "ref_1_1": manager.store.get_ref.return_value
        }
        manager.store.append_step.return_value = 2

        class FakeSearXNG:
            def __init__(self, *_a, **_k):
                pass

            async def search(self, *a, **k):
                return (
                    [
                        {
                            "url": "https://barrier.test/deep",
                            "title": "D",
                            "description": "d",
                        }
                    ],
                    MagicMock(),
                )

            async def close(self):
                pass

        class FlaggedScraper:
            def __init__(self, *_a, **_k):
                pass

            base_url = "http://scraper"

            async def scrape_with_fallback(self, url: str, **kwargs) -> dict:
                return _flagged_success()

            async def close(self):
                pass

        class FailLLM:
            def __init__(self, *_a, **_k):
                raise AssertionError("LLM must not be invoked in this scenario")

        with (
            patch.object(session_mod, "SearXNGClient", FakeSearXNG),
            patch.object(session_mod, "ScraperClient", FlaggedScraper),
            patch.object(session_mod, "LLMClient", FailLLM),
        ):
            outcome = await manager._step_deepen(
                "sess-1",
                {"ref_id": "ref_1_1", "sub_topic": "go deeper", "max_sources": 1},
                "http://searxng",
                "http://scraper",
                "http://llm",
                "k",
                "m",
            )

        assert outcome["new_sources"] == []
        assert manager.store.add_ref.call_count == 0
        assert not _contains_challenge(outcome["new_findings"])
        artifact_section = manager.store.append_artifact.call_args[0][1]
        assert not _contains_challenge(artifact_section)


# ── Agent /v2/scrape error-code consistency (polish item 5) ──────


class TestBarrierErrorSurfaceConsistency:
    def test_barrier_refusal_renders_consistent_error_code_top_level(self):
        """BarrierDetectedError renders BARRIER_DETECTED at top level too.

        routes/scrape.py raises the dedicated subclass; through the
        production-style handler the rendered top-level ``error_code`` now
        matches ``details.error_code`` instead of the generic SCRAPE_FAILED.
        """
        from agent.exceptions import ScrapeError
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        app, tracker = _build_surface_app(_flagged_success())

        @app.exception_handler(ScrapeError)
        async def _handler(_request, exc):  # pragma: no cover - simple mapping
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "error": exc.detail,
                    "error_code": exc.error_code,
                    "details": exc.details,
                },
            )

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/v2/scrape",
            json={"url": "https://challenge.test/page", "formats": ["markdown"]},
        )

        assert resp.status_code == 502
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == "BARRIER_DETECTED", body
        assert (body.get("details") or {}).get("error_code") == "BARRIER_DETECTED"
        assert "barrier" in body["error"].lower()
        tracker.create_background_task.assert_not_called()


def _build_surface_app(payload: dict):
    from agent.routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.state.rate_limiter = MagicMock()
    app.state.job_store = MagicMock()
    app.state.max_searches_per_request = 5
    tracker = MagicMock()
    tracker.create_background_task = MagicMock()
    app.state.task_tracker = tracker
    scraper_client = MagicMock()
    scraper_client.scrape = AsyncMock(return_value=payload)
    scraper_client.close = AsyncMock()
    app.state.scraper_client = scraper_client
    app.include_router(router)
    return app, tracker
