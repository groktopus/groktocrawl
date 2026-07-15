"""Small fixture website with three behaviors:
- llms.txt site
- markdown-negotiation site
- dynamic JS-rendered site
- Expanded endpoints for crawl/scope testing
"""

import asyncio
import gzip
import os
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

app = FastAPI(title="GroktoCrawl Test Site", version="0.2.0")

ENABLE_LLMS_TXT = os.getenv("ENABLE_LLMS_TXT", "0") == "1"
ENABLE_MARKDOWN = os.getenv("ENABLE_MARKDOWN", "0") == "1"
ENABLE_DYNAMIC = os.getenv("ENABLE_DYNAMIC", "0") == "1"
SITE_NAME = os.getenv("SITE_NAME", "Fixture Site")

# Base URL used in sitemap and robots.txt — defaults to Docker internal hostname.
# Override via SITE_BASE_URL env var if needed (e.g., for non-Docker testing).
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://test-site:8005")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/anything")
async def anything():
    """Catch-all endpoint for scraper tests."""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        "<html><body><h1>Anything</h1><p>This is the anything page.</p><a href='/pricing'>Pricing</a><a href='/about'>About</a></body></html>"
    )


@app.get("/llms.txt")
async def llms_txt():
    if not ENABLE_LLMS_TXT:
        return PlainTextResponse("not found", status_code=404)
    return PlainTextResponse(
        f"# {SITE_NAME}\n\nThis is the llms.txt entrypoint.\n\n- /pricing\n- /about\n- /dynamic\n",
        media_type="text/plain",
    )


@app.api_route("/pricing", methods=["GET", "HEAD"])
async def pricing(request: Request):
    accept = request.headers.get("accept", "")
    if ENABLE_MARKDOWN and "text/markdown" in accept:
        return PlainTextResponse(
            f"# {SITE_NAME} Pricing\n\n- Free: $0\n- Pro: $10\n- Business: $25\n",
            media_type="text/markdown",
        )
    return HTMLResponse(
        f"""
        <html>
          <body>
            <h1>{SITE_NAME} Pricing</h1>
            <p>Free: $0</p>
            <p>Pro: $10</p>
            <p>Business: $25</p>
          </body>
        </html>
        """
    )


@app.get("/")
async def root():
    return HTMLResponse(
        f"""
        <html>
          <body>
            <h1>{SITE_NAME}</h1>
            <a href="/pricing">Pricing</a>
            <a href="/about">About</a>
            <a href="/dynamic">Dynamic</a>
            <a href="/llms.txt">llms</a>
            <a href="/section/">Section</a>
            <a href="/external-links">External Links</a>
            <a href="/subdomain-links">Subdomain Links</a>
            <a href="/content/near-duplicate">Near Duplicate</a>
            <a href="/content/multi-sentence">Multi Sentence</a>
            <a href="/content/with-meta">With Meta</a>
            <a href="/content/with-boilerplate">With Boilerplate</a>
            <a href="/canonical-source">Canonical Source</a>
            <a href="/canonical-duplicate">Canonical Duplicate</a>
            <a href="/canonical-self">Canonical Self</a>
            <a href="/external-canonical">External Canonical</a>
            <a href="/mirror-a">Mirror A</a>
            <a href="/mirror-b">Mirror B</a>
            <a href="/near-duplicate-timestamp">Near Duplicate Timestamp</a>
          </body>
        </html>
        """
    )


@app.get("/about")
async def about():
    return HTMLResponse(
        f"""
        <html><body><h1>About {SITE_NAME}</h1><p>Self-contained fixture site.</p></body></html>
        """
    )


@app.get("/dynamic")
async def dynamic():
    if not ENABLE_DYNAMIC:
        return HTMLResponse("<html><body><h1>Dynamic page disabled</h1></body></html>")
    return HTMLResponse(
        """
        <html>
          <head>
            <title>Tier 3 Dynamic Page</title>
            <meta name="description" content="This page requires JavaScript rendering for its content.">
          </head>
          <body>
            <h1>Dynamic Page</h1>
            <div id="content">Loading...</div>
            <div id="content2">Also loading...</div>
            <script>
              setTimeout(() => {
                document.getElementById('content').innerText = 'Dynamic Content Loaded';
                document.getElementById('content2').innerText = 'Secondary dynamic content is also rendered.';
              }, 50);
            </script>
          </body>
        </html>
        """
    )


@app.get("/captcha-hcaptcha")
async def captcha_hcaptcha():
    """Fixture-only same-origin hCaptcha-shaped checkbox challenge."""
    return HTMLResponse(
        """<html><body><div class="h-captcha"><textarea name="h-captcha-response"></textarea>
        <iframe src="/captcha-hcaptcha-frame"></iframe></div>
        <article><h1>Fixture CAPTCHA Article</h1><p>This verified article content is returned after the fixture checkbox sets its token.</p></article></body></html>"""
    )


