#!/usr/bin/env python3
"""Run a local fixed-source development probe of the ADR-0080 successor."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from agent.experimental.lean_construction import RequiredQuestion
from agent.experimental.lean_journey import run_lean_journey
from agent.experimental.model_review import ModelReply, ReviewRequest
from agent.experimental.model_transport import ReviewTransport
from agent.experimental.passage_preparation import (
    RetainedSourceText,
    prepare_source_passages,
)


def _input(path: Path) -> tuple[
    str, tuple[RequiredQuestion, ...], tuple[RetainedSourceText, ...]
]:
    value: Any = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {"objective", "questions", "sources"}:
        raise ValueError("pilot input must contain only objective, questions, and sources")
    objective = value["objective"]
    questions = value["questions"]
    sources = value["sources"]
    if not isinstance(objective, str) or not isinstance(questions, list) or not isinstance(
        sources, list
    ):
        raise ValueError("pilot input fields have invalid types")
    if any(not isinstance(item, dict) or set(item) != {"question_id", "text"} for item in questions):
        raise ValueError("pilot questions have invalid shape")
    if any(not isinstance(item, dict) or set(item) != {"snapshot_id", "text"} for item in sources):
        raise ValueError("pilot sources have invalid shape")
    return (
        objective,
        tuple(RequiredQuestion.model_validate(item) for item in questions),
        tuple(RetainedSourceText(**item) for item in sources),
    )


async def main(input_path: Path, output: Path) -> None:
    llm_url = os.environ["LLM_BASE_URL"]
    objective, questions, sources = _input(input_path)
    passages = prepare_source_passages(sources)
    output.mkdir(parents=True, exist_ok=False)
    usage: list[dict[str, object]] = []
    try:
        async with httpx.AsyncClient() as client:
            transport = ReviewTransport(
                client,
                base_url=llm_url,
                api_key=os.environ.get("LLM_API_KEY", ""),
            )

            async def complete(request: ReviewRequest) -> ModelReply:
                if len(usage) >= 2:
                    raise ValueError("lean pilot model-call budget exhausted")
                receipt: dict[str, object] = {
                    "requested_model": request.requested_model,
                    "status": "pending",
                    "resolved_model": None,
                    "input_tokens": None,
                    "output_tokens": None,
                }
                usage.append(receipt)
                try:
                    reply = await transport(request)
                except BaseException:
                    receipt["status"] = "failed_or_cancelled"
                    raise
                receipt.update(
                    status="received",
                    resolved_model=reply.resolved_model,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    raw_content_digest=reply.raw_content_digest,
                )
                return reply

            result = await run_lean_journey(
                objective,
                questions,
                passages,
                complete=complete,
                model="local",
            )
        (output / "answer.txt").write_text(result.answer.text + "\n")
        (output / "answer.json").write_text(
            result.answer.model_dump_json(indent=2) + "\n"
        )
        # The terminal summary is written last; partial runs never claim completion.
        (output / "result.json").write_text(
            json.dumps(
                {
                    "status": "complete_development_probe",
                    "provider_calls": result.provider_calls,
                    "reported_input_tokens": result.reported_input_tokens,
                    "reported_output_tokens": result.reported_output_tokens,
                    "construction_prompt_digest": result.construction.prompt_digest,
                    "review_prompt_digest": result.review.prompt_digest,
                    "resolved_models": [
                        item["resolved_model"] for item in usage if item["status"] == "received"
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        (output / "usage.json").write_text(json.dumps(usage, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Fixed-source JSON input")
    parser.add_argument("output", type=Path, help="New output directory")
    arguments = parser.parse_args()
    try:
        asyncio.run(main(arguments.input, arguments.output))
    except Exception:
        parser.exit(
            1,
            "Lean research did not complete; no successful result is claimed. Check the private usage ledger.\n",
        )
