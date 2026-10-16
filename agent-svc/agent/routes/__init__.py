"""Routes package for GroktoCrawl API — combines all domain routers into one."""

import logging

from fastapi import APIRouter

from .activity import router as activity_router
from .agent import router as agent_router
from .browser import router as browser_router
from .citations import router as citations_router
from .crawl import router as crawl_router
from .documents import router as documents_router
from .enrich import router as enrich_router
from .experimental_research import router as experimental_research_router
from .extract import router as extract_router
from .find_similar import router as find_similar_router
from .followup import router as followup_router
from .llmstxt import router as llmstxt_router
from .map import router as map_router
from .monitor import router as monitor_router
from .parse import router as parse_router
from .plan import router as plan_router
from .research_memory import router as research_memory_router
from .scrape import router as scrape_router
from .search import router as search_router
from .session import router as session_router
from .webhook import router as webhook_router

logger = logging.getLogger(__name__)

router = APIRouter()

router.include_router(activity_router)
router.include_router(scrape_router)
router.include_router(agent_router)
router.include_router(crawl_router)
router.include_router(search_router)
router.include_router(map_router)
router.include_router(extract_router)
router.include_router(monitor_router)
router.include_router(browser_router)
router.include_router(webhook_router)
router.include_router(llmstxt_router)
router.include_router(citations_router)
router.include_router(plan_router)
router.include_router(session_router)
router.include_router(documents_router)
router.include_router(research_memory_router)
router.include_router(parse_router)
router.include_router(find_similar_router)
router.include_router(enrich_router)
router.include_router(experimental_research_router)

router.include_router(followup_router)
