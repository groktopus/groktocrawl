from scripts.run_w11_saved_search import assess


def test_assess_accepts_no_change_pause_replay_ack_resume_and_delete() -> None:
    event = {"event_id": "event-1", "event_type": "definition_paused"}
    public = assess(
        baseline={"reports": [{"status": "baseline_initialized"}]},
        compared={
            "reports": [
                {"status": "compared", "events": []},
                {"status": "baseline_initialized", "events": []},
            ],
            "note": "absence is never a deletion",
        },
        paused={"paused": True},
        paused_report_count=2,
        first_events={"events": [event]},
        repeated_events={"events": [event]},
        first_ack={"acknowledged_cursor": "1-0"},
        repeated_ack={"acknowledged_cursor": "1-0"},
        after_ack={"events": []},
        resumed={"paused": False},
        deleted={"state": "deleted"},
    )
    assert public["hard_gate_passed"] is True


def test_assess_rejects_a_change_event_during_no_change_interval() -> None:
    event = {"event_id": "event-1", "event_type": "definition_paused"}
    public = assess(
        baseline={"reports": [{"status": "baseline_initialized"}]},
        compared={
            "reports": [
                {"status": "compared", "events": [{"kind": "changed"}]},
                {"status": "baseline_initialized", "events": []},
            ],
            "note": "absence is never a deletion",
        },
        paused={"paused": True},
        paused_report_count=2,
        first_events={"events": [event]},
        repeated_events={"events": [event]},
        first_ack={"acknowledged_cursor": "1-0"},
        repeated_ack={"acknowledged_cursor": "1-0"},
        after_ack={"events": []},
        resumed={"paused": False},
        deleted={"state": "deleted"},
    )
    assert public["hard_gate_passed"] is False
