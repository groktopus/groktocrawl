"""Optional post-scrape Jev contribution routing for research sources."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from agent.research.jev_filter import JevContributionFilter
from agent.research.sources import SourceArtifact


@pytest.fixture(autouse=True)
def public_fixture_hosts(monkeypatch):
    monkeypatch.setattr("agent.research.jev_filter.is_private_host", lambda url: False)


def _jev_client(score):
    seen = []

    def respond(request):
        import json

        body = json.loads(request.content)
        seen.append(body)
        value = score(body) if callable(score) else score
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"material_contribution": {"type": "noul", "noul": value}},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return client, seen


@pytest.mark.asyncio
async def test_low_score_removes_only_noise_and_preserves_metadata():
    client, seen = _jev_client(
        lambda body: 0.02 if "site could not load" in body["state"]["passage"] else 0.93
    )
    router = JevContributionFilter("test-key", client=client)
    garbage = SourceArtifact(
        url="https://example.org/error", markdown="site could not load"
    )
    contextual = SourceArtifact(
        url="https://example.org/study", markdown="A useful comparison."
    )
    await router.assess_all("research question", [garbage, contextual])
    assert not router.retain(garbage)
    assert router.retain(contextual)
    assert contextual.to_source_detail()["material_contribution_score"] == 0.93
    assert "material_contribution_score: 0.93" in contextual.to_document(max_chars=None)
    assert [body["state"]["query"] for body in seen] == ["research question"] * 2
    assert all(set(body["state"]) == {"query", "passage"} for body in seen)
    await client.aclose()


@pytest.mark.asyncio
async def test_long_page_is_fully_assessed_and_any_useful_chunk_keeps_it():
    client, seen = _jev_client(
        lambda body: 0.98 if "decisive finding" in body["state"]["passage"] else 0.03
    )
    router = JevContributionFilter("test-key", client=client)
    artifact = SourceArtifact(
        url="https://example.org/long",
        markdown="ordinary background\n" * 8000 + "decisive finding at the end",
    )
    await router.assess_all("research question", [artifact])
    assert len(seen) > 1
    assert artifact.material_contribution_score == 0.98
    assert router.retain(artifact)
    assert "decisive finding at the end" in artifact.to_document(max_chars=None)
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_failure_and_invalid_response_fail_open():
    async def failed(request):
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(failed))
    router = JevContributionFilter("test-key", client=client)
    artifact = SourceArtifact(
        url="https://example.org/source", markdown="Potential evidence"
    )
    await router.assess_all("query", [artifact])
    assert artifact.material_contribution_score is None
    assert router.retain(artifact)
    assert "material_contribution_score" not in artifact.to_source_detail()
    await client.aclose()

    malformed, _ = _jev_client(1.5)
    router = JevContributionFilter("test-key", client=malformed)
    await router.assess_all("query", [artifact])
    assert artifact.material_contribution_score is None
    assert router.retain(artifact)
    await malformed.aclose()


@pytest.mark.asyncio
async def test_private_source_is_never_sent_to_jev(monkeypatch):
    monkeypatch.setattr("agent.research.jev_filter.is_private_host", lambda url: True)
    client, seen = _jev_client(0.01)
    router = JevContributionFilter("test-key", client=client)
    artifact = SourceArtifact(
        url="http://internal.local/page", markdown="Private content"
    )
    await router.assess_all("query", [artifact])
    assert seen == []
    assert router.retain(artifact)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_key", [False, True])
async def test_research_loop_filters_after_scrape_only_with_key(monkeypatch, with_key):
    from agent.research import loop

    good = SourceArtifact(
        url="https://example.org/good",
        markdown="useful evidence " * 700,
        source="test",
        char_count=11200,
    )
    garbage = SourceArtifact(
        url="https://example.org/error",
        markdown="site could not load",
        source="test",
        char_count=19,
    )
    artifacts = [good, garbage]
    monkeypatch.setattr(
        loop,
        "load_settings",
        lambda: MagicMock(
            typesafe_api_key="test-key" if with_key else "",
            typesafe_jev_min_score=0.10,
        ),
    )
    monkeypatch.setattr(
        loop, "SearXNGClient", lambda *a, **kw: MagicMock(close=AsyncMock())
    )
    monkeypatch.setattr(
        loop, "ScraperClient", lambda *a, **kw: MagicMock(close=AsyncMock())
    )
    llm = MagicMock(generate=AsyncMock(return_value="answer"), close=AsyncMock())
    monkeypatch.setattr(loop, "LLMClient", lambda *a, **kw: llm)
    monkeypatch.setattr(
        loop,
        "_generate_research_plan",
        AsyncMock(
            return_value={
                "focused_queries": ["query"],
                "research_strategy": "focused",
                "reasoning": "",
            }
        ),
    )
    monkeypatch.setattr(loop, "_detect_gaps", AsyncMock(return_value=[]))

    async def discovery(**kwargs):
        return {
            "context": "\n\n---\n\n".join(a.to_document() for a in artifacts),
            "source_details": [a.to_source_detail() for a in artifacts],
            "artifacts": artifacts,
            "new_artifacts": artifacts,
            "credits_used": 2,
        }

    monkeypatch.setattr(loop, "_run_research_discover_and_scrape", discovery)
    calls = []

    class FakeJev:
        def __init__(self, key, **kwargs):
            calls.append(key)
            self.min_score = kwargs["min_score"]

        async def assess_all(self, query, found):
            assert query == "query"
            for artifact in found:
                artifact.material_contribution_score = (
                    0.98 if artifact is good else 0.02
                )

        def retain(self, artifact):
            return (
                artifact.material_contribution_score is None
                or artifact.material_contribution_score >= self.min_score
            )

        async def close(self):
            pass

    monkeypatch.setattr(loop, "JevContributionFilter", FakeJev)
    events = [
        event
        async for event in loop._run_research_events(
            "query",
            llm_model="test-model",
            search_type="focused",
        )
    ]
    done = next(event for event in events if event["type"] == "done")
    context = llm.generate.call_args.kwargs["context"]
    if with_key:
        assert calls == ["test-key"]
        assert done["sources"] == [good.url]
        assert "material_contribution_score: 0.98" in context
        assert "site could not load" not in context
        assert len(context) > 8000  # The retained passage is not first-8k clipped.
    else:
        assert not calls
        assert done["sources"] == [good.url, garbage.url]
        assert "material_contribution_score" not in context
        assert (
            good.markdown in context
        )  # Keyless synthesis also retains full permitted text.
        assert done["evidence_coverage"]["selected_chars"] == len(good.markdown) + len(
            garbage.markdown
        )
