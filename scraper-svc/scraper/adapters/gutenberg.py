"""
Project Gutenberg adapter — extracts books as chapter-structured markdown.

Fallback chain:
  1. EPUB — download the EPUB, extract XHTML files, convert to markdown,
     detect chapter boundaries.
  2. Plain text — download pg{id}.txt, strip PG boilerplate, split by
     chapter headings.
  3. AdapterError — fall through to the generic scrape pipeline.

Metadata is enriched via the Gutendex API (non-fatal on failure).

URL patterns:
  - www.gutenberg.org/ebooks/<id> (and optional .epub.images suffix)
  - www.gutenberg.org/files/<id>/
  - gutenberg.org/cache/epub/<id>/
"""

from __future__ import annotations

import logging
import re
import zipfile
from io import BytesIO
from typing import Any

import httpx

from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

# ── URL pattern matching ─────────────────────────────────────────

_GUTENBERG_URL_PATTERNS = [
    # www.gutenberg.org/ebooks/<id>  (optional .epub.images suffix)
    re.compile(r"^https?://www\.gutenberg\.org/ebooks/(\d+)(?:\.epub\.images)?"),
    # www.gutenberg.org/files/<id>/
    re.compile(r"^https?://www\.gutenberg\.org/files/(\d+)/"),
    # gutenberg.org/cache/epub/<id>/
    re.compile(r"^https?://gutenberg\.org/cache/epub/(\d+)/"),
]

# ── Boilerplate regex (plain text) ──────────────────────────────

# Strip everything before START OF PROJECT GUTENBERG EBOOK
_START_BOILERPLATE = re.compile(
    r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.DOTALL,
)
# Strip everything after END OF PROJECT GUTENBERG EBOOK
_END_BOILERPLATE = re.compile(
    r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.DOTALL,
)

# ── Chapter heading patterns (plain text) ────────────────────────

_CHAPTER_PATTERN = re.compile(
    r"^(CHAPTER |Chapter |Book |PART |Part |SECTION |Section |Canto )",
    re.MULTILINE,
)


# ── ID extraction ────────────────────────────────────────────────


def _extract_book_id(url: str) -> str | None:
    """Extract the numeric Gutenberg book ID from a URL.

    Returns the ID string (e.g. ``"11"``) or ``None``.
    """
    for pat in _GUTENBERG_URL_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


# ── EPUB extraction helpers ──────────────────────────────────────


def _parse_epub_metadata(zf: zipfile.ZipFile) -> dict[str, Any]:
    """Extract title and author from an EPUB archive.

    Looks in ``content.opf`` (or the first ``.opf`` file) and
    ``toc.ncx`` for metadata.
    """
    metadata: dict[str, Any] = {}

    # Find the OPF file
    opf_name: str | None = None
    for name in zf.namelist():
        if name.endswith(".opf"):
            opf_name = name
            break

    if opf_name:
        try:
            from bs4 import BeautifulSoup

            opf_content = zf.read(opf_name)
            soup = BeautifulSoup(opf_content, "xml")

            # Title from <dc:title>
            title_el = soup.find("dc:title")
            if title_el:
                metadata["title"] = title_el.get_text(strip=True)

            # Author from <dc:creator>
            creator_el = soup.find("dc:creator")
            if creator_el:
                metadata["author"] = creator_el.get_text(strip=True)

            # Language from <dc:language>
            lang_el = soup.find("dc:language")
            if lang_el:
                metadata["language"] = lang_el.get_text(strip=True)

            # Subjects from <dc:subject>
            subject_els = soup.find_all("dc:subject")
            if subject_els:
                metadata["subjects"] = [s.get_text(strip=True) for s in subject_els]

        except Exception as exc:
            logger.debug("Failed to parse OPF metadata: %s", exc)

    # Fallback: try toc.ncx for title
    if "title" not in metadata:
        for name in zf.namelist():
            if name.endswith(".ncx"):
                try:
                    ncx_content = zf.read(name)
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(ncx_content, "html.parser")
                    title_el = soup.find("ncx:title") or soup.find("title")
                    if title_el:
                        metadata["title"] = title_el.get_text(strip=True)
                except Exception as e:
                    logger.debug("NCX metadata extraction failed for %s: %s", name, e)
                break

    return metadata


