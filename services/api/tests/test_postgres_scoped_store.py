"""Offline PostgreSQL adapter contracts using SQLAlchemy statement compilation.

These tests do not claim real database execution; the CI pgvector service job
runs the corresponding real-RLS, version, Run/Event verification separately.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.domain.errors import VersionConflict
from app.domain.specs import AgentSpec, Goal
from app.infrastructure.postgres.scoped_store import PgScopedStore
from app.infrastructure.postgres.transaction import workspace_transaction
from sqlalchemy.dialects import postgresql


def valid_spec() -> AgentSpec:
    return AgentSpec(
        name="PostgreSQL 存储适配器测试",
        goal=Goal(task="验证 PostgreSQL 授权范围内的配置版本和任务创建"),
    )


def connection(*, role: str = "agentstudio_runtime", scope: uuid.UUID | None = None) -> MagicMock:
    instance = MagicMock()
    instance.dialect.name = "postgresql"
    instance.execute.return_value.rowcount = 1
    instance.execute.return_value.one.return_value = (
        role,
        str(uuid.uuid4()),
        str(scope) if scope is not None else None,
    )
    return instance


class PgScopedRepositoryContractTests(unittest.TestCase):
    def setUp(self):
        self.workspace_id = uuid.uuid4()
        self.agent_id = uuid.uuid4()

    def test_scoped_store_refuses_wrong_role_or_missing_scope(self):
        for role, scope in (
            ("studio_migrator", self.workspace_id),
            ("agentstudio_runtime", None),
            ("postgres", self.workspace_id),
            ("agentstudio_runtime", uuid.uuid4()),
        ):
            with self.subTest(role=role, scope=scope):
                conn = connection(role=role, scope=scope)
                with self.assertRaises(PermissionError):
                    PgScopedStore(conn, self.workspace_id)
                self.assertEqual(conn.execute.call_count, 1)

    def test_store_rejects_malformed_actor_id(self):
        conn = connection(scope=self.workspace_id)
        conn.execute.return_value.one.return_value = (
            "agentstudio_runtime",
            "invalid-user-uuid",
            str(self.workspace_id),
        )
        with self.assertRaises(PermissionError):
            PgScopedStore(conn, self.workspace_id)

    def test_sqlite_and_unverified_workspace_type_are_rejected(self):
        conn = connection(scope=self.workspace_id)
        conn.dialect.name = "sqlite"
        with self.assertRaises(ValueError):
            PgScopedStore(conn, self.workspace_id)
        conn.dialect.name = "postgresql"
        with self.assertRaises(ValueError):
            PgScopedStore(conn, str(self.workspace_id))

    def test_create_agent_generates_atomic_core_insert_statements(self):
        conn = connection(scope=self.workspace_id)
        store = PgScopedStore(conn, self.workspace_id)
        created = store.create_agent(valid_spec())
        self.assertEqual(created["workspace_id"], str(self.workspace_id))
        self.assertEqual(created["current_version"], 1)
        statements = [
            str(call.args[0].compile(dialect=postgresql.dialect()))
            for call in conn.execute.call_args_list[1:]
        ]
        self.assertEqual(len(statements), 2)
        self.assertIn("INSERT INTO agents", statements[0])
        self.assertIn("INSERT INTO agent_versions", statements[1])
        # No conn.commit(): transaction commit/rollback belongs to UnitOfWork.
        self.assertEqual(conn.commit.call_count, 0)

    def test_agent_read_is_scoped_in_sql_as_well_as_rls(self):
        conn = connection(scope=self.workspace_id)
        store = PgScopedStore(conn, self.workspace_id)
        now = datetime.now(UTC)
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": self.agent_id,
            "workspace_id": self.workspace_id,
            "spec": valid_spec().model_dump(mode="json"),
            "created_at": now,
            "current_version": 1,
        }
        agent = store.get_agent(self.agent_id)
        self.assertEqual(agent["id"], str(self.agent_id))
        sql = str(conn.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("agents.workspace_id", sql)
        self.assertIn("agents.id", sql)

    def test_version_cas_and_queue_run_attach_first_event(self):
        conn = connection(scope=self.workspace_id)
        store = PgScopedStore(conn, self.workspace_id)
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "current_version": 1
        }
        newer = store.create_version(self.agent_id, valid_spec(), expected_version=1)
        self.assertEqual(newer["version"], 2)
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "current_version": 2
        }
        original_count = conn.execute.call_count
        with self.assertRaises(VersionConflict):
            store.create_version(self.agent_id, valid_spec(), expected_version=1)
        self.assertEqual(conn.execute.call_count, original_count + 1)

        version_id = uuid.uuid4()
        conn.execute.return_value.one_or_none.return_value = (
            version_id,
            valid_spec().model_dump(mode="json"),
        )
        start = conn.execute.call_count
        run = store.queue_run(self.agent_id, "核验工作空间 Run/Event 的一致性")
        self.assertEqual(run["agent_version_id"], str(version_id))
        commands = [
            str(call.args[0].compile(dialect=postgresql.dialect()))
            for call in conn.execute.call_args_list[start:]
        ]
        self.assertEqual(len(commands), 3)
        self.assertIn("agents.workspace_id", commands[0])
        self.assertIn("INSERT INTO runs", commands[1])
        self.assertIn("INSERT INTO run_events", commands[2])
        self.assertEqual(conn.commit.call_count, 0)

    def test_version_update_cas_requires_exactly_one_affected_row(self):
        conn = connection(scope=self.workspace_id)
        store = PgScopedStore(conn, self.workspace_id)
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "current_version": 1
        }
        conn.execute.return_value.rowcount = 0
        with self.assertRaises(VersionConflict):
            store.create_version(self.agent_id, valid_spec(), expected_version=1)
        self.assertEqual(conn.commit.call_count, 0)

    def test_scoped_store_rejects_invalid_limits_without_querying(self):
        conn = connection(scope=self.workspace_id)
        store = PgScopedStore(conn, self.workspace_id)
        initial = conn.execute.call_count
        with self.assertRaises(ValueError):
            store.list_agents(0)
        with self.assertRaises(ValueError):
            store.list_runs(600)
        with self.assertRaises(ValueError):
            store.queue_run(self.agent_id, "")
        with self.assertRaises(ValueError):
            store.get_events(self.agent_id, after_seq=-1)
        self.assertEqual(conn.execute.call_count, initial)

    def test_workspace_transaction_never_allows_admin_engine(self):
        conn = connection(scope=self.workspace_id)
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        engine.begin.return_value.__enter__.return_value = conn
        conn.execute.return_value.one.return_value = ("studio_migrator", True, True)
        with self.assertRaises(PermissionError):
            with workspace_transaction(
                engine, user_id=uuid.uuid4(), workspace_id=self.workspace_id
            ):
                pass
        self.assertEqual(conn.execute.call_count, 1)

    def test_workspace_transaction_uses_local_role_and_scoped_context(self):
        conn = connection(scope=self.workspace_id)
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        engine.begin.return_value.__enter__.return_value = conn
        conn.execute.return_value.one.return_value = ("studio_app_login", False, False)
        user = uuid.uuid4()
        with workspace_transaction(engine, user_id=user, workspace_id=self.workspace_id):
            pass
        statements = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertIn("SET LOCAL ROLE agentstudio_runtime", statements[1])
        self.assertIn("set_config('agent_studio.user_id'", statements[2])
        self.assertIn("set_config('agent_studio.workspace_id'", statements[3])
        self.assertEqual(conn.execute.call_count, 4)


if __name__ == "__main__":
    unittest.main()
