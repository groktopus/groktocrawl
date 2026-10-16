"""Recipient retention cannot exceed any independent lifetime boundary."""

from datetime import UTC, datetime, timedelta

import pytest
from agent.experimental.import_store import effective_retention
from agent.experimental.source_store import StorageConflictError

NOW = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    "exported,origin,expected", [(2, 5, 2), (5, 2, 2), (60, 60, 30)]
)
def test_shortest_retention_wins(exported, origin, expected):
    assert effective_retention(
        NOW + timedelta(days=exported), NOW + timedelta(days=origin), NOW
    ) == NOW + timedelta(days=expected)


@pytest.mark.parametrize("exported,origin", [(0, 5), (5, 0), (-1, 5)])
def test_expired_import_is_rejected(exported, origin):
    with pytest.raises(StorageConflictError):
        effective_retention(
            NOW + timedelta(days=exported), NOW + timedelta(days=origin), NOW
        )


@pytest.mark.parametrize("position", range(3))
def test_unqualified_time_is_rejected(position):
    times = [NOW + timedelta(days=1), NOW + timedelta(days=2), NOW]
    times[position] = times[position].replace(tzinfo=None)
    with pytest.raises(ValueError):
        effective_retention(*times)
