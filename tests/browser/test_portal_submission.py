"""Browser regression for #653; requires Playwright Firefox and Chromium.

Run: python -m playwright install --with-deps firefox chromium
     pytest tests/browser/test_portal_submission.py --no-cov
The real portal HTML runs with intercepted HTTP responses; no live agent or CDN.
"""

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.parametrize("browser_name", ["firefox", "chromium"])
@pytest.mark.parametrize("entry", ["deep", "ask", "enter", "recent"])
def test_query_submission_without_navigation(browser_name, entry):
    html = (
        Path(__file__).resolve().parents[2] / "portal-svc/portal/templates/index.html"
    ).read_text()
    requests = []
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def respond(route):
            request = route.request
            if request.url == "http://portal.test/":
                route.fulfill(content_type="text/html", body=html)
            elif request.url.startswith("http://portal.test/ask"):
                requests.append(request)
                events = [
                    {"type": "token", "content": "Research result"},
                    {"type": "done", "result": "Research result", "sources": []},
                ]
                route.fulfill(
                    content_type="text/event-stream",
                    body="".join(f"data: {json.dumps(event)}\n\n" for event in events),
                )
            elif "marked" in request.url:
                route.fulfill(
                    content_type="application/javascript",
                    body="window.marked = {parse: text => text};",
                )
            else:
                route.abort()

        page.route("**/*", respond)
        page.add_init_script(
            "localStorage.setItem('groktocrawl_recent', JSON.stringify(['test query']));"
        )
        page.goto("http://portal.test/")
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        page.locator("#queryInput").fill("test query")
        if entry == "enter":
            page.locator("#queryInput").press("Enter")
        elif entry == "recent":
            page.locator(".recent-item").click()
        else:
            page.locator("#deepBtn" if entry == "deep" else "#askBtn").click()

        expect(page.locator("#answerArea")).to_have_text("Research result")
        expect(page.locator("#deepBtn")).to_be_enabled()
        expect(page.locator("#askBtn")).to_be_enabled()
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert requests[0].url == (
            "http://portal.test/ask/deep"
            if entry == "deep"
            else "http://portal.test/ask"
        )
        assert "test query" in requests[0].post_data
        assert ("num_sources" in requests[0].post_data) == (entry != "deep")
        assert navigations == []
        assert errors == []
        browser.close()
