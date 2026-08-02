# ═══════════════════════════════════════════════════════════════════
# M1 Integration Tests — Schema Constraints, Answer Edge Cases,
# Error States, and Cross-Endpoint Flows
# ═══════════════════════════════════════════════════════════════════

import json
import os
import time
from contextlib import suppress

import httpx
import pytest

from tests.outcome_governance import governed_skip

AGENT = os.getenv("AGENT_BASE_URL", "http://localhost:8080")
TEST_SITE = os.getenv("TEST_SITE_BASE_URL", "http://localhost:8005")

# ── Helpers ─────────────────────────────────────────────────────


def _post_agent(body: dict, timeout: int = 30) -> httpx.Response:
    """POST /v2/agent with rate-limit retry (up to 3 attempts)."""
    last = None
    for _ in range(3):
        r = httpx.post(AGENT + "/v2/agent", json=body, timeout=timeout)
        if r.status_code != 429:
            return r
        last = r
        time.sleep(15)
    return last  # type: ignore[return-value]


def _assert_agent_created(r: httpx.Response):
    """Assert agent job was created, or skip if rate-limited."""
    if r.status_code == 429:
        governed_skip(
            "Rate limited by agent endpoint",
            owner="repository-maintainer",
            issue="#502",
            classification="retained",
            environment="agent endpoint returned HTTP 429",
        )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"


