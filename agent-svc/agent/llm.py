"""OpenAI-compatible LLM client.

Works with any OpenAI-compatible API: OpenAI, Anthropic, OpenRouter,
Ollama, llama.cpp, vLLM, etc.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from common.stage_metrics import inc_counter, observe_elapsed

from .admission import AdmissionRejectedError, get_admission
from .cancel import JobCancelledError, raise_if_cancelled
from .exceptions import ProviderOutputError, RetryableRateLimitError
from .settings import load_settings

logger = logging.getLogger(__name__)

_LLM_CALL_SECONDS = "groktocrawl_llm_call_seconds"
_LLM_CALL_SECONDS_HELP = "LLM call latency by research stage"
_LLM_CALLS_TOTAL = "groktocrawl_llm_calls_total"
_LLM_CALLS_TOTAL_HELP = "Total LLM calls by research stage and outcome"
_MAX_RETRY_AFTER_SECONDS = 60


def _token_budget_params(*, health: bool = False) -> dict[str, int]:
    """Return the configured budget under exactly the selected provider field."""
    settings = load_settings()
    if health:
        budget = settings.llm_health_token_budget
    elif settings.llm_token_parameter == "max_completion_tokens":
        budget = settings.llm_max_completion_tokens
    else:
        budget = settings.llm_max_tokens
    return {settings.llm_token_parameter: budget}


def _completion_content(result: object) -> str:
    """Extract only a complete, non-refusal provider completion."""
    if not isinstance(result, dict):
        raise ProviderOutputError(detail="LLM provider returned an invalid response")
    try:
        choice = result["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason")
        if message.get("refusal") or finish_reason == "length":
            raise ProviderOutputError(
                detail="LLM provider returned an unusable response"
            )
        content = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderOutputError(
            detail="LLM provider returned an invalid response"
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ProviderOutputError(detail="LLM provider returned an invalid response")
    return content


def _parse_retry_after(value: str | None) -> float | None:
    """Parse and bound numeric upstream Retry-After metadata."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return max(1.0, min(seconds, _MAX_RETRY_AFTER_SECONDS))


