"""Centralized settings for scraper-svc."""

import functools
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ScraperSettings(BaseModel):
    """All env-var-driven configuration for scraper-svc."""

    valkey_host: str = Field(default="valkey", alias="VALKEY_HOST")
    valkey_port: int = Field(default=6379, alias="VALKEY_PORT")
    valkey_db: int = Field(default=0, alias="VALKEY_DB")

    qa_min_content_chars: int = Field(default=200, alias="QA_MIN_CONTENT_CHARS")
    qa_min_title_chars: int = Field(default=10, alias="QA_MIN_TITLE_CHARS")
    qa_max_boilerplate_ratio: float = Field(
        default=0.7, alias="QA_MAX_BOILERPLATE_RATIO"
    )
    qa_min_quality_threshold: float = Field(
        default=0.3, alias="QA_MIN_QUALITY_THRESHOLD"
    )

    flare_solverr_url: str = Field(
        default="http://flare-solverr:8191/v1", alias="FLARE_SOLVERR_URL"
    )
    scrape_cache_ttl: int = Field(default=3600, alias="SCRAPE_CACHE_TTL")
    scrape_cache_min_ttl: int = Field(default=60, alias="SCRAPE_CACHE_MIN_TTL")
    scrape_cache_max_ttl: int = Field(default=86400, alias="SCRAPE_CACHE_MAX_TTL")
    scrape_cache_stable_multiplier: float = Field(
        default=2.0, alias="SCRAPE_CACHE_STABLE_MULTIPLIER"
    )
    scrape_cache_volatile_cap: int = Field(
        default=300, alias="SCRAPE_CACHE_VOLATILE_CAP"
    )
    scrape_cache_domain_ttls: str = Field(
        default="{}", alias="SCRAPE_CACHE_DOMAIN_TTLS"
    )
    scraper_proxy_url: str = Field(default="", alias="SCRAPER_PROXY_URL")
    scraper_private_url_allowlist: str = Field(
        default="", alias="SCRAPER_PRIVATE_URL_ALLOWLIST"
    )
    browser_svc_url: str = Field(
        default="http://browser-svc:8012", alias="BROWSER_SVC_URL"
    )

    politeness_enabled: bool = Field(default=False, alias="SCRAPER_POLITENESS_ENABLED")
    politeness_crawl_delay: float = Field(
        default=1.0, alias="SCRAPER_POLITENESS_CRAWL_DELAY"
    )
    politeness_robots_ttl: int = Field(
        default=3600, alias="SCRAPER_POLITENESS_ROBOTS_TTL"
    )
    politeness_robots_timeout: float = Field(
        default=5.0, alias="SCRAPER_POLITENESS_ROBOTS_TIMEOUT"
    )

    recovery_llm_timeout: int = Field(default=15, alias="RECOVERY_LLM_TIMEOUT")
    recovery_llm_base_url: str = Field(
        default="http://llm-svc:4001/v1", alias="LLM_BASE_URL"
    )
    recovery_llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    recovery_llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    captcha_vision_base_url: str = Field(default="", alias="CAPTCHA_VISION_BASE_URL")
    captcha_vision_api_key: str = Field(default="", alias="CAPTCHA_VISION_API_KEY")
    captcha_vision_model: str = Field(default="", alias="CAPTCHA_VISION_MODEL")
    captcha_vision_timeout: int = Field(
        default=60, alias="CAPTCHA_VISION_TIMEOUT", gt=0
    )

    section_filter_default_include: str = Field(
        default="", alias="SECTION_FILTER_DEFAULT_INCLUDE"
    )
    section_filter_default_exclude: str = Field(
        default="", alias="SECTION_FILTER_DEFAULT_EXCLUDE"
    )
    section_filter_default_verbosity: str = Field(
        default="standard", alias="SECTION_FILTER_DEFAULT_VERBOSITY"
    )

    @field_validator("politeness_enabled", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return False


@functools.cache
def load_settings() -> ScraperSettings:
    return ScraperSettings.model_validate(dict(os.environ))