def _detect_chapter_in_xhtml(html_content: str) -> bool:
    """Check whether an XHTML document contains a chapter-level heading."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if re.search(
            r"(CHAPTER|Chapter|Book|Part|Section|Canto|Prologue|Epilogue|Preface)",
            text,
        ):
            return True
    return False


def _extract_chapter_title_xhtml(html_content: str) -> str | None:
    """Extract the chapter title from an XHTML document heading."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if text:
            return text
    return None


def _epub_to_markdown(zf: zipfile.ZipFile) -> tuple[str, list[str], int]:
    """Convert an EPUB archive's XHTML files to chapter-structured markdown.

    Returns ``(markdown_body, chapter_titles, word_count)``.
    """
    # Collect XHTML/HTML files
    xhtml_files: list[str] = []
    for name in zf.namelist():
        if name.endswith((".xhtml", ".html", ".htm")):
            xhtml_files.append(name)

    xhtml_files.sort()

    chapters_md: list[str] = []
    chapter_titles: list[str] = []
    total_words = 0
    has_any_chapter_heading = False

    for fname in xhtml_files:
        try:
            content = zf.read(fname)
            html = content.decode("utf-8", errors="replace")
        except Exception:
            continue

        from markdownify import markdownify as md

        markdown_text = md(html, heading_style="ATX", strip=["script", "style"])
        markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text).strip()

        if not markdown_text:
            continue

        word_count = len(markdown_text.split())
        total_words += word_count

        chapter_title = _extract_chapter_title_xhtml(html)
        is_chapter = _detect_chapter_in_xhtml(html)

        if is_chapter:
            has_any_chapter_heading = True
            if chapter_title:
                chapter_titles.append(chapter_title)
            else:
                chapter_titles.append(f"Chapter {len(chapter_titles) + 1}")
            chapters_md.append(
                f"## {chapter_title or f'Chapter {len(chapters_md) + 1}'}\n\n{markdown_text}"
            )
        else:
            # Non-chapter file (cover page, etc.) — prepend as-is if first
            if not chapters_md and not has_any_chapter_heading:
                chapters_md.append(markdown_text)
                # If there's a title heading, extract it
                if chapter_title and not chapter_titles:
                    chapter_titles.append(chapter_title)
            elif markdown_text and chapters_md:
                # Append as continuation of last chapter
                chapters_md[-1] += f"\n\n{markdown_text}"

    # If no chapter headings detected, return as single document
    if not has_any_chapter_heading and chapters_md:
        combined = "\n\n".join(chapters_md)
        return combined, chapter_titles or [], total_words

    body = "\n\n---\n\n".join(chapters_md)
    return body, chapter_titles, total_words


async def _fetch_epub(book_id: str) -> bytes | None:
    """Download an EPUB file from Project Gutenberg.

    Returns raw bytes or ``None`` on failure.
    """
    epub_url = (
        f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}-images-3.epub"
    )
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(epub_url)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
            logger.debug(
                "EPUB download returned %d (%d bytes) for %s",
                resp.status_code,
                len(resp.content),
                epub_url,
            )
    except Exception as exc:
        logger.debug("EPUB download failed for %s: %s", epub_url, exc)
    return None


# ── Plain-text extraction helpers ────────────────────────────────


def _strip_boilerplate(text: str) -> str:
    """Remove the Project Gutenberg boilerplate header and footer."""
    text = _START_BOILERPLATE.sub("", text)
    text = _END_BOILERPLATE.sub("", text)
    return text.strip()


