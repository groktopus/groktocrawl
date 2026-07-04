"""OpenAI-compatible LLM client.

Works with any OpenAI-compatible API: OpenAI, Anthropic, OpenRouter,
Ollama, llama.cpp, vLLM, etc.
"""

import json
import logging
from collections.abc import AsyncGenerator

import httpx

from .settings import load_settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Client for any OpenAI-compatible LLM API."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=120)

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        context: str | None = None,
    ) -> AsyncGenerator[dict[str, str], None]:
        """Generate a streaming response from the LLM (SSE).

        Yields dicts with keys:
          - {"type": "token", "content": str} — a single token
          - {"type": "done", "full_content": str} — final complete text
          - {"type": "error", "content": str} — error message

        Args:
            system_prompt: System-level instructions.
            user_prompt: The user's task/question.
            context: Optional scraped context to include.
        """
        messages = [{"role": "system", "content": system_prompt}]

        if context:
            messages.append(
                {
                    "role": "user",
                    "content": "Here is the information I gathered:\n\n"
                    f"{context}\n\nBased on this, {user_prompt}",
                }
            )
        else:
            messages.append({"role": "user", "content": user_prompt})

        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 8192,
            "stream": True,
        }

        # Only enable thinking/reasoning for providers that support it
        # (Anthropic/DeepSeek). Default is off; omit the param otherwise.
        _llm_settings = load_settings()
        if _llm_settings.llm_enable_thinking:
            body["enable_thinking"] = True

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        full_content = ""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                ) as resp:
                    if resp.status_code != 200:
                        error_text = await resp.aread()
                        logger.error(
                            "LLM API error %d: %s", resp.status_code, error_text[:500]
                        )
                        yield {
                            "type": "error",
                            "content": f"LLM API returned {resp.status_code}",
                        }
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            choices = chunk.get("choices", [{}])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                full_content += token
                                yield {"type": "token", "content": token}
                        except json.JSONDecodeError:
                            continue

            yield {"type": "done", "full_content": full_content}

        except Exception as e:
            logger.error("LLM stream call failed: %s", e)
            yield {"type": "error", "content": f"LLM call failed: {e}"}

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context: str | None = None,
        schema: dict | None = None,
    ) -> str:
        """Generate a response from the LLM.

        Args:
            system_prompt: System-level instructions.
            user_prompt: The user's task/question.
            context: Optional scraped context to include.
            schema: Optional JSON Schema for structured output.

        Returns:
            The LLM's response text.
        """
        messages = [{"role": "system", "content": system_prompt}]

        if context:
            messages.append(
                {
                    "role": "user",
                    "content": f"Here is the information I gathered:\n\n{context}\n\nBased on this, {user_prompt}",
                }
            )
        else:
            messages.append({"role": "user", "content": user_prompt})

        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 8192,
        }

        # Only enable thinking/reasoning for providers that support it
        # (Anthropic/DeepSeek). Default is off; omit the param otherwise.
        _llm_settings = load_settings()
        if _llm_settings.llm_enable_thinking:
            body["enable_thinking"] = True

        # If schema is provided, request structured JSON output
        if schema:
            body["response_format"] = {"type": "json_object"}
            # Inject schema into the system prompt
            messages[0]["content"] += (
                f"\n\nYou MUST respond with valid JSON matching this schema:\n"
                f"{json.dumps(schema, indent=2)}"
            )

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                logger.error("LLM API error %d: %s", resp.status_code, resp.text[:500])
                return f"Error: LLM API returned {resp.status_code}"

            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            return content  # type: ignore[no-any-return]

        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"Error: LLM call failed: {e}"

    async def check_health(self) -> bool:
        """Check if the LLM backend is reachable and responding.

        Sends a minimal request (max_tokens=1, stream=False) with a
        short 5s timeout. Returns True if the backend responds with
        HTTP 200, False otherwise. Never raises exceptions.
        """
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                if resp.status_code == 200:
                    return True
                logger.error(
                    "LLM health check failed: HTTP %d — %s",
                    resp.status_code,
                    resp.text[:500],
                )
                return False
        except Exception as e:
            logger.error("LLM health check failed: %s", e)
            return False

    async def close(self) -> None:
        await self._client.aclose()
