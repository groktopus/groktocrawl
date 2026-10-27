"""Dispatch accounting is enforced before sends, including failures/retries."""

import asyncio
import gzip
import hashlib
import json

import httpx
import pytest
from agent.experimental.calibration_budget import (
    BudgetedTransport,
    CalibrationLedger,
    CalibrationStoppedError,
    ExclusiveJournal,
    ScreeningCaps,
    inspect_journal,
    unresolved_source_key,
)

MODEL = "https://configured-gateway.example/v1/chat/completions"
SCRAPE = "http://configured-scraper:8001/scrape"
RECEIPTS = {"x-litellm-attempted-retries": "0", "x-litellm-attempted-fallbacks": "0"}


def ledger(**kwargs):
    log = []
    result = CalibrationLedger("screen-1", log.append, **kwargs)
    result.start_attempt("case-a/current")
    return result, log


def model_request(**extra):
    return {
        "model": "free",
        "messages": [{"role": "user", "content": "public question"}],
        "max_tokens": 8192,
        **extra,
    }


def model_response(**extra):
    return {
        "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        **extra,
    }


@pytest.mark.asyncio
async def test_reservation_is_journaled_before_send_and_bounds_are_request_local():
    book, log = ledger()
    seen = []

    async def send(request):
        assert log[-1]["state"] == "dispatch_reserved"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=model_response(), headers=RECEIPTS)

    transport = BudgetedTransport(book, "model", MODEL, httpx.MockTransport(send))
    async with httpx.AsyncClient(transport=transport) as client:
        for _ in range(4):
            response = await client.post(
                MODEL,
                json=model_request(),
                headers={"Authorization": "Bearer DO-NOT-LOG"},
            )
            assert response.json()["usage"]["completion_tokens"] == 4
        with pytest.raises(CalibrationStoppedError, match="model_cap"):
            await client.post(MODEL, json=model_request())
    assert len(seen) == 4 and all(x["max_tokens"] == 256 for x in seen)
    assert all(
        x["num_retries"] == x["max_retries"] == 0 and x["disable_fallbacks"] is True
        for x in seen
    )
    assert "DO-NOT-LOG" not in json.dumps(log)
    assert "public question" not in json.dumps(log)
    assert sum(x.get("output_token_reservation", 0) for x in log) == 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancel", "network"])
async def test_ambiguous_dispatch_latches_stop_even_if_caller_retries(failure):
    book, log = ledger()
    calls = 0

    async def send(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise TimeoutError
        if failure == "cancel":
            raise asyncio.CancelledError
        raise httpx.ReadError("connection lost")

    transport = BudgetedTransport(book, "model", MODEL, httpx.MockTransport(send))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises((TimeoutError, asyncio.CancelledError, httpx.ReadError)):
            await client.post(MODEL, json=model_request())
        with pytest.raises(CalibrationStoppedError, match="outcome_unknown"):
            await client.post(MODEL, json=model_request())
    assert calls == 1
    assert log[-1]["state"] == "outcome_unknown"
    assert log[-1]["server_cancellation_confirmed"] is False
    with pytest.raises(CalibrationStoppedError, match="outcome_unknown"):
        book.finish_attempt("failed")


@pytest.mark.asyncio
async def test_real_async_deadline_is_not_server_cancellation_confirmation():
    book, log = ledger(caps=ScreeningCaps(request_seconds=0.01))

    async def send(request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=BudgetedTransport(book, "model", MODEL, httpx.MockTransport(send))
    ) as client:
        with pytest.raises(TimeoutError):
            await client.post(MODEL, json=model_request())
    assert book.halted == "outcome_unknown"
    assert log[-1]["server_cancellation_confirmed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"usage": {"prompt_tokens": 1, "completion_tokens": True}}]
)
async def test_missing_usage_stops_without_inventing_zero_cost(body):
    book, log = ledger()
    async with httpx.AsyncClient(
        transport=BudgetedTransport(
            book,
            "model",
            MODEL,
            httpx.MockTransport(
                lambda r: httpx.Response(200, json=body, headers=RECEIPTS)
            ),
        )
    ) as client:
        with pytest.raises(CalibrationStoppedError, match="model_usage_unavailable"):
            await client.post(MODEL, json=model_request())
    event = next(x for x in log if x["state"] == "response_complete")
    assert event["usage"] is None and event["accounting_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"url": "https://public.example"},
        {
            "url": "https://public.example",
            "lightweight_only": True,
            "force_browser": True,
        },
        {
            "url": "https://public.example",
            "lightweight_only": True,
            "ignore_robots_txt": True,
        },
        {
            "url": "https://public.example",
            "lightweight_only": True,
            "contents": {"summary": True},
        },
    ],
)
async def test_unbounded_acquisition_is_rejected_before_dispatch(body):
    book, log = ledger()
    sent = []
    async with httpx.AsyncClient(
        transport=BudgetedTransport(
            book, "acquire", SCRAPE, httpx.MockTransport(lambda r: sent.append(r))
        )
    ) as client:
        with pytest.raises(CalibrationStoppedError):
            await client.post(SCRAPE, json=body)
    assert not sent and not any(x["state"] == "dispatch_reserved" for x in log)