class LLMClient:
    """Client for any OpenAI-compatible LLM API."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "",
        admission=None,
    ):
        if not model:
            raise ValueError(
                "model is required — set LLM_MODEL env var or pass model= explicitly"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._admission = admission if admission is not None else get_admission()
        self._client = httpx.AsyncClient(timeout=load_settings().llm_call_timeout)

    def _completion_url(self) -> str:
        """Append the endpoint while preserving optional fixture query params."""
        url = httpx.URL(self.base_url)
        path = url.path.rstrip("/") + "/chat/completions"
        return str(url.copy_with(path=path))

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        context: str | None = None,
        schema: dict | None = None,
        stage: str = "other",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate a streaming response from the LLM (SSE).

        When ``schema`` is provided, delegates to :meth:`generate` for a
        non-streaming call (structured output requires the full JSON to be
        valid before it can be returned).  Yields only a ``"done"`` event
        (or ``"error"``).

        When ``schema`` is ``None``, streams tokens as usual.

        Yields dicts with keys:
          - {"type": "token", "content": str} — a single token
          - {"type": "done", "full_content": str} — final complete text
          - {"type": "error", "content": str} — error message

        Args:
            system_prompt: System-level instructions.
            user_prompt: The user's task/question.
            context: Optional scraped context to include.
            schema: Optional JSON Schema for structured output.  When
                provided, the entire generation is performed non-streaming
                and returned as a single ``"done"`` event.
            stage: Bounded stage identifier for latency telemetry.
        """
        # Schema mode: delegate to generate() non-streaming
        if schema:
            try:
                content = await self.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    context=context,
                    schema=schema,
                    stage=stage,
                )
            except RetryableRateLimitError as exc:
                yield {
                    "type": "error",
                    "classification": "retryable",
                    "retry_after_seconds": exc.retry_after_seconds,
                    "content": exc.detail,
                }
            except ProviderOutputError as exc:
                yield {
                    "type": "error",
                    "classification": "non_retryable",
                    "content": exc.detail,
                }
            else:
                yield {"type": "done", "full_content": content}
            return

        raise_if_cancelled()
        llm_weight = self._admission.weight_for("llm")
        try:
            await self._admission.acquire("llm", weight=llm_weight)
        except AdmissionRejectedError as exc:
            yield {"type": "error", "content": f"Error: LLM admission rejected: {exc}"}
            return

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
            "stream": True,
        }
        body.update(_token_budget_params())

        # Only enable thinking/reasoning for providers that support it
        # (Anthropic/DeepSeek). Default is off; omit the param otherwise.
        _llm_settings = load_settings()
        if _llm_settings.llm_enable_thinking:
            body["enable_thinking"] = True

        # Disable template-level reasoning for llama.cpp Qwen-family models
        # that ignore enable_thinking:false on the top-level body.
        if _llm_settings.llm_llama_cpp_disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        full_content = ""
        saw_done = False
        outcome = "success"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=load_settings().llm_call_timeout
            ) as client:
                async with client.stream(
                    "POST",
                    self._completion_url(),
                    headers=headers,
                    json=body,
                ) as resp:
                    if resp.status_code != 200:
                        outcome = "error"
                        error_text = await resp.aread()
                        logger.error(
                            "LLM API error %d: %s", resp.status_code, error_text[:500]
                        )
                        if resp.status_code == 429:
                            outcome = "rate_limited"
                            yield {
                                "type": "error",
                                "classification": "retryable",
                                "retry_after_seconds": _parse_retry_after(
                                    resp.headers.get("Retry-After")
                                ),
                                "content": "LLM provider rate limit exceeded",
                            }
                            return
                        outcome = "provider_error"
                        yield {
                            "type": "error",
                            "classification": "non_retryable",
                            "content": f"LLM provider returned HTTP {resp.status_code}",
                        }
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            saw_done = True
                            break
                        try:
                            chunk = json.loads(data_str)
                            if not isinstance(chunk, dict):
                                raise ValueError("SSE payload is not an object")
                            choices = chunk.get("choices", [{}])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                full_content += token
                                yield {"type": "token", "content": token}
                        except (
                            json.JSONDecodeError,
                            ValueError,
                            TypeError,
                            KeyError,
                            AttributeError,
                        ):
                            outcome = "malformed"
                            yield {
                                "type": "error",
                                "classification": "malformed",
                                "content": "LLM provider returned malformed SSE",
                            }
                            return

            if not saw_done:
                outcome = "truncated"
                yield {
                    "type": "error",
                    "classification": "truncated",
                    "content": "LLM provider stream ended before [DONE]",
                }
                return
            yield {"type": "done", "full_content": full_content}

        except asyncio.CancelledError:
            # CancelledError is a BaseException, so the generic handler above
            # never sees it. Record the outcome before unwinding so a
            # client-cancelled SSE generation is not counted as "success".
            outcome = "cancelled"
            raise
        except JobCancelledError:
            raise
        except RetryableRateLimitError:
            outcome = "rate_limited"
            raise
        except Exception as e:
            outcome = "error"
            # str(e) can be empty (e.g. httpx read timeouts); always log the
            # exception type name so failures stay diagnosable.
            logger.error("LLM stream call failed (%s): %s", type(e).__name__, e)
            yield {
                "type": "error",
                "classification": "non_retryable",
                "content": "LLM provider stream failed",
            }
        finally:
            observe_elapsed(
                _LLM_CALL_SECONDS, _LLM_CALL_SECONDS_HELP, {"stage": stage}, started
            )
            inc_counter(
                _LLM_CALLS_TOTAL,
                _LLM_CALLS_TOTAL_HELP,
                {"stage": stage, "outcome": outcome},
            )
            self._admission.release("llm", weight=llm_weight)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context: str | None = None,
        schema: dict | None = None,
        stage: str = "other",
    ) -> str:
        """Generate a response from the LLM.

        Args:
            system_prompt: System-level instructions.
            user_prompt: The user's task/question.
            context: Optional scraped context to include.
            schema: Optional JSON Schema for structured output.
            stage: Bounded stage identifier for latency telemetry.

        Returns:
            The LLM's response text.
        """
        raise_if_cancelled()
        llm_weight = self._admission.weight_for("llm")
        try:
            await self._admission.acquire("llm", weight=llm_weight)
        except AdmissionRejectedError as exc:
            return f"Error: LLM admission rejected: {exc}"

        started = time.monotonic()
        outcome = "success"
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
        }
        body.update(_token_budget_params())

        # Only enable thinking/reasoning for providers that support it
        # (Anthropic/DeepSeek). Default is off; omit the param otherwise.
        _llm_settings = load_settings()
        if _llm_settings.llm_enable_thinking:
            body["enable_thinking"] = True

        # Disable template-level reasoning for llama.cpp Qwen-family models
        # that ignore enable_thinking:false on the top-level body.
        if _llm_settings.llm_llama_cpp_disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        # If schema is provided, request structured JSON output
        # Uses json_object mode (widely supported across providers) with
        # schema injected into system prompt.  json_schema strict mode is
        # provider-specific (DeepSeek, Anthropic, etc. may not support it).
        # Empty schema {} is treated as no-schema — do not send response_format
        if schema and any(schema):
            body["response_format"] = {"type": "json_object"}
            # Also inject schema into the system prompt as a fallback hint
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
                self._completion_url(),
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                outcome = "error"
                logger.error("LLM API error %d: %s", resp.status_code, resp.text[:500])
                if resp.status_code == 429:
                    raise RetryableRateLimitError(
                        detail="LLM provider rate limit exceeded",
                        retry_after_seconds=_parse_retry_after(
                            resp.headers.get("Retry-After")
                        ),
                    )
                raise ProviderOutputError(
                    detail=f"LLM provider returned HTTP {resp.status_code}"
                )

            try:
                result = resp.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ProviderOutputError(
                    detail="LLM provider returned malformed JSON"
                ) from exc
            return _completion_content(result)

        except JobCancelledError:
            raise
        except RetryableRateLimitError:
            outcome = "rate_limited"
            raise
        except ProviderOutputError:
            outcome = "provider_error"
            raise
        except httpx.HTTPError as exc:
            outcome = "provider_error"
            logger.error("LLM transport failed: %s", type(exc).__name__)
            raise ProviderOutputError(detail="LLM provider transport failed") from exc
        except Exception as e:
            outcome = "error"
            # str(e) can be empty (e.g. httpx read timeouts); always log the
            # exception type name so failures stay diagnosable.
            logger.error("LLM call failed (%s): %s", type(e).__name__, e)
            return f"Error: LLM call failed: {e}"
        finally:
            observe_elapsed(
                _LLM_CALL_SECONDS, _LLM_CALL_SECONDS_HELP, {"stage": stage}, started
            )
            inc_counter(
                _LLM_CALLS_TOTAL,
                _LLM_CALLS_TOTAL_HELP,
                {"stage": stage, "outcome": outcome},
            )
            self._admission.release("llm", weight=llm_weight)

    async def check_health(self) -> bool:
        """Check if the LLM backend is reachable and responding.

        Sends a minimal request using the configured budget field with a
        short 5s timeout. Returns True if the backend responds with
        HTTP 200, False otherwise. Never raises exceptions.
        """
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        body.update(_token_budget_params(health=True))
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    self._completion_url(),
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
