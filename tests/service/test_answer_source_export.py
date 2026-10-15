"""Answer pyramid exports reuse the exact already acquired source bodies."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from agent.routes.agent import router
from fastapi import FastAPI

from tests.service.test_cli import _cli_ns
from tests.service.test_issue624_source_registry import (
    _patch_research_clients,
    _research_clients,
)

URL = "https://fixture.example/spec"
TEXT = "General introduction.\n" * 2000 + "Aurora quantum value 791."


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("export", [False, True])
async def test_answer_api_exports_full_sources_only_on_request(streaming, export):
    clients = _research_clients(
        {"Aurora quantum": [{"url": URL, "title": "Spec"}]}, {URL: TEXT}
    )

    async def generate_stream(**_kwargs):
        yield {"type": "token", "content": "answer [1]"}
        yield {"type": "done", "full_content": "answer [1]"}

    clients[2].generate_stream = MagicMock(side_effect=generate_stream)
    app = FastAPI()
    app.include_router(router)
    for key, value in {
        "rate_limiter": SimpleNamespace(
            check=AsyncMock(return_value=(True, 9)), limit=10
        ),
        "max_searches_per_request": 5,
        "searxng_url": "http://search.fixture",
        "scraper_url": "http://scraper.fixture",
        "semantic_url": "http://semantic.fixture",
        "llm_base_url": "http://llm.fixture",
        "llm_api_key": "",
        "llm_model": "fixture",
    }.items():
        setattr(app.state, key, value)
    with _patch_research_clients(*clients):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v2/answer",
                json={
                    "query": "Aurora quantum",
                    "num_sources": 1,
                    "stream": streaming,
                    "include_source_content": export,
                    "evidence_budget_chars": 256,
                },
            )
    assert response.status_code == 200
    if streaming:
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        payload = next(event for event in events if event["type"] == "done")
        assert all(
            "source_contents" not in event
            for event in events
            if event["type"] != "done"
        )
    else:
        payload = response.json()
    if export:
        assert payload["source_contents"] == {URL: TEXT}
    else:
        assert "source_contents" not in payload
        assert TEXT not in response.text
    assert payload["evidence_coverage"]["selected_chars"] <= 256
    assert payload["evidence_coverage"]["omitted_chars"] > 0
    assert clients[0].search.await_count == 1
    assert clients[1].calls == [URL]
    generation = clients[2].generate_stream if streaming else clients[2].generate
    assert "791" in generation.call_args.kwargs["context"]


@pytest.mark.parametrize("streaming", [False, True])
def test_answer_cli_pyramid_writes_retained_source_without_refetch(
    tmp_path, monkeypatch, streaming
):
    monkeypatch.setitem(_cli_ns, "QUIET", True)
    monkeypatch.setitem(_cli_ns, "JSON_OUTPUT", False)
    source = {"url": URL, "title": "Spec"}
    payload = {
        "answer": "answer [1]",
        "sources": [source],
        "citations": [{"index": 1, "url": URL}],
        "source_contents": {URL: TEXT},
    }
    client = MagicMock(dry_run=False)
    if streaming:
        lines = [
            {"type": "sources", "sources": [source]},
            {"type": "done", **payload},
        ]
        stream = MagicMock()
        stream.iter_lines.return_value = [
            "data: " + json.dumps(event) for event in lines
        ] + ["data: [DONE]"]
        client.answer.return_value = {"_stream": stream}
    else:
        client.answer.return_value = payload
    args = SimpleNamespace(
        query="Aurora quantum",
        num_sources=1,
        sync=not streaming,
        pyramid=True,
        output_dir=str(tmp_path / "pyramid"),
        evidence_budget_chars=256,
    )
    _cli_ns["cmd_answer"](client, args)
    assert client.answer.call_args.kwargs["include_source_content"] is True
    dossier = next((tmp_path / "pyramid" / "03-dossiers").glob("consulted-*.md"))
    assert TEXT in dossier.read_text()
    assert len(client.method_calls) == 1
    assert client.method_calls[0][0] == "answer"


def test_answer_cli_client_omits_export_flag_unless_enabled():
    client = _cli_ns["Client"](server="http://fixture", dry_run=False)
    client._request = MagicMock(return_value={})
    client.answer("question")
    assert "include_source_content" not in client._request.call_args.kwargs["json_data"]
    client.answer("question", include_source_content=True)
    assert (
        client._request.call_args.kwargs["json_data"]["include_source_content"] is True
    )