@pytest.mark.asyncio
async def test_previous_unresolved_source_cannot_be_replayed():
    book, _ = ledger()
    source = "https://public.example/unresolved"
    hashes = frozenset(
        {hashlib.sha256(unresolved_source_key(source).encode()).hexdigest()}
    )
    transport = BudgetedTransport(
        book,
        "acquire",
        SCRAPE,
        httpx.MockTransport(lambda r: pytest.fail("must not send")),
        blocked_source_sha256=hashes,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(
            CalibrationStoppedError, match="unresolved_previous_source_operation"
        ):
            await client.post(SCRAPE, json={"url": source, "lightweight_only": True})


@pytest.mark.asyncio
async def test_redirect_cannot_spend_another_request_on_another_endpoint():
    book, _ = ledger()
    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/search"})

    async with httpx.AsyncClient(
        transport=BudgetedTransport(
            book,
            "search",
            "https://configured.example/search",
            httpx.MockTransport(send),
        ),
        follow_redirects=True,
    ) as client:
        with pytest.raises(CalibrationStoppedError, match="unexpected_endpoint"):
            await client.get("https://configured.example/search?q=public")
    assert len(calls) == 1


def test_exclusive_journal_retains_unfinished_reservation_after_crash(tmp_path):
    path = tmp_path / "receipts.jsonl"
    journal = ExclusiveJournal(path)
    book = CalibrationLedger("screen", journal)
    book.start_attempt("one")
    operation = book.reserve("search", httpx.Request("GET", "http://configured/search"))
    journal.close()
    assert inspect_journal(path)["unresolved_operation_ids"] == [operation]
    assert inspect_journal(path)["replay_authorized"] is False
    with pytest.raises(FileExistsError):
        ExclusiveJournal(path)


@pytest.mark.asyncio
async def test_journal_failure_prevents_any_network_send():
    book, _ = ledger()
    book.journal = lambda event: (_ for _ in ()).throw(OSError("disk full"))
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(lambda r: pytest.fail("must not send")),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="journal failed"):
            await client.post(MODEL, json=model_request())
    assert book.halted == "journal_failed"


def test_attempt_count_and_clock_deadlines_cannot_be_reset():
    clock = [0.0]
    book, _ = ledger(clock=lambda: clock[0])
    book.finish_attempt("completed")
    for i in range(5):
        book.start_attempt(str(i))
        book.finish_attempt("completed")
    with pytest.raises(CalibrationStoppedError, match="attempt_cap"):
        book.start_attempt("seventh")
    book, _ = ledger(clock=lambda: clock[0])
    clock[0] = 46
    with pytest.raises(CalibrationStoppedError, match="deadline"):
        book.reserve("search", httpx.Request("GET", "http://configured/search"))


