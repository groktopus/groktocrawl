"""Container-level CAPTCHA recovery probe for the Docker integration gate.

Run inside scraper-svc so it exercises the packaged CloakBrowser runtime. The
fixture-only private-host exception is process-local and never changes the
service's SSRF policy.
"""

import asyncio
import os

from scraper import app as scraper_app
from scraper import fetch_tiers
from scraper.cache import _get_cache_client, _scrape_cache_key
from scraper.exceptions import CaptchaError


async def main() -> None:
    fixture = os.getenv("TIER3_FIXTURE_BASE_URL", "http://tier3-fixture:8000")
    solved_url = f"{fixture}/captcha-hcaptcha-grid"
    blocked_url = f"{fixture}/captcha-unresolved"

    # The fixture lives on the Compose network. Keep this exception inside this
    # probe process rather than weakening the running service's SSRF guard.
    fetch_tiers._is_private_url = lambda _url: (False, "fixture probe")

    cache = await _get_cache_client()
    await cache.delete(_scrape_cache_key(solved_url), _scrape_cache_key(blocked_url))

    solved = await scraper_app.scrape(
        scraper_app.ScrapeRequest(url=solved_url, force_browser=True)
    )
    markdown = (solved.data or {}).get("markdown", "")
    assert "Fixture CAPTCHA Grid Article" in markdown, markdown[:500]

    try:
        await scraper_app.scrape(
            scraper_app.ScrapeRequest(url=blocked_url, force_browser=True)
        )
    except CaptchaError as exc:
        assert exc.error_code == "CAPTCHA_UNRESOLVED"
        assert (exc.details or {}).get("provider") == "hcaptcha"
    else:
        raise AssertionError("unresolved CAPTCHA did not raise CaptchaError")

    assert await cache.exists(_scrape_cache_key(blocked_url)) == 0
    print(
        "CAPTCHA runtime probe: solved fixture, typed unresolved error, no cache entry"
    )


if __name__ == "__main__":
    asyncio.run(main())
