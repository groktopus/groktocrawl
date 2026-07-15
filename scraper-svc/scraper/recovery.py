"""LLM-assisted recovery tier for failed scrapes.

When all three standard tiers fail, the scraper can use an LLM to analyze
the failed response and suggest a recovery path — extract an iframe URL,
identify a bot challenge, or find an alternative access route.

Configured via env vars:
  LLM_BASE_URL      (default: http://llm-svc:4001/v1)
  LLM_API_KEY       (optional)
  LLM_MODEL         (default: gpt-4o-mini)
  RECOVERY_LLM_TIMEOUT (default: 15 — seconds)
"""

import json
import logging

import httpx

from .settings import load_settings

logger = logging.getLogger(__name__)

_rec_settings = load_settings()
RECOVERY_TIMEOUT = _rec_settings.recovery_llm_timeout
LLM_BASE_URL = _rec_settings.recovery_llm_base_url
LLM_API_KEY = _rec_settings.recovery_llm_api_key
LLM_MODEL = _rec_settings.recovery_llm_model

RECOVERY_SYSTEM_PROMPT = """You are a web scraping recovery assistant. The scraper tried to fetch a URL and got a page that is clearly not the target content.

Analyze what happened. Choose ONE of these actions:

(a) **iframe_url** — the real content is at an alternative URL visible in the page (e.g., iframe src, data-src, a download link). Extract the exact URL.
(b) **extracted_content** — the actual text/content is embedded in the page despite extraction failing. Return up to 2000 characters.
(c) **bot_challenge** — the page is a Cloudflare or similar bot challenge.
(d) **irrecoverable** — the fetch genuinely failed and there is no alternative path.

Note: This page was flagged for potentially having embedded document content
(iframes, embeds, or objects pointing to PDFs or documents). If you see an
iframe with a document URL, choose action (a) and extract the iframe's src.

Respond with valid JSON matching this schema:
{
  "action": "iframe_url" | "extracted_content" | "bot_challenge" | "irrecoverable",
  "url": "<if action is iframe_url, the extracted URL>",
  "content": "<if action is extracted_content, up to 2000 chars>",
  "challenge_type": "<if action is bot_challenge: cloudflare_js, cloudflare_captcha, other>",
  "reason": "<if action is irrecoverable: explanation>"
}"""

RECOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "iframe_url",
                "extracted_content",
                "bot_challenge",
                "irrecoverable",
            ],
        },
        "url": {"type": "string"},
        "content": {"type": "string"},
        "challenge_type": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}

# ── Cloudflare classification (last-resort analysis) ───────────

CLOUDFLARE_CLASSIFICATION_PROMPT = """We attempted to fetch {url} and all bypass methods failed. The page content suggests a Cloudflare block was encountered.

We tried:
1. Direct HTTP fetch
2. Playwright headless browser with stealth configuration
3. FlareSolverr (dedicated Cloudflare challenge solver)

All failed. Analyze what type of block we're facing.

Return a JSON object with these fields:
- block_type: "captcha" | "js_challenge" | "rate_limit" | "ip_block" | "unknown" | "not_cloudflare"
- confidence: "high" | "medium" | "low"
- page_indicators: list of strings identifying what was seen
- alternative_paths: list of objects with type, description, and url for alternative access (e.g., wayback machine, google cache)
- human_action_required: boolean — does this need a human to solve?
- message: plain language explanation"""


async def classify_cloudflare_block(url: str, page_text: str) -> dict | None:
    """Last-resort Cloudflare classification when all bypass attempts fail.

    This does NOT attempt to fix the fetch. It explains the failure so the
    caller can decide what to do (retry, try different route, give up).

    Returns a dict with classification metadata, or None on failure/timeout.
    """
    if not page_text:
        return None

    try:
        async with httpx.AsyncClient(timeout=RECOVERY_TIMEOUT) as client:
            body = {
                "model": LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": CLOUDFLARE_CLASSIFICATION_PROMPT.format(url=url),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"---PAGE CONTENT (first 3000 chars)---\n"
                            f"{page_text[:3000]}\n"
                            f"---END---"
                        ),
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
            }

            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY:
                headers["Authorization"] = f"Bearer {LLM_API_KEY}"

            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )

            if resp.status_code != 200:
                return None

            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            logger.info(
                "Cloudflare classification for %s: %s (confidence: %s)",
                url,
                parsed.get("block_type", "unknown"),
                parsed.get("confidence", "low"),
            )

            return {
                "markdown": "",
                "source": "llm-classification",
                "url": url,
                "error": parsed.get("message", "Cloudflare block encountered"),
                "classification": {
                    "block_type": parsed.get("block_type", "unknown"),
                    "confidence": parsed.get("confidence", "low"),
                    "human_action_required": parsed.get("human_action_required", False),
                    "alternative_paths": parsed.get("alternative_paths", []),
                    "page_indicators": parsed.get("page_indicators", []),
                },
            }

    except (httpx.TimeoutException, httpx.ConnectError):
        logger.debug("Cloudflare classification timed out or unavailable for %s", url)
        return None
    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("Cloudflare classification failed for %s: %s", url, e)
        return None


async def attempt_llm_recovery(url: str, page_text: str) -> dict | None:
    """Try to recover from a failed scrape using LLM analysis.

    Args:
        url: The original URL that was scraped.
        page_text: The content received (markdown or raw text, up to 4000 chars).

    Returns:
        A result dict if recovery succeeded, or None on failure/timeout.
    """
    if not page_text:
        logger.debug("LLM recovery skipped: no page content to analyze")
        return None

    try:
        async with httpx.AsyncClient(timeout=RECOVERY_TIMEOUT) as client:
            body = {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": RECOVERY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"URL fetched: {url}\n\n"
                            f"---PAGE CONTENT---\n"
                            f"{page_text}\n"
                            f"---END---"
                        ),
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
            }

            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY:
                headers["Authorization"] = f"Bearer {LLM_API_KEY}"

            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )

            if resp.status_code != 200:
                logger.warning(
                    "LLM recovery API error %d for %s", resp.status_code, url
                )
                return None

            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            action = parsed.get("action")

            logger.info("LLM recovery for %s: action=%s", url, action)

            if action == "iframe_url" and parsed.get("url"):
                extracted_url = parsed["url"]
                # Strip URL fragments (#...) to avoid issues with HTTP clients
                if "#" in extracted_url:
                    extracted_url = extracted_url.split("#")[0]
                logger.info("LLM recovery: retrying on extracted URL %s", extracted_url)
                from .fetch import smart_scrape

                return await smart_scrape(extracted_url)

            elif action == "extracted_content" and parsed.get("content"):
                return {
                    "markdown": parsed["content"],
                    "source": "llm-recovery",
                    "url": url,
                }

            elif action == "bot_challenge":
                return {
                    "error": f"Bot challenge detected: {parsed.get('challenge_type', 'unknown')}",
                    "markdown": "",
                    "source": "llm-recovery",
                    "url": url,
                }

            else:
                return None

    except (httpx.TimeoutException, httpx.ConnectError):
        logger.debug("LLM recovery timed out or unavailable for %s", url)
        return None
    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("LLM recovery failed for %s: %s", url, e)
        return None
