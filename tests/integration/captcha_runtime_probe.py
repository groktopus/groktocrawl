"""Container-level CAPTCHA recovery probe for the Docker integration gate.

Run inside scraper-svc so it exercises the packaged CloakBrowser runtime. The
fixture hostnames are admitted by CI's exact private-host allowlist.
"""

import asyncio
import os
from uuid import uuid4

import httpx
from scraper import app as scraper_app
from scraper.cache import _get_cache_client, _scrape_cache_key
from scraper.exceptions import CaptchaError


async def main() -> None:
    fixture = os.getenv("TIER3_FIXTURE_BASE_URL", "http://tier3-fixture:8000")
    solved_url = f"{fixture}/captcha-hcaptcha-grid"
    blocked_url = f"{fixture}/captcha-unresolved"

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
    # Verify the public API also reaches automatic recovery without asking the
    # caller to select a browser or manually retry. Unique URLs avoid reusing the
    # direct scraper result above.
    api = os.getenv("AGENT_BASE_URL", "http://agent-svc:8080")
    nonce = uuid4().hex
    api_solved_url = f"{solved_url}?journey={nonce}"
    api_blocked_url = f"{blocked_url}?journey={nonce}"
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}
    async with httpx.AsyncClient(timeout=120, headers=headers) as client:
        recovered = await client.post(f"{api}/v2/scrape", json={"url": api_solved_url})
        assert recovered.status_code == 200, recovered.text
        recovered_body = recovered.json()
        assert recovered_body["success"] is True
        assert "Fixture CAPTCHA Grid Article" in recovered_body["data"]["markdown"]
        refused = await client.post(f"{api}/v2/scrape", json={"url": api_blocked_url})
        assert refused.status_code == 502, refused.text
        assert refused.json()["error_code"] == "CAPTCHA_UNRESOLVED", refused.text
    assert await cache.exists(_scrape_cache_key(api_blocked_url)) == 0
    print(
        "CAPTCHA runtime probe: scraper and public API recover automatically; "
        "unresolved challenge returns a typed error and is not cached"
    )


if __name__ == "__main__":
    asyncio.run(main())
