"""Regression tests for card-grid extraction yield recovery (issue #587).

The open-neuromorphic.org hardware directory wraps every card's ``<h3>``/
``<p>`` inside its parent ``<a>`` and places the card grid in a SIBLING of
the intro container. readability-lxml scores nothing in the grid (link
density 1.0) and silently drops it, returning intro-only output (~766 chars
from >10KB of HTML). These tests pin the fix:

- Primary: low-yield detection recovers the full body via an uncapped
  full-page conversion (output must exceed the 10,000-char structural cap).
- Secondary: anchor-unwrap preprocessing preserves card hrefs as markdown
  links on the card titles.
- Tertiary: a volume-comparison quality check surfaces a low-yield warning
  on below-floor extractions and stays silent on recovered pages.
- Shared consumers (adapters helper, ``filter_sections``, Tier wiring) all
  benefit from the single shared recovery path.

The deterministic fixture under ``tests/fixtures/html/`` is modeled on a
saved snapshot of the live reproduction URL (same topology: intro container
plus sibling anchor-wrapped card grid, 25 cards, >10KB HTML). The raw
snapshot itself is committed alongside it as ``hardware-page-live.html``
(captured 2026-08-22, sole reference used by the milestone-3 validator) so
mechanical drift between the modeled fixture and the real page can be
checked without re-fetching.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRAPER_SVC = Path(__file__).resolve().parents[2] / "scraper-svc"
if str(SCRAPER_SVC) not in sys.path:
    sys.path.insert(0, str(SCRAPER_SVC))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
FIXTURE = FIXTURES / "card_grid_anchor_wrapped.html"
LIVE_SNAPSHOT = FIXTURES / "hardware-page-live.html"

# The 25 card titles/hrefs encoded in the deterministic fixture.
CARD_HREFS = [
    ("/neuromorphic-computing/hardware/ada-neucom/", "ADA - Neucom"),
    (
        "/neuromorphic-computing/hardware/tsp1-time-series-processor-applied-brain-research/",
        "TSP1 - Applied Brain Research",
    ),
    ("/neuromorphic-computing/hardware/texel/", "TEXEL - University of Groningen"),
    ("/neuromorphic-computing/hardware/pulsar-by-innatera/", "Pulsar - Innatera"),
    (
        "/neuromorphic-computing/hardware/snp-by-innatera/",
        "Spiking Neural Processor T1 by Innatera",
    ),
    (
        "/neuromorphic-computing/hardware/dynap-se2-institute-of-neuroinformatics/",
        "DYNAP-SE2 - Institute of Neuroinformatics",
    ),
    ("/neuromorphic-computing/hardware/northpole-ibm/", "NorthPole - IBM"),
    ("/neuromorphic-computing/hardware/speck-synsense/", "Speck - SynSense"),
    ("/neuromorphic-computing/hardware/akida-brainchip/", "Akida - BrainChip"),
    ("/neuromorphic-computing/hardware/seneca-imec/", "SENeCA by imec"),
    (
        "/neuromorphic-computing/hardware/brainscales-2-universitat-heidelberg/",
        "BrainScaleS-2 - Heidelberg University",
    ),
    ("/neuromorphic-computing/hardware/reckon-frenkel/", "ReckOn - Charlotte Frenkel"),
    ("/neuromorphic-computing/hardware/xylo-synsense/", "Xylo - SynSense"),
    ("/neuromorphic-computing/hardware/loihi-2-intel/", "Loihi 2 - Intel"),
    (
        "/neuromorphic-computing/hardware/spinnaker-2-university-of-dresden/",
        "SpiNNaker 2 - University of Dresden",
    ),
    (
        "/neuromorphic-computing/hardware/tianjic-tsinghua-university/",
        "Tianjic - Tsinghua University",
    ),
    ("/neuromorphic-computing/hardware/dynap-cnn-synsense/", "DynapCNN - SynSense"),
    ("/neuromorphic-computing/hardware/odin-frenkel/", "Odin by Charlotte Frenkel"),
    ("/neuromorphic-computing/hardware/loihi-intel/", "Loihi - Intel"),
    (
        "/neuromorphic-computing/hardware/brainscales-1-universitat-heidelberg/",
        "BrainScaleS-1 - Heidelberg University",
    ),
    ("/neuromorphic-computing/hardware/rolls-ini/", "ROLLS - INI"),
    ("/neuromorphic-computing/hardware/truenorth-ibm/", "TrueNorth - IBM"),
    (
        "/neuromorphic-computing/hardware/neurogrid-braindrop-stanford/",
        "NeuroGrid (BrainDrop) - Stanford",
    ),
    (
        "/neuromorphic-computing/hardware/spikey-universitat-heidelberg/",
        "Spikey - Heidelberg University",
    ),
    (
        "/neuromorphic-computing/hardware/sakura-example-institute/",
        "Sakura - Example Institute",
    ),
]

SENTINEL = "The directory index was last reconciled by the maintainers guild."


@pytest.fixture(scope="module")
def card_grid_html() -> str:
    return FIXTURE.read_text()


# ── Fixture sanity ───────────────────────────────────────────────


def test_card_grid_fixture_replicates_bug_topology(card_grid_html):
    """Fixture mirrors the live bug: >10KB, anchor-wrapped cards, sibling grid."""
    from bs4 import BeautifulSoup

    assert FIXTURE.stat().st_size > 10240
    soup = BeautifulSoup(card_grid_html, "html.parser")
    cards = soup.select("a.hardware-card-wide-link")
    assert len(cards) == 25
    # Every card's h3 lives INSIDE its parent anchor (link density trap).
    wrapped = sum(1 for a in cards if a.find("h3") is not None)
    assert wrapped == 25
    # Intro container and the card-grid tree are siblings under a common
    # parent (climb from the card anchor to the OUTERMOST enclosing list).
    prose = soup.select_one("div.prose")
    top_list = cards[0].find_parent("ul")
    while top_list.parent.name in ("li", "ul"):
        top_list = top_list.parent
    assert prose is not None and top_list is not None
    assert prose.parent is top_list.parent


def test_html_to_markdown_preserves_all_rendered_quote_records():
    """Readability must not silently drop middle records from a rendered list."""
    from scraper.fetch_quality import _add_quality, html_to_markdown

    records = [
        (
            "The world as we have created it is a process of our thinking. It "
            "cannot be changed without changing our thinking.",
            "Albert Einstein",
            "change deep-thoughts thinking world",
        ),
        (
            "It is our choices, Harry, that show what we truly are, far more than "
            "our abilities.",
            "J.K. Rowling",
            "abilities choices",
        ),
        (
            "There are only two ways to live your life. One is as though nothing "
            "is a miracle. The other is as though everything is a miracle.",
            "Albert Einstein",
            "inspirational life live miracle miracles",
        ),
        (
            "The person, be it gentleman or lady, who has not pleasure in a good "
            "novel, must be intolerably stupid.",
            "Jane Austen",
            "aliteracy books classic humor",
        ),
        (
            "Imperfection is beauty, madness is genius and it's better to be "
            "absolutely ridiculous than absolutely boring.",
            "Marilyn Monroe",
            "be-yourself inspirational",
        ),
        (
            "Try not to become a man of success. Rather become a man of value.",
            "Albert Einstein",
            "adulthood success value",
        ),
        (
            "It is better to be hated for what you are than to be loved for what "
            "you are not.",
            "André Gide",
            "life love",
        ),
        (
            "I have not failed. I've just found 10,000 ways that won't work.",
            "Thomas A. Edison",
            "edison failure inspirational paraphrased",
        ),
        (
            "A woman is like a tea bag; you never know how strong it is until "
            "it's in hot water.",
            "Eleanor Roosevelt",
            "misattributed-eleanor-roosevelt",
        ),
        (
            "A day without sunshine is like, you know, night.",
            "Steve Martin",
            "humor obvious simile",
        ),
    ]
    cards = "".join(
        f'<div class="quote"><span class="text">“{quote}”</span>'
        f'<span>by <small class="author">{author}</small></span>'
        '<div class="tags">Tags: '
        + " ".join(f'<a class="tag">{tag}</a>' for tag in tags.split())
        + "</div></div>"
        for quote, author, tags in records
    )
    html = (
        "<html><head><title>Quotes to Scrape</title></head><body>"
        '<div class="container"><div class="row header-box">'
        "<h1>Quotes to Scrape</h1></div>"
        f'{cards}<nav><a href="/js/page/2/">Next</a></nav></div>'
        '<footer class="footer">Made with love</footer></body></html>'
    )

    markdown = html_to_markdown(html)
    result = _add_quality({"markdown": markdown, "raw_html_start": html})

    missing = [author for _quote, author, _tags in records if author not in markdown]
    assert not missing, f"readability dropped rendered quote records: {missing}"
    assert "Next" not in markdown
    assert "Made with love" not in markdown
    assert result["quality"]["checks"]["completeness"] == "pass"

    incomplete = "\n\n".join(
        f"“{quote}”\nby {author}\n\nTags: {tags}"
        for quote, author, tags in records
        if author not in {"Thomas A. Edison", "Eleanor Roosevelt"}
    )
    incomplete_result = _add_quality({"markdown": incomplete, "raw_html_start": html})
    assert incomplete_result["quality"]["checks"]["completeness"] == "fail"


def test_repeated_boilerplate_in_navigation_does_not_trigger_content_recovery(
    monkeypatch,
):
    """Repeated navigation links are chrome, not evidence of dropped records."""
    import scraper.fetch_quality as fetch_quality

    nav = "".join(
        f'<li class="nav-item"><a href="/{index}">Navigation item {index}</a></li>'
        for index in range(8)
    )
    ads = "".join(
        f'<div class="ad-card">Advertisement {index} offers a sponsored product '
        "with promotional details that are unrelated to the article content.</div>"
        for index in range(6)
    )
    html = (
        f"<html><body><nav><ul>{nav}</ul></nav>"
        f'<div class="ad-grid">{ads}</div>'
        "<main><article><h1>Useful article</h1>"
        "<p>This article has enough real text to remain the selected content. "
        "It explains a useful subject in several clear sentences.</p>"
        "<p>A second paragraph adds context and detail to the same subject.</p>"
        "</article></main></body></html>"
    )

    def unexpected_recovery(_html):
        raise AssertionError("navigation links must not trigger full-page recovery")

    monkeypatch.setattr(fetch_quality, "_full_page_markdown", unexpected_recovery)
    fetch_quality.html_to_markdown(html)


# ── VAL-YIELD-003: card-grid body survives html_to_markdown ──────


def test_html_to_markdown_recovers_card_grid_body(card_grid_html):
    """Output far exceeds the intro-only regime and exceeds the 10K cap."""
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    # Must beat BOTH the intro-only regime (~766 chars) and the structural
    # extractor's hard 10,000-char cap.
    assert len(markdown) >= 10240
    titles_present = sum(1 for _, title in CARD_HREFS if title in markdown)
    assert titles_present >= 20, f"only {titles_present}/25 card titles retained"


def test_html_to_markdown_card_grid_contains_directory_terms(card_grid_html):
    """The contract tokens (Loihi, Akida, SpiNNaker) appear in recovered output."""
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    assert "Loihi" in markdown
    assert "Akida" in markdown
    assert "SpiNNaker" in markdown


def test_html_to_markdown_card_grid_intro_only_regime_gone(card_grid_html):
    """Pre-fix, exactly the intro paragraphs came back and nothing else."""
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    # Card descriptions live only in the grid; their presence proves the
    # sibling block was extracted rather than dropped.
    assert "Event-based neural network processor" in markdown
    assert "Wafer-scale analog system" in markdown


# ── VAL-YIELD-011: href preservation on unwrapped card anchors ───


def test_html_to_markdown_preserves_card_hrefs_as_title_links(card_grid_html):
    """Unwrapped anchors keep their href as [Title](href) on the title."""
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    linked = sum(
        1
        for href, title in CARD_HREFS
        if f"[{title}]({href})" in markdown.replace("\n", "")
    )
    assert linked >= 20, f"only {linked}/25 card hrefs preserved as title links"


# ── VAL-YIELD-004: sibling-topology sentinel survives ────────────


def test_sibling_block_sentinel_survives_recovery(card_grid_html):
    """Text living ONLY in the sibling card-grid tree must survive."""
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    assert SENTINEL in markdown


# ── VAL-YIELD-005: no regression on normal articles ──────────────


def test_html_to_markdown_article_no_regression():
    """A representative article keeps its full body through the fix."""
    from scraper.fetch_quality import html_to_markdown

    mid_sentence = (
        "Deep in the middle of the article the author explains the tradeoffs "
        "between analog cores and digital meshes with unusual candor."
    )
    closing = "The closing section ties the survey together with deployment guidance."
    html = f"""<html><head><title>Neuromorphic Survey</title></head>
    <body>
    <header>Site Header Boilerplate</header>
    <nav><a href="/">Home</a><a href="/archive">Archive</a></nav>
    <article>
      <h1>A Survey of Neuromorphic Processors</h1>
      <p>The survey opens with a history of spiking hardware from the late
      eighties onward, setting context for the comparisons that follow.</p>
      <p>{mid_sentence}</p>
      <p>Later sections benchmark throughput per watt across three generations
      of devices, with methodology notes appended for reproducibility.</p>
      <p>{closing}</p>
    </article>
    <footer>Copyright Boilerplate</footer>
    </body></html>"""

    markdown = html_to_markdown(html)
    assert mid_sentence in markdown
    assert closing in markdown
    # Chrome filtering still applies on the normal readability path.
    assert "Site Header Boilerplate" not in markdown
    assert "Copyright Boilerplate" not in markdown
    assert "[Home](/)" not in markdown


# ── VAL-YIELD-009: SPA shell / tiny input degradation parity ─────


def test_html_to_markdown_large_spa_shell_still_degrades_small():
    """Huge script-only shells still degrade gracefully (no fabricated body)."""
    from scraper.fetch_quality import html_to_markdown

    filler = "".join(f"console.log('bundle chunk {i} payload');\n" for i in range(400))
    html = (
        "<html><head><title>Single Page App</title>"
        '<meta name="description" content="Client-rendered dashboard.">'
        f"<script>{filler}</script></head>"
        "<body><div id='root'></div><footer>Legal</footer></body></html>"
    )
    assert len(html) > 10240  # source is large; recovered text must not be
    markdown = html_to_markdown(html)
    assert len(markdown) < 2000  # no fabricated content from scripts
    assert "Single Page App" in markdown
    assert "console.log" not in markdown


def test_html_to_markdown_tiny_input_unchanged():
    """Tiny pages keep today's exact structural-fallback behavior."""
    from scraper.fetch_quality import html_to_markdown

    html = "<html><head><title>Page</title></head><body><p>Hello</p></body></html>"
    markdown = html_to_markdown(html)
    assert 0 < len(markdown) < 200
    assert "Page" in markdown or "Hello" in markdown