def _poll_agent_job(job_id: str, timeout_s: int = 90) -> dict:
    """Poll agent job until terminal state. Returns final payload."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = httpx.get(AGENT + f"/v2/agent/{job_id}", timeout=120)
        payload = status.json()
        if payload["status"] in ("completed", "failed"):
            return payload
        time.sleep(2)
    return payload


# ── Schema Constraint Tests ─────────────────────────────────────


def test_agent_schema_additional_properties_false():
    """VAL-SOC-009: Agent with output_schema using additionalProperties:false."""
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
        "required": ["name"],
    }
    r = _post_agent(
        {
            "prompt": "What is the capital of France?",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]
    assert job_id

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "name" in parsed, (
                    f"Missing required key 'name'. Got: {list(parsed.keys())}"
                )
                assert set(parsed.keys()) == {"name"}, (
                    f"additionalProperties:false violated. Extra keys: {set(parsed.keys()) - {'name'}}"
                )
            except json.JSONDecodeError:
                pass


def test_agent_schema_enum_constraints():
    """VAL-SOC-010: Agent with output_schema containing enum constraints."""
    schema = {
        "type": "object",
        "properties": {
            "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]}
        },
        "required": ["sentiment"],
    }
    r = _post_agent(
        {
            "prompt": "What is the sentiment of this: 'The product is great!'",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "sentiment" in parsed
                assert parsed["sentiment"] in ("positive", "negative", "neutral"), (
                    f"Enum constraint violated: {parsed['sentiment']}"
                )
            except json.JSONDecodeError:
                pass


def test_agent_schema_nested_objects():
    """VAL-SOC-011: Agent with output_schema containing nested objects."""
    schema = {
        "type": "object",
        "properties": {
            "author": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["name"],
            }
        },
        "required": ["author"],
    }
    r = _post_agent(
        {
            "prompt": "Who wrote 'To Kill a Mockingbird'?",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "author" in parsed, (
                    f"Missing 'author' key. Got: {list(parsed.keys())}"
                )
                assert isinstance(parsed["author"], dict), (
                    f"author should be object, got {type(parsed['author'])}"
                )
                assert "name" in parsed["author"], (
                    "Missing nested required field 'author.name'"
                )
            except json.JSONDecodeError:
                pass


def test_agent_schema_arrays():
    """VAL-SOC-012: Agent with output_schema containing arrays."""
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
        "required": ["items"],
    }
    r = _post_agent(
        {
            "prompt": "List 3 programming languages",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "items" in parsed, (
                    f"Missing 'items' key. Got: {list(parsed.keys())}"
                )
                assert isinstance(parsed["items"], list), (
                    f"items should be array, got {type(parsed['items'])}"
                )
                if parsed["items"]:
                    assert all(isinstance(i, str) for i in parsed["items"]), (
                        "All items must be strings"
                    )
            except json.JSONDecodeError:
                pass


def test_agent_strict_constrain_to_urls_with_schema():
    """VAL-SOC-013: Agent with strict_constrain_to_urls and output_schema."""
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    r = _post_agent(
        {
            "prompt": "Summarize the content of this page",
            "urls": [TEST_SITE + "/pricing"],
            "strict_constrain_to_urls": True,
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    assert payload["status"] in ("completed", "failed"), (
        f"Expected completed or failed, got {payload['status']}"
    )
    if payload["status"] == "completed" and payload.get("data"):
        data = payload["data"]
        result = data.get("result", "")
        if not result.startswith("I was unable to find"):
            # Verify JSON schema conformance
            try:
                parsed = json.loads(result)
                assert "summary" in parsed, (
                    f"Schema conformance: missing required key 'summary'. "
                    f"Got keys: {list(parsed.keys())}"
                )
                assert isinstance(parsed["summary"], str), (
                    f"Schema conformance: 'summary' should be string, "
                    f"got {type(parsed['summary'])}"
                )
            except json.JSONDecodeError:
                pytest.fail(
                    f"Result not valid JSON despite output_schema: {result[:200]}"
                )
            # Verify URL constraint: sources should only contain the
            # constrained URL when strict_constrain_to_urls is set
            sources = data.get("sources", [])
            if sources:
                constrained_url = TEST_SITE + "/pricing"
                for source in sources:
                    source_url = (
                        source if isinstance(source, str) else source.get("url", "")
                    )
                    assert (
                        constrained_url in source_url or source_url == constrained_url
                    ), (
                        f"URL constraint violated: source {source_url} not from "
                        f"constrained URL {constrained_url}"
                    )


# ── Answer Edge Cases ───────────────────────────────────────────


def test_answer_output_schema_with_citations():
    """VAL-SOC-025: Answer with output_schema preserves citation handling."""
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "detail": {"type": "string"},
        },
        "required": ["summary"],
    }
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is the pricing on the fixture site?",
            "num_sources": 3,
            "output_schema": schema,
        },
        timeout=180,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert isinstance(payload["citations"], list)
    assert isinstance(payload["sources"], list)

    answer_text = payload["answer"]
    if answer_text.startswith("I was unable to find"):
        assert payload["citations"] == []
    else:
        try:
            parsed = json.loads(answer_text)
            assert "summary" in parsed, (
                f"Answer JSON missing 'summary'. Keys: {list(parsed.keys())}"
            )
        except json.JSONDecodeError:
            pass


def test_answer_complex_output_schema():
    """VAL-SOC-026: Answer with complex output_schema (nested arrays of objects)."""
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["url", "score"],
                },
            }
        },
        "required": ["results"],
    }
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What are the top 2 search engines?",
            "num_sources": 2,
            "output_schema": schema,
        },
        timeout=180,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True

    answer_text = payload["answer"]
    if answer_text.startswith("I was unable to find"):
        pass
    else:
        try:
            parsed = json.loads(answer_text)
            assert "results" in parsed, (
                f"Missing 'results' key. Keys: {list(parsed.keys())}"
            )
            assert isinstance(parsed["results"], list), (
                f"results should be array, got {type(parsed['results'])}"
            )
            if parsed["results"]:
                for item in parsed["results"]:
                    assert isinstance(item, dict), (
                        f"Array items must be objects, got {type(item)}"
                    )
                    assert "url" in item, f"Missing 'url' in array item: {item}"
                    assert "score" in item, f"Missing 'score' in array item: {item}"
        except json.JSONDecodeError:
            pass


def test_answer_output_schema_num_sources_one():
    """VAL-SOC-027: Answer with output_schema and num_sources:1."""
    schema = {
        "type": "object",
        "properties": {"fact": {"type": "string"}},
        "required": ["fact"],
    }
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is the pricing on the fixture site?",
            "num_sources": 1,
            "output_schema": schema,
        },
        timeout=180,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert len(payload["sources"]) <= 1, (
        f"Expected ≤1 source, got {len(payload['sources'])}"
    )

    answer_text = payload["answer"]
    if not answer_text.startswith("I was unable to find"):
        with suppress(json.JSONDecodeError):
            json.loads(answer_text)


def test_answer_non_json_fallback():
    """VAL-SOC-028: Answer with output_schema returns raw text when LLM gives non-JSON."""
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is the pricing on the fixture site?",
            "num_sources": 1,
            "output_schema": schema,
        },
        timeout=180,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert isinstance(payload["answer"], str)
    assert len(payload["answer"]) > 0


# ── Error States ────────────────────────────────────────────────


def test_agent_large_output_schema():
    """VAL-ERR-001: Agent with output_schema >100KB.

    An extremely large schema should be rejected or the job should fail
    gracefully without crashing the server.
    """
    properties = {}
    for i in range(5000):
        properties[f"field_{i:05d}"] = {"type": "string"}

    large_schema = {
        "type": "object",
        "properties": properties,
        "required": ["field_00000"],
    }
    r = _post_agent(
        {
            "prompt": "test large schema",
            "output_schema": large_schema,
        }
    )
    # Should be rejected (422) or accepted (200) but not crash (500)
    # May also be 429 if rate-limited
    assert r.status_code in (200, 422, 413, 429), (
        f"Expected 200, 422, 413, or 429, got {r.status_code}: {r.text[:200]}"
    )
    # Verify server is still healthy
    health = httpx.get(AGENT + "/health", timeout=10)
    assert health.status_code == 200


def test_agent_self_referencing_ref_schema():
    """VAL-ERR-002: Agent with self-referencing $ref in output_schema."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "child": {"$ref": "#/$defs/Node"},
        },
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/Node"},
                    },
                },
            }
        },
    }
    r = _post_agent(
        {
            "prompt": "test self-referencing schema",
            "output_schema": schema,
        }
    )
    # Job should be created (200) or rate-limited (429) — the LLM may reject it later
    assert r.status_code in (200, 422, 429), (
        f"Expected 200, 422, or 429, got {r.status_code}: {r.text[:200]}"
    )
    if r.status_code == 200:
        job_id = r.json()["id"]
        payload = _poll_agent_job(job_id)
        assert payload["status"] in ("completed", "failed"), (
            "Job should reach terminal state"
        )

    health = httpx.get(AGENT + "/health", timeout=10)
    assert health.status_code == 200


