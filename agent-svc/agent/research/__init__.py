"""Research package — domain modules for the agent research loop."""

from .acquisition import AcquisitionResult, acquire_source_artifacts
from .citations import _apply_citation_style
from .contents import process_contents_for_results
from .discovery import _scrape_urls
from .enrich import run_enrich_pipeline
from .loop import (
    run_answer,
    run_answer_stream,
    run_extract,
    run_research,
    run_research_stream,
)
from .rerank import _rerank_answer_sources
from .scoring import _is_video_platform_url
from .search import run_deep_search, run_rich_search, run_search_stream
from .similar import run_find_similar
from .streaming import stream_cached_artifact, stream_research_live
from .utils import _validate_json_if_schema

__all__ = [
    "AcquisitionResult",
    "_apply_citation_style",
    "_is_video_platform_url",
    "_rerank_answer_sources",
    "_scrape_urls",
    "_validate_json_if_schema",
    "acquire_source_artifacts",
    "process_contents_for_results",
    "run_answer",
    "run_answer_stream",
    "run_deep_search",
    "run_enrich_pipeline",
    "run_extract",
    "run_find_similar",
    "run_research",
    "run_research_stream",
    "run_rich_search",
    "run_search_stream",
    "stream_cached_artifact",
    "stream_research_live",
]
