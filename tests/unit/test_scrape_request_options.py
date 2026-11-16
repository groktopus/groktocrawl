"""Contracts for public scrape extraction options."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRAPER_SVC = Path(__file__).resolve().parents[2] / "scraper-svc"
if str(SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(SCRAPER_SVC))


@pytest.mark.asyncio
async def test_public_scrape_maps_full_page_option_to_scraper_contents():
    from agent.models import ScrapeRequest
    from agent.routes.scrape import scrape

    captured = {}

    class Client:
        async def scrape(self, _url, **kwargs):
            captured.update(kwargs)
            return {"success": True, "data": {"markdown": ""}}

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scraper_client=Client()))
    )

    await scrape(
        request,
        ScrapeRequest(
            url="https://example.test/article",
            only_main_content=False,
            formats=["markdown", "links"],
        ),
    )

    assert captured == {
        "scrape_options": {"formats": ["markdown", "links"]},
        "contents": {"text": {"verbosity": "full"}},
    }


@pytest.mark.asyncio
async def test_full_page_scrape_keeps_article_title_and_visible_publication_date(
    monkeypatch,
):
    import scraper.app as scraper_app

    html = """<!doctype html>
    <html><head><title>Page title</title></head><body>
      <nav>Site navigation</nav>
      <article>
        <header><h1>NIST AI RMF release</h1>
          <time datetime="2023-01-26">January 26, 2023</time>
        </header>
        <p>NIST released the framework on this date.</p>
      </article>
      <footer>Site footer</footer>
    </body></html>"""

    async def scraped(*_args, **_kwargs):
        return {
            "markdown": "NIST released the framework on this date.",
            "raw_html_start": html,
            "source": "playwright",
            "url": "https://example.test/article",
        }

    monkeypatch.setattr(scraper_app, "smart_scrape", scraped)

    response = await scraper_app.scrape(
        scraper_app.ScrapeRequest(
            url="https://example.test/article",
            contents={"text": {"verbosity": "full"}},
        )
    )

    assert "NIST AI RMF release" in response.data["markdown"]
    assert "January 26, 2023" in response.data["markdown"]
    assert "Site navigation" in response.data["markdown"]
    assert "Site footer" in response.data["markdown"]
