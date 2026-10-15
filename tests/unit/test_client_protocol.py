"""Golden and negative client protocol trace fixtures."""

import pytest
from agent.experimental.client_protocol import (
    ProtocolEvent,
    client_projections,
    replay_after,
    validate_trace,
)


def accepted() -> ProtocolEvent:
    return ProtocolEvent(
        protocol_version="research/1",
        run_id="run-1",
        sequence=1,
        event="accepted",
        state="accepted",
    )


def progress(sequence: int = 2) -> ProtocolEvent:
    return ProtocolEvent(
        protocol_version="research/1",
        run_id="run-1",
        sequence=sequence,
        event="progress",
        state="running",
        stage="verification",
    )


def done(sequence: int = 3) -> ProtocolEvent:
    return ProtocolEvent(
        protocol_version="research/1",
        run_id="run-1",
        sequence=sequence,
        event="done",
        state="completed",
        result={"artifact_set_id": "set-1", "answer_coverage": "partial"},
    )


def test_status_cli_and_mcp_share_one_terminal_projection():
    trace = validate_trace((accepted(), progress(), done()))
    projections = client_projections(trace)
    assert projections["status"] == projections["cli"] == projections["mcp"]
    assert projections["status"]["result"]["artifact_set_id"] == "set-1"


def test_replay_deduplicates_from_a_valid_cursor():
    trace = (accepted(), progress(), done())
    assert replay_after(trace, "run-1:1") == trace[1:]
    assert replay_after((*trace, done()), "run-1:2") == (trace[2],)


def test_foreign_or_gapped_cursor_fails_closed():
    trace = (accepted(), progress(), done())
    with pytest.raises(ValueError, match="another run"):
        replay_after(trace, "run-2:1")
    with pytest.raises(ValueError, match="outside"):
        replay_after(trace, "run-1:4")
    with pytest.raises(ValueError, match="contiguous"):
        validate_trace((accepted(), done(3)))


@pytest.mark.parametrize(
    "events,match",
    [
        ((progress(1), done(2)), "begin with accepted"),
        ((accepted(), done(2), progress(3)), "terminal event must be last"),
        ((accepted(), progress(), done(), done(4)), "terminal event must be last"),
    ],
)
def test_terminal_and_replay_invariants_reject_invalid_traces(events, match):
    with pytest.raises(ValueError, match=match):
        validate_trace(events)


def test_error_and_cancellation_are_distinct_terminal_projections():
    error = (
        accepted(),
        ProtocolEvent(
            protocol_version="research/1",
            run_id="run-1",
            sequence=2,
            event="error",
            state="failed",
            error={"code": "render_audit_failed", "retryable": False},
        ),
    )
    cancelled = (
        accepted(),
        ProtocolEvent(
            protocol_version="research/1",
            run_id="run-1",
            sequence=2,
            event="cancelled",
            state="cancelled",
        ),
    )
    assert client_projections(error)["status"]["state"] == "failed"
    assert client_projections(cancelled)["status"]["state"] == "cancelled"