def _split_into_chapters(text: str) -> list[tuple[str, str]]:
    """Split plain text into chapters based on heading patterns.

    Returns a list of ``(title, content)`` tuples.
    """
    splits = _CHAPTER_PATTERN.split(text)
    chapters: list[tuple[str, str]] = []

    if len(splits) <= 1:
        # No chapter headings found
        return [("", text.strip())]

    # splits: [before_first, heading1, content1, heading2, content2, ...]
    # Skip the text before any heading
    i = 1
    while i < len(splits) - 1:
        heading = splits[i].strip()
        content = splits[i + 1].strip() if i + 1 < len(splits) else ""
        # heading is like "CHAPTER I" or "Chapter 1" — just the prefix
        # The actual heading text follows in content
        # We need to extract the full line
        chapters.append((heading, content))
        i += 2

    return chapters


async def _fetch_plain_text(book_id: str) -> str | None:
    """Download the plain-text version of a Gutenberg book.

    Returns the raw text or ``None`` on failure.
    """
    txt_url = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(txt_url)
            if resp.status_code == 200 and len(resp.text) > 100:
                return resp.text
            logger.debug(
                "Plain text download returned %d for %s",
                resp.status_code,
                txt_url,
            )
    except Exception as exc:
        logger.debug("Plain text download failed for %s: %s", txt_url, exc)
    return None


# ── Gutendex metadata enrichment ─────────────────────────────────