@pytest.mark.asyncio
async def test_large_input_and_response_and_provider_overrun_are_explicit():
    book, _ = ledger()
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(lambda r: pytest.fail("must not send")),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="model_input_byte_cap"):
            await client.post(
                MODEL,
                json=model_request(messages=[{"role": "user", "content": "x" * 12000}]),
            )
    book, _ = ledger(caps=ScreeningCaps(response_bytes_per_dispatch=10))
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 11)),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="response_byte_cap"):
            await client.post(MODEL, json=model_request())
    assert book.halted == "outcome_unknown"
    book, _ = ledger()
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json=model_response(
                    usage={"prompt_tokens": 1, "completion_tokens": 257}
                ),
                headers=RECEIPTS,
            )
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(
            CalibrationStoppedError, match="provider_exceeded_output_cap"
        ):
            await client.post(MODEL, json=model_request())


@pytest.mark.asyncio
async def test_compressed_body_is_not_decoded_twice():
    book, _ = ledger()
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=gzip.compress(json.dumps(model_response()).encode()),
                headers={"Content-Encoding": "gzip", **RECEIPTS},
            )
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(MODEL, json=model_request())
    assert response.json()["usage"]["completion_tokens"] == 4


@pytest.mark.asyncio
async def test_real_upstream_model_client_uses_the_transport_reservation():
    from agent.llm import LLMClient

    book, log = ledger()
    seen = []

    def send(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=model_response(), headers=RECEIPTS)

    client = LLMClient("https://configured-gateway.example/v1", "fixture-key", "free")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=BudgetedTransport(book, "model", MODEL, httpx.MockTransport(send))
    )
    try:
        assert (
            await client.generate("system", "question", stage="gap_detection")
            == "answer"
        )
    finally:
        await client.close()
    assert len(seen) == 1 and seen[0]["max_tokens"] == 256
    assert log[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "1"])
async def test_missing_or_nonzero_gateway_retry_receipt_halts(header):
    book, log = ledger()
    headers = dict(RECEIPTS)
    if header is None:
        headers.pop("x-litellm-attempted-retries")
    else:
        headers["x-litellm-attempted-retries"] = header
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(
            lambda r: httpx.Response(200, json=model_response(), headers=headers)
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="gateway_attempt_accounting"):
            await client.post(MODEL, json=model_request())
    assert log[-2]["accounting_complete"] is False
    with pytest.raises(CalibrationStoppedError):
        book.finish_attempt("completed")
    assert log[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_six_attempts_cannot_exceed_global_token_reservation():
    book, log = ledger()
    for index in range(6):
        if index:
            book.start_attempt(str(index))
        transport = BudgetedTransport(
            book,
            "model",
            MODEL,
            httpx.MockTransport(
                lambda r: httpx.Response(200, json=model_response(), headers=RECEIPTS)
            ),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(4):
                await client.post(MODEL, json=model_request())
        book.finish_attempt("completed")
    assert sum(x.get("output_token_reservation", 0) for x in log) == 6144
    assert len([x for x in log if x["state"] == "dispatch_reserved"]) == 24
    with pytest.raises(CalibrationStoppedError):
        book.start_attempt("more")


@pytest.mark.asyncio
async def test_parallel_sends_cannot_oversubscribe_and_latched_stop_is_not_success():
    book, log = ledger(caps=ScreeningCaps(model_per_attempt=1))
    started = asyncio.Event()
    release = asyncio.Event()

    async def send(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=model_response(), headers=RECEIPTS)

    transport = BudgetedTransport(book, "model", MODEL, httpx.MockTransport(send))
    async with httpx.AsyncClient(transport=transport) as client:
        first = asyncio.create_task(client.post(MODEL, json=model_request()))
        await started.wait()
        with pytest.raises(CalibrationStoppedError, match="model_cap"):
            await client.post(MODEL, json=model_request())
        release.set()
        await first
    with pytest.raises(CalibrationStoppedError, match="model_cap"):
        book.finish_attempt("completed")
    assert len([x for x in log if x["state"] == "dispatch_reserved"]) == 1
    assert log[-1]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,cap", [("search", 2), ("acquire", 4)])
async def test_other_dispatch_caps_are_enforced_before_send(kind, cap):
    book, _ = ledger()
    url = "https://configured.example/search" if kind == "search" else SCRAPE
    sent = []

    def send(request):
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    transport = BudgetedTransport(book, kind, url, httpx.MockTransport(send))
    async with httpx.AsyncClient(transport=transport) as client:
        for _ in range(cap):
            await client.post(
                url, json={"url": "https://public.example", "lightweight_only": True}
            )
        with pytest.raises(CalibrationStoppedError):
            await client.post(
                url, json={"url": "https://public.example", "lightweight_only": True}
            )
    assert len(sent) == cap


@pytest.mark.parametrize(
    "override",
    [
        {"attempts": 7},
        {"model_per_attempt": 5},
        {"output_tokens_per_dispatch": 257},
        {"experiment_seconds": 301},
        {"request_seconds": float("nan")},
        {"attempts": True},
    ],
)
def test_screening_caps_cannot_be_silently_expanded(override):
    with pytest.raises(ValueError):
        ScreeningCaps(**override)


@pytest.mark.asyncio
async def test_install_preserves_all_existing_proxy_mounts(monkeypatch):
    from agent.experimental.calibration_budget import install_budget_boundary

    monkeypatch.setenv("HTTPS_PROXY", "http://configured-proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "")
    book = CalibrationLedger("screen", lambda event: None)
    async with httpx.AsyncClient() as client:
        patterns = set(client._mounts)
        assert patterns
        report = install_budget_boundary(client, book, "model", MODEL)
        assert set(client._mounts) == patterns
        assert isinstance(client._transport, BudgetedTransport)
        assert all(
            t is None or isinstance(t, BudgetedTransport)
            for t in client._mounts.values()
        )
        assert report["proxy_mounts_preserved"] is True


@pytest.mark.asyncio
async def test_install_rejects_hidden_transport_retries_before_mutation():
    from agent.experimental.calibration_budget import install_budget_boundary

    book = CalibrationLedger("screen", lambda event: None)
    original = httpx.AsyncHTTPTransport(retries=1)
    async with httpx.AsyncClient(transport=original) as client:
        with pytest.raises(CalibrationStoppedError, match="retries are not zero"):
            install_budget_boundary(client, book, "model", MODEL)
        assert client._transport is original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"api_base": "https://other.example"},
        {"tools": [{}]},
        {"model": "paid-model"},
        {"temperature": 1.0},
    ],
)
async def test_model_routing_and_sampling_cannot_change(options):
    book, _ = ledger()
    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(lambda r: pytest.fail("must not send")),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError):
            await client.post(MODEL, json=model_request(**options))


