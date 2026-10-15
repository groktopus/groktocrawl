"""Actual PostgreSQL connection saturation, cleanup and fail-fast behavior."""

import asyncio
import unittest

import psycopg
from agent.experimental.research_import_store import ResearchImportStore
from agent.experimental.source_store import SourceStore
from agent.experimental.storage_admission import StorageAdmission, StorageBusyError


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = StorageAdmission(2)
        self.first = SourceStore(admission=self.guard)
        self.second = ResearchImportStore(admission=self.guard)

    async def check_reuse(self):
        async with self.first._transaction(read=True) as conn:
            row = await (await conn.execute("SELECT 42 AS answer")).fetchone()
            self.assertEqual(row["answer"], 42)
        self.assertTrue(conn.closed)

    async def test_burst_across_subclasses_rejected_before_connect(self):
        # Invalid service proves overflow never reaches libpq connection setup.
        overflow = SourceStore(
            "service=groktocrawl_x_missing_admission_probe", admission=self.guard
        )

        async def excess():
            with self.assertRaises(StorageBusyError):
                async with overflow._transaction(bootstrap=True):
                    self.fail("overflow transaction entered")

        async with self.first._transaction() as first:
            async with self.second._transaction(read=True) as second:
                self.assertNotEqual(first.info.backend_pid, second.info.backend_pid)
                await asyncio.gather(*(excess() for _ in range(20)))
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        await self.check_reuse()

    async def test_sql_failure_releases_slot_after_close(self):
        self.first = SourceStore(admission=StorageAdmission(1))
        with self.assertRaises(psycopg.errors.DivisionByZero):
            async with self.first._transaction() as conn:
                await conn.execute("SELECT 1/0")
        self.assertTrue(conn.closed)
        await self.check_reuse()

    async def test_connect_failure_releases_slot(self):
        guard = StorageAdmission(1)
        broken = SourceStore(
            "service=groktocrawl_x_missing_admission_probe", admission=guard
        )
        with self.assertRaises(psycopg.OperationalError):
            async with broken._transaction():
                self.fail("invalid service connected")
        self.first = SourceStore(admission=guard)
        await self.check_reuse()

    async def test_cancellation_closes_before_readmission(self):
        self.first = SourceStore(admission=StorageAdmission(1))
        entered = asyncio.Event()
        connection = None

        async def operation():
            nonlocal connection
            async with self.first._transaction() as conn:
                connection = conn
                entered.set()
                await asyncio.Future()

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            with self.assertRaises(StorageBusyError):
                await self.check_reuse()
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(connection)
        self.assertTrue(connection.closed)
        await self.check_reuse()

    async def test_statement_timeout_releases_slot(self):
        self.first = SourceStore(admission=StorageAdmission(1))
        with self.assertRaises(psycopg.errors.QueryCanceled):
            async with self.first._transaction() as conn:
                await conn.execute("SET LOCAL statement_timeout = 10")
                await conn.execute("SELECT pg_sleep(1)")
        self.assertTrue(conn.closed)
        await self.check_reuse()


if __name__ == "__main__":
    unittest.main(verbosity=2)