# ── VAL-YIELD-007/010: low-yield diagnostic pairing ──────────────


def test_assess_quality_volume_check_flags_below_floor_extraction():
    """Markdown far below the sanity floor on a large source trips the check."""
    from scraper.extract import assess_quality

    thin_markdown = "# Intro\n\n" + "Short intro paragraph only. " * 15  # ~450 chars
    result = assess_quality(thin_markdown, html_size=94978)
    assert result["checks"]["volume"] == "fail"
    assert "volume:fail" in result["detail"]


def test_assess_quality_volume_check_passes_on_recovered_page(card_grid_html):
    """Recovered yield is above the floor — the diagnostic must not fire."""
    from scraper.extract import assess_quality
    from scraper.fetch_quality import html_to_markdown

    markdown = html_to_markdown(card_grid_html)
    result = assess_quality(markdown, html_size=len(card_grid_html))
    assert result["checks"]["volume"] == "pass"


def test_assess_quality_volume_check_skipped_without_source_size():
    """No source-size information -> check passes without opinion."""
    from scraper.extract import assess_quality

    result = assess_quality("Some modest content here.", html_size=0)
    assert result["checks"]["volume"] == "pass"
    result = assess_quality("Some modest content here.")
    assert result["checks"]["volume"] == "pass"


def test_assess_quality_volume_check_skipped_for_small_sources():
    """Sources below the minimum size gate never trip the ratio check."""
    from scraper.extract import assess_quality

    result = assess_quality("Tiny page, tiny source.", html_size=900)
    assert result["checks"]["volume"] == "pass"