@pytest.mark.asyncio
async def test_close_failure_cannot_leave_dispatch_unlatched():
    book, log = ledger()

    class BrokenClose(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(model_response()).encode()

        async def aclose(self):
            raise httpx.CloseError("close failed")

    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(
            lambda r: httpx.Response(200, stream=BrokenClose(), headers=RECEIPTS)
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.CloseError):
            await client.post(MODEL, json=model_request())
        with pytest.raises(CalibrationStoppedError, match="outcome_unknown"):
            await client.post(MODEL, json=model_request())
    assert log[-1]["state"] == "outcome_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "https://public.example/unresolved#section",
        "https://PUBLIC.EXAMPLE:443/unresolved",
    ],
)
async def test_canonical_unresolved_resource_variants_never_dispatch(variant):
    book, _ = ledger()
    blocked = frozenset(
        {
            hashlib.sha256(
                unresolved_source_key("https://public.example/unresolved").encode()
            ).hexdigest()
        }
    )
    transport = BudgetedTransport(
        book,
        "acquire",
        SCRAPE,
        httpx.MockTransport(lambda r: pytest.fail("equivalent resource must not send")),
        blocked_source_sha256=blocked,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="unresolved_previous"):
            await client.post(SCRAPE, json={"url": variant, "lightweight_only": True})


@pytest.mark.asyncio
async def test_hanging_cleanup_respects_original_request_deadline():
    book, _ = ledger(caps=ScreeningCaps(request_seconds=0.01))

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadError("stream interrupted")
            yield b""

        async def aclose(self):
            await asyncio.Event().wait()

    transport = BudgetedTransport(
        book,
        "model",
        MODEL,
        httpx.MockTransport(
            lambda r: httpx.Response(200, stream=BrokenStream(), headers=RECEIPTS)
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CalibrationStoppedError, match="response_cleanup_deadline"):
            await asyncio.wait_for(client.post(MODEL, json=model_request()), 0.1)
    assert book.halted == "outcome_unknown"
