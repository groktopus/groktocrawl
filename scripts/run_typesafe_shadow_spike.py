#!/usr/bin/env python3
"""Run the exposed TypeSafe shadow-routing calibration corpus.

Provider receipts contain only decisions, usage, timing, and input digests.  The
script never writes the API key or raw HTTP response.  Live outputs belong in a
private path and must not be committed without a separate publication review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-svc"))

from agent.experimental.typesafe_shadow import (
    PassageState,
    Route,
    ShadowReceipt,
    TypeSafeShadowRouter,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Case(_StrictModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    stratum: str
    state: PassageState
    reference_route: Route
    notes: str


class Corpus(_StrictModel):
    schema_version: str
    exposure: str
    cases: tuple[Case, ...] = Field(min_length=12, max_length=100)


async def run(args: argparse.Namespace) -> dict:
    corpus = Corpus.model_validate_json(args.corpus.read_bytes())
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    semaphore = asyncio.Semaphore(args.max_concurrency)
    async with httpx.AsyncClient() as client:
        router = TypeSafeShadowRouter(
            client,
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            timeout_seconds=args.timeout,
        )

        async def one(case: Case) -> dict:
            async with semaphore:
                receipt: ShadowReceipt = await router.shadow(case.state)
            observed = (
                receipt.answers.route.choice if receipt.answers is not None else None
            )
            return {
                "case_id": case.case_id,
                "stratum": case.stratum,
                "input_digest": receipt.input_digest,
                "reference_route": case.reference_route,
                "observed_route": observed,
                "route_match": observed == case.reference_route if observed else None,
                "receipt": receipt.model_dump(mode="json"),
            }

        records = await asyncio.gather(*(one(case) for case in corpus.cases))
    completed = [record for record in records if record["observed_route"] is not None]
    try:
        corpus_label = str(args.corpus.resolve().relative_to(ROOT))
    except ValueError:
        corpus_label = "external-private-corpus"
    return {
        "schema_version": "typesafe-shadow-run/1",
        "experimental": True,
        "provider_results_publishable": False,
        "corpus": corpus_label,
        "corpus_exposure": corpus.exposure,
        "requested_model": args.model,
        "key_present": bool(api_key),
        "summary": {
            "cases": len(records),
            "completed": len(completed),
            "route_matches": sum(record["route_match"] is True for record in records),
            "fallback_or_skipped": len(records) - len(completed),
        },
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "docs/experiments/typesafe-jev/exposed-routing-corpus.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument("--base-url", default="https://api.typesafe.ai")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--max-concurrency", type=int, default=5, choices=range(1, 11))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = asyncio.run(run(args))
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
