"""Deterministic HTTP twin for the SlopSearX JSON boundary."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["v1"] = "v1"
MAX_DELAY_MS = 2_000
MAX_LEDGER = 200
FIXTURE_SITE_BASE_URL = os.getenv("FIXTURE_SITE_BASE_URL", "http://test-site:8000")
FIXTURE_VERSION = "v3"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SCENARIOS = {
    "healthy",
    "zero-results",
    "partial-degraded",
    "unauthorized",
    "forbidden",
    "rate-limit-retry-after",
    "rate-limit-retry-after-fractional",
    "rate-limit-retry-after-zero",
    "rate-limit-no-retry-after",
    "quota-exhaustion",
    "server-error",
    "delayed",
    "timeout",
    "malformed-json",
    "variant-fields",
    "pagination",
    "contradictory-sources",
}


class Engine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str
    results: int = Field(ge=0)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    content: str
    engine: str


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["v1"]
    query: str
    number_of_results: int = Field(ge=0)
    results: list[SearchResult]
    engines: list[Engine]
    page: int = Field(ge=1)


@dataclass
class FixtureState:
    """Process-local state keeps quota diagnostics isolated between app instances."""

    default_scenario: str = "healthy"
    ledger: list[dict[str, object]] = field(default_factory=list)
    request_number: int = 0
    scenario_requests: dict[tuple[str, str, str | None], int] = field(
        default_factory=dict
    )

    def next_scenario_request(
        self, scenario: str, query: str, run_id: str | None
    ) -> int:
        """Return the ordinal for a non-sensitive scenario/query partition."""
        partition = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        key = (scenario, partition, run_id)
        ordinal = self.scenario_requests.pop(key, 0) + 1
        self.scenario_requests[key] = ordinal
        while len(self.scenario_requests) > MAX_LEDGER:
            del self.scenario_requests[next(iter(self.scenario_requests))]
        return ordinal

    def record(
        self,
        *,
        scenario: str,
        status: int,
        classification: str,
        categories: str,
        page: int,
        result_count: int,
        run_id: str | None,
    ) -> None:
        self.request_number += 1
        self.ledger.append(
            {
                "request_id": self.request_number,
                "scenario": scenario,
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "classification": classification,
                "categories": categories,
                "page": page,
                "result_count": result_count,
                "fixture_version": FIXTURE_VERSION,
                "run_id": run_id,
            }
        )
        del self.ledger[:-MAX_LEDGER]


def _result(index: int, engine: str = "brave") -> SearchResult:
    page = ("pricing", "docs", "about", "blog", "contact")[index % 5]
    return SearchResult(
        url=f"{FIXTURE_SITE_BASE_URL}/{page}",
        title=f"Fixture {page.title()}",
        content=f"Deterministic fixture result {index}.",
        engine=engine,
    )


def _contradictory_results() -> tuple[list[SearchResult], list[Engine]]:
    """Two deterministic fixture pages with genuinely conflicting claims.

    ``/pricing`` states Pro costs $10 while ``/pricing-v2`` states Pro costs
    $99. The grounded-answer eval harness uses this to exercise the real
    contradictory-evidence abstention path (issue #570).
    """
    return (
        [
            SearchResult(
                url=f"{FIXTURE_SITE_BASE_URL}/pricing",
                title="Fixture Pricing",
                content="Fixture pricing page: Free: $0, Pro: $10, Business: $25.",
                engine="brave",
            ),
            SearchResult(
                url=f"{FIXTURE_SITE_BASE_URL}/pricing-v2",
                title="Fixture Pricing V2",
                content="Fixture pricing-v2 page: Pro: $99.",
                engine="google",
            ),
        ],
        [Engine(engine="brave", results=1), Engine(engine="google", results=1)],
    )


def create_app(*, default_scenario: str = "healthy") -> FastAPI:
    """Create an isolated fixture app; each app instance owns its ledger."""
    state = FixtureState(default_scenario=default_scenario)
    app = FastAPI(title="SlopSearX fixture", version=SCHEMA_VERSION)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "slopsearx-fixture",
            "schema_version": SCHEMA_VERSION,
        }

    @app.get("/ledger")
    async def ledger(run_id: str | None = Query(default=None)) -> dict[str, object]:
        if run_id is not None and not RUN_ID_PATTERN.fullmatch(run_id):
            raise HTTPException(status_code=400, detail="invalid run_id")
        entries = (
            state.ledger
            if run_id is None
            else [entry for entry in state.ledger if entry.get("run_id") == run_id]
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "entries": entries,
        }

    @app.post("/ledger/reset")
    async def reset_ledger(run_id: str | None = Query(default=None)) -> dict[str, str]:
        if run_id is not None and not RUN_ID_PATTERN.fullmatch(run_id):
            raise HTTPException(status_code=400, detail="invalid run_id")
        if run_id is None:
            state.ledger.clear()
            state.scenario_requests.clear()
        else:
            state.ledger[:] = [
                entry for entry in state.ledger if entry.get("run_id") != run_id
            ]
            state.scenario_requests = {
                key: value
                for key, value in state.scenario_requests.items()
                if key[2] != run_id
            }
        return {"status": "ok"}

    @app.get("/search")
    async def search(
        request: Request,
        q: str = Query(default=""),
        format: str = Query(default="json"),
        language: str = Query(default="en"),
        pageno: int = Query(default=1, ge=1),
        categories: str = Query(default="general"),
        scenario: str | None = Query(default=None),
        scenario_version: str = Query(default=SCHEMA_VERSION),
        limit: int = Query(default=10, ge=0, le=50),
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
        run_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$"),
    ) -> Response:
        del request, format, language
        if scenario_version != SCHEMA_VERSION:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported scenario version: {scenario_version}",
            )
        # Explicit, fixture-only selection for callers whose public API carries
        # a query but cannot forward the fixture's scenario query parameter.
        query_scenario = None
        if q.startswith("[fixture:") and "]" in q:
            query_scenario = q[len("[fixture:") : q.index("]")]
        selected = scenario or query_scenario or state.default_scenario
        if selected not in SCENARIOS:
            raise HTTPException(status_code=400, detail=f"Unknown scenario: {selected}")
        scenario_request = state.next_scenario_request(selected, q, run_id)
        effective_delay = (
            delay_ms
            if selected not in {"delayed", "timeout"}
            else max(delay_ms, MAX_DELAY_MS)
        )
        if effective_delay:
            await asyncio.sleep(effective_delay / 1000)

        status = 200
        classification = "success"
        result_count = 0
        if selected in {"unauthorized", "forbidden"}:
            status = 401 if selected == "unauthorized" else 403
            classification = "auth_error"
            payload: Response = JSONResponse(
                {"error": f"fixture {selected}"}, status_code=status
            )
        elif selected in {
            "rate-limit-retry-after",
            "rate-limit-no-retry-after",
            "rate-limit-retry-after-fractional",
            "rate-limit-retry-after-zero",
        } or (selected == "quota-exhaustion" and scenario_request > 1):
            status, classification = 429, "rate_limited"
            retry_after = {
                "rate-limit-retry-after": "2",
                "quota-exhaustion": "2",
                "rate-limit-retry-after-fractional": "0.5",
                "rate-limit-retry-after-zero": "0",
            }.get(selected)
            headers = {"Retry-After": retry_after} if retry_after else {}
            payload = JSONResponse(
                {"error": "fixture rate limited"}, status_code=status, headers=headers
            )
        elif selected == "server-error":
            status, classification = 503, "upstream_error"
            payload = JSONResponse({"error": "fixture unavailable"}, status_code=status)
        elif selected == "malformed-json":
            classification = "malformed_json"
            payload = Response(
                "{not-json", status_code=200, media_type="application/json"
            )
        elif selected == "variant-fields":
            classification = "schema_variant"
            payload = JSONResponse(
                {
                    "schema_version": SCHEMA_VERSION,
                    "results": [{"title": "No URL"}],
                    "engines": [{"engine": "brave"}],
                }
            )
        else:
            if selected == "zero-results":
                results, engines = (
                    [],
                    [
                        Engine(engine="brave", results=1),
                        Engine(engine="google", results=1),
                    ],
                )
            elif selected == "partial-degraded":
                results, engines = (
                    [_result(0)],
                    [
                        Engine(engine="brave", results=1),
                        Engine(engine="google", results=0),
                        Engine(engine="duckduckgo", results=0),
                    ],
                )
                classification = "degraded"
            elif selected == "contradictory-sources":
                results, engines = _contradictory_results()
            else:
                start = (pageno - 1) * limit if selected == "pagination" else 0
                results = [
                    _result(index, "brave" if index % 2 == 0 else "google")
                    for index in range(start, start + limit)
                ]
                engines = [Engine(engine="brave", results=len(results))]
            result_count = len(results)
            payload = JSONResponse(
                SearchResponse(
                    schema_version=SCHEMA_VERSION,
                    query=q,
                    number_of_results=result_count,
                    results=results,
                    engines=engines,
                    page=pageno,
                ).model_dump()
            )

        state.record(
            scenario=selected,
            status=status,
            classification=classification,
            categories=categories,
            page=pageno,
            result_count=result_count,
            run_id=run_id,
        )
        return payload

    return app


app = create_app()
