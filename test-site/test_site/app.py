"""Small fixture website with three behaviors:
- llms.txt site
- markdown-negotiation site
- dynamic JS-rendered site
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI(title="GroktoCrawl Test Site", version="0.1.0")

ENABLE_LLMS_TXT = os.getenv("ENABLE_LLMS_TXT", "0") == "1"
ENABLE_MARKDOWN = os.getenv("ENABLE_MARKDOWN", "0") == "1"
ENABLE_DYNAMIC = os.getenv("ENABLE_DYNAMIC", "0") == "1"
SITE_NAME = os.getenv("SITE_NAME", "Fixture Site")


@app.get("/health")
async def health():
    return {"status": "ok", "site": SITE_NAME}


@app.get("/llms.txt")
async def llms_txt():
    if not ENABLE_LLMS_TXT:
        return PlainTextResponse("not found", status_code=404)
    return PlainTextResponse(
        f"# {SITE_NAME}\n\nThis is the llms.txt entrypoint.\n\n- /pricing\n- /about\n- /dynamic\n",
        media_type="text/plain",
    )


@app.get("/pricing")
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
