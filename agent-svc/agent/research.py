"""The agent research loop: search → scrape → think → answer.

Also provides the extract endpoint: scrape given URLs → LLM → structured data.
"""

import json
import logging
from typing import Any

from common.url import extract_domain

from .llm import LLMClient
from .scraper_client import ScraperClient
from .searxng_client import SearXNGClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are GroktoCrawl, a determined web research agent. Your job is to
thoroughly investigate each question by synthesizing information from the web
sources you have gathered. You are not a summarizer — you are a researcher.

IDENTITY
You are a research assistant who cares about getting the right answer. You weigh
evidence, identify patterns, detect contradictions, and flag uncertainty. You
are thorough and precise: you prefer specific, well-supported information over
vague generalizations, and you organise your findings so the reader can act on
them.

SOURCE QUALITY
Not all sources are equal. Evaluate each web page you draw from:
• Official documentation, academic / scientific publications, government or
  regulatory data, primary-source repositories — high authority.
• Established news outlets, technical reports by reputable organisations,
  industry analysts — medium authority.
• Personal / company blogs, forums, Q&A sites, social media — lower authority.
• Aggregators, clickbait, pages with no identifiable author or publication
  date — lowest authority.

When you must rely on lower-authority sources, say so explicitly. When several
independent, high-quality sources agree on a point, treat it as stronger
evidence. When they conflict, present both perspectives, assess the evidence
each side offers, and explain why the disagreement exists.

SYNTHESIS
• Look for consensus across the sources you have and highlight it.
• When sources contradict, present each viewpoint and compare the evidence.
• Note when the available sources are thin, one-sided, or incomplete.
• If the context does not contain enough information to answer the question
  fully, say so clearly and suggest what kind of sources would be needed for a
  complete answer.

INTEGRITY
• Base your answer ONLY on the web content provided in the context below.
  Do not use your own pre-training knowledge to fill gaps.
• Never fabricate information or invent sources. Every factual claim must be
  traceable to a specific source in the context.
• Cite sources by their URL whenever you use information from a specific page.

OUTPUT QUALITY
• Lead with the most important finding, then support it with evidence.
• Organise information clearly — use paragraphs, concise sections, or lists
  as appropriate.
• Be precise: specific numbers, names, and dates are better than general
  statements.
• For comparisons or trade-off analyses, present a balanced, point-by-point
  treatment.
• If structured output (JSON) is requested, gather your reasoning first, then
  format your final answer to match the requested schema exactly.
• Format your answer in clean markdown unless a JSON schema is provided."""

EXTRACT_SYSTEM_PROMPT = """You are GroktoCrawl, a structured data extraction agent.
Your job is to extract the requested information from the provided web content
as completely and accurately as possible.

Rules:
- Extract data based ONLY on the content provided below.
- If multiple instances of the requested data exist, extract ALL of them —
  do not stop after the first match.
- If a value is missing, incomplete, or ambiguous, note it rather than fabricating.
- If the content doesn't contain the requested information at all, return an
  empty result.
