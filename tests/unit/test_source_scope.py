"""Caller source boundaries are validated and enforced before acquisition."""

import json
from datetime import UTC, datetime

import pytest
from agent.experimental.model_review import ModelReply
from agent.experimental.query_construction import CapturedSource
from agent.experimental.query_runner import run_query
from agent.experimental.source_scope import (
    SourceScope,
    SourceScopeUnavailableError,
    canonical_source_url,
)
from agent.routes.experimental_research import CreateResearchRunRequest
from pydantic import ValidationError

from tests.unit.test_query_construction import (
    QUESTION,
    TEXT,
    batch_decisions,
    proposal,
)

SEED = "https://docs.python.org/3/tutorial/introduction.html"


def test_open_scope_rejects_constraints() -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="open", seed_urls=(SEED,))
    with pytest.raises(ValidationError):
        SourceScope(mode="open", allowed_hosts=("docs.python.org",))


def test_seeds_only_requires_seeds_and_forbids_hosts() -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="seeds_only")
    with pytest.raises(ValidationError):
        SourceScope(
            mode="seeds_only",
            seed_urls=(SEED,),
            allowed_hosts=("docs.python.org",),
        )


def test_allowlist_requires_hosts_and_seed_in_scope() -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="allowlist")
    with pytest.raises(ValidationError):
        SourceScope(
            mode="allowlist",
            seed_urls=("https://outside.test/a",),
            allowed_hosts=("docs.python.org",),
        )
    scope = SourceScope(
        mode="allowlist",
        seed_urls=(SEED,),
        allowed_hosts=("docs.python.org",),
    )
    assert scope.seed_urls == (SEED,)


def test_empty_cjson_object_is_treated_as_an_empty_collection() -> None:
    # The durable claim script round-trips payloads through Lua cjson, which
    # encodes an empty JSON array as an empty object.
    scope = SourceScope.model_validate(
        {
            "mode": "seeds_only",
            "seed_urls": [SEED],
            "allowed_hosts": {},
        }
    )
    assert scope.allowed_hosts == ()
    with pytest.raises(ValidationError):
        SourceScope.model_validate(
            {
                "mode": "seeds_only",
                "seed_urls": [SEED],
                "allowed_hosts": {"example.org": True},
            }
        )


def test_canonicalization_and_exact_seed_matching() -> None:
    scope = SourceScope(
        mode="seeds_only",
        seed_urls=(" HTTPS://Docs.Python.org./3/tutorial/introduction.html#x ",),
    )
    assert scope.seed_urls == (SEED,)
    assert scope.permits_url(SEED)
    assert not scope.permits_url("https://docs.python.org/3/tutorial/classes.html")
    assert canonical_source_url("https://EXAMPLE.org") == "https://example.org/"


def test_subdomain_matching_and_lookalike_rejection() -> None:
    scope = SourceScope(mode="allowlist", allowed_hosts=("example.org",))
    assert scope.permits_url("https://example.org/a")
    assert scope.permits_url("https://docs.example.org/a")
    assert not scope.permits_url("https://badexample.org/a")
    assert not scope.permits_url("https://example.org.evil.test/a")


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.org/a",
        "https://user:pass@example.org/a",
        "https://example.org/a b",
        "not-a-url",
    ],
)
def test_rejects_non_source_urls(value: str) -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="seeds_only", seed_urls=(value,))


def test_rejects_ip_literal_allowed_host() -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="allowlist", allowed_hosts=("127.0.0.1",))
    with pytest.raises(ValidationError):
        SourceScope(mode="allowlist", allowed_hosts=("https://example.org",))


def test_duplicate_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceScope(mode="seeds_only", seed_urls=(SEED, SEED))
    with pytest.raises(ValidationError):
        SourceScope(
            mode="allowlist",
            allowed_hosts=("example.org", "example.org"),
        )


def test_unknown_request_fields_are_rejected_instead_of_dropped() -> None:
    with pytest.raises(ValidationError):
        CreateResearchRunRequest.model_validate(
            {
                "objective": "Use only the official tutorial",
                "urls": [SEED],
                "allowed_domains": ["docs.python.org"],
            }
        )


def test_unscoped_request_digest_preserves_previous_shape() -> None:
    request = CreateResearchRunRequest(objective="x")
    previous = json.dumps(
        {
            "protocol_version": "research/1",
            "objective": "x",
            "as_of": None,
            "webhook": None,
        },
        separators=(",", ":"),
    )
    assert request.model_dump_json(exclude={"source_scope"}) == previous
    scoped = CreateResearchRunRequest(
        objective="x",
        source_scope=SourceScope(mode="seeds_only", seed_urls=(SEED,)),
    )
    assert "source_scope" in scoped.model_dump_json()


async def _complete(request):
    value = json.loads(request.payload)
    content = proposal() if "schema" in value else batch_decisions(value)
    return ModelReply(json.dumps(content).encode(), "test-model", 1, 1)


@pytest.mark.asyncio
async def test_seeds_only_acquires_without_search() -> None:
    captured = []

    async def search(*_args):
        raise AssertionError("seeds_only must not search")

    async def acquire(url):
        captured.append(url)
        return CapturedSource(url, TEXT, "2026-09-06T00:00:00Z")

    result = await run_query(
        QUESTION,
        search=search,
        acquire=acquire,
        complete=_complete,
        scope_id="owner",
        seed_urls=(SEED,),
        discover=False,
        queries=(QUESTION,),
        source_limit=8,
        allow_partial=True,
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert captured == [SEED]
    published = b"".join(report.body for report in result.reports).decode()
    assert SEED in published


@pytest.mark.asyncio
async def test_allowlist_excludes_a_tempting_out_of_scope_result() -> None:
    captured = []

    async def search(*_args):
        return ("https://outside.test/tempting", "https://allowed.test/ok")

    async def acquire(url):
        captured.append(url)
        return CapturedSource(url, TEXT, "2026-09-06T00:00:00Z")

    result = await run_query(
        QUESTION,
        search=search,
        acquire=acquire,
        complete=_complete,
        scope_id="owner",
        allowed_hosts=("allowed.test",),
        queries=(QUESTION,),
        source_limit=8,
        allow_partial=True,
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert captured == ["https://allowed.test/ok"]
    published = b"".join(report.body for report in result.reports).decode()
    assert "https://allowed.test/ok" in published
    assert "outside.test" not in published


@pytest.mark.asyncio
async def test_unavailable_permitted_source_fails_without_substitution() -> None:
    async def search(*_args):
        raise AssertionError("seeds_only must not search")

    async def acquire(_url):
        raise ValueError("confirmed unavailable")

    with pytest.raises(SourceScopeUnavailableError):
        await run_query(
            QUESTION,
            search=search,
            acquire=acquire,
            complete=_complete,
            scope_id="owner",
            seed_urls=("https://allowed.test/missing",),
            discover=False,
            queries=(QUESTION,),
            source_limit=8,
            allow_partial=True,
        )