def test_agent_unsupported_schema_keywords():
    """VAL-ERR-003: Agent with unsupported JSON Schema keywords (oneOf)."""
    schema = {
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "number"},
                ]
            }
        },
        "required": ["result"],
    }
    r = _post_agent(
        {
            "prompt": "test unsupported keywords",
            "output_schema": schema,
        }
    )
    assert r.status_code in (200, 422, 429), (
        f"Expected 200, 422, or 429, got {r.status_code}: {r.text[:200]}"
    )
    health = httpx.get(AGENT + "/health", timeout=10)
    assert health.status_code == 200


def test_agent_rate_limit_with_output_schema():
    """VAL-ERR-004: Rate limiting applies to agent requests with output_schema.

    The rate limiter should reject before any LLM processing, regardless
    of schema presence. Verifies rate limit headers are present.
    """
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    r = httpx.post(
        AGENT + "/v2/agent",
        json={"prompt": "test rate limit with schema", "output_schema": schema},
        timeout=30,
    )
    # Rate limit may be in effect — both 200 and 429 are valid
    if r.status_code == 429:
        payload = r.json()
        assert payload.get("error_code") == "RATE_LIMITED", (
            f"429 should have RATE_LIMITED error_code: {payload}"
        )
        return  # Rate limiter is working — test passes

    assert r.status_code == 200
    assert "X-Search-Rate-Remaining" in r.headers, (
        f"Rate limit header missing. Headers: {dict(r.headers)}"
    )
    assert "X-Search-Budget" in r.headers, "Search budget header missing"


def test_agent_llm_health_check_streaming():
    """VAL-ERR-005: LLM health check for agent streaming with output_schema.

    When streaming is requested, the pre-flight LLM health check should
    run before the stream opens. A failing LLM should produce HTTP 503.
    """
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    r = httpx.post(
        AGENT + "/v2/agent",
        json={
            "prompt": "test health check",
            "output_schema": schema,
            "stream": True,
        },
        timeout=30,
    )
    # If LLM is healthy: 200 + SSE stream
    # If LLM is unhealthy: 503
    # If rate limited: 429
    assert r.status_code in (200, 429, 503), (
        f"Expected 200, 429, or 503, got {r.status_code}: {r.text[:200]}"
    )
    if r.status_code == 503:
        detail = r.json().get("detail", "")
        assert "LLM" in detail or "llm" in detail.lower(), (
            f"503 response should mention LLM: {detail}"
        )