@app.get("/captcha-hcaptcha-frame")
async def captcha_hcaptcha_frame():
    return HTMLResponse(
        """<html><body><button id="checkbox" role="checkbox" onclick="parent.document.querySelector('[name=h-captcha-response]').value='fixture-token'">Verify</button></body></html>"""
    )


@app.get("/captcha-hcaptcha-grid")
async def captcha_hcaptcha_grid():
    return HTMLResponse(
        """<html><body><div class="h-captcha"><textarea name="h-captcha-response"></textarea>
        <iframe src="/captcha-hcaptcha-grid-frame"></iframe></div>
        <article><h1>Fixture CAPTCHA Grid Article</h1><p>This article follows the fixture-only 3 by 3 grid.</p></article></body></html>"""
    )


@app.get("/captcha-hcaptcha-grid-frame")
async def captcha_hcaptcha_grid_frame():
    tiles = "".join('<button class="task-image">tile</button>' for _ in range(9))
    return HTMLResponse(
        f"""<html><body><div class="challenge-container"><p class="prompt-text">Select fixture tiles</p>{tiles}<button class="button-submit" onclick="parent.document.querySelector('[name=h-captcha-response]').value='fixture-token'">Submit</button></div></body></html>"""
    )


@app.get("/captcha-unresolved")
async def captcha_unresolved():
    return HTMLResponse(
        """<html><body><div class="h-captcha"><textarea name="h-captcha-response"></textarea>
        <iframe src="/captcha-unresolved-frame"></iframe></div></body></html>"""
    )


@app.get("/captcha-unresolved-frame")
async def captcha_unresolved_frame():
    return HTMLResponse(
        "<html><body><div class='challenge-container'>Unavailable fixture</div></body></html>"
    )


# ----- llms.txt test fixtures -----


@app.get("/content/multi-sentence")
async def content_multi_sentence():
    """A page with long multi-sentence content for sentence-boundary testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Multi-Sentence Page</h1>
            <p>This is the first sentence of the description. This is the second
            sentence that continues the thought with more detail. This third sentence
            adds even more context for the reader to understand the page topic.
            And here is a fourth sentence just to make sure we have enough content
            to test the sentence boundary detection logic.</p>
          </body>
        </html>
        """
    )


@app.get("/content/with-meta")
async def content_with_meta():
    """A page with explicit <meta name="description"> for meta tag preference testing."""
    return HTMLResponse(
        """
        <html>
          <head>
            <title>Meta Tag Page</title>
            <meta name="description" content="This is the meta description for testing that GroktoCrawl prefers structured metadata over body content when generating llms.txt entries.">
            <meta property="og:description" content="This is the Open Graph description for testing.">
          </head>
          <body>
            <h1>Meta Tag Page</h1>
            <p>This body text should not be used because the meta description is better.</p>
          </body>
        </html>
        """
    )


@app.get("/content/with-boilerplate")
async def content_with_boilerplate():
    """A page with cookie banner and nav content before the main content."""
    return HTMLResponse(
        """
        <html>
          <body>
            <div id="cookie-banner">
              <p>This website uses cookies to improve your experience. Accept all cookies? Cookie settings here.</p>
            </div>
            <nav>
              <ul>
                <li><a href="/">Home</a></li>
                <li><a href="/pricing">Pricing</a></li>
                <li><a href="/about">About</a></li>
              </ul>
            </nav>
            <div id="main-content">
              <h1>Boilerplate Test Page</h1>
              <p>This is the real page content that should be extracted as the description after skipping the cookie banner and navigation boilerplate elements effectively.</p>
            </div>
          </body>
        </html>
        """
    )


# ----- Crawl/scope-testing fixtures -----


