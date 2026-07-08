"""Session state machine for multi-step agent research.

SessionManager orchestrates the session lifecycle: create, step (search,
scrape, query), export, and delete.  Each step accumulates results into
a server-side artifact tree backed by ``SessionStore``.
"""

import logging
from typing import Any

from .llm import LLMClient
from .scraper_client import ScraperClient
from .searxng_client import SearXNGClient
from .session_store import SessionStore

logger = logging.getLogger(__name__)

# ── Session query system prompt ────────────────────────────────

SESSION_QUERY_SYSTEM_PROMPT = """You are GroktoCrawl, a session-aware research agent. Your
job is to answer questions using the accumulated research context in this session.

RULES:
- Base your answer ONLY on the accumulated session artifact and refs provided below.
- Cite sources using [ref_N_M] markers where N is the step index and M is the source index.
- If the session context doesn't contain enough information, say so clearly and suggest
  what kind of data would help (e.g., "search for X" or "scrape ref_2_3").
- Be concise but thorough. Lead with the direct answer, then add supporting detail.
- Use clean markdown formatting.
- Do not fabricate information or invent sources."""


class SessionManager:
    """State machine for multi-step research sessions.

    Coordinates session storage, action execution, and artifact
    accumulation.  Uses the existing search, scrape, and LLM
    pipeline functions.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 3600,
    ):
        self.store = SessionStore(redis_url=redis_url, default_ttl=default_ttl)
        self.default_ttl = default_ttl

    # ── Lifecycle ───────────────────────────────────────────────

    async def create_session(self, ttl: int | None = None) -> str:
        """Create a new research session.

        Args:
            ttl: Session TTL in seconds.  Defaults to 1 hour.

        Returns:
            The new session ID.
        """
        return self.store.create(ttl=ttl)

    async def get_session(self, session_id: str) -> dict | None:
        """Get session metadata + step summaries (no full refs)."""
        return self.store.get(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all associated data."""
        return self.store.delete(session_id)

    # ── Step Execution ──────────────────────────────────────────

    async def step(
        self,
        session_id: str,
        action: str,
        params: dict[str, Any],
        searxng_url: str = "http://searxng:8080",
        scraper_url: str = "http://scraper-svc:8001",
        llm_base_url: str = "https://api.openai.com/v1",
        llm_api_key: str = "",
        llm_model: str | None = None,
    ) -> dict:
        """Execute a step action and accumulate results into the session.

        Supported actions:
            - ``search``: Search via SearXNG, store results as refs.
            - ``scrape``: Scrape specific URLs, store content as refs.
            - ``query``: Run LLM over accumulated session context.
            - ``deepen``: Drill deeper into a cited source.  Searches for
              new sources based on the cited content + depth prompt, scrapes
              them, runs LLM synthesis, and stores findings as new refs.

        Args:
            session_id: The session to modify.
            action: One of ``search``, ``scrape``, ``query``, ``deepen``.
            params: Action-specific parameters (see below).

        Returns:
            A dict with ``step_index``, ``action``, ``summary``, and
            action-specific result fields.  Raises ``ValueError`` for
            unknown actions or missing sessions.
        """
        session = self.store.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Acquire per-session lock for concurrent step serialisation.
        # Uses async backoff so the FastAPI event loop is not blocked
        # while waiting for the lock (ADR-0040).
        # lease_ttl=120 covers long scrape steps (timeout=70s + overhead).
        owner_token = await self.store.acquire_lock(
            session_id, timeout=30, lease_ttl=120
        )
        if owner_token is None:
            raise ValueError(
                f"Session {session_id} is currently executing another step. "
                "Retry in a moment."
            )

        if llm_model is None:
            raise ValueError("llm_model is required — set via LLM_MODEL env var")

        try:
            result = await self._execute_step(
                session_id=session_id,
                action=action,
                params=params,
                searxng_url=searxng_url,
                scraper_url=scraper_url,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
            )
        finally:
            self.store.release_lock(session_id, owner_token)

        return result

    async def _execute_step(
        self,
        session_id: str,
        action: str,
        params: dict[str, Any],
        searxng_url: str = "http://searxng:8080",
        scraper_url: str = "http://scraper-svc:8001",
        llm_base_url: str = "https://api.openai.com/v1",
        llm_api_key: str = "",
        llm_model: str | None = None,
    ) -> dict:
        """Internal dispatch for step actions (called under lock)."""
        if llm_model is None:
            raise ValueError("llm_model is required — set via LLM_MODEL env var")
        if action == "search":
            result = await self._step_search(
                session_id=session_id,
                params=params,
                searxng_url=searxng_url,
            )
        elif action == "scrape":
            result = await self._step_scrape(
                session_id=session_id,
                params=params,
                scraper_url=scraper_url,
            )
        elif action == "query":
            result = await self._step_query(
                session_id=session_id,
                params=params,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
            )
        elif action == "deepen":
            result = await self._step_deepen(
                session_id=session_id,
                params=params,
                searxng_url=searxng_url,
                scraper_url=scraper_url,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
            )
        else:
            raise ValueError(
                f"Unknown action: {action!r}. Supported: search, scrape, query, deepen"
            )

        return result

    async def resolve_ref(self, session_id: str, ref_id: str) -> dict | None:
        """Resolve a single reference ID to its full source content.

        Returns the full ref data including ``url``, ``title``, ``markdown``,
        ``source``, ``char_count``, and ``scraped_at``.  Returns ``None`` if
        the ref or session does not exist.
        """
        return self.store.get_ref(session_id, ref_id)

    async def resolve_refs(
        self, session_id: str, ref_ids: list[str]
    ) -> dict[str, dict]:
        """Resolve multiple reference IDs to their full source content.

        Returns a dict mapping ``ref_id`` → ``ref_data``.  Missing refs are
        silently omitted (the caller can detect gaps by comparing requested
        vs returned keys).
        """
        result: dict[str, dict] = {}
        for ref_id in ref_ids:
            ref_data = self.store.get_ref(session_id, ref_id)
            if ref_data is not None:
                result[ref_id] = ref_data
        return result

    async def export_session(self, session_id: str) -> dict:
        """Export the accumulated session artifact as markdown.

        Returns a dict with ``artifact`` (full markdown), ``steps``
        (step history), ``refs`` (all reference metadata), and
        ``session_id``.
        """
        session = self.store.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        artifact = self.store.get_artifact(session_id)
        steps = self.store.get_steps(session_id)
        refs = self.store.get_refs(session_id)

        # Build a compact refs view (URLs + titles only, no full content)
        compact_refs: dict[str, dict[str, str]] = {}
        for ref_id, ref_data in refs.items():
            char_count = ref_data.get("char_count", 0)
            compact_refs[ref_id] = {
                "url": ref_data.get("url", ""),
                "title": ref_data.get("title", ""),
                "char_count": str(char_count),
            }

        return {
            "session_id": session_id,
            "artifact": artifact,
            "steps": steps,
            "refs": compact_refs,
            "artifact_length": len(artifact),
        }

    # ── Step Implementations ────────────────────────────────────

    async def _step_search(
        self,
        session_id: str,
        params: dict[str, Any],
        searxng_url: str,
    ) -> dict:
        """Execute a search step: SearXNG → store results as refs."""
        query = params.get("query", "")
        limit = params.get("limit", 10)
        sources = params.get("sources")
        categories = params.get("categories")

        if not query:
            raise ValueError("search action requires a 'query' parameter")

        searxng = SearXNGClient(searxng_url)
        try:
            results, _health = await searxng.search(
                query=query,
                limit=limit,
                sources=sources,
                categories=categories,
            )
        finally:
            await searxng.close()

        step_index = self.store.append_step(
            session_id,
            {
                "action": "search",
                "params": {"query": query, "limit": limit},
                "summary": f"Search: {query} ({len(results)} results)",
            },
        )
        if step_index is None:
            raise ValueError(f"Session not found: {session_id}")

        # Store search results as references
        top_urls: list[dict[str, str]] = []
        ref_count = 0
        for i, r in enumerate(results):
            url = r.get("url", "")
            if not url:
                continue
            ref_id = f"ref_{step_index}_{i + 1}"
            ref_data = {
                "url": url,
                "title": r.get("title", ""),
                "markdown": r.get("description", ""),
                "scraped_at": "",
                "source": "search",
                "relevance": r.get("description", ""),
                "char_count": len(r.get("description", "")),
            }
            self.store.add_ref(session_id, ref_id, ref_data)
            top_urls.append({"ref_id": ref_id, "url": url, "title": r.get("title", "")})
            ref_count += 1

        # Append search results section to artifact
        section = f"\n\n## Step {step_index}: Search — {query}\n\n"
        for r in top_urls[:10]:
            section += f"- [{r['title'] or r['url']}]({r['url']}) `{r['ref_id']}`\n"
        section += f"\n*{ref_count} results stored as references.*\n"
        self.store.append_artifact(session_id, section)

        return {
            "step_index": step_index,
            "action": "search",
            "query": query,
            "ref_count": ref_count,
            "top_refs": top_urls[:10],
            "summary": f"Search '{query}' returned {ref_count} results, stored as refs {step_index}_1 through {step_index}_{ref_count}",
        }

    async def _step_scrape(
        self,
        session_id: str,
        params: dict[str, Any],
        scraper_url: str,
    ) -> dict:
        """Execute a scrape step: fetch URLs → store content as refs."""
        urls = params.get("urls", [])
        scrape_options = params.get("scrape_options")

        if not urls:
            raise ValueError("scrape action requires a 'urls' parameter (list of URLs)")

        scraper = ScraperClient(scraper_url)
        try:
            import asyncio

            semaphore = asyncio.Semaphore(3)
            scraped: list[dict] = []

            async def _scrape_one(url: str) -> dict | None:
                async with semaphore:
                    try:
                        result = await asyncio.wait_for(
                            scraper.scrape_with_fallback(
                                url, scrape_options=scrape_options
                            ),
                            timeout=70,
                        )
                        if result.get("success") and result.get("data", {}).get(
                            "markdown"
                        ):
                            return {
                                "url": url,
                                "markdown": result["data"]["markdown"],
                                "source": result["data"].get("source", "unknown"),
                                "char_count": len(result["data"]["markdown"]),
                            }
                        return None
                    except Exception as e:
                        logger.warning("Scrape failed for %s: %s", url, e)
                        return None

            tasks = [_scrape_one(url) for url in urls]
            results = await asyncio.gather(*tasks)
            scraped = [r for r in results if r is not None]
        finally:
            await scraper.close()

        step_index = self.store.append_step(
            session_id,
            {
                "action": "scrape",
                "params": {"urls": urls},
                "summary": f"Scrape: {len(scraped)}/{len(urls)} URLs succeeded",
            },
        )
        if step_index is None:
            raise ValueError(f"Session not found: {session_id}")

        # Store scraped content as references
        refs_added: list[dict[str, str | int]] = []
        for i, s in enumerate(scraped):
            ref_id = f"ref_{step_index}_{i + 1}"
            ref_data = {
                "url": s["url"],
                "title": s.get("title", s["url"]),
                "markdown": s["markdown"],
                "scraped_at": "",
                "source": s.get("source", "unknown"),
                "char_count": s["char_count"],
            }
            self.store.add_ref(session_id, ref_id, ref_data)
            refs_added.append(
                {"ref_id": ref_id, "url": s["url"], "char_count": s["char_count"]}
            )

        # Append scrape results section to artifact
        section = f"\n\n## Step {step_index}: Scrape — {len(scraped)} URLs\n\n"
        for r in refs_added:
            url = r["url"]
            section += (
                f"### Source: {url}\n\n"
                f"> Reference: `{r['ref_id']}` | {r['char_count']} chars\n\n"
            )
            # Include first 500 chars of each scraped doc as preview
            for s in scraped:
                if s["url"] == url:
                    section += s["markdown"][:500] + "...\n\n"
                    break
        self.store.append_artifact(session_id, section)

        total_chars = sum(s["char_count"] for s in scraped)
        return {
            "step_index": step_index,
            "action": "scrape",
            "ref_count": len(scraped),
            "refs": refs_added,
            "char_count": total_chars,
            "succeeded": len(scraped),
            "failed": len(urls) - len(scraped),
            "summary": f"Scraped {len(scraped)}/{len(urls)} URLs, stored as refs {step_index}_1 through {step_index}_{len(scraped)}",
        }

    async def _step_query(
        self,
        session_id: str,
        params: dict[str, Any],
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
    ) -> dict:
        """Execute a query step: LLM over accumulated session context."""
        question = params.get("question", "")
        model = params.get("model", llm_model)

        if not question:
            raise ValueError("query action requires a 'question' parameter")

        # Gather accumulated context
        artifact = self.store.get_artifact(session_id)
        refs = self.store.get_refs(session_id)

        if not artifact and not refs:
            raise ValueError(
                "Session has no accumulated context. Run a search or scrape step first."
            )

        # Build context with ref summaries (not full content — too large)
        context_parts = [f"## Accumulated Research\n\n{artifact}\n\n"]
        context_parts.append("## Reference Index\n\n")
        for ref_id, ref_data in refs.items():
            url = ref_data.get("url", "")
            title = ref_data.get("title", "")
            chars = ref_data.get("char_count", 0)
            context_parts.append(
                f"- `{ref_id}`: [{title or url}]({url}) ({chars} chars)\n"
            )
        context = "".join(context_parts)

        effective_model = model if model != "default" else llm_model
        llm = LLMClient(llm_base_url, llm_api_key, effective_model)
        try:
            answer = await llm.generate(
                system_prompt=SESSION_QUERY_SYSTEM_PROMPT,
                user_prompt=question,
                context=context,
            )
        finally:
            await llm.close()

        step_index = self.store.append_step(
            session_id,
            {
                "action": "query",
                "params": {"question": question},
                "summary": f"Query: {question[:80]}...",
            },
        )
        if step_index is None:
            raise ValueError(f"Session not found: {session_id}")

        # Append query + answer to artifact
        section = (
            f"\n\n## Step {step_index}: Query\n\n**Q:** {question}\n\n**A:** {answer}\n"
        )
        self.store.append_artifact(session_id, section)

        return {
            "step_index": step_index,
            "action": "query",
            "question": question,
            "answer": answer,
            "ref_count": len(refs),
            "summary": f"Query answered ({len(answer)} chars), {len(refs)} refs available",
        }

    async def _step_deepen(
        self,
        session_id: str,
        params: dict[str, Any],
        searxng_url: str,
        scraper_url: str,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
    ) -> dict:
        """Execute a deepen step: drill deeper into a cited source.

        1. Finds the cited source in the session's accumulated content.
        2. Generates a targeted search query based on sub_topic + source.
        3. Searches for new sources (skipping already-scraped URLs).
        4. Scrapes new sources.
        5. Runs LLM to synthesise findings.
        6. Returns new findings with insertion point reference.

        Args:
            session_id: The session to modify.
            params: Must contain ``ref_id`` (citation ref from session, e.g.,
                ``ref_2_3``) and ``sub_topic`` (follow-up question).
                Optional ``max_sources`` (default 3, max 10).

        Returns:
            A dict with ``step_index``, ``action``, ``new_findings``,
            ``new_sources``, ``inserted_at``, and ``summary``.

        Raises:
            ValueError: If ``ref_id`` or ``sub_topic`` is missing, the
                referenced source is not found, or the session doesn't exist.
        """
        ref_id = params.get("ref_id", "")
        sub_topic = params.get("sub_topic", "")
        max_sources = params.get("max_sources", 3)

        if not ref_id:
            raise ValueError(
                "deepen action requires a 'ref_id' parameter (citation reference)"
            )
        if not sub_topic:
            raise ValueError("deepen action requires a 'sub_topic' parameter")

        # Clamp max_sources
        max_sources = max(1, min(max_sources, 10))

        # 1. Find the cited source in the session's refs
        ref_data = self.store.get_ref(session_id, ref_id)
        if ref_data is None:
            raise ValueError(
                f"Citation reference {ref_id!r} not found in session {session_id}. "
                f"Available refs: {list(self.store.get_refs(session_id).keys())}"
            )

        source_url = ref_data.get("url", "")
        source_title = ref_data.get("title", "")
        source_markdown = ref_data.get("markdown", "")
        if not source_markdown:
            raise ValueError(
                f"Reference {ref_id!r} has no scraped content. "
                f"Scrape the URL ({source_url}) before deepening."
            )

        # 2. Generate a targeted search query
        # Build context from the source content (truncated for LLM efficiency)
        source_context = source_markdown[:3000]
        query_prompt = (
            f"Based on the following source content and the user's follow-up question, "
            f"generate 2-3 highly specific web search queries to find additional "
            f"information that deepens the investigation.\n\n"
            f"SOURCE ({source_title or source_url}):\n{source_context}\n\n"
            f"FOLLOW-UP QUESTION: {sub_topic}\n\n"
            f"Return ONLY the search queries, one per line. No other text."
        )

        searxng = SearXNGClient(searxng_url)
        try:
            # 3. Search for new sources
            # First, use the sub_topic directly for the primary search
            primary_results, _health = await searxng.search(
                sub_topic, limit=max_sources
            )

            # Collect existing URLs to skip duplicates
            existing_refs = self.store.get_refs(session_id)
            existing_urls: set[str] = set()
            for _, rd in existing_refs.items():
                url = rd.get("url", "")
                if url:
                    existing_urls.add(url)

            # Filter out already-scraped URLs
            new_urls: list[dict] = []
            for r in primary_results:
                url = r.get("url", "")
                if url and url not in existing_urls:
                    new_urls.append(r)
                    existing_urls.add(url)

            # If not enough new URLs found, try generating query-based search
            if len(new_urls) < max_sources:
                llm_query = LLMClient(llm_base_url, llm_api_key, llm_model)
                try:
                    gen_response = await llm_query.generate(
                        system_prompt="You generate precise web search queries. Return only the queries, one per line.",
                        user_prompt=query_prompt,
                    )
                    # Parse queries from response
                    gen_queries = [
                        q.strip()
                        for q in gen_response.splitlines()
                        if q.strip() and not q.strip().startswith(("#", "//"))
                    ]
                    # Search each generated query for more URLs
                    for gq in gen_queries[:3]:
                        if len(new_urls) >= max_sources:
                            break
                        try:
                            gq_results, _gh = await searxng.search(gq, limit=3)
                            for r in gq_results:
                                url = r.get("url", "")
                                if url and url not in existing_urls:
                                    new_urls.append(r)
                                    existing_urls.add(url)
                                    if len(new_urls) >= max_sources:
                                        break
                        except Exception:
                            continue
                finally:
                    await llm_query.close()

        finally:
            await searxng.close()

        # 4. Scrape new sources
        scraper = ScraperClient(scraper_url)
        try:
            import asyncio

            semaphore = asyncio.Semaphore(3)
            scraped: list[dict] = []

            async def _scrape_one(url: str) -> dict | None:
                async with semaphore:
                    try:
                        result = await asyncio.wait_for(
                            scraper.scrape_with_fallback(url),
                            timeout=70,
                        )
                        if result.get("success") and result.get("data", {}).get(
                            "markdown"
                        ):
                            return {
                                "url": url,
                                "markdown": result["data"]["markdown"],
                                "source": result["data"].get("source", "unknown"),
                                "char_count": len(result["data"]["markdown"]),
                            }
                        return None
                    except Exception as e:
                        logger.warning("Deepen scrape failed for %s: %s", url, e)
                        return None

            urls_to_scrape = [r["url"] for r in new_urls[:max_sources]]
            tasks = [_scrape_one(url) for url in urls_to_scrape]
            results = await asyncio.gather(*tasks)
            scraped = [r for r in results if r is not None]
        finally:
            await scraper.close()

        # 5. Run LLM to synthesise findings
        if scraped:
            new_context_parts = [s["markdown"] for s in scraped]
            new_context = "\n\n---\n\n".join(new_context_parts)

            synthesis_prompt = (
                f"ORIGINAL SOURCE ({ref_id}):\n{source_context}\n\n"
                f"NEW SOURCES (deep-dive results):\n{new_context}\n\n"
                f"DEPTH PROMPT: {sub_topic}\n\n"
                f"Synthesise the new findings. Focus on what NEW information the "
                f"deep-dive sources add beyond the original source. Be specific and "
                f"cite sources by URL. Format in clean markdown."
            )

            llm_synth = LLMClient(llm_base_url, llm_api_key, llm_model)
            try:
                new_findings = await llm_synth.generate(
                    system_prompt=(
                        "You are GroktoCrawl, a deep-research agent. Synthesise new "
                        "findings from deep-dive sources. Focus on novelty — what "
                        "does the new research add? Be concise, specific, and cite "
                        "sources by URL."
                    ),
                    user_prompt=synthesis_prompt,
                )
            finally:
                await llm_synth.close()
        else:
            new_findings = (
                f"No new sources could be discovered or scraped for the "
                f"deep-dive on {ref_id!r} with prompt: {sub_topic}"
            )

        # Store findings
        step_index = self.store.append_step(
            session_id,
            {
                "action": "deepen",
                "params": {
                    "ref_id": ref_id,
                    "sub_topic": sub_topic,
                    "max_sources": max_sources,
                },
                "summary": f"Deepen: {ref_id!r} — {sub_topic[:80]}...",
            },
        )
        if step_index is None:
            raise ValueError(f"Session not found: {session_id}")

        # Store new sources as refs
        new_refs: list[dict] = []
        for i, s in enumerate(scraped):
            new_ref_id = f"ref_{step_index}_{i + 1}"
            ref_data_entry = {
                "url": s["url"],
                "title": s.get("title", s["url"]),
                "markdown": s["markdown"],
                "scraped_at": "",
                "source": s.get("source", "unknown"),
                "char_count": s["char_count"],
            }
            self.store.add_ref(session_id, new_ref_id, ref_data_entry)
            new_refs.append(
                {"ref_id": new_ref_id, "url": s["url"], "char_count": s["char_count"]}
            )

        # Append deepen findings to artifact
        section = (
            f"\n\n## Step {step_index}: Deepen — {ref_id}\n\n"
            f"**Sub-topic:** {sub_topic}\n\n"
            f"{new_findings}\n\n"
            f"*Deeper sources:*\n"
        )
        for r in new_refs:
            section += f"- [{r['url']}]({r['url']}) `{r['ref_id']}` ({r['char_count']} chars)\n"
        section += "\n"
        self.store.append_artifact(session_id, section)

        inserted_at = ref_id  # Findings are inserted after the cited source

        # Build new_sources list for the response
        new_sources_list = [
            {"url": r["url"], "title": r.get("title", r["url"]), "ref_id": r["ref_id"]}
            for r in new_refs
        ]

        return {
            "step_index": step_index,
            "action": "deepen",
            "new_findings": new_findings,
            "new_sources": new_sources_list,
            "inserted_at": inserted_at,
            "ref_id": ref_id,
            "summary": f"Deepen on {ref_id!r}: {len(scraped)} new sources, {len(new_findings)} chars of findings",
        }
