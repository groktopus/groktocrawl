"""Fail-fast connection admission for trusted experimental storage owners."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore


class StorageBusyError(RuntimeError):
    """No connection slot available; no connection or transaction was started."""


class StorageAdmission:
    """One shared budget across instances, threads and event loops in a process.

    No waiter queue, automatic retries or connection pooling. Separate processes
    and explicitly separate guards have independent budgets.
    """

    def __init__(self, limit: int = 8) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("connection limit must be a positive integer")
        self._slots = BoundedSemaphore(limit)

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            raise StorageBusyError("experimental storage connection budget exhausted")
        try:
            yield
        finally:
            self._slots.release()


# Shared by every adapter subclass and connection destination unless a trusted
# owner deliberately injects a separately budgeted guard. Contains no credentials.
DEFAULT_STORAGE_ADMISSION = StorageAdmission()