- If a schema is provided, respond with valid JSON matching that schema exactly.
- Organise extracted data clearly. If no schema is provided, format your answer
  in clean markdown with structure (tables, lists, sections as appropriate)."""


async def run_research(
    prompt: str,
    urls: list[str] | None = None,
    schema: dict | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
    requested_model: str | None = None,
) -> dict:
    """Execute the research loop: search → scrape → think → answer."""
    searxng = SearXNGClient(searxng_url)
    scraper = ScraperClient(scraper_url)
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        target_urls = list(urls) if urls else []
        if not target_urls:
            logger.info("No URLs provided. Searching for: %s", prompt)
            search_results, _health = await searxng.search(prompt, limit=10)
            target_urls = [r["url"] for r in search_results if r.get("url")]

        preferred = [u for u in target_urls if not _is_video_platform_url(u)]
        deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

        documents, source_details = await _scrape_urls(
            preferred,
            scraper,
            min_sources=3,
            max_attempts=len(preferred) or 10,
        )
        logger.info(
            "run_research: scraped %d docs from %d preferred URLs (attempts=%d)",
            len(documents),
            len(preferred),
            len(preferred) or 10,
        )

        if len(documents) < 3 and deprioritized:
            remaining = 3 - len(documents)
            extra_docs, extra_details = await _scrape_urls(
                deprioritized,
                scraper,
                min_sources=remaining,
                max_attempts=remaining * 2,
            )
            documents.extend(extra_docs)
            source_details.extend(extra_details)

        context = "\n\n---\n\n".join(documents) if documents else ""

        if not context:
            return {
                "result": "I was unable to find or scrape any relevant web pages to answer your question.",
                "sources": [],
                "source_details": [],
            }

        answer = await llm.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            context=context,
            schema=schema,
        )
        _validate_json_if_schema(answer, schema)
        return {
            "result": answer,
            "sources": [s["url"] for s in source_details],
            "source_details": source_details,
        }
    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


async def run_research_stream(
    prompt: str,
    urls: list[str] | None = None,
    schema: dict | None = None,
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
    requested_model: str | None = None,
):
    """Streaming version of run_research. Yields SSE-suitable dicts.

    Phase 1 - Discovery: search + scrape, yielding progress events.
    Phase 2 - Synthesis: LLM token stream (or full response for schema-based output).

    Yields:
      {"type": "sources_pending", "sources": [...]} — search results (before scrape)
      {"type": "source_scraped", "url": "...", "title": "...", "chars": N} — each scraped page
      {"type": "token", "content": "..."} — individual tokens from the LLM (no schema)
      {"type": "done", "result": "...", "sources": [...], "latency_ms": N} — final
      {"type": "error", "content": "..."} — error
    """
    import time

    start = time.monotonic()

    searxng = SearXNGClient(searxng_url)
    scraper = ScraperClient(scraper_url)
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        target_urls = list(urls) if urls else []
        search_results = []
        if not target_urls:
            logger.info("Research stream: searching for: %s", prompt)
            search_results, _health = await searxng.search(prompt, limit=10)
            target_urls = [r["url"] for r in search_results if r.get("url")]

        preferred = [u for u in target_urls if not _is_video_platform_url(u)]
        deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

        # Yield pending sources for progress visibility
        pending_sources = (
            [
                {
                    "url": r["url"],
                    "title": r.get("title", ""),
                    "relevance": r.get("description", ""),
                }
                for r in search_results
                if r.get("url")
            ]
            if not urls
            else [{"url": u, "title": "", "relevance": ""} for u in urls]
        )
        yield {"type": "sources_pending", "sources": pending_sources}

        # Phase 1: Scrape with progress events
        logger.info("Research stream: scraping URLs")
        documents: list[str] = []
        source_details: list[dict] = []
        import asyncio

        semaphore = asyncio.Semaphore(2)
        url_timeout = 20

        async def _scrape_one(url: str) -> tuple[str | None, dict | None]:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        scraper.scrape(url), timeout=url_timeout
                    )
                    if result.get("success") and result.get("data", {}).get("markdown"):
                        md = result["data"]["markdown"]
                        domain = extract_domain(url)
                        doc = f"Source: {url} (domain: {domain})\n\n{md[:8000]}"
                        src = {
                            "url": url,
                            "source": result["data"].get("source", "unknown"),
                            "char_count": len(md),
                        }
                        return doc, src
                    else:
                        logger.warning(
                            "Failed to scrape %s: %s", url, result.get("error")
                        )
                        return None, None
                except TimeoutError:
                    logger.warning("Timeout scraping %s after %ss", url, url_timeout)
                    return None, None
                except Exception as e:
                    logger.warning("Error scraping %s: %s", url, e)
                    return None, None

        pending = list(preferred)
        tasks: set[asyncio.Task] = set()
        task_to_url: dict[asyncio.Task, str] = {}

        while pending or tasks:
            while len(tasks) < 2 and pending:
                url = pending.pop(0)
                task = asyncio.create_task(_scrape_one(url))
                task_to_url[task] = url
                tasks.add(task)

            if not tasks:
                break

            done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                doc, src = task.result()
                url = task_to_url.pop(task, "")
                if doc and src:
                    documents.append(doc)
                    source_details.append(src)
                    yield {
                        "type": "source_scraped",
                        "url": src["url"],
                        "source": src["source"],
                        "chars": src["char_count"],
                    }

        # Fall back to deprioritized (video-platform) URLs if we don't have enough sources
        if len(documents) < 3 and deprioritized:
            remaining = 3 - len(documents)
            logger.info(
                "Research stream: %d/3 from preferred sources, falling back to %d video-platform URLs",
                len(documents),
                min(remaining, len(deprioritized)),
            )
            extra_docs, extra_details = await _scrape_urls(
                deprioritized,
                scraper,
                min_sources=remaining,
                max_attempts=remaining * 2,
            )
            for extra_doc, extra_src in zip(extra_docs, extra_details, strict=False):
                documents.append(extra_doc)
                source_details.append(extra_src)
                yield {
                    "type": "source_scraped",
                    "url": extra_src["url"],
                    "source": extra_src["source"],
                    "chars": extra_src["char_count"],
                }

        context = "\n\n---\n\n".join(documents) if documents else ""

        if not context:
            elapsed = int((time.monotonic() - start) * 1000)
            yield {"type": "sources", "sources": []}
            yield {
                "type": "done",
                "result": "I was unable to find or scrape any relevant web pages.",
                "sources": [],
                "latency_ms": elapsed,
            }
            return

        # Phase 2: Synthesis
        # If schema is provided, use synchronous generation (structured JSON output)
        if schema:
            answer = await llm.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                context=context,
                schema=schema,
            )
            _validate_json_if_schema(answer, schema)
            source_list = [s["url"] for s in source_details]
            elapsed = int((time.monotonic() - start) * 1000)
            yield {"type": "sources", "sources": source_list}
            yield {
                "type": "done",
                "result": answer,
                "sources": source_list,
                "latency_ms": elapsed,
            }
            return

        # No schema — stream tokens from the LLM
        yield {"type": "sources", "sources": [s["url"] for s in source_details]}

        full_answer = ""
        async for event in llm.generate_stream(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            context=context,
        ):
            if event["type"] == "token":
                full_answer += event["content"]
                yield {"type": "token", "content": event["content"]}
            elif event["type"] == "error":
                yield {"type": "error", "content": event["content"]}
                return
            elif event["type"] == "done":
                full_answer = event["full_content"]

        source_list = [s["url"] for s in source_details]
        elapsed = int((time.monotonic() - start) * 1000)
        yield {
            "type": "done",
            "result": full_answer,
            "sources": source_list,
            "latency_ms": elapsed,
        }

    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


async def run_extract(
    urls: list[str],
    prompt: str | None = None,
    schema: dict | None = None,
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
) -> dict:
    """Extract structured data from given URLs. No search step."""
    scraper = ScraperClient(scraper_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        documents, source_details = await _scrape_urls(urls, scraper)
        context = "\n\n---\n\n".join(documents) if documents else ""

        if not context:
            return {
                "result": "No content could be extracted from the provided URLs.",
                "sources": [],
                "source_details": [],
            }

        user_prompt = (
            prompt or "Extract the requested information from the provided content."
        )
        answer = await llm.generate(
            system_prompt=EXTRACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            context=context,
            schema=schema,
        )
        _validate_json_if_schema(answer, schema)
        return {
            "result": answer,
            "sources": [s["url"] for s in source_details],
            "source_details": source_details,
        }
    finally:
        await scraper.close()
        await llm.close()


async def _scrape_urls(
    urls: list[str],
    scraper: ScraperClient,
    min_sources: int = 3,
    max_attempts: int | None = None,
) -> tuple[list[str], list[dict]]:
    """Scrape URLs with bounded concurrency and return (documents, source_details).

    Tries URLs in batches until ``min_sources`` are successfully scraped
    or the list is exhausted (whichever comes first).
    Uses a semaphore (max 2 concurrent) with per-URL timeout (20s).
    ``max_attempts`` sets an upper bound on how many URLs are tried.
    """
    import asyncio

    documents: list[str] = []
    source_details: list[dict] = []
    max_attempts = max_attempts or len(urls)
    semaphore = asyncio.Semaphore(2)
    url_timeout = 20

    async def _scrape_one(url: str) -> tuple[str | None, dict | None]:
        async with semaphore:
            try:
                logger.info("Scraping: %s", url)
                result = await asyncio.wait_for(
                    scraper.scrape(url), timeout=url_timeout
                )
                if result.get("success") and result.get("data", {}).get("markdown"):
                    md = result["data"]["markdown"]
                    domain = extract_domain(url)
                    doc = f"Source: {url} (domain: {domain})\n\n{md[:8000]}"
                    src = {
                        "url": url,
                        "source": result["data"].get("source", "unknown"),
                        "char_count": len(md),
                    }
                    return doc, src
                else:
                    logger.warning("Failed to scrape %s: %s", url, result.get("error"))
                    return None, None
            except TimeoutError:
                logger.warning("Timeout scraping %s after %ss", url, url_timeout)
                return None, None
            except Exception as e:
                logger.warning("Error scraping %s: %s", url, e)
                return None, None

    # Process URLs in batches — launch concurrent tasks, collect results,
    # stop when min_sources is reached or max_attempts exhausted
    pending = list(urls)
    task_to_url: dict[asyncio.Task, str] = {}
    tasks: set[asyncio.Task] = set()
    attempts = 0

    while pending or tasks:
        # Fill slots up to our budget
        while len(tasks) < 2 and pending and attempts < max_attempts:
            url = pending.pop(0)
            attempts += 1
            task = asyncio.create_task(_scrape_one(url))
            task_to_url[task] = url
            tasks.add(task)

        if not tasks:
            break

        # Wait for at least one task to complete
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            doc, src = task.result()
            if doc and src:
                documents.append(doc)
                source_details.append(src)
                if len(documents) >= min_sources:
                    # Cancel remaining tasks and stop
                    for t in tasks:
                        t.cancel()
                    tasks.clear()
                    return documents, source_details

    return documents, source_details


def _validate_json_if_schema(answer: str, schema: dict | None) -> None:
    """If a schema was provided, attempt to parse the answer as JSON."""
    if not schema:
        return
    try:
        cleaned = answer.strip()
        cleaned = cleaned.removeprefix("```json")
        cleaned = cleaned.removeprefix("```")
        cleaned = cleaned.removesuffix("```")
        json.loads(cleaned)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM response not valid JSON despite schema: %s", e)


# ── Video-platform URL filtering ────────────────────────────────
# Domains whose primary content is audio-visual (video, audio,
# short-form) rather than text.  Transcripts extracted from these
# platforms are low-signal for factual text queries and pollute the
# LLM context.  They are deprioritised — only used as a fallback
# when text sources can't fill the ``min_sources`` quota.
_VIDEO_PLATFORM_DOMAINS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "tiktok.com",
        "www.tiktok.com",
        "vm.tiktok.com",
        "instagram.com",
        "www.instagram.com",
    }
)


def _is_video_platform_url(url: str) -> bool:
    """Return True when *url* belongs to a video-first platform."""
    hostname = extract_domain(url).lower()
    # Strip leading "www." for comparison (the frozenset includes
    # both canonicalised and www-prefixed variants).
    return (
        hostname in _VIDEO_PLATFORM_DOMAINS
        or hostname.removeprefix("www.") in _VIDEO_PLATFORM_DOMAINS
    )


ANSWER_SYSTEM_PROMPT = """You are GroktoCrawl, a helpful Q&A agent. Your job is to answer
the user's question using ONLY the web search results provided below.

