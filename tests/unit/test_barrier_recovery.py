import pytest
from scraper.barrier_recovery import (
    attach_recovery,
    browser_service_applicable,
    flaresolverr_applicable,
    new_recovery_receipt,
    record_attempt,
)


def test_turnstile_receipt_preserves_in_page_attempts_and_selects_all_fallbacks():
    receipt = new_recovery_receipt(
        {
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {
                "type": "captcha",
                "provider": "turnstile",
                "attempted_strategies": [
                    "passive_wait",
                    "checkbox",
                    "vision_grid",
                ],
            },
        }
    )

    assert flaresolverr_applicable(receipt) is True
    assert browser_service_applicable(receipt) is True
    assert receipt["attempts"][0]["sub_attempts"] == [
        "passive_wait",
        "checkbox",
        "vision_grid",
    ]


def test_non_cloudflare_captcha_skips_flaresolverr_but_uses_fresh_browser():
    receipt = new_recovery_receipt(
        {"barrier": {"type": "captcha", "provider": "hcaptcha"}}
    )

    assert flaresolverr_applicable(receipt) is False
    assert browser_service_applicable(receipt) is True


def test_unclassified_browser_miss_keeps_cloudflare_recovery_available():
    receipt = new_recovery_receipt(None)

    assert flaresolverr_applicable(receipt) is True
    assert browser_service_applicable(receipt) is True


def test_rate_limit_does_not_invoke_unrelated_browser_countermeasures():
    receipt = new_recovery_receipt({"barrier": {"type": "rate-limit"}})

    assert flaresolverr_applicable(receipt) is False
    assert browser_service_applicable(receipt) is False


def test_finalized_receipt_is_copied_and_marks_exhaustion():
    receipt = new_recovery_receipt({"barrier": {"type": "fastly"}})
    record_attempt(receipt, "flaresolverr", "skipped", "not_applicable")
    result = attach_recovery(
        {"error": "Barrier detected", "barrier": {"type": "fastly"}},
        receipt,
        recovered=False,
    )

    assert result["recovery"]["outcome"] == "unresolved"
    assert result["recovery"]["exhausted"] is True
    assert receipt["outcome"] == "in_progress"


@pytest.mark.asyncio
async def test_recovery_preserves_typed_captcha_after_later_barriers(monkeypatch):
    import scraper.fetch as fetch

    async def allow(*_args, **_kwargs):
        return None

    async def flare(_url):
        return {
            "error": "Cloudflare remained",
            "barrier": {"type": "cloudflare"},
            "source": "barrier-detection",
        }

    async def browser(_url):
        return None

    monkeypatch.setattr(fetch, "_politeness_check_for_tier", allow)
    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", flare)
    monkeypatch.setattr(fetch, "_fetch_via_browser_svc", browser)
    result = await fetch._recover_from_barrier(
        "https://example.test",
        {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"type": "captcha", "provider": "turnstile"},
        },
        [],
    )

    assert result["error_code"] == "CAPTCHA_UNRESOLVED"
    assert [a["outcome"] for a in result["recovery"]["attempts"]] == [
        "barrier",
        "barrier",
        "unavailable",
    ]


@pytest.mark.asyncio
async def test_recovery_rejects_low_quality_results_and_finishes_typed(monkeypatch):
    import scraper.fetch as fetch

    async def allow(*_args, **_kwargs):
        return None

    async def low_quality(_url):
        return {"markdown": "thin", "source": "candidate"}

    async def reject(result, _label, best_effort):
        best_effort.append(result)
        return None

    monkeypatch.setattr(fetch, "_politeness_check_for_tier", allow)
    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", low_quality)
    monkeypatch.setattr(fetch, "_fetch_via_browser_svc", low_quality)
    monkeypatch.setattr(fetch, "_maybe_degrade", reject)
    result = await fetch._recover_from_barrier(
        "https://example.test",
        {"error": "Barrier detected", "barrier": {"type": "cloudflare"}},
        [],
    )

    assert result["error_code"] == "BARRIER_DETECTED"
    assert [a["outcome"] for a in result["recovery"]["attempts"]] == [
        "barrier",
        "low_quality",
        "low_quality",
    ]


@pytest.mark.asyncio
async def test_recovery_stops_when_politeness_blocks_a_countermeasure(monkeypatch):
    import scraper.fetch as fetch

    async def blocked(*_args, **_kwargs):
        return {"error": "Blocked by politeness", "source": "politeness"}

    monkeypatch.setattr(fetch, "_politeness_check_for_tier", blocked)
    result = await fetch._recover_from_barrier(
        "https://example.test",
        {"error": "Barrier detected", "barrier": {"type": "cloudflare"}},
        [],
    )

    assert result["error"] == "Blocked by politeness"
    assert result["recovery"]["exhausted"] is True


@pytest.mark.asyncio
async def test_recovery_caches_accepted_browser_result(monkeypatch):
    import scraper.fetch as fetch

    writes = []

    async def allow(*_args, **_kwargs):
        return None

    async def browser(_url):
        return {"markdown": "recovered " * 40, "source": "browser-svc"}

    async def accept(result, *_args):
        return result

    async def cache(*args, **kwargs):
        writes.append((args, kwargs))

    monkeypatch.setattr(fetch, "_politeness_check_for_tier", allow)
    monkeypatch.setattr(fetch, "_fetch_via_browser_svc", browser)
    monkeypatch.setattr(fetch, "_maybe_degrade", accept)
    monkeypatch.setattr(fetch, "_set_cache", cache)
    result = await fetch._recover_from_barrier(
        "https://example.test",
        {
            "error": "CAPTCHA challenge could not be resolved",
            "error_code": "CAPTCHA_UNRESOLVED",
            "barrier": {"type": "captcha", "provider": "hcaptcha"},
        },
        [],
        prior_cache_entry={"cached": True},
        cache_success=True,
    )

    assert result["recovery"]["outcome"] == "recovered"
    assert len(writes) == 1
