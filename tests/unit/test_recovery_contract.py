"""Recovery ownership, fencing and terminal-race contract fixtures."""

import pytest
from agent.experimental.recovery_contract import RecoveryContract, RecoveryLedger


def contract(**overrides):
    values = {
        "schema_version": "recovery-contract-prototype/1",
        "execution_owner": "application-owner",
        "authoritative_store": "artifact-authority",
        "lease_seconds": 30.0,
        "reclaim_delay_seconds": 10.0,
        "heartbeat_seconds": 5.0,
        "max_run_age_seconds": 3600.0,
        "retry_window_seconds": 300.0,
        "provider_timeout_seconds": 20.0,
        "retry_owner": "application-owner",
        "outbox_authority": "artifact-authority",
        "reconciliation_owner": "application-owner",
    }
    values.update(overrides)
    return RecoveryContract(**values)


def test_contract_requires_fenced_timing_relationships():
    assert contract().lease_seconds == 30
    with pytest.raises(ValueError, match="shorter than the lease"):
        contract(heartbeat_seconds=30.0)
    with pytest.raises(ValueError, match="cannot precede heartbeat"):
        contract(reclaim_delay_seconds=1.0)
    with pytest.raises(ValueError, match="at-least-once"):
        contract(allows_exactly_once_network_effects=True)


def test_stale_owner_cannot_dispatch_or_publish():
    ledger = RecoveryLedger()
    ledger.admit("op-1", "a" * 64)
    ledger.dispatch("op-1", 1, "attempt-1")
    ledger.reclaim("op-1")
    with pytest.raises(ValueError, match="stale owner"):
        ledger.receipt("op-1", 1, "b" * 64)
    ledger.dispatch("op-1", 2, "attempt-2")
    ledger.receipt("op-1", 2, "b" * 64)
    ledger.publish("op-1", 2, "b" * 64)
    assert ledger.operations["op-1"].state == "committed"


def test_receipts_are_idempotent_but_conflicts_fail():
    ledger = RecoveryLedger()
    ledger.admit("op-1", "a" * 64)
    ledger.dispatch("op-1", 1, "attempt-1")
    ledger.receipt("op-1", 1, "b" * 64)
    assert ledger.receipt("op-1", 1, "b" * 64) == "receipt"
    with pytest.raises(ValueError, match="conflicts"):
        ledger.receipt("op-1", 1, "c" * 64)


def test_cancellation_wins_before_publication_and_publication_wins_after():
    ledger = RecoveryLedger()
    ledger.admit("cancelled", "a" * 64)
    ledger.dispatch("cancelled", 1, "attempt-1")
    assert ledger.cancel("cancelled", 1) == "cancelled"
    with pytest.raises(ValueError, match="exact receipt"):
        ledger.publish("cancelled", 1, "b" * 64)

    ledger.admit("published", "c" * 64)
    ledger.dispatch("published", 1, "attempt-2")
    ledger.receipt("published", 1, "d" * 64)
    ledger.publish("published", 1, "d" * 64)
    assert ledger.cancel("published", 1) == "committed"


def test_provider_confirmation_reconciles_to_one_publishable_receipt():
    ledger = RecoveryLedger()
    ledger.admit("ambiguous", "a" * 64)
    ledger.dispatch("ambiguous", 1, "attempt-1")
    ledger.mark_unknown("ambiguous", 1)
    assert ledger.reconcile_unknown("ambiguous", 1, "confirmed", "b" * 64) == "receipt"
    ledger.publish("ambiguous", 1, "b" * 64)
    assert ledger.operations["ambiguous"].state == "committed"


def test_provider_absence_reopens_dispatch_without_a_receipt():
    ledger = RecoveryLedger()
    ledger.admit("absent", "a" * 64)
    ledger.dispatch("absent", 1, "attempt-1")
    ledger.mark_unknown("absent", 1)
    assert ledger.reconcile_unknown("absent", 1, "absent") == "dispatch_intent"
    assert ledger.operations["absent"].receipt_digest is None
    ledger.dispatch("absent", 1, "attempt-2")
    ledger.receipt("absent", 1, "c" * 64)
    ledger.publish("absent", 1, "c" * 64)


def test_persistent_ambiguity_stays_nonterminal_and_can_be_cancelled():
    ledger = RecoveryLedger()
    ledger.admit("still-unknown", "a" * 64)
    ledger.dispatch("still-unknown", 1, "attempt-1")
    ledger.mark_unknown("still-unknown", 1)
    assert ledger.reconcile_unknown("still-unknown", 1, "unknown") == "outcome_unknown"
    with pytest.raises(ValueError, match="exact receipt"):
        ledger.publish("still-unknown", 1, "b" * 64)
    assert ledger.cancel("still-unknown", 1) == "cancelled"


def test_stale_owner_cannot_reconcile_an_unknown_attempt():
    ledger = RecoveryLedger()
    ledger.admit("stale-unknown", "a" * 64)
    ledger.dispatch("stale-unknown", 1, "attempt-1")
    ledger.reclaim("stale-unknown")
    with pytest.raises(ValueError, match="stale owner"):
        ledger.mark_unknown("stale-unknown", 1)
    ledger.dispatch("stale-unknown", 2, "attempt-2")
    ledger.mark_unknown("stale-unknown", 2)
    with pytest.raises(ValueError, match="stale owner"):
        ledger.reconcile_unknown("stale-unknown", 1, "confirmed", "c" * 64)