def _build_sitemap_xml() -> str:
    """Build a valid XML sitemap listing all fixture pages."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017

    # All fixture pages to include in the sitemap
    pages = [
        {"loc": "/", "priority": "1.0", "changefreq": "daily"},
        {"loc": "/pricing", "priority": "0.8", "changefreq": "weekly"},
        {"loc": "/about", "priority": "0.6", "changefreq": "monthly"},
        {"loc": "/dynamic", "priority": "0.5", "changefreq": "weekly"},
        {"loc": "/llms.txt", "priority": "0.3", "changefreq": "monthly"},
        {"loc": "/content/multi-sentence", "priority": "0.4"},
        {"loc": "/content/with-meta", "priority": "0.4"},
        {"loc": "/content/with-boilerplate", "priority": "0.4"},
        {"loc": "/content/near-duplicate", "priority": "0.4"},
        {"loc": "/section/", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/section/page-1", "priority": "0.6"},
        {"loc": "/section/page-2", "priority": "0.6"},
        {"loc": "/section/page-3", "priority": "0.6"},
        {"loc": "/section/page-1/subpage", "priority": "0.5"},
        {"loc": "/section/page-2/subpage", "priority": "0.5"},
        {"loc": "/section/page-3/subpage", "priority": "0.5"},
        {"loc": "/external-links", "priority": "0.5"},
        {"loc": "/subdomain-links", "priority": "0.5"},
        {"loc": "/canonical-source", "priority": "0.5"},
        {"loc": "/canonical-duplicate", "priority": "0.5"},
        {"loc": "/canonical-self", "priority": "0.5"},
        {"loc": "/external-canonical", "priority": "0.5"},
        {"loc": "/mirror-a", "priority": "0.5"},
        {"loc": "/mirror-b", "priority": "0.5"},
        {"loc": "/near-duplicate-timestamp", "priority": "0.5"},
    ]

    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    xhtml_ns = "http://www.w3.org/1999/xhtml"

    urlset = Element("urlset", xmlns=ns)

    for i, page in enumerate(pages):
        url = SubElement(urlset, "url")
        loc = SubElement(url, "loc")
        loc.text = f"{SITE_BASE_URL}{page['loc']}"
        lastmod = SubElement(url, "lastmod")
        lastmod.text = now
        if "changefreq" in page:
            cf = SubElement(url, "changefreq")
            cf.text = page["changefreq"]
        if "priority" in page:
            pr = SubElement(url, "priority")
            pr.text = page["priority"]

        # Add xhtml:link alternate elements to some entries (for VAL-SCOPE-052)
        if i % 3 == 0:
            alt = SubElement(url, f"{{{xhtml_ns}}}link")
            alt.set("rel", "alternate")
            alt.set("hreflang", "es")
            alt.set("href", f"{SITE_BASE_URL}/es{page['loc']}")
            alt2 = SubElement(url, f"{{{xhtml_ns}}}link")
            alt2.set("rel", "alternate")
            alt2.set("hreflang", "fr")
            alt2.set("href", f"{SITE_BASE_URL}/fr{page['loc']}")

    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return xml_declaration + tostring(urlset, encoding="unicode")


@app.get("/sitemap.xml")
async def sitemap_xml(delay: int = 0):
    """Return a valid XML sitemap listing all fixture pages.
    Supports ?delay=N for simulating slow sitemap fetches (VAL-SCOPE-050).
    """
    if delay > 0:
        await asyncio.sleep(delay)

    xml_content = _build_sitemap_xml()
    return Response(
        content=xml_content,
        media_type="application/xml",
    )


@app.get("/sitemap.xml.gz")
async def sitemap_xml_gz():
    """Return a gzip-compressed sitemap (VAL-SCOPE-051)."""
    xml_content = _build_sitemap_xml()
    compressed = gzip.compress(xml_content.encode("utf-8"))
    return Response(
        content=compressed,
        media_type="application/x-gzip",
        headers={"Content-Encoding": "gzip"},
    )


@app.get("/robots.txt")
async def robots_txt():
    """Return robots.txt with Sitemap directive, Disallow, and Crawl-delay."""
    content = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "Disallow: /private/\n"
        "Crawl-delay: 1\n"
        f"Sitemap: {SITE_BASE_URL}/sitemap.xml\n"
    )
    return PlainTextResponse(content, media_type="text/plain")


@app.get("/section/")
async def section_index():
    """Section index page with links to subsection pages (3-level hierarchy)."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Index</h1>
            <p>This is the section landing page with links to child pages.</p>
            <a href="/section/page-1">Page 1</a>
            <a href="/section/page-2">Page 2</a>
            <a href="/section/page-3">Page 3</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-1")
async def section_page_1():
    """First subsection page with links to a sub-subpage and parent."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 1</h1>
            <p>This is the first page in the section hierarchy.</p>
            <a href="/section/page-1/subpage">Subpage</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-2")
async def section_page_2():
    """Second subsection page with links to a sub-subpage and parent."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 2</h1>
            <p>This is the second page in the section hierarchy. It has different content from page 1.</p>
            <a href="/section/page-2/subpage">Subpage</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-3")
async def section_page_3():
    """Third subsection page with links to a sub-subpage and parent."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 3</h1>
            <p>This is the third page in the section hierarchy. It has yet different content from pages 1 and 2.</p>
            <a href="/section/page-3/subpage">Subpage</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-1/subpage")
async def section_page_1_subpage():
    """Deepest page in the 3-level hierarchy under page-1."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 1 - Subpage</h1>
            <p>This is a subpage at depth 3 under the section hierarchy.</p>
            <a href="/section/page-1">Back to Page 1</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-2/subpage")