RULES:
- Base your answer ONLY on the context provided. Do not use your pre-training knowledge.
- Cite sources using inline markers like [1], [2], etc. Each marker corresponds to a source URL listed below.
- If the context doesn't contain enough information to answer fully, say so clearly.
- Be concise but thorough. Lead with the direct answer, then add supporting detail.
- Use clean markdown formatting.
- Do not fabricate information or invent sources."""


async def _rerank_answer_sources(
    search_results: list[dict],
    query: str,
    retrieval_mode: str,
    semantic_url: str,
    scraper_url: str,
    limit: int,
) -> list[dict]:
    """Rerank or augment search results for the answer pipeline."""
    if retrieval_mode == "keyword" or not search_results:
        return search_results

    from .scraper_client import ScraperClient
    from .semantic_client import SemanticClient

    semantic = SemanticClient(semantic_url)
    scraper = ScraperClient(scraper_url)
    try:
        if retrieval_mode in ("semantic", "hybrid"):
            urls_to_scrape = [r["url"] for r in search_results[:limit]]
            contents = []
            for url in urls_to_scrape:
                try:
                    scraped = await scraper.scrape(url)
                    c = (
                        scraped.get("data", {}).get("markdown", "")
                        if scraped.get("success")
                        else ""
                    )
                    contents.append(c[:2000])
                except Exception:
                    contents.append("")

            if retrieval_mode == "semantic":
                embeddings = await semantic.embed([query] + contents)
                similarities = [
                    sum(a * b for a, b in zip(embeddings[0], emb))
                    for emb in embeddings[1:]
                ]
                ranked = sorted(
                    range(len(similarities)),
                    key=lambda i: similarities[i],
                    reverse=True,
                )
                return [search_results[i] for i in ranked if i < len(search_results)]
            else:
                reranked = await semantic.rerank(
                    query,
                    [r.get("description", "") for r in search_results[:limit]],
                    top_k=limit,
                )
                new_order = [item["index"] for item in reranked]
                return [search_results[i] for i in new_order if i < len(search_results)]

        elif retrieval_mode == "vector":
            vector_results = await semantic.search_vector(query, limit=limit)
            return [
                {"url": r["url"], "title": r["title"], "description": ""}
                for r in vector_results
            ]

        elif retrieval_mode == "hybrid_vector":
            vector_results = await semantic.search_vector(query, limit=limit)
            seen: set[str] = set()
            merged: list[dict] = []
            for r in search_results[:limit]:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    merged.append(r)
            for r in vector_results:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    merged.append(
                        {"url": r["url"], "title": r["title"], "description": ""}
                    )
            return merged[:limit]

        return search_results
    finally:
        await semantic.close()
        await scraper.close()


async def run_answer(
    query: str,
    num_sources: int = 5,
    search_type: str = "auto",
    retrieval_mode: str = "keyword",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
    requested_model: str | None = None,
) -> dict:
    """Run a grounded Q&A pipeline: search → scrape → LLM → citations.

    Returns a dict with keys: answer, sources (list of dicts), citations (list of dicts),
    search_type, latency_ms.
    """
    import time

    start = time.monotonic()

    searxng = SearXNGClient(searxng_url)
    scraper = ScraperClient(scraper_url)
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        # Step 1: Search (fetch extra results to allow for scrape failures)
        logger.info("Answer: searching for: %s", query)
        search_results, _health = await searxng.search(query, limit=num_sources * 2)

        if retrieval_mode != "keyword":
            search_results = await _rerank_answer_sources(
                search_results,
                query,
                retrieval_mode,
                semantic_url,
                scraper_url,
                num_sources,
            )

        target_urls = [r["url"] for r in search_results if r.get("url")]

        # Step 2: Scrape (prefer text sources, use video platforms as fallback)
        preferred = [u for u in target_urls if not _is_video_platform_url(u)]
        deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

        if deprioritized:
            logger.info(
                "Answer: %d preferred + %d video-platform URLs (deprioritized)",
                len(preferred),
                len(deprioritized),
            )

        documents, source_details = await _scrape_urls(
            preferred,
            scraper,
            min_sources=num_sources,
            max_attempts=num_sources * 2,
        )

        if len(documents) < num_sources and deprioritized:
            logger.info(
                "Answer: %d/%d from preferred sources, falling back to video-platform URLs",
                len(documents),
                num_sources,
            )
            remaining = num_sources - len(documents)
            more_docs, more_details = await _scrape_urls(
                deprioritized,
                scraper,
                min_sources=remaining,
                max_attempts=remaining * 2,
            )
            documents.extend(more_docs)
            source_details.extend(more_details)

        # Step 3: Build context with source markers
        context_parts = []
        for i, (doc, detail) in enumerate(zip(documents, source_details), start=1):
            url = detail["url"]
            title = next(
                (r.get("title", "") for r in search_results if r.get("url") == url), ""
            )
            context_parts.append(f"[{i}] Source: {url}\nTitle: {title}\n\n{doc}")

        context = "\n\n---\n\n".join(context_parts) if context_parts else ""

        if not context:
            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "answer": "I was unable to find or scrape any relevant web pages to answer your question.",
                "sources": [],
                "citations": [],
                "search_type": search_type,
                "latency_ms": elapsed,
            }

        # Step 4: Build source_map for citation resolution
        source_map: list[dict[str, str]] = []
        for r in search_results:
            if r.get("url") in [s["url"] for s in source_details]:
                source_map.append(
                    {
                        "url": r["url"],
                        "title": r.get("title", ""),
                        "relevance": r.get("description", ""),
                    }
                )

        # Step 5: Call LLM
        user_prompt = (
            f"Answer the following question using ONLY the sources provided above.\n\n"
            f"Question: {query}\n\n"
            f"Cite sources using [1], [2], etc. corresponding to the source numbers above."
        )
        answer = await llm.generate(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            context=context,
        )

        # Step 6: Parse citations [N] from the answer
        citations: list[dict] = []
        seen_indices: set[int] = set()
        import re

        for match in re.finditer(r"\[(\d+)\]", answer):
            idx = int(match.group(1))
            if idx not in seen_indices and 1 <= idx <= len(source_map):
                seen_indices.add(idx)
                citations.append({"index": idx, "url": source_map[idx - 1]["url"]})

        elapsed = int((time.monotonic() - start) * 1000)

        return {
            "answer": answer,
            "sources": source_map,
            "citations": citations,
            "search_type": search_type,
            "latency_ms": elapsed,
        }
    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


async def run_answer_stream(
    query: str,
    num_sources: int = 5,
    search_type: str = "auto",
    retrieval_mode: str = "keyword",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    semantic_url: str = "http://semantic-svc:8003",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
    requested_model: str | None = None,
):
    """Streaming version of run_answer. Yields SSE-suitable dicts.

    Yields:
      {"type": "sources", "sources": [...]} — source list (sent once before tokens)
      {"type": "token", "content": "..."} — individual tokens from the LLM
      {"type": "done", "answer": "...", "citations": [...], "latency_ms": N} — final
      {"type": "error", "content": "..."} — error
    """
    import time

    start = time.monotonic()

    searxng = SearXNGClient(searxng_url)
    scraper = ScraperClient(scraper_url)
    effective_model = (
        requested_model
        if requested_model and requested_model != "default"
        else llm_model
    )
    llm = LLMClient(llm_base_url, llm_api_key, effective_model)

    try:
        # Step 1: Search (fetch extra results to allow for scrape failures)
        logger.info("Answer (stream): searching for: %s", query)
        search_results, _health = await searxng.search(query, limit=num_sources * 2)

        if retrieval_mode != "keyword":
            search_results = await _rerank_answer_sources(
                search_results,
                query,
                retrieval_mode,
                semantic_url,
                scraper_url,
                num_sources,
            )
        target_urls = [r["url"] for r in search_results if r.get("url")]

        # Yield pending sources for progress visibility
        pending_sources = [
            {
                "url": r["url"],
                "title": r.get("title", ""),
                "relevance": r.get("description", ""),
            }
            for r in search_results
            if r.get("url")
        ]
        yield {"type": "sources_pending", "sources": pending_sources}

        # Step 2: Scrape (prefer text sources, use video platforms as fallback)
        preferred = [u for u in target_urls if not _is_video_platform_url(u)]
        deprioritized = [u for u in target_urls if _is_video_platform_url(u)]

        if deprioritized:
            logger.info(
                "Answer (stream): %d preferred + %d video-platform URLs (deprioritized)",
                len(preferred),
                len(deprioritized),
            )

        documents, source_details = await _scrape_urls(
            preferred,
            scraper,
            min_sources=num_sources,
            max_attempts=num_sources * 2,
        )

        if len(documents) < num_sources and deprioritized:
            logger.info(
                "Answer (stream): %d/%d from preferred sources, falling back to video-platform URLs",
                len(documents),
                num_sources,
            )
            remaining = num_sources - len(documents)
            more_docs, more_details = await _scrape_urls(
                deprioritized,
                scraper,
                min_sources=remaining,
                max_attempts=remaining * 2,
            )
            documents.extend(more_docs)
            source_details.extend(more_details)

        # Step 3: Build context
        context_parts = []
        source_map: list[dict[str, str]] = []
        for i, (doc, detail) in enumerate(zip(documents, source_details), start=1):
            url = detail["url"]
            title = next(
                (r.get("title", "") for r in search_results if r.get("url") == url), ""
            )
            context_parts.append(f"[{i}] Source: {url}\nTitle: {title}\n\n{doc}")
            source_map.append(
                {
                    "url": url,
                    "title": title,
                    "relevance": next(
                        (
                            r.get("description", "")
                            for r in search_results
                            if r.get("url") == url
                        ),
                        "",
                    ),
                }
            )

        context = "\n\n---\n\n".join(context_parts) if context_parts else ""

        if not context:
            yield {"type": "sources", "sources": []}
            yield {
                "type": "done",
                "answer": "No relevant web pages found.",
                "citations": [],
                "latency_ms": int((time.monotonic() - start) * 1000),
            }
            return

        # Yield sources before streaming tokens
        yield {"type": "sources", "sources": source_map}

        # Step 4: Stream LLM response
        user_prompt = (
            f"Answer the following question using ONLY the sources provided above.\n\n"
            f"Question: {query}\n\n"
            f"Cite sources using [1], [2], etc. corresponding to the source numbers above."
        )
        full_answer = ""
        async for event in llm.generate_stream(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            context=context,
        ):
            if event["type"] == "token":
                full_answer += event["content"]
                yield {"type": "token", "content": event["content"]}
            elif event["type"] == "error":
                yield {"type": "error", "content": event["content"]}
                return
            elif event["type"] == "done":
                full_answer = event["full_content"]

        # Step 5: Parse citations
        import re

        citations: list[dict] = []
        seen_indices: set[int] = set()
        for match in re.finditer(r"\[(\d+)\]", full_answer):
            idx = int(match.group(1))
            if idx not in seen_indices and 1 <= idx <= len(source_map):
                seen_indices.add(idx)
                citations.append({"index": idx, "url": source_map[idx - 1]["url"]})

        elapsed = int((time.monotonic() - start) * 1000)
        yield {
            "type": "done",
            "answer": full_answer,
            "citations": citations,
            "latency_ms": elapsed,
        }

    finally:
        await searxng.close()
        await scraper.close()
        await llm.close()


RICH_SEARCH_SYSTEM_PROMPT = """You are a search result enrichment engine. Your job is to take raw web search results
and produce improved output without inventing information.

