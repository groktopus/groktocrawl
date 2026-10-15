"""Tests for the authorized Candidate D comparison runner."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_candidate_d_comparison.py"
SPEC = importlib.util.spec_from_file_location("candidate_d_comparison", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_schedule_is_paired_bounded_and_reproducible() -> None:
    cases = [{"case_id": f"case-{index}"} for index in range(30)]

    first = MODULE.schedule(cases, 20260910)
    second = MODULE.schedule(cases, 20260910)

    assert first == second
    assert len(first) == 300
    for index in range(0, len(first), 2):
        pair = first[index : index + 2]
        assert {item["arm"] for item in pair} == {"A", "D"}
        assert len({item["case_id"] for item in pair}) == 1
        assert len({item["trial"] for item in pair}) == 1


def test_selected_sources_preserve_packet_order() -> None:
    case = {
        "required_subquestions": [
            {"resolving_source_ids": ["source-3", "source-1"]},
            {"resolving_source_ids": ["source-3"]},
        ]
    }
    sources = [
        {"source_id": "source-1"},
        {"source_id": "source-2"},
        {"source_id": "source-3"},
    ]

    selected = MODULE.selected_sources(case, sources)

    assert [item["source_id"] for item in selected] == ["source-1", "source-3"]


@pytest.mark.asyncio
async def test_meter_refuses_call_past_arm_ceiling() -> None:
    class UnusedTransport:
        async def __call__(self, request: object) -> None:
            raise AssertionError("ceiling must be checked before transport")

    metered = MODULE.MeteredTransport(UnusedTransport())
    metered.arm = "A"
    metered.calls["A"] = 150

    with pytest.raises(ValueError, match="ceiling exhausted"):
        await metered.complete(object())


@pytest.mark.asyncio
async def test_transport_failure_streak_resets_only_after_success() -> None:
    replies = [
        RuntimeError("down"),
        RuntimeError("down"),
        SimpleNamespace(
            resolved_model="local",
            input_tokens=1,
            output_tokens=1,
            raw_content_digest="sha256:test",
        ),
    ]

    class SequencedTransport:
        async def __call__(self, request: object) -> object:
            result = replies.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    metered = MODULE.MeteredTransport(SequencedTransport())
    metered.arm = "A"
    request = SimpleNamespace(requested_model="local")
    for expected in (1, 2):
        with pytest.raises(RuntimeError, match="down"):
            await metered.complete(request)
        assert metered.consecutive_transport_failures == expected

    assert await metered.complete(request) is not None
    assert metered.consecutive_transport_failures == 0