async def section_page_2_subpage():
    """Deepest page in the 3-level hierarchy under page-2."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 2 - Subpage</h1>
            <p>This is a subpage at depth 3 under the section hierarchy.</p>
            <a href="/section/page-2">Back to Page 2</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/section/page-3/subpage")
async def section_page_3_subpage():
    """Deepest page in the 3-level hierarchy under page-3."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Section Page 3 - Subpage</h1>
            <p>This is a subpage at depth 3 under the section hierarchy.</p>
            <a href="/section/page-3">Back to Page 3</a>
            <a href="/section/">Back to Section Index</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/external-links")
async def external_links():
    """Page with external domain links and private IP links for scope testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>External Links Page</h1>
            <p>This page contains links to external domains and private IPs for crawl scope testing.</p>
            <a href="https://example.com">Example Domain</a>
            <a href="https://httpbin.org">HTTPBin</a>
            <a href="https://www.wikipedia.org">Wikipedia</a>
            <a href="http://127.0.0.1/">Localhost</a>
            <a href="http://192.168.1.1/">Private IP</a>
            <a href="https://github.com">GitHub</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/subdomain-links")
async def subdomain_links():
    """Page with subdomain links for allowSubdomains testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Subdomain Links Page</h1>
            <p>This page contains links to subdomains for scope testing.</p>
            <a href="http://sub.test-site:8005/">Subdomain</a>
            <a href="http://sub2.test-site:8005/">Subdomain 2</a>
            <a href="http://blog.test-site:8005/">Blog Subdomain</a>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/content/near-duplicate")
async def content_near_duplicate():
    """Page with near-duplicate content for content dedup testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Near Duplicate Page</h1>
            <p>This page has content that is similar to but not identical to other pages, for testing content deduplication.</p>
            <p>GroktoCrawl is a self-hosted alternative to Firecrawl. It provides web scraping, crawling, and search capabilities through a unified API. This paragraph is intentionally similar to other content on the site to test near-duplicate detection.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


# ----- Content-dedup test fixtures -----


@app.get("/canonical-source")
async def canonical_source():
    """A page that serves as a canonical target."""
    return HTMLResponse(
        """
        <html>
          <head>
            <link rel="canonical" href="http://test-site:8005/canonical-source">
          </head>
          <body>
            <h1>Canonical Source Page</h1>
            <p>This is the original page that other pages canonical-point to.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/canonical-duplicate")
async def canonical_duplicate():
    """A page with a canonical tag pointing to /canonical-source."""
    return HTMLResponse(
        """
        <html>
          <head>
            <link rel="canonical" href="http://test-site:8005/canonical-source">
          </head>
          <body>
            <h1>Canonical Duplicate Page</h1>
            <p>This page should be skipped during crawl because it points to /canonical-source.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/canonical-self")
async def canonical_self():
    """A page with a self-referencing canonical tag (should be scraped normally)."""
    return HTMLResponse(
        """
        <html>
          <head>
            <link rel="canonical" href="http://test-site:8005/canonical-self">
          </head>
          <body>
            <h1>Self-Referencing Canonical Page</h1>
            <p>This page has a self-referencing canonical tag and should be scraped normally.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/external-canonical")
async def external_canonical():
    """A page with a canonical tag pointing to an external domain."""
    return HTMLResponse(
        """
        <html>
          <head>
            <link rel="canonical" href="https://example.com/some-page">
          </head>
          <body>
            <h1>External Canonical Page</h1>
            <p>This page has a canonical pointing to an external domain. It should be scraped normally.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/mirror-a")
async def mirror_a():
    """Page A with content identical to /mirror-b for content hash dedup testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Mirror Content</h1>
            <p>This content is byte-for-byte identical to /mirror-b for testing content hash deduplication.</p>
            <p>This is a test page with predictable content.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/mirror-b")
async def mirror_b():
    """Page B with content identical to /mirror-a for content hash dedup testing."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Mirror Content</h1>
            <p>This content is byte-for-byte identical to /mirror-b for testing content hash deduplication.</p>
            <p>This is a test page with predictable content.</p>
            <a href="/">Home</a>
          </body>
        </html>
        """
    )


@app.get("/near-duplicate-timestamp")
async def near_duplicate_timestamp():
    """Page with near-identical content to /content/near-duplicate but with a different timestamp."""
    return HTMLResponse(
        """
        <html>
          <body>
            <h1>Near Duplicate Page</h1>
            <p>This page has content that is similar to but not identical to other pages, for testing content deduplication.</p>
            <p>GroktoCrawl is a self-hosted alternative to Firecrawl. It provides web scraping, crawling, and search capabilities through a unified API. This paragraph is intentionally similar to other content on the site to test near-duplicate detection.</p>
            <meta name="generated-at" content="2024-06-19T12:00:01Z">
            <a href="/">Home</a>
          </body>
        </html>
        """
    )