async def _fetch_gutendex_metadata(book_id: str) -> dict[str, Any] | None:
    """Try to enrich metadata via the Gutendex API.

    Returns a metadata dict or ``None`` on timeout/failure.
    Uses a short 3s timeout — failures are non-fatal.
    """
    url = f"https://gutendex.com/books/{book_id}"
    try:
        async with httpx.AsyncClient(
            timeout=3,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                result: dict[str, Any] = {}
                if data.get("title"):
                    result["title"] = data["title"]
                authors = data.get("authors", [])
                if authors:
                    author = authors[0]
                    name_parts = []
                    if author.get("name"):
                        name_parts.append(author["name"])
                    if author.get("birth_year"):
                        birth = author["birth_year"]
                        death = author.get("death_year") or ""
                        name_parts.append(f"({birth}-{death})")
                    result["author"] = " ".join(name_parts)
                if data.get("languages"):
                    result["language"] = data["languages"][0]  # Primary language
                if data.get("subjects"):
                    result["subjects"] = data["subjects"]
                if data.get("download_count") is not None:
                    result["download_count"] = data["download_count"]
                return result
    except httpx.TimeoutException:
        logger.debug("Gutendex timed out for book %s", book_id)
    except Exception as exc:
        logger.debug("Gutendex request failed for book %s: %s", book_id, exc)
    return None


# ── Adapter class ────────────────────────────────────────────────


@adapter
class GutenbergAdapter(SiteAdapter):
    """Extract books from Project Gutenberg as chapter-structured markdown.

    Primary path: EPUB download → XHTML extraction → markdown conversion.
    Fallback: plain text download → boilerplate stripping → chapter splitting.
    Last resort: raise AdapterError, falling through to the generic pipeline.
    """

    name = "gutenberg"

    patterns = _GUTENBERG_URL_PATTERNS

    # File/structured content tier — same as substack, nvd, github
    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        logger.info("Gutenberg adapter: url=%s", url)

        book_id = _extract_book_id(url)
        if not book_id:
            raise AdapterError(f"Could not extract book ID from URL: {url}")

        logger.info("Gutenberg adapter: book_id=%s", book_id)

        # ── Tier 1: EPUB extraction ─────────────────────────────
        logger.info("Gutenberg adapter: trying EPUB for book %s", book_id)
        epub_bytes = await ctx.with_timeout(_fetch_epub(book_id), timeout=35)
        if epub_bytes:
            try:
                zf = zipfile.ZipFile(BytesIO(epub_bytes))
                epub_metadata = _parse_epub_metadata(zf)
                markdown_body, chapter_titles, word_count = _epub_to_markdown(zf)
                zf.close()

                if markdown_body:
                    # Try Gutendex metadata enrichment (non-fatal)
                    gutendex_meta = await _fetch_gutendex_metadata(book_id)
                    if gutendex_meta:
                        epub_metadata.update(gutendex_meta)

                    # Build metadata
                    metadata: dict[str, Any] = {
                        "title": epub_metadata.get("title", ""),
                        "author": epub_metadata.get("author", ""),
                        "gutenberg_id": int(book_id),
                        "language": epub_metadata.get("language", "en"),
                        "subjects": epub_metadata.get("subjects", []),
                        "download_count": epub_metadata.get("download_count", 0),
                        "chapters": chapter_titles,
                        "word_count": word_count,
                        "source": "gutenberg-epub",
                    }

                    logger.info(
                        "Gutenberg adapter: EPUB hit for book %s (%d chars, %d chapters)",
                        book_id,
                        len(markdown_body),
                        len(chapter_titles),
                    )
                    return AdapterResult(
                        success=True,
                        markdown=markdown_body,
                        metadata=metadata,
                        source="gutenberg-epub",
                        url=url,
                    )
            except Exception as exc:
                logger.debug("EPUB extraction failed for book %s: %s", book_id, exc)

        # ── Tier 2: Plain text download ─────────────────────────
        logger.info("Gutenberg adapter: trying plain text for book %s", book_id)
        raw_text = await ctx.with_timeout(_fetch_plain_text(book_id), timeout=20)
        if raw_text:
            cleaned = _strip_boilerplate(raw_text)
            if cleaned:
                chapters = _split_into_chapters(cleaned)

                chapter_titles_pt: list[str] = []
                chapter_bodies: list[str] = []

                for heading, content in chapters:
                    if heading:
                        # Normalize heading: if it's just a prefix like "CHAPTER "
                        # we need the full line. Extract the first line of content
                        # as the title.
                        lines = content.split("\n")
                        chapter_line = lines[0].strip() if lines else ""
                        if chapter_line and not chapter_line.startswith("#"):
                            full_title = f"{heading}{chapter_line}"
                        else:
                            full_title = heading.strip()
                        chapter_titles_pt.append(full_title)
                        remaining = "\n".join(lines[1:]) if len(lines) > 1 else ""
                        chapter_bodies.append(f"## {full_title}\n\n{remaining.strip()}")

                if not chapter_bodies:
                    # No chapter structure — single document
                    chapter_bodies.append(cleaned)

                body = "\n\n---\n\n".join(chapter_bodies)
                word_count_pt = len(cleaned.split())

                # Try Gutendex metadata for plain text too
                gutendex_meta = await _fetch_gutendex_metadata(book_id)
                metadata_pt: dict[str, Any] = {
                    "title": gutendex_meta.get("title", "") if gutendex_meta else "",
                    "author": gutendex_meta.get("author", "") if gutendex_meta else "",
                    "gutenberg_id": int(book_id),
                    "language": (
                        gutendex_meta.get("language", "en") if gutendex_meta else "en"
                    ),
                    "subjects": (
                        gutendex_meta.get("subjects", []) if gutendex_meta else []
                    ),
                    "download_count": (
                        gutendex_meta.get("download_count", 0) if gutendex_meta else 0
                    ),
                    "chapters": chapter_titles_pt,
                    "word_count": word_count_pt,
                    "source": "gutenberg-plaintext",
                }

                logger.info(
                    "Gutenberg adapter: plain text hit for book %s (%d chars, %d chapters)",
                    book_id,
                    len(body),
                    len(chapter_titles_pt),
                )
                return AdapterResult(
                    success=True,
                    markdown=body,
                    metadata=metadata_pt,
                    source="gutenberg-plaintext",
                    url=url,
                )

        raise AdapterError(
            f"Could not extract content from Gutenberg URL {url} (book_id={book_id})"
        )