Given a list of search results (each with url, title, and full page content), produce
enriched results by:

1. Writing a longer, more informative description for each result — 2-3 sentences that
   capture the key information from the page content relevant to the search query.

2. If an output_schema is provided, extract structured data from the page content
   matching the schema. Each extracted field must be grounded in the source content.
   Include a grounding field mapping each output field to the source URL.

Do NOT:
- Change URLs or titles
- Invent information not present in the page content
- Omit results — every result gets a description
- Return markdown formatting in descriptions (plain text only)

When system_prompt is provided, use it to guide your preferences (source selection,
recency, strictness)."""


async def run_rich_search(
    search_results: list[dict],
    query: str,
    limit: int = 5,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    scraper_url: str = "http://scraper-svc:8001",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_api_key: str = "",
    llm_model: str = "gpt-4o-mini",
) -> dict[str, Any] | None:
    """Enrich search results with scraped content and optional structured extraction.

    Returns dict with keys: content (structured data) and grounding (citations),
    or None if no results could be enriched.
    """
    import time

    start = time.monotonic()
    scraper = ScraperClient(scraper_url)
    llm = LLMClient(llm_base_url, llm_api_key, llm_model)

    try:
        top_results = search_results[:limit]

        # Scrape top results
        enriched = []
        for r in top_results:
            url = r.get("url", "")
            if not url:
                continue
            try:
                resp = await scraper.scrape(url)
                if resp.get("success") and resp.get("data", {}).get("markdown"):
                    content = resp["data"]["markdown"][:3000]  # Trim to 3K chars
                    enriched.append(
                        {
                            "url": url,
                            "title": r.get("title", ""),
                            "content": content,
                        }
                    )
                else:
                    enriched.append(
                        {
                            "url": url,
                            "title": r.get("title", ""),
                            "content": r.get("description", ""),
                        }
                    )
            except Exception:
                enriched.append(
                    {
                        "url": url,
                        "title": r.get("title", ""),
                        "content": r.get("description", ""),
                    }
                )

        if not enriched:
            return None

        # Build context for LLM
        context_parts = []
        for i, item in enumerate(enriched, start=1):
            context_parts.append(
                f"[{i}] URL: {item['url']}\nTitle: {item['title']}\nContent: {item['content']}"
            )

        context = "\n\n---\n\n".join(context_parts)

        # Build prompt
        prompt_parts = [
            f"Search query: {query}",
            f"\nSearch results with full content:\n\n{context}",
        ]

        if output_schema:
            schema_json = json.dumps(output_schema, indent=2)
            prompt_parts.append(
                f"\nExtract structured data matching this JSON Schema:\n```json\n{schema_json}\n```"
            )

        prompt = "\n".join(prompt_parts)

        # LLM call for enrichment or structured extraction
        effective_system = system_prompt or RICH_SEARCH_SYSTEM_PROMPT
        content = await llm.generate(
            system_prompt=effective_system,
            user_prompt=prompt,
        )

        result: dict[str, Any] = {}

        if output_schema:
            # Try to parse structured JSON from the response
            try:
                parsed = json.loads(content)
                result["content"] = parsed
            except json.JSONDecodeError:
                # Extract JSON block if wrapped in markdown
                if "```json" in content:
                    block = content.split("```json")[1].split("```")[0].strip()
                    try:
                        result["content"] = json.loads(block)
                    except json.JSONDecodeError:
                        result["content"] = content
                else:
                    result["content"] = content

            # Build grounding citations
            grounding = []
            for item in enriched:
                grounding.append(
                    {
                        "url": item["url"],
                        "title": item["title"],
                    }
                )
            result["grounding"] = grounding
        else:
            # Enrichment mode: parse the improved descriptions
            result["content"] = content
            result["grounding"] = [
                {"url": item["url"], "title": item["title"]} for item in enriched
            ]

        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("Rich search completed in %dms for query: %s", elapsed, query)

        return result

    finally:
        await scraper.close()
        await llm.close()