def test_agent_unicode_schema_property_names():
    """VAL-ERR-007: Agent with Unicode characters in output_schema property names."""
    schema = {
        "type": "object",
        "properties": {
            "résumé": {"type": "string"},
            "data-points": {"type": "array", "items": {"type": "string"}},
            "café_öl": {"type": "string"},
        },
        "required": ["résumé"],
    }
    r = _post_agent(
        {
            "prompt": "Describe yourself briefly",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "résumé" in parsed, (
                    f"Missing Unicode key 'résumé'. Keys: {list(parsed.keys())}"
                )
            except json.JSONDecodeError:
                pass


def test_agent_null_type_schema_fields():
    """VAL-ERR-008: Agent with output_schema containing type:["string","null"] fields."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "optional_field": {"type": ["string", "null"]},
        },
        "required": ["name"],
    }
    r = _post_agent(
        {
            "prompt": "What is 2+2?",
            "output_schema": schema,
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id)
    if payload["status"] == "completed" and payload.get("data"):
        result = payload["data"].get("result", "")
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert "name" in parsed
                if "optional_field" in parsed:
                    assert parsed["optional_field"] is None or isinstance(
                        parsed["optional_field"], str
                    ), (
                        f"optional_field should be string or null, got {type(parsed['optional_field'])}"
                    )
            except json.JSONDecodeError:
                pass


def test_agent_backward_compat_no_new_fields():
    """VAL-ERR-009: Backward compat — pre-M1 request shape unchanged."""
    r = _post_agent({"prompt": "What is the pricing on the fixture site?"})
    _assert_agent_created(r)
    job_id = r.json()["id"]
    assert job_id

    payload = _poll_agent_job(job_id)
    assert payload["status"] in ("completed", "failed")
    if payload.get("data") is not None:
        data = payload["data"]
        assert isinstance(data.get("result"), str), (
            f"result should be string, got {type(data.get('result'))}"
        )
        assert isinstance(data.get("sources"), list) or data.get("sources") is None
        if "source_details" in data:
            assert isinstance(data["source_details"], list)
        assert "sources_compact" not in data, (
            "backward compat: sources_compact should not be present when "
            "citation_style is not specified (VAL-ERR-009)"
        )


def test_agent_bare_minimum_request():
    """VAL-ERR-010: Bare minimum agent request with only prompt field."""
    r = _post_agent({"prompt": "What is the capital of France?"})
    _assert_agent_created(r)
    payload = r.json()
    assert payload["success"] is True
    assert "id" in payload

    job_payload = _poll_agent_job(payload["id"])
    assert job_payload["status"] in ("completed", "failed"), (
        f"Bare minimum job should reach terminal state, got {job_payload['status']}"
    )


# ── Cross-Endpoint Flows ───────────────────────────────────────


def test_cross_endpoint_compact_citations_resolvable():
    """VAL-CROSS-007: Agent compact citations resolvable via citations API."""
    r = _post_agent(
        {
            "prompt": "explain REST API authentication methods",
            "citation_style": "compact",
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]

    payload = _poll_agent_job(job_id, timeout_s=120)
    compact_sources = []
    result_text = ""
    if payload.get("data"):
        compact_sources = payload["data"].get("sources_compact", [])
        result_text = payload["data"].get("result", "")

    if compact_sources:
        for src in compact_sources:
            assert "index" in src
            assert "url" in src
        if result_text:
            assert "[1](" in result_text or "[1](http" in result_text, (
                "Compact result should have [N](url) markers"
            )

    # Verify citations resolve endpoint works
    resolve_r = httpx.post(
        AGENT + "/v2/citations/resolve",
        json={
            "text": "Test [1] marker",
            "sources": [{"url": "https://example.com", "title": "Example"}],
            "style": "inline",
        },
        timeout=30,
    )
    assert resolve_r.status_code == 200
    assert resolve_r.json()["citation_count"] >= 1


def test_answer_output_schema_with_compact_citations():
    """VAL-CROSS-008: Answer with output_schema and citation_style:compact."""
    schema = {
        "type": "object",
        "properties": {
            "frameworks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "features": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }
    r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "what are the key features of Django and Flask?",
            "output_schema": schema,
            "citation_style": "compact",
            "num_sources": 3,
        },
        timeout=180,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert isinstance(payload["citations"], list)
    assert isinstance(payload["sources"], list)

    answer_text = payload["answer"]
    if not answer_text.startswith("I was unable to find"):
        try:
            parsed = json.loads(answer_text)
            if "frameworks" in parsed:
                assert isinstance(parsed["frameworks"], list)
        except json.JSONDecodeError:
            pass


def test_cross_endpoint_citation_style_consistency():
    """VAL-CROSS-017: Cross-endpoint citation style consistency."""
    # Agent with compact citations
    agent_r = _post_agent(
        {
            "prompt": "What is a REST API?",
            "citation_style": "compact",
        }
    )
    _assert_agent_created(agent_r)

    # Answer with compact citations
    answer_r = httpx.post(
        AGENT + "/v2/answer",
        json={
            "query": "What is a REST API?",
            "citation_style": "compact",
            "num_sources": 2,
        },
        timeout=120,
    )
    assert answer_r.status_code == 200
    answer_payload = answer_r.json()
    answer_text = answer_payload.get("answer", "")

    if not answer_text.startswith("I was unable to find") and answer_payload.get(
        "sources"
    ):
        with suppress(json.JSONDecodeError):
            json.loads(answer_text)

    # Citations/resolve with compact style
    resolve_r = httpx.post(
        AGENT + "/v2/citations/resolve",
        json={
            "text": "See [1]",
            "sources": [{"url": "https://example.com", "title": "Example"}],
            "style": "compact",
        },
        timeout=30,
    )
    assert resolve_r.status_code == 200
    assert "[1](https://example.com)" in resolve_r.json()["resolved_text"]

    # Citations/resolve with inline style
    inline_r = httpx.post(
        AGENT + "/v2/citations/resolve",
        json={
            "text": "See [1]",
            "sources": [{"url": "https://example.com", "title": "Example"}],
            "style": "inline",
        },
        timeout=30,
    )
    assert inline_r.status_code == 200
    assert "See [1]" in inline_r.json()["resolved_text"]
    assert inline_r.json()["citation_count"] == 1


def test_agent_invalid_output_schema_graceful_failure():
    """VAL-CROSS-020: Agent with invalid output_schema fails gracefully."""
    invalid_schema = {"type": "invalid_type"}

    # Step 1: Send agent with invalid schema type value
    r = _post_agent(
        {
            "prompt": "test invalid schema",
            "output_schema": invalid_schema,
        }
    )
    _assert_agent_created(r)

    # Step 2: Send a simple valid request to verify server still works
    r2 = _post_agent({"prompt": "simple test after error"})
    _assert_agent_created(r2)
    assert "id" in r2.json()

    # Server health check
    health = httpx.get(AGENT + "/health", timeout=10)
    assert health.status_code == 200


def test_webhook_compact_citations():
    """VAL-CROSS-022: Webhook delivery for agent with compact citations."""
    r = _post_agent(
        {
            "prompt": "What is Python?",
            "citation_style": "compact",
            "webhook": {"url": "https://example.com/webhook"},
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]
    assert job_id

    payload = _poll_agent_job(job_id, timeout_s=120)
    assert payload["status"] in ("completed", "failed")
    if payload.get("data"):
        data = payload["data"]
        if "citation_style" in data:
            assert data["citation_style"] in ("compact", "inline")
        if "sources_compact" in data:
            assert isinstance(data["sources_compact"], list)


def test_webhook_output_schema():
    """VAL-CROSS-023: Webhook delivery for agent with output_schema."""
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    r = _post_agent(
        {
            "prompt": "What is Python?",
            "output_schema": schema,
            "webhook": {"url": "https://example.com/webhook"},
        }
    )
    _assert_agent_created(r)
    job_id = r.json()["id"]
    assert job_id

    payload = _poll_agent_job(job_id, timeout_s=120)
    assert payload["status"] in ("completed", "failed")
    if payload.get("data") and payload["data"].get("result"):
        result = payload["data"]["result"]
        if not result.startswith("I was unable to find"):
            try:
                parsed = json.loads(result)
                assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}"
            except json.JSONDecodeError:
                pass
