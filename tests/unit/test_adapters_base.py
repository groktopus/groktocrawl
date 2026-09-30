"""Tests for the adapter framework (scraper-svc/scraper/adapters/base.py).

Tests the base contracts: AdapterResult, AdapterContext, SiteAdapter,
AdapterRegistry, and the @adapter decorator.
"""

import os
import re
import subprocess
import sys

import pytest
from scraper.adapters.base import (
    AdapterContext,
    AdapterError,
    AdapterRegistry,
    AdapterResult,
    AdapterTimeoutError,
    SiteAdapter,
    adapter,
    get_registry,
)


class TestAdapterResult:
    def test_basic_construction(self):
        r = AdapterResult(
            success=True, markdown="# Hello", source="test", url="https://example.com"
        )
        assert r.success is True
        assert r.markdown == "# Hello"
        assert r.metadata == {}
        assert r.source == "test"
        assert r.url == "https://example.com"

    def test_with_frontmatter_no_metadata(self):
        r = AdapterResult(success=True, markdown="plain text")
        assert r.with_frontmatter() == "plain text"

    def test_with_frontmatter_includes_metadata(self):
        r = AdapterResult(
            success=True, markdown="body", metadata={"title": "Test", "author": "Me"}
        )
        expected = "---\ntitle: Test\nauthor: Me\n---\n\nbody"
        assert r.with_frontmatter() == expected

    def test_to_dict_structure(self):
        r = AdapterResult(
            success=True,
            markdown="md",
            metadata={"k": "v"},
            source="src",
            url="https://x.com",
        )
        d = r.to_dict()
        assert d["markdown"].startswith("---")
        assert d["metadata"] == {"k": "v"}
        assert d["source"] == "src"
        assert d["url"] == "https://x.com"


class TestAdapterContext:
    @pytest.mark.asyncio
    async def test_with_timeout_completes(self):
        ctx = AdapterContext()

        async def fast():
            return 42

        result = await ctx.with_timeout(fast(), timeout=5)
        assert result == 42

    @pytest.mark.asyncio
    async def test_with_timeout_raises_on_timeout(self):
        ctx = AdapterContext()

        async def slow():
            import asyncio

            await asyncio.sleep(100)
            return 42

        with pytest.raises(AdapterTimeoutError, match=r"Timed out after 0.01s"):
            await ctx.with_timeout(slow(), timeout=0.01)

    def test_default_values(self):
        ctx = AdapterContext()
        assert ctx.browser_svc_url == ""
        assert ctx.config == {}


class TestSiteAdapterConcrete:
    """Test that a concrete SiteAdapter subclass works."""

    def test_subclass_must_implement_scrape(self):
        with pytest.raises(TypeError):
            # Can't instantiate abstract class
            class Bad(SiteAdapter):  # type: ignore
                name = "bad"
                patterns = [re.compile(".*")]

            Bad()  # raises TypeError

    @pytest.mark.asyncio
    async def test_concrete_adapter_works(self):
        class GoodAdapter(SiteAdapter):
            name = "good"
            patterns = [re.compile(r"https://good\.example\.com/.*")]
            priority = 200

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="Good!", source="good")

        adapter = GoodAdapter()
        assert adapter.name == "good"
        assert adapter.priority == 200
        assert await adapter.can_handle("https://good.example.com/page") is True

    @pytest.mark.asyncio
    async def test_can_handle_override(self):
        class PickyAdapter(SiteAdapter):
            name = "picky"
            patterns = [re.compile(r"https://example\.com/.*")]

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="ok", source="picky")

            async def can_handle(self, url):
                return "allowed" in url

        adapter = PickyAdapter()
        assert await adapter.can_handle("https://example.com/allowed") is True
        assert await adapter.can_handle("https://example.com/denied") is False


class TestAdapterRegistry:
    @pytest.mark.asyncio
    async def test_dispatch_no_matches_returns_none(self):
        registry = AdapterRegistry()

        class NoopAdapter(SiteAdapter):
            name = "noop"
            patterns = [re.compile(r"https://irrelevant\.com/.*")]

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="", source="noop")

        registry.register(NoopAdapter())
        result = await registry.dispatch("https://example.com/page", AdapterContext())
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_first_matching_wins(self):
        registry = AdapterRegistry()

        class LowAdapter(SiteAdapter):
            name = "low"
            patterns = [re.compile(r"https://example\.com/.*")]
            priority = 50

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="low", source="low")

        class HighAdapter(SiteAdapter):
            name = "high"
            patterns = [re.compile(r"https://example\.com/.*")]
            priority = 200

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="high", source="high")

        registry.register(LowAdapter())
        registry.register(HighAdapter())
        result = await registry.dispatch("https://example.com/page", AdapterContext())
        assert result is not None
        assert result.markdown == "high"  # higher priority runs first

    @pytest.mark.asyncio
    async def test_dispatch_falls_through_on_adapter_error(self):
        registry = AdapterRegistry()

        class FailingAdapter(SiteAdapter):
            name = "fails"
            patterns = [re.compile(r"https://example\.com/.*")]
            priority = 100

            async def scrape(self, url, ctx):
                raise AdapterError("nope")

        class SuccessAdapter(SiteAdapter):
            name = "works"
            patterns = [re.compile(r"https://example\.com/.*")]
            priority = 50

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="works!", source="works")

        registry.register(FailingAdapter())
        registry.register(SuccessAdapter())
        result = await registry.dispatch("https://example.com/page", AdapterContext())
        assert result is not None
        assert result.markdown == "works!"

    @pytest.mark.asyncio
    async def test_dispatch_skips_can_handle_false(self):
        registry = AdapterRegistry()

        class SelectiveAdapter(SiteAdapter):
            name = "selective"
            patterns = [re.compile(r"https://example\.com/.*")]

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="hit!", source="selective")

            async def can_handle(self, url):
                return "selective-only" in url

        registry.register(SelectiveAdapter())
        result = await registry.dispatch("https://example.com/other", AdapterContext())
        assert result is None

        result = await registry.dispatch(
            "https://example.com/selective-only", AdapterContext()
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_does_not_match_non_matching_pattern(self):
        registry = AdapterRegistry()

        class GithubAdapter(SiteAdapter):
            name = "github"
            patterns = [re.compile(r"github\.com")]

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="gh", source="github")

        registry.register(GithubAdapter())
        result = await registry.dispatch("https://gitlab.com/foo", AdapterContext())
        assert result is None


class TestGetRegistry:
    def test_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2


class TestAdapterDecorator:
    def test_decorator_registers_class(self):
        @adapter
        class DecoratedAdapter(SiteAdapter):
            name = "decorated"
            patterns = [re.compile(".*")]

            async def scrape(self, url, ctx):
                return AdapterResult(success=True, markdown="dec", source="dec")

        from scraper.adapters.base import _registry_list

        assert DecoratedAdapter in _registry_list
        _registry_list.clear()  # cleanup for other tests

    def test_registry_load_all(self):
        """load_all() triggers @adapter registration and populates the registry."""
        # Exercise fresh-import registration in a separate interpreter. Reloading
        # adapters in the pytest worker invalidates classes imported by other tests.
        code = """
from scraper.adapters.base import AdapterRegistry
registry = AdapterRegistry()
registry.load_all()
names = {entry.name for entry in registry._entries}
assert {"youtube", "github", "substack"} <= names, names
"""
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=True,
            capture_output=True,
            text=True,
        )
