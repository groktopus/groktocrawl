"""Bounded OpenAI-compatible transport for experimental model review."""

import hashlib
import json

import httpx

from .model_review import ModelReply, ModelReviewAdapter, ReviewRequest


class ReviewTransport:
    """Server-configured endpoint/key; no redirects, retries or logged response bodies."""

    def __init__(
        self, client: httpx.AsyncClient, *, base_url: str, api_key: str
    ) -> None:
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"https", "http"}
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("invalid configured model endpoint")
        self._url = str(url.copy_with(path=url.path.rstrip("/") + "/chat/completions"))
        self._client = client
        self._key = api_key

    async def __call__(self, request: ReviewRequest) -> ModelReply:
        payload = {
            "model": request.requested_model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.payload.decode("utf-8")},
            ],
            "stream": False,
            "max_tokens": request.max_output_tokens,
            "response_format": (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "experimental_research_response",
                        "strict": True,
                        "schema": request.response_schema,
                    },
                }
                if request.response_schema is not None
                else {"type": "json_object"}
            ),
        }
        try:
            async with self._client.stream(
                "POST",
                self._url,
                json=payload,
                headers={"Authorization": "Bearer " + self._key},
                follow_redirects=False,
                timeout=90,
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 65_536:
                        raise ValueError("model envelope too large")
                    body.extend(chunk)
            result = json.loads(body)
            choice = result["choices"][0]
            message = choice["message"]
            if (
                choice["finish_reason"] != "stop"
                or message.get("refusal")
                or message.get("tool_calls")
            ):
                raise ValueError("model completion is not a final judgment")
            content, model = message["content"], result["model"]
            if not isinstance(content, str) or not isinstance(model, str) or not model:
                raise ValueError("model response missing content or identity")
            raw_digest = hashlib.sha256(content.encode()).hexdigest()
            # Some compatible gateways return one JSON code fence despite
            # json_object. Unwrap only that exact whole-response envelope; never
            # extract a favorable fragment or repair JSON/semantic fields.
            normalized = content.strip()
            if normalized.startswith("```json\n") and normalized.endswith("\n```"):
                content = normalized[len("```json\n") : -len("\n```")]
            usage = result.get("usage") or {}
            return ModelReply(
                content.encode(),
                model,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                raw_digest,
            )
        except Exception:
            raise ValueError("model transport failed") from None


def configured_model_review(
    client: httpx.AsyncClient, *, base_url: str, api_key: str, model: str = "local"
) -> ModelReviewAdapter:
    """Wire the experimental reviewer to the owner's LiteLLM alias.

    The server passes its configured endpoint/key; model selection is independent
    of the inherited production LLM_MODEL. No fallback to another alias occurs.
    """
    return ModelReviewAdapter(
        provider="configured-litellm",
        model=model,
        complete=ReviewTransport(client, base_url=base_url, api_key=api_key),
    )