def test_add_quality_sets_warning_for_below_floor_result():
    """Below-floor extraction surfaces the warning on the result dict."""
    from scraper.fetch_quality import _add_quality

    result = {
        "markdown": "# Intro\n\n" + "Short intro paragraph only. " * 15,
        "url": "https://example.test/hardware",
        "source_html_size": 94978,
    }
    enriched = _add_quality(result)
    assert enriched["quality"]["checks"]["volume"] == "fail"
    assert enriched.get("warning")
    assert "low yield" in enriched["warning"].lower()


def test_add_quality_no_warning_for_recovered_result(card_grid_html):
    """Recovered pages present as clean successes — no warning field."""
    from scraper.fetch_quality import _add_quality, html_to_markdown

    result = {
        "markdown": html_to_markdown(card_grid_html),
        "url": "https://example.test/hardware",
        "source_html_size": len(card_grid_html),
    }
    enriched = _add_quality(result)
    assert enriched["quality"]["checks"]["volume"] == "pass"
    assert not enriched.get("warning")


@pytest.mark.asyncio
async def test_tier2_fallback_carries_int_source_html_size():
    """Tier 2 HTML-fallback results expose their source size as a native int.

    The quality-assessment volume gate consumes ``source_html_size``
    arithmetically, so the tier must store the byte count as ``int`` (not a
    string needing a compensating cast downstream).
    """
    from scraper import fetch_tiers

    html = (
        "<html><head><title>Tier 2 Probe</title></head><body>"
        "<p>Static HTML fallback body long enough to exceed the hundred "
        "character minimum gate for the content-negotiation conversion.</p>"
        "</body></html>"
    )

    class _Resp:
        status_code = 200
        text = html
        content = html.encode()

        headers = {"content-type": "text/html; charset=utf-8"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __exit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return _Resp()

    result = await fetch_tiers.fetch_via_content_negotiation(
        "https://example.test/page", _Client()
    )
    assert result is not None
    assert result["source"] == "content-negotiation"
    assert isinstance(result["source_html_size"], int), (
        f"expected int source_html_size, got {type(result['source_html_size'])}"
    )
    assert result["source_html_size"] == len(html)


def test_add_quality_consumes_source_html_size_natively():
    """_add_quality must read ``source_html_size`` as-is (no int() re-cast).

    With tiers storing the size natively as int, a compensating ``int()``
    cast in the consumer would be dead weight masking contract drift.
    """
    import inspect

    from scraper import fetch_quality

    source = inspect.getsource(fetch_quality._add_quality)
    assert "int(" not in source, (
        "_add_quality should consume source_html_size natively instead of "
        "re-casting with int()"
    )


def test_add_quality_presence_check_is_explicit_none_guard():
    """The size-presence check must be an explicit ``is not None``.

    A truthiness guard (``if result.get(...)``) silently discards the
    legitimate ``source_html_size: 0`` sentinel and reads as accidental
    rather than intentional; the explicit None check documents that only
    absence falls back to measuring caller-provided HTML.
    """
    import inspect

    from scraper import fetch_quality

    source = inspect.getsource(fetch_quality._add_quality)
    assert 'result.get("source_html_size") is not None' in source, (
        "_add_quality should guard on `is not None` so a 0-byte size stays "
        f"authoritative; got:\n{source}"
    )


# ── Hardening: yield-floor constants re-exported, not re-declared ──


def test_yield_floor_constants_reexported_from_extract():
    """fetch_quality must re-export extract's authoritative constants.

    Re-declaring the literals here lets the warning-message floor silently
    diverge from the predicate that decides when warnings fire.
    """
    import scraper.extract as extract_mod
    from scraper import fetch_quality

    assert fetch_quality.MIN_LOW_YIELD_SOURCE_CHARS is (
        extract_mod.MIN_LOW_YIELD_SOURCE_CHARS
    )
    assert fetch_quality.VOLUME_YIELD_RATIO_FLOOR is (
        extract_mod.VOLUME_YIELD_RATIO_FLOOR
    )


# ── VAL-YIELD-008: shared consumers aligned ──────────────────────


@pytest.mark.asyncio
async def test_scrape_page_full_body(monkeypatch):
    """adapters/_helpers.scrape_page() must return the full card-grid body via
    the shared recovery path instead of its private readability pipeline."""
    import httpx
    from scraper.adapters import _helpers

    class _Resp:
        status_code = 200
        text = FIXTURE.read_text()

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await _helpers.scrape_page("https://example.test/hardware/")
    assert result is not None
    titles_present = sum(1 for _, title in CARD_HREFS if title in result)
    assert titles_present >= 20
    assert SENTINEL in result


def test_scrape_page_delegates_to_shared_html_to_markdown():
    """The helper must call the shared implementation, not re-roll readability."""
    import inspect

    from scraper.adapters import _helpers

    source = inspect.getsource(_helpers)
    assert "html_to_markdown" in source


def test_filter_sections_standard_returns_full_card_grid_body(card_grid_html):
    """filter_sections (standard verbosity) recovers the sibling card grid."""
    from scraper.extract import filter_sections

    markdown = filter_sections(card_grid_html, verbosity="standard")
    titles_present = sum(1 for _, title in CARD_HREFS if title in markdown)
    assert titles_present >= 20
    assert SENTINEL in markdown


def test_filter_sections_delegates_to_shared_html_to_markdown():
    """Standard verbosity must reuse html_to_markdown (single shared path)."""
    import inspect

    import scraper.extract as extract_mod

    source = inspect.getsource(extract_mod)
    assert source.count("Document(") <= 1, (
        "filter_sections should delegate to html_to_markdown instead of "
        "running a second private readability pipeline"
    )


def test_fetch_tiers_uses_shared_html_to_markdown():
    """Tier implementations import the shared converter (wiring sentinel)."""
    import inspect

    from scraper import fetch_tiers

    source = inspect.getsource(fetch_tiers)
    assert "from .fetch_quality import" in source or ("from .fetch_quality" in source)
    assert "html_to_markdown" in source


# ── Review hardening: recovery must not replace complete fragments ──


def test_recovery_keeps_complete_short_article_fragment():
    """A complete short article on a large page is NOT swapped for a
    full-page conversion that would merge sidebars/div-footers into it."""
    from scraper.fetch_quality import html_to_markdown

    article = (
        "<p>This compact article is entirely complete despite its brevity. "
        "It explains one narrow idea across three tidy paragraphs and needs "
        "no further sections to stand alone as useful extracted content.</p>"
        "<p>The second paragraph rounds out the argument with a concrete "
        "example that readers can reproduce on their own hardware quickly.</p>"
    )
    # Filler bytes (HTML comments) push the source past the low-yield size
    # gate without adding extractable text.
    filler = "<!-- analytics blob " + "x" * 12000 + " -->"
    html = (
        "<html><head><title>Compact Note</title></head><body>"
        f"{filler}<article>{article}</article>"
        '<aside class="sidebar">Sidebar Navigation Text About Widgets</aside>'
        "<footer>Div Footer Boilerplate Line</footer>"
        "</body></html>"
    )
    markdown = html_to_markdown(html)
    # Article survives intact...
    assert "entirely complete despite its brevity" in markdown
    # ...without the surrounding chrome a full-page dump would drag in.
    assert "Sidebar Navigation Text About Widgets" not in markdown
    assert "Div Footer Boilerplate Line" not in markdown


# ── Hardening: 2x meaningful-gain gate boundary behavior ─────────
#
# html_to_markdown keeps the low-yield recovery only when
# ``len(recovered) >= len(fragment) * 2``, where ``fragment`` is the
# readability output that tripped the low-yield check (~766 chars on this
# fixture — NOT the final recovered output). These tests drive that exact
# comparison by monkeypatching ``_full_page_markdown`` to controlled sizes
# and deriving the true fragment length by disabling the low-yield branch,
# so the boundary is pinned deterministically.


@pytest.fixture()
def recovery_gate(monkeypatch):
    """Instrument the 2x gain gate with controllable recovery sizes.

    Returns a harness with:
    - ``fragment_length()``: length of the readability fragment that the
      gate compares against (low-yield branch disabled for the probe).
    - ``set_stub_size(n)``: next ``run()`` sees ``_full_page_markdown``
      return an ``n``-char string (0 = empty recovery).
    - ``run()``: executes ``html_to_markdown`` on the fixture.
    """
    from types import SimpleNamespace

    from scraper import fetch_quality

    state: dict = {"size": None}
    real_full_page = fetch_quality._full_page_markdown
    real_is_low_yield = fetch_quality._is_low_yield

    def _stub_full_page(html_arg):
        if state["size"] is not None:
            return "r" * state["size"]
        return real_full_page(html_arg)

    def _no_recovery(markdown, source_size):
        return False

    monkeypatch.setattr(fetch_quality, "_full_page_markdown", _stub_full_page)

    def fragment_length():
        # Disable the low-yield branch so html_to_markdown returns exactly
        # the readability fragment the gate compares against.
        monkeypatch.setattr(fetch_quality, "_is_low_yield", _no_recovery)
        length = len(fetch_quality.html_to_markdown(FIXTURE.read_text()))
        monkeypatch.setattr(fetch_quality, "_is_low_yield", real_is_low_yield)
        return length

    def run():
        return fetch_quality.html_to_markdown(FIXTURE.read_text())

    def set_stub_size(n):
        state["size"] = n

    return SimpleNamespace(
        fragment_length=fragment_length, run=run, set_stub_size=set_stub_size
    )


def test_gain_gate_exact_boundary_swaps_to_recovery(recovery_gate):
    """recovered == 2x fragment exactly -> recovery IS kept (>= comparison)."""
    g = recovery_gate
    fragment_len = g.fragment_length()
    assert fragment_len > 0

    g.set_stub_size(2 * fragment_len)
    swapped = g.run()
    assert len(swapped) == 2 * fragment_len


def test_gain_gate_below_2x_keeps_readability_fragment(recovery_gate):
    """recovered < 2x fragment -> readability fragment kept verbatim."""
    g = recovery_gate
    fragment_len = g.fragment_length()
    assert fragment_len > 0

    g.set_stub_size(2 * fragment_len - 1)  # one char short of the >= gate
    kept = g.run()
    assert len(kept) == fragment_len, (
        f"expected the {fragment_len}-char fragment to be kept, got "
        f"{len(kept)} chars (sub-threshold recovery was swapped in)"
    )


def test_gain_gate_above_2x_swaps_to_recovery(recovery_gate):
    """recovered clearly above 2x fragment -> recovery swapped in (#587 case)."""
    g = recovery_gate
    fragment_len = g.fragment_length()

    g.set_stub_size(4 * fragment_len)
    swapped = g.run()
    assert len(swapped) == 4 * fragment_len


def test_gain_gate_empty_recovery_keeps_fragment(recovery_gate):
    """An empty recovery never replaces the fragment (guards SPA shells)."""
    g = recovery_gate
    fragment_len = g.fragment_length()

    g.set_stub_size(0)
    kept = g.run()
    assert len(kept) == fragment_len


# ── Hardening: markdown-link construction escapes specials ────────
#
# Titles/hrefs containing ] ( ) would otherwise render broken markdown
# links like [Vector [core] (2024)](/wiki/C++). The unwrap helper escapes
# those characters; markdownify preserves the backslash escapes verbatim.
# Text content itself stays preserved — this is purely link-rendering
# hygiene.

SPECIALS_HTML = (
    "<html><head><title>Specials</title></head><body>"
    "<a href='/wiki/C++'><h3>Vector [core] (2024)</h3>"
    "<p>Bracketed card body copy.</p></a>"
    "<a href='/w/a(b)'><h3>Paren Href Card</h3>"
    "<p>Another card body.</p></a>"
    "</body></html>"
)


def test_unwrap_escapes_specials_in_title_link_text():
    """] ( ) in titles are backslash-escaped inside the [title](href) span."""
    from bs4 import BeautifulSoup
    from scraper.fetch_quality import _unwrap_anchor_wrapped_cards

    soup = BeautifulSoup(SPECIALS_HTML, "html.parser")
    _unwrap_anchor_wrapped_cards(soup)
    span = soup.find("span")
    assert span is not None
    text = span.get_text()
    assert text == "[Vector \\[core\\] \\(2024\\)](/wiki/C++)", text


def test_unwrap_escapes_parens_in_href():
    """Unescaped parens in an href would terminate the link target early."""
    from bs4 import BeautifulSoup
    from scraper.fetch_quality import _unwrap_anchor_wrapped_cards

    soup = BeautifulSoup(SPECIALS_HTML, "html.parser")
    _unwrap_anchor_wrapped_cards(soup)
    spans = soup.find_all("span")
    paren_href_span = next(s for s in spans if "/w/a" in s.get_text())
    assert "\\(b\\)" in paren_href_span.get_text()


def test_unwrap_leaves_plain_titles_and_hrefs_untouched():
    """No escape noise for titles/hrefs without special characters."""
    from bs4 import BeautifulSoup
    from scraper.fetch_quality import _unwrap_anchor_wrapped_cards

    plain_html = (
        "<html><body><a href='/cards/plain'><h3>Plain Card</h3>"
        "<p>Body copy.</p></a></body></html>"
    )
    soup = BeautifulSoup(plain_html, "html.parser")
    _unwrap_anchor_wrapped_cards(soup)
    text = soup.find("span").get_text()
    assert text == "[Plain Card](/cards/plain)"
    assert "\\" not in text


def test_unwrap_preserves_title_text_with_specials_end_to_end():
    """Escaped link syntax does not lose any visible title characters."""
    from bs4 import BeautifulSoup
    from scraper.fetch_quality import _unwrap_anchor_wrapped_cards

    soup = BeautifulSoup(SPECIALS_HTML, "html.parser")
    _unwrap_anchor_wrapped_cards(soup)
    text = soup.find("span").get_text()
    # Strip only the link-syntax scaffolding; the title characters remain.
    inner = text[len("[") : text.rindex("](")]
    assert inner.replace("\\", "") == "Vector [core] (2024)"


# ── Hardening: live-snapshot drift reference ─────────────────────


def test_live_snapshot_fixture_present_and_real_page_shaped():
    """The raw live-page snapshot is committed for drift comparison.

    It must stay byte-stable once committed (it is the validator's sole
    offline reference) and keep the real page's defining properties.
    """
    assert LIVE_SNAPSHOT.exists(), (
        "hardware-page-live.html snapshot missing; it is the committed "
        "drift-comparison reference for the deterministic fixture"
    )
    html = LIVE_SNAPSHOT.read_text()
    # Captured 2026-08-22 from open-neuromorphic.org hardware directory.
    assert len(html) > 50000
    assert "open-neuromorphic" in html
    # Same card-grid topology as the deterministic fixture.
    assert "hardware-card-wide-link" in html
    for token in ("Loihi", "Akida", "SpiNNaker"):
        assert token in html, f"contract token {token} missing from live snapshot"


def test_live_snapshot_drift_vs_deterministic_fixture():
    """Mechanical drift comparison: live snapshot vs modeled fixture.

    The deterministic fixture intentionally models the live page's
    topology. This test pins the shared structural invariants so silent
    divergence between the two references surfaces as a test failure.
    """
    from bs4 import BeautifulSoup

    live = BeautifulSoup(LIVE_SNAPSHOT.read_text(), "html.parser")
    modeled = BeautifulSoup(FIXTURE.read_text(), "html.parser")

    def topology(soup):
        cards = soup.select("a.hardware-card-wide-link")
        prose = soup.select_one("div.prose")
        top_list = cards[0].find_parent("ul") if cards else None
        while top_list is not None and top_list.parent.name in ("li", "ul"):
            top_list = top_list.parent
        wrapped = sum(1 for a in cards if a.find("h3") is not None)
        return {
            "cards": len(cards),
            "wrapped_h3": wrapped,
            "has_prose": prose is not None,
            "grid_is_sibling_of_prose": (
                prose is not None
                and top_list is not None
                and prose.parent is top_list.parent
            ),
        }

    live_shape = topology(live)
    modeled_shape = topology(modeled)
    # Both pages carry the anchor-wrapped sibling-grid trap.
    assert live_shape["wrapped_h3"] == live_shape["cards"] > 0
    assert modeled_shape["wrapped_h3"] == modeled_shape["cards"] == 25
    assert live_shape["has_prose"] and modeled_shape["has_prose"]
    assert live_shape["grid_is_sibling_of_prose"]
    assert modeled_shape["grid_is_sibling_of_prose"]

    # Card-level overlap: every fixture card models a real live-page card.
    live_text = LIVE_SNAPSHOT.read_text()
    missing_from_live = [href for href, _ in CARD_HREFS if href not in live_text]
    # The fixture's synthetic Sakura entry is the only invented card.
    assert all("sakura" in href for href in missing_from_live), (
        f"unexpected fixture/live drift: {missing_from_live}"
    )

    # Recovery parity: the extractor handles both references the same way.
    from scraper.fetch_quality import html_to_markdown

    live_md = html_to_markdown(LIVE_SNAPSHOT.read_text())
    modeled_md = html_to_markdown(FIXTURE.read_text())
    assert len(live_md) >= 10240 and len(modeled_md) >= 10240
    for token in ("Loihi", "Akida", "SpiNNaker"):
        assert token in live_md, f"{token} lost recovering the live snapshot"
        assert token in modeled_md, f"{token} lost recovering the fixture"


@pytest.mark.asyncio
async def test_scrape_page_does_not_duplicate_title(monkeypatch):
    """When the shared pipeline already starts with the page title, the
    helper must not prepend it again."""
    import httpx
    from scraper.adapters import _helpers

    html = (
        "<html><head><title>Dup Title Probe</title></head><body><p>Hello"
        "</p></body></html>"
    )

    class _Resp:
        status_code = 200
        text = html

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await _helpers.scrape_page("https://example.test/note")
    assert result is not None
    assert result.count("Dup Title Probe") == 1
