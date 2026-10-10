"""Offline PostgreSQL scoped queue regression tests (no database contacted).

Real PostgreSQL RLS, SKIP LOCKED concurrency and restart tests remain blocked
until the staging Alembic migration and authenticated DB2 tests are confirmed.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import MagicMock

from app.infrastructure.postgres.scoped_run_queue import PgScopedRunQueue
from app.infrastructure.postgres.scoped_store import PgScopedStore
from sqlalchemy.dialects import postgresql


class PostgreSQLQueueTests(unittest.TestCase):
    def setUp(self):
        # Only test fixture bypasses store constructor; production always
        # requires a store validated by PgScopedStore.__init__.
        store = object.__new__(PgScopedStore)
        store.connection = MagicMock()
        store.workspace_id = uuid.uuid4()
        self.queue = PgScopedRunQueue(store)
        self.conn = store.connection
        self.run_id = uuid.uuid4()
        self.statements = []

    def set_sql_results(
        self, *, claim_attempt: int | None = 0, expired_attempts: tuple[int, ...] = ()
    ):
        def fake_execute(statement):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            self.statements.append(sql)
            result = MagicMock()
            result.rowcount = 1
            if "FOR UPDATE SKIP LOCKED" in sql and "FROM runs" in sql:
                if "runs.status" in sql and "runs.lease_until" in sql:
                    result.all.return_value = [
                        (uuid.uuid4(), attempt) for attempt in expired_attempts
                    ]
                else:
                    result.one_or_none.return_value = (
                        (self.run_id, claim_attempt) if claim_attempt is not None else None
                    )
            elif "SELECT run_events.id" in sql:
                result.scalar_one_or_none.return_value = None
            elif "coalesce(max(run_events.seq)" in sql:
                result.scalar_one.return_value = 2
            elif "FOR UPDATE" in sql and "FROM runs" in sql:
                result.scalar_one_or_none.return_value = self.run_id
            return result

        self.conn.execute.side_effect = fake_execute

    def test_requires_verified_postgres_store(self):
        with self.assertRaises(TypeError):
            PgScopedRunQueue(MagicMock())

    def test_claim_uses_skip_locked_and_generation_token(self):
        self.set_sql_results(claim_attempt=1)
        lease = self.queue.claim_next(worker_id="worker-01", lease_seconds=180)
        self.assertIsNotNone(lease)
        self.assertEqual(lease["run_id"], str(self.run_id))
        self.assertEqual(lease["attempt"], 2)
        self.assertEqual(lease["workspace_id"], str(self.queue.workspace_id))
        self.assertIn("FOR UPDATE SKIP LOCKED", self.statements[0])
        self.assertIn("runs.workspace_id", self.statements[0])
        self.assertTrue(any("INSERT INTO run_events" in sql for sql in self.statements))
        self.conn.commit.assert_not_called()

    def test_empty_queue_does_not_modify_anything(self):
        self.set_sql_results(claim_attempt=None)
        self.assertIsNone(self.queue.claim_next(worker_id="worker-01"))
        self.assertEqual(len(self.statements), 1)

    def test_heartbeat_is_fenced_by_attempt_and_lease(self):
        self.set_sql_results()
        self.assertTrue(self.queue.heartbeat(self.run_id, attempt=3))
        sql = self.statements[-1]
        self.assertIn("UPDATE runs", sql)
        self.assertIn("runs.attempts", sql)
        self.assertIn("runs.lease_until", sql)
        self.assertIn("runs.workspace_id", sql)
        self.conn.execute.side_effect = None
        self.conn.execute.return_value.rowcount = 0
        self.assertFalse(self.queue.heartbeat(self.run_id, attempt=3))

    def test_stale_finish_does_not_write_terminal_result(self):
        self.set_sql_results()
        self.conn.execute.side_effect = None
        self.conn.execute.return_value.scalar_one_or_none.return_value = None
        self.assertFalse(self.queue.finalize(self.run_id, attempt=3, status="SUCCEEDED"))
        self.assertEqual(self.conn.execute.call_count, 1)

    def test_valid_finish_records_one_append_only_event(self):
        self.set_sql_results()
        self.assertTrue(
            self.queue.finalize(self.run_id, attempt=1, status="SUCCEEDED", output="ok")
        )
        self.assertIn("FOR UPDATE", self.statements[0])
        self.assertIn("UPDATE runs", self.statements[1])
        self.assertTrue(any("INSERT INTO run_events" in sql for sql in self.statements))
        self.conn.commit.assert_not_called()

    def test_recover_expired_locked_work_and_exhausted_attempt(self):
        self.set_sql_results(expired_attempts=(2, 5))
        recovered = self.queue.recover_expired(max_attempts=5)
        self.assertEqual([x["status"] for x in recovered], ["QUEUED", "FAILED"])
        self.assertIn("FOR UPDATE SKIP LOCKED", self.statements[0])
        self.assertIn("runs.workspace_id", self.statements[0])
        self.assertEqual(sum("INSERT INTO run_events" in s for s in self.statements), 2)

    def test_invalid_mutating_inputs_fail_without_query(self):
        self.set_sql_results()
        for operation in (
            lambda: self.queue.claim_next(worker_id=""),
            lambda: self.queue.claim_next(worker_id="valid", lease_seconds=5),
            lambda: self.queue.claim_next(worker_id="valid", max_attempts=11),
            lambda: self.queue.heartbeat(self.run_id, attempt=0),
            lambda: self.queue.finalize(self.run_id, attempt=1, status="QUEUED"),
            lambda: self.queue.recover_expired(max_count=0),
        ):
            with self.subTest(operation=str(operation)):
                with self.assertRaises(ValueError):
                    operation()
        self.assertEqual(self.conn.execute.call_count, 0)


if __name__ == "__main__":
    unittest.main()
