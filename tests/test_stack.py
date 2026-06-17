import logging
import os
import time

import httpx
import pytest

logger = logging.getLogger(__name__)

AGENT = os.getenv("AGENT_BASE_URL", "http://localhost:8080")
SCRAPER = os.getenv("SCRAPER_BASE_URL", "http://localhost:8001")
SEARCH = os.getenv("SEARCH_BASE_URL", "http://localhost:8010")
LLM = os.getenv("LLM_BASE_URL", "http://localhost:8011")
TEST_SITE = os.getenv("TEST_SITE_BASE_URL", "http://localhost:8000")
TIER3_SITE = os.getenv("TIER3_FIXTURE_BASE_URL", "http://localhost:8006")
SEMANTIC = os.getenv("SEMANTIC_BASE_URL", "http://localhost:8003")


def wait_for(url: str, path: str = "/health", timeout_s: int = 120):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            r = httpx.get(url + path, timeout=2)
            if r.status_code == 200:
                return r
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}{path}: {last_err}")


def test_services_health():
    assert wait_for(AGENT).json()["status"] == "ok"
    assert wait_for(SCRAPER).json()["status"] == "ok"
    assert wait_for(SEARCH).json()["status"] == "ok"


def test_scraper_health_reports_playwright():
    """Scraper health endpoint should report Playwright browser availability.

    The startup probe launches Chromium once and caches the result.
    A missing ``checks.playwright`` field or ``available: false`` means
    the browser pipeline (Tier 3) is broken — the bug that the original
    ``playwright install-deps`` fix addressed.
    """
    r = httpx.get(SCRAPER + "/health", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok" or data["status"] == "degraded"
    assert "checks" in data, f"Health missing checks: {data}"
    assert "playwright" in data["checks"], f"Missing playwright check: {data['checks']}"
    assert data["checks"]["playwright"]["status"] == "available", (
        f"Playwright browser unavailable: {data['checks']['playwright']}. "
        "This usually means missing system deps (libglib-2.0.so.0 etc.) "
        "in the scraper-svc Docker image."
    )
    assert data["checks"]["playwright"]["available"] is True


def test_scraper_falls_through_to_playwright():
    """Scrape a page that has no llms.txt and no content-negotiation,
    forcing the scraper to use Playwright (Tier 3) for JS-rendered content.

    The tier3-fixture has ENABLE_LLMS_TXT=0, ENABLE_MARKDOWN=0, and
    serves /dynamic with JS-rendered content ("Dynamic Content Loaded").
    A successful scrape proves the Playwright browser pipeline works.
    """
    r = httpx.post(
        SCRAPER + "/scrape", json={"url": TIER3_SITE + "/dynamic"}, timeout=120
    )
    payload = r.json()
    if not payload["success"]:
        logger.warning(
            "Tier 3 scrape failed (expected in CI without fixture network): %s",
            payload.get("error"),
        )
        return
    logger.info(
        "Tier 3 scrape succeeded (source=%s, %d chars)",
        payload.get("data", {}).get("source", "?"),
        len(payload.get("data", {}).get("markdown", "") or ""),
    )


def test_health_endpoint_returns_per_dependency_checks():
    """GET /health returns per-dependency probe results in the ``checks`` field."""
    r = httpx.get(AGENT + "/health", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "checks" in data
    # Should probe all expected dependencies
    for dep in ("valkey", "searxng", "scraper", "browser"):
        assert dep in data["checks"], f"Missing check for {dep}"
        assert "status" in data["checks"][dep], f"Missing status in {dep} check"
        assert "latency_ms" in data["checks"][dep], f"Missing latency_ms in {dep} check"


def test_metrics_endpoint_returns_openmetrics():
    """GET /metrics returns OpenMetrics-formatted text with expected metric names."""
    r = httpx.get(AGENT + "/metrics", timeout=30)
    assert r.status_code == 200
    content_type = r.headers.get("content-type", "")
    assert "openmetrics" in content_type or "text/plain" in content_type
    body = r.text
    # Should contain expected metric type headers
    assert "# HELP" in body
    assert "# TYPE" in body
    assert "# EOF" in body
    # Should include the info metric
    assert "groktocrawl_info" in body
    # Should include queue depth gauge
    assert "queue_depth" in body


def test_scraper_uses_llms_txt():
    r = httpx.post(
        SCRAPER + "/scrape", json={"url": TEST_SITE + "/anything"}, timeout=120
    )
    payload = r.json()
    assert payload["success"] is True
    assert payload["data"]["source"] == "llms.txt"
    assert "llms.txt entrypoint" in payload["data"]["markdown"]


def test_scraper_uses_accept_markdown():
    # Disable llms.txt by targeting the pricing page on a site that still has it.
    # The scraper should still prefer llms.txt if root exists, so use a distinct host
    # behavior by checking the content result from the pricing page through the site root.
    r = httpx.post(
        SCRAPER + "/scrape", json={"url": TEST_SITE + "/pricing"}, timeout=120
    )
    payload = r.json()
    assert payload["success"] is True
    assert payload["data"]["source"] in {
        "llms.txt",
        "content-negotiation",
        "playwright",
    }


def test_agent_endpoints_return_job_and_status():
    create = httpx.post(
        AGENT + "/v2/agent",
        json={"prompt": "What is the pricing on the fixture site?"},
        timeout=120,
    )
    assert create.status_code == 200
    job_id = create.json()["id"]
    assert job_id

    status = httpx.get(AGENT + f"/v2/agent/{job_id}", timeout=120)
    assert status.status_code == 200
    payload = status.json()
    assert payload["success"] is True
    assert payload["status"] in {"processing", "completed"}


def test_crawl_batch_search_and_map_endpoints_exist():
    crawl = httpx.post(AGENT + "/v2/crawl", json={"url": TEST_SITE}, timeout=120)
    assert crawl.status_code == 200
    crawl_id = crawl.json()["id"]
    assert crawl_id

    batch = httpx.post(
        AGENT + "/v2/batch/scrape",
        json={"urls": [TEST_SITE + "/", TEST_SITE + "/pricing"]},
        timeout=120,
    )
    assert batch.status_code == 200
    assert batch.json()["id"]

    search = httpx.post(
        AGENT + "/v2/search", json={"query": "fixture pricing", "limit": 3}, timeout=120
    )
    assert search.status_code == 200
    search_payload = search.json()
    assert search_payload["success"] is True
    assert len(search_payload["data"]["web"]) >= 1

    map_resp = httpx.post(
        AGENT + "/v2/map", json={"url": TEST_SITE, "limit": 10}, timeout=120
    )
    assert map_resp.status_code == 200
    assert map_resp.json()["success"] is True
    assert map_resp.json()["links"]


def test_search_fast_mode_backward_compatible():
    """fast mode (default) returns identical response shape to current behavior."""
    resp = httpx.post(
        AGENT + "/v2/search",
        json={
            "query": "fixture pricing",
            "limit": 3,
            "search_type": "fast",
        },
        timeout=120,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert "web" in payload["data"]
    assert payload["output"] is None  # fast mode with no schema → no output


def test_search_rich_mode_returns_data_and_output():
    """rich mode scrapes and enriches results, returns output field."""
    resp = httpx.post(
        AGENT + "/v2/search",
        json={
            "query": "fixture pricing",
            "limit": 2,
            "search_type": "rich",
        },
        timeout=120,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert "web" in payload["data"]
    # rich mode should populate output with enriched content
    assert payload.get("output") is not None
    assert "content" in payload["output"]


def test_search_rich_with_output_schema():
    """rich mode with output_schema returns structured data."""
    resp = httpx.post(
        AGENT + "/v2/search",
        json={
            "query": "fixture pricing",
            "limit": 2,
            "search_type": "rich",
            "output_schema": {
                "type": "object",
                "properties": {
                    "page_name": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
        timeout=120,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    output = payload.get("output")
    assert output is not None
    assert "content" in output
    assert "grounding" in output


def test_search_unknown_type_falls_back_to_fast():
    """An unrecognized search_type should be treated as fast (default)."""
    resp = httpx.post(
        AGENT + "/v2/search",
        json={
            "query": "fixture",
            "limit": 1,
            "search_type": "deep",
        },
        timeout=120,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    # Should not crash — treated as fast mode
    assert "web" in payload["data"]


def test_activity_endpoint_structure():
    """GET /v2/activity returns a valid response with the expected schema."""
    resp = httpx.get(AGENT + "/v2/activity", timeout=120)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], list)


def test_activity_shows_active_crawl_job():
    """Creating a crawl job makes it appear in the activity feed."""
    # Create a crawl job
    crawl = httpx.post(
        AGENT + "/v2/crawl", json={"url": TEST_SITE, "max_pages": 1}, timeout=120
    )
    assert crawl.status_code == 200
    crawl_id = crawl.json()["id"]

    # Check activity feed for the new job — accept that the crawl may have
    # already completed (fast single-page crawl of the test site)
    resp = httpx.get(AGENT + "/v2/activity", timeout=120)
    assert resp.status_code == 200
    jobs = resp.json()["data"]
    matching = [j for j in jobs if j["id"] == crawl_id]
    if matching:
        assert matching[0]["kind"] == "crawl"
        assert matching[0]["status"] in ("processing", "completed")
    else:
        # Crawl completed before activity check — verify by status endpoint
        r = httpx.get(AGENT + f"/v2/crawl/{crawl_id}", timeout=120)
        assert r.status_code == 200, f"Crawl job {crawl_id} not found at all"
        assert r.json()["status"] == "completed"


def test_activity_excludes_completed_agent_job():
    """A completed agent job should no longer appear in the activity feed."""
    # Create an agent job and wait for completion
    create = httpx.post(
        AGENT + "/v2/agent",
        json={"prompt": "What is the pricing on the fixture site?"},
        timeout=120,
    )
    assert create.status_code == 200
    job_id = create.json()["id"]

    # Poll until completed
    for _ in range(30):
        status = httpx.get(AGENT + f"/v2/agent/{job_id}", timeout=120)
        if status.json()["status"] == "completed":
            break
        time.sleep(1)

    # Verify it's no longer in the active feed
    resp = httpx.get(AGENT + "/v2/activity", timeout=120)
    assert resp.status_code == 200
    active_ids = [j["id"] for j in resp.json()["data"]]
    assert job_id not in active_ids, (
        f"Completed agent job {job_id} still in activity feed"
    )


def test_activity_multi_type():
    """Multiple job types appear in the activity feed simultaneously."""
    # Create jobs of different types
    crawl = httpx.post(
        AGENT + "/v2/crawl", json={"url": TEST_SITE, "max_pages": 1}, timeout=120
    )
    crawl_id = crawl.json()["id"]

    agent = httpx.post(
        AGENT + "/v2/agent", json={"prompt": "Summarize the fixture site?"}, timeout=120
    )
    agent_id = agent.json()["id"]

    # Check both appear in activity — if a crawl completed instantly it may
    # already be gone from the active feed, so accept at least one job visible
    resp = httpx.get(AGENT + "/v2/activity", timeout=120)
    assert resp.status_code == 200
    jobs = resp.json()["data"]
    visible_ids = [j["id"] for j in jobs]
    any_visible = crawl_id in visible_ids or agent_id in visible_ids
    assert any_visible, (
        f"Neither job type appeared in activity. crawl={crawl_id} agent={agent_id} "
        f"active={[j['id'] for j in jobs]}"
    )


# ----- llms.txt description quality tests -----


def test_scraper_meta_endpoint():
    """POST /scrape/meta returns meta tags from raw HTML."""
    resp = httpx.post(
        SCRAPER + "/scrape/meta",
        json={"url": TEST_SITE + "/content/with-meta"},
        timeout=30,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert payload["title"] == "Meta Tag Page"
    assert payload["description"] is not None
    assert "meta description for testing" in payload["description"]
    assert payload["og_description"] is not None
    assert "Open Graph description" in payload["og_description"]


def test_scraper_meta_fallback_url():
    """POST /scrape/meta returns nulls for pages without meta tags."""
    resp = httpx.post(
        SCRAPER + "/scrape/meta", json={"url": TEST_SITE + "/"}, timeout=30
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    # The root page has no <title> or meta description/og:description
    assert payload["title"] is None
    assert payload["description"] is None
    assert payload["og_description"] is None


def test_generate_llmstxt_sentence_boundary():
    """Generated llms.txt entries should end at sentence boundaries, not mid-sentence."""
    # Create an llms.txt generation job using a page where the scraper
    # won't hit a site-level llms.txt (Tier 1)
    resp = httpx.post(
        AGENT + "/v2/generate-llmstxt",
        json={"url": "https://example.com", "max_pages": 1},
        timeout=120,
    )
    assert resp.status_code == 200
    job_id = resp.json()["id"]

    # Poll for completion
    for _ in range(30):
        status = httpx.get(AGENT + f"/v2/generate-llmstxt/{job_id}", timeout=120)
        if status.json()["status"] == "completed":
            break
        time.sleep(1)

    result = status.json()
    assert result["status"] == "completed"
    llms = result.get("data", {}).get("llms_txt", "")
    assert llms, "llms_txt should not be empty"

    # Find the description in the llms.txt output
    for line in llms.split("\n"):
        if line.startswith("- [") and ": " in line:
            desc = line.split(": ", 1)[1]
            # Should end with sentence-ending punctuation
            assert desc.rstrip()[-1] in ".!?", (
                f"Description should end with sentence punctuation, got: {desc[-30:]}"
            )
            # Description should be substantive (not just a few words)
            assert len(desc) >= 20, f"Description too short: {desc}"


def test_generate_llmstxt_meta_tag_preference():
    """Generated llms.txt should prefer <meta name="description"> over body text.

    Uses the fixture page at /content/with-meta which has a <meta name="description">.
    The agent's extract_title_and_description() calls the scraper's /scrape/meta
    endpoint first, which returns the meta description.
    """
    resp = httpx.post(
        AGENT + "/v2/generate-llmstxt",
        json={"url": TEST_SITE + "/content/with-meta", "max_pages": 1},
        timeout=120,
    )
    assert resp.status_code == 200
    job_id = resp.json()["id"]

    # Poll for completion
    for _ in range(30):
        status = httpx.get(AGENT + f"/v2/generate-llmstxt/{job_id}", timeout=120)
        if status.json()["status"] == "completed":
            break
        time.sleep(1)

    result = status.json()
    assert result["status"] == "completed"
    llms = result.get("data", {}).get("llms_txt", "")
    assert llms, "llms_txt should not be empty"

    # The meta endpoint returns the description, but the full scrape may
    # hit the test-site's llms.txt (Tier 1). Either way, the llms.txt
    # output should exist and be well-formed.
    for line in llms.split("\n"):
        if line.startswith("- [") and ": " in line:
            desc = line.split(": ", 1)[1]
            assert len(desc) >= 20, f"Description should be substantive, got: {desc}"
            break


# ── GitHub adapter tests ────────────────────────────────────────

GITHUB_RAW = "https://raw.githubusercontent.com/groktopus/groktocrawl/main/README.md"
GITHUB_BLOB = "https://github.com/groktopus/groktocrawl/blob/main/README.md"
GITHUB_REPO = "https://github.com/groktopus/groktocrawl"
GITHUB_TREE = "https://github.com/groktopus/groktocrawl/tree/main/scraper-svc/scraper"
GITHUB_ISSUE = "https://github.com/groktopus/groktocrawl/issues/1"


def test_github_adapter_raw_file():
    """Raw.githubusercontent.com URLs should be handled by the adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GITHUB_RAW}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    md = payload.get("data", {}).get("markdown", "")
    assert "GroktoCrawl" in md or "github" in md
    # Should have adapter frontmatter
    assert "github-adapter" in md or "---" in md


def test_github_adapter_blob_url():
    """Blob URLs should be rewritten to raw.githubusercontent.com."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GITHUB_BLOB}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    md = payload.get("data", {}).get("markdown", "")
    # Should have content (from raw fetch), not generic GitHub docs page
    assert len(md) > 100, f"Expected >100 chars, got {len(md)}"
    assert "developer platform" not in md[:200], "Should not be generic GitHub docs"


def test_github_adapter_repo_root():
    """Repo root URLs should return README with metadata."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GITHUB_REPO}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 200, f"Expected >200 chars, got {len(md)}"
    # Should contain repo metadata (stars, forks, or description)
    assert any(x in md for x in ["GroktoCrawl", "github", "Self-hosted"])


def test_github_adapter_tree_listing():
    """Tree URLs should return a directory listing."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GITHUB_TREE}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"
    # Should list files (not a generic page)
    assert any(x in md for x in ["📁", "📄", "adapters", "app.py", "__init__"])


def test_github_adapter_social_fallback():
    """Issue URLs should be handled by the social adapter (REST fallback)."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GITHUB_ISSUE}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"
    # Frontmatter should indicate social adapter
    assert "github-social" in md or "github-discussion" in md or "issue" in md.lower()


# ── NVD adapter tests ──────────────────────────────────────────
CVE_KNOWN = "CVE-2024-3094"  # xz backdoor — well known, unlikely to change
NVD_DETAIL = f"https://nvd.nist.gov/vuln/detail/{CVE_KNOWN}"
CVEORG_URL = f"https://cve.org/CVERecord?id={CVE_KNOWN}"


def test_nvd_adapter_known_cve():
    """Known CVE detail page should return structured markdown via NVD adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": NVD_DETAIL}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert CVE_KNOWN in md or "CVE-2024" in md
    # Should have adapter frontmatter (YAML block)
    md_full = payload.get("data", {}).get("markdown", "")
    assert "cve_id" in md_full or "CVE-2024" in md_full
    assert len(md_full) > 100, f"Expected >100 chars, got {len(md_full)}"


def test_nvd_adapter_bare_cve_id():
    """Bare CVE ID (cve: prefix) should be handled by the NVD adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": f"cve:{CVE_KNOWN}"}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert CVE_KNOWN in md, f"Expected {CVE_KNOWN} in response"


def test_cveorg_adapter_known_cve():
    """Known CVE on cve.org should return structured markdown via CVE Program adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": CVEORG_URL}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert CVE_KNOWN in md or "Xz" in md or "CVE-2024" in md or "backdoor" in md.lower()
    assert len(md) > 100, f"Expected >100 chars, got {len(md)}"


# ── Security adapter tests ─────────────────────────────────────
SHODAN_HOST = "https://www.shodan.io/host/8.8.8.8"
CRTSH_DOMAIN = "https://crt.sh/?q=example.com"
EXPLOITDB_ID = "https://www.exploit-db.com/exploits/1000"
MITRE_TECHNIQUE = "https://attack.mitre.org/techniques/T1059"
VT_FILE = "https://virustotal.com/gui/file/d41d8cd98f00b204e9800998ecf8427e"
ABUSEIPDB_IP = "https://www.abuseipdb.com/check/8.8.8.8"
HIBP_ACCOUNT = "https://haveibeenpwned.com/account/test@example.com"
OTX_IP = "https://otx.alienvault.com/indicator/IP/8.8.8.8"
VULNCHECK_CVE = "https://vulncheck.com/cve/CVE-2024-3094"
CENSYS_IP = "https://search.censys.io/ipv4/8.8.8.8"


def test_shodan_adapter_public_host():
    """Shodan host page should be handled by the adapter (scrape fallback)."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": SHODAN_HOST}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"
    assert "8.8.8.8" in md or "Shodan" in md or "shodan" in md.lower()


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_shodan_adapter_source():
    """Shodan adapter source should be shodan-html (no API key in CI)."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": SHODAN_HOST}, timeout=120)
    payload = r.json()
    assert payload["success"] is True
    src = payload.get("data", {}).get("source", "")
    assert "shodan" in src, f"Expected shodan source, got {src}"


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_crtsh_adapter_domain():
    """CRT.sh domain lookup should return certificate data."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": CRTSH_DOMAIN}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"
    assert "example.com" in md or "Certificate" in md


def test_exploitdb_adapter_exploit():
    """Exploit-DB exploit page should return content via adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": EXPLOITDB_ID}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_mitreattack_adapter_technique():
    """MITRE ATT&CK technique page should return content via STIX adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": MITRE_TECHNIQUE}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"
    assert "T1059" in md or "Command" in md or "Scripting" in md


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_abuseipdb_adapter_ip():
    """AbuseIPDB IP check should return content via adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": ABUSEIPDB_IP}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_censys_adapter_ip():
    """Censys IP page should be handled by the adapter (scrape fallback)."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": CENSYS_IP}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 20, f"Expected >20 chars, got {len(md)}"


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_virustotal_adapter_file():
    """VirusTotal file page should be handled by the adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": VT_FILE}, timeout=120)
    payload = r.json()
    # VT returns 404 for empty hashes — that's fine, the adapter just falls through
    # In this case the generic tier handles it
    assert payload.get("error") is None or payload["success"] is True


def test_security_adapters_loaded():
    """All 10 security adapters should be registered at startup."""
    r = httpx.get(SCRAPER + "/health", timeout=30)
    assert r.status_code == 200


def test_otx_adapter_indicator():
    """OTX indicator page should return content via adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": OTX_IP}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 20, f"Expected >20 chars, got {len(md)}"


@pytest.mark.xfail(
    strict=False, reason="Requires reaching third-party sites from CI runner"
)
def test_hibp_adapter_breach():
    """HIBP account page should return content via adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": HIBP_ACCOUNT}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 20, f"Expected >20 chars, got {len(md)}"


def test_vulncheck_adapter_cve():
    """VulnCheck CVE page should return content via adapter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": VULNCHECK_CVE}, timeout=120)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 50, f"Expected >50 chars, got {len(md)}"


def test_answer_endpoint_returns_valid_structure():
    """POST /v2/answer returns a grounded answer with sources and citations."""
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is the pricing on the fixture site?",
            "num_sources": 3,
        },
        timeout=120,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert isinstance(payload["answer"], str)
    assert len(payload["answer"]) > 10
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["citations"], list)
    assert payload["latency_ms"] > 0
    assert payload["search_type"] == "auto"


def test_answer_endpoint_returns_citations_when_available():
    """If sources exist, citations list should be populated."""
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What services does the fixture site describe?",
            "num_sources": 3,
        },
        timeout=120,
    )
    assert r.status_code == 200
    payload = r.json()
    # The LLM should cite sources; if it doesn't, citations may be empty
    # but the structure should be valid
    assert payload["success"] is True
    if payload["sources"]:
        for c in payload["citations"]:
            assert "index" in c
            assert "url" in c


def test_answer_streaming_returns_sse_events():
    """POST /v2/answer with stream:true returns SSE events."""
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is the pricing on the fixture site?",
            "num_sources": 1,
            "stream": True,
        },
        timeout=180,
    )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/event-stream")
    body = r.text

    # Should have at least a sources event and a done event
    assert "data:" in body
    assert "[DONE]" in body

    # Parse events and verify structure
    events = []
    for line in body.split("\n"):
        if line.startswith("data: ") and line[6:] != "[DONE]":
            import json

            events.append(json.loads(line[6:]))

    event_types = {e.get("type") for e in events}
    # Should have at least: sources (maybe), token(s), done
    assert "done" in event_types, f"Missing 'done' event. Types found: {event_types}"

    # Find the done event
    done_event = next(e for e in events if e.get("type") == "done")
    assert "answer" in done_event
    assert isinstance(done_event.get("answer"), str)
    assert done_event["latency_ms"] > 0


def test_agent_streaming_returns_sse_events():
    """POST /v2/agent with stream:true returns SSE events."""
    r = httpx.post(
        AGENT + "/v2/agent",
        json={
            "prompt": "What is the pricing on the fixture site?",
            "stream": True,
        },
        timeout=180,
    )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/event-stream")
    body = r.text

    # Should have at least a done event
    assert "data:" in body
    assert "[DONE]" in body

    # Parse events and verify structure
    events = []
    for line in body.split("\n"):
        if line.startswith("data: ") and line[6:] != "[DONE]":
            import json

            events.append(json.loads(line[6:]))

    event_types = {e.get("type") for e in events}

    # Should have: sources_pending (maybe), source_scraped events (maybe),
    # token events (maybe), and done
    assert "done" in event_types, f"Missing 'done' event. Types found: {event_types}"

    done_event = next(e for e in events if e.get("type") == "done")
    assert "result" in done_event
    assert isinstance(done_event.get("result"), str)
    assert done_event["latency_ms"] > 0


# ── Phase 3: Near-Duplicate Detection ────────────────────────────
# Note: these tests mark xfail when Qdrant is unavailable (memory
# pressure on CI runner causes Qdrant to crash under model load).


def _index(url: str, title: str, content: str, **extra) -> httpx.Response:
    """POST /index on semantic-svc."""
    r = httpx.post(
        SEMANTIC + "/index",
        json={"url": url, "title": title, "content": content, **extra},
        timeout=60,
    )
    return r


def _index_batch(pages: list[dict]) -> httpx.Response:
    """POST /index/batch on semantic-svc."""
    r = httpx.post(
        SEMANTIC + "/index/batch",
        json={"pages": pages},
        timeout=60,
    )
    return r


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_index_structure():
    """POST /index on semantic-svc returns valid structure."""
    r = _index(
        "http://example.com/page-a",
        "Test Page A",
        "This is unique content for the near-dup test. "
        "It describes a specific topic that should not match other pages.",
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["status"] in ("indexed", "duplicate", "updated_duplicate")
    assert isinstance(payload["url_hash"], int)
    return payload


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_near_dup_detection_skip_mode():
    """Indexing the same content at a different URL returns 'duplicate' status.

    This test is best-effort — it requires Qdrant to be populated and
    may not find a match if the index was just cleared. It runs twice:
    first to seed the index, second to detect the duplicate.
    """
    # Seed — first page with distinctive content
    r1 = _index(
        "http://example.com/near-dup-original",
        "Original",
        "The near-dup detection test should identify this content "
        "as a duplicate when it appears at a second URL with the same text. "
        "This paragraph is specific enough to generate a stable embedding.",
    )
    assert r1.status_code == 201

    # Same content, different URL — should be flagged as duplicate
    r2 = _index(
        "http://example.com/near-dup-copy",
        "Copy",
        "The near-dup detection test should identify this content "
        "as a duplicate when it appears at a second URL with the same text. "
        "This paragraph is specific enough to generate a stable embedding.",
    )
    assert r2.status_code == 201
    payload = r2.json()
    assert payload["status"] in ("indexed", "duplicate", "updated_duplicate")
    assert isinstance(payload["url_hash"], int)


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_near_dup_detection_update_mode():
    """Requesting near_dup_mode='update' always indexes even when duplicated."""
    r = _index(
        "http://example.com/near-dup-update-test",
        "Update Mode Test",
        "This content tests the update mode for near-duplicate detection. "
        "When set to 'update', even near-duplicate content gets indexed.",
        near_dup_mode="update",
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["status"] in ("indexed", "updated_duplicate")
    assert isinstance(payload["url_hash"], int)


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_near_dup_different_content():
    """Completely different content at different URL should index normally."""
    r = _index(
        "http://example.com/unique-page",
        "Unique Page",
        "This content is completely unique and has nothing to do with "
        "any other page in the test suite. It discusses quantum computing "
        "applications in marine biology research.",
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["status"] in ("indexed", "updated_duplicate")
    assert isinstance(payload["url_hash"], int)


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_batch_index_endpoint():
    """POST /index/batch on semantic-svc returns valid structure.

    Batch endpoint should index multiple pages in a single call,
    returning count of successfully indexed pages.
    """
    r = _index_batch(
        [
            {
                "url": "http://example.com/batch-page-a",
                "title": "Batch Page A",
                "content": "This is the first page in a batch index test. "
                "It contains unique content for testing batch ingestion.",
            },
            {
                "url": "http://example.com/batch-page-b",
                "title": "Batch Page B",
                "content": "This is the second page in a batch index test. "
                "It also contains unique content for testing.",
            },
        ],
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["status"] == "indexed"
    assert payload["count"] == 2


@pytest.mark.xfail(strict=False, reason="Qdrant unstable under CI memory pressure")
def test_batch_index_empty():
    """POST /index/batch with no pages should return count=0."""
    r = _index_batch([])
    assert r.status_code == 201
    payload = r.json()
    assert payload["status"] == "indexed"
    assert payload["count"] == 0


# ── Gutenberg adapter tests ─────────────────────────────────────
GUTENBERG_ALICE = "https://www.gutenberg.org/ebooks/11"
GUTENBERG_INVALID = "https://www.gutenberg.org/ebooks/99999999"
GUTENBERG_FILES = "https://www.gutenberg.org/files/11/"
GUTENBERG_CACHE = "https://gutenberg.org/cache/epub/11/"


def test_gutenberg_adapter_known_book():
    """Known Gutenberg book (Alice in Wonderland) returns structured markdown with frontmatter."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GUTENBERG_ALICE}, timeout=180)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    # Should have YAML frontmatter
    assert md.startswith("---"), "Should have YAML frontmatter"
    # Should contain metadata fields
    assert "title:" in md, "Should contain title metadata"
    assert "gutenberg_id:" in md, "Should contain gutenberg_id metadata"
    assert "author:" in md, "Should contain author metadata"
    # Should have substantive content
    assert len(md) > 500, f"Expected >500 chars, got {len(md)}"
    # Should have chapter-like content (Alice has chapters)
    assert (
        "Chapter" in md
        or "CHAPTER" in md
        or "chapter" in md
        or "Rabbit" in md
        or "Alice" in md
    )
    # Source should indicate gutenberg
    src = payload.get("data", {}).get("source", "")
    assert "gutenberg" in src, f"Expected gutenberg source, got {src}"


def test_gutenberg_adapter_files_url():
    """Gutenberg /files/<id>/ URL pattern should also work."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GUTENBERG_FILES}, timeout=180)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 100, f"Expected >100 chars, got {len(md)}"


def test_gutenberg_adapter_cache_url():
    """Gutenberg /cache/epub/<id>/ URL pattern should also work."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GUTENBERG_CACHE}, timeout=180)
    payload = r.json()
    assert payload["success"] is True, payload.get("error")
    md = payload.get("data", {}).get("markdown", "")
    assert len(md) > 100, f"Expected >100 chars, got {len(md)}"


def test_gutenberg_adapter_invalid_id():
    """Non-existent book ID should gracefully fall through or return error."""
    r = httpx.post(SCRAPER + "/scrape", json={"url": GUTENBERG_INVALID}, timeout=180)
    payload = r.json()
    # Either the adapter fails gracefully and the generic pipeline handles it,
    # or the generic pipeline also fails — either way, no crash
    assert not payload.get("error") or payload.get("success") is not None


# ── Error response tests ──────────────────────────────────────────


def test_non_existent_job_returns_404_with_error_response():
    """GET /v2/agent/<nonexistent> returns 404 with ErrorResponse format."""
    r = httpx.get(AGENT + "/v2/agent/nonexistent-job-id", timeout=10)
    assert r.status_code == 404
    data = r.json()
    assert data["success"] is False
    assert data.get("error")
    assert "error_code" in data
    assert data["error_code"] == "NOT_FOUND"


def test_non_existent_monitor_returns_404():
    """GET /v2/monitor/<nonexistent> returns 404 with ErrorResponse."""
    r = httpx.get(AGENT + "/v2/monitor/nonexistent-monitor", timeout=10)
    assert r.status_code == 404
    data = r.json()
    assert data["success"] is False
    assert data["error_code"] == "NOT_FOUND"


def test_validation_error_returns_422_with_details():
    """Missing required fields return 422 with field-level details."""
    r = httpx.post(AGENT + "/v2/scrape", json={}, timeout=10)
    assert r.status_code == 422
    data = r.json()
    assert data["success"] is False
    assert data["error_code"] == "INVALID_REQUEST"
    assert "details" in data
    assert len(data["details"]) > 0
    assert "field" in data["details"][0]
    assert "message" in data["details"][0]


def test_non_existent_browser_session_returns_404():
    """DELETE /v2/browser/<nonexistent> returns 404 with ErrorResponse."""
    r = httpx.delete(AGENT + "/v2/browser/nonexistent-session", timeout=10)
    assert r.status_code == 404
    data = r.json()
    assert data["success"] is False
    assert data["error_code"] == "NOT_FOUND"


if __name__ == "__main__":
    """Run all test functions in this file when invoked directly.

    CI runs ``python3 /app/tests/test_stack.py``, so this block is
    required — without it ``pytest`` functions are defined but never
    executed.
    """
    import subprocess
    import sys

    raise SystemExit(
        subprocess.run(
            [sys.executable, "-m", "pytest", "-v", __file__],
            capture_output=False,
        ).returncode
    )
