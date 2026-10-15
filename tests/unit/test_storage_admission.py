"""Admission is process-shared, immediate and independent of event-loop ownership."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.source_store import SourceStore
from agent.experimental.storage_admission import StorageAdmission, StorageBusyError


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "8"])
def test_invalid_connection_budget(limit):
    with pytest.raises(ValueError, match="positive integer"):
        StorageAdmission(limit)


def test_saturation_and_exception_release():
    guard = StorageAdmission(1)
    with pytest.raises(LookupError):
        with guard.slot():
            with pytest.raises(StorageBusyError):
                with guard.slot():
                    pytest.fail("overflow admitted")
            raise LookupError("operation failed")
    with guard.slot():
        pass


def test_shared_across_threads_without_waiting():
    guard = StorageAdmission(1)

    def enter():
        with guard.slot():
            return "admitted"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with guard.slot():
            with pytest.raises(StorageBusyError):
                executor.submit(enter).result(timeout=2)
        assert executor.submit(enter).result(timeout=2) == "admitted"


def test_default_shared_across_instances_subclasses_and_destinations():
    first = SourceStore("service=first")
    second = ResearchImportStore("service=second")
    assert first._admission is second._admission
    isolated = StorageAdmission(1)
    assert SourceStore(admission=isolated)._admission is isolated
