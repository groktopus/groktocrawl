"""Centralized settings for agent-svc."""

import functools
import os
from typing import Literal

from pydantic import BaseModel, Field


class AgentSettings(BaseModel):
    """All env-var-driven configuration for agent-svc."""

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    valkey_host: str = Field(default="valkey", alias="VALKEY_HOST")
    valkey_port: int = Field(default=6379, alias="VALKEY_PORT")
    valkey_db: int = Field(default=0, alias="VALKEY_DB")
    scraper_url: str = Field(default="http://scraper-svc:8001", alias="SCRAPER_URL")
    searxng_url: str = Field(default="http://searxng:8080", alias="SEARXNG_URL")
    semantic_url: str = Field(default="http://semantic-svc:8003", alias="SEMANTIC_URL")
    llm_base_url: str = Field(default="http://llm-svc:8011/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="deepseek-v4-flash", alias="LLM_MODEL")
    # OpenAI-compatible providers disagree on the completion-budget field.
    # Keep the legacy field as the default and require explicit operator choice.
    llm_token_parameter: Literal["max_tokens", "max_completion_tokens"] = Field(
        default="max_tokens", alias="LLM_TOKEN_PARAMETER"
    )
    llm_max_tokens: int = Field(default=8192, alias="LLM_MAX_TOKENS", gt=0)
    # This budget counts reasoning and visible output together.
    llm_max_completion_tokens: int = Field(
        default=32768, alias="LLM_MAX_COMPLETION_TOKENS", gt=0
    )
    llm_health_token_budget: int = Field(
        default=1, alias="LLM_HEALTH_TOKEN_BUDGET", gt=0
    )
    llm_enable_thinking: bool = Field(default=False, alias="LLM_ENABLE_THINKING")
    llm_llama_cpp_disable_thinking: bool = Field(
        default=False, alias="LLM_LLAMA_CPP_DISABLE_THINKING"
    )
    # Per-operation idle bound (seconds) applied to LLM API calls: an httpx
    # scalar timeout bounds connect/read/write inactivity individually, with
    # no whole-request deadline, so streaming responses may legally run
    # longer than this as long as tokens keep arriving. Raise it (e.g. 300)
    # when pointing LLM_MODEL at reasoning models whose long thinking
    # pauses exceed the default.
    llm_call_timeout: float = Field(default=120.0, alias="LLM_CALL_TIMEOUT", gt=0)
    api_key: str = Field(default="", alias="API_KEY")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    max_searches_per_request: int = Field(
        default=5, alias="AGENT_MAX_SEARCHES_PER_REQUEST"
    )
    search_rate_limit: str = Field(default="10/60s", alias="AGENT_SEARCH_RATE_LIMIT")
    # Job-time retry policy for downstream 429 RATE_LIMITED conditions
    # (ADR-0053). Maximum total attempts for the blocked operation;
    # the fallback delay is used when the downstream response carries no
    # retry metadata, and server-provided delays are clamped to
    # ``job_retry_max_wait_seconds``.
    job_retry_max_attempts: int = Field(default=3, alias="JOB_RETRY_MAX_ATTEMPTS", ge=1)
    job_retry_fallback_seconds: float = Field(
        default=1.0, alias="JOB_RETRY_FALLBACK_SECONDS", ge=0
    )
    job_retry_max_wait_seconds: float = Field(
        default=60.0, alias="JOB_RETRY_MAX_WAIT_SECONDS", ge=0
    )
    crawl_max_duration_seconds: int = Field(
        default=1800, alias="CRAWL_MAX_DURATION_SECONDS"
    )
    crawl_idle_timeout_seconds: int = Field(
        default=300, alias="CRAWL_IDLE_TIMEOUT_SECONDS"
    )
    research_memory_ttl: int = Field(default=604800, alias="RESEARCH_MEMORY_TTL")
    research_memory_max_artifact_bytes: int = Field(
        default=5_242_880, alias="RESEARCH_MEMORY_MAX_ARTIFACT_BYTES"
    )
    # Global weighted admission budgets (ADR-0051). Values are weighted
    # units; per-operation weights are fetch=1, llm=4, browser=8.
    admission_light_fetch_limit: int = Field(
        default=64, alias="ADMISSION_LIGHT_FETCH_LIMIT"
    )
    admission_browser_limit: int = Field(default=32, alias="ADMISSION_BROWSER_LIMIT")
    admission_llm_limit: int = Field(default=32, alias="ADMISSION_LLM_LIMIT")


@functools.cache
def load_settings() -> AgentSettings:
    return AgentSettings.model_validate(dict(os.environ))
