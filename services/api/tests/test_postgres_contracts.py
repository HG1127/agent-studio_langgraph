"""PostgreSQL schema contract and offline migration tests.

No live PG server is available in the current runner. These tests verify
SQL generation and fail-closed API behavior, not live PostgreSQL RLS semantics.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.infrastructure.postgres.models import Base
from app.infrastructure.postgres.transaction import workspace_transaction
from app.main import app
from fastapi.testclient import TestClient
from pglast import parse_sql
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

REPO = Path(__file__).resolve().parents[3]
API = REPO / "services" / "api"


class PostgreSQLSchemaContractTests(unittest.TestCase):
    def test_metadata_contains_all_expected_bounded_entities(self):
        self.assertEqual(
            set(Base.metadata.tables),
            {
                "users",
                "sessions",
                "workspaces",
                "workspace_members",
                "agents",
                "agent_versions",
                "runs",
                "run_events",
                "knowledge_sources",
                "knowledge_chunks",
                "memory_records",
                "quarantined_legacy_rows",
            },
        )
        agents = Base.metadata.tables["agents"]
        runs = Base.metadata.tables["runs"]
        events = Base.metadata.tables["run_events"]
        for entity in (agents, runs, events):
            self.assertFalse(entity.c.workspace_id.nullable)
        fks = [fk.name for fk in runs.foreign_key_constraints]
        self.assertIn("fk_run_same_workspace", fks)
        self.assertIn("fk_run_version_same_agent", fks)
        self.assertIn(
            "fk_event_same_workspace",
            [fk.name for fk in events.foreign_key_constraints],
        )

    def test_pgvector_and_jsonb_compile_to_postgresql_types(self):
        ddl = str(
            CreateTable(Base.metadata.tables["knowledge_chunks"]).compile(
                dialect=postgresql.dialect()
            )
        ).upper()
        self.assertIn("EMBEDDING VECTOR", ddl)
        self.assertIn("FOREIGN KEY(SOURCE_ID, WORKSPACE_ID)", ddl)
        agent = str(
            CreateTable(Base.metadata.tables["agents"]).compile(dialect=postgresql.dialect())
        ).upper()
        self.assertIn("SPEC JSONB NOT NULL", agent)

    def test_alembic_offline_sql_has_expected_rls_and_tenant_guards(self):
        generated = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", "--sql"],
            cwd=API,
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        ).stdout
        for expression in (
            "CREATE EXTENSION IF NOT EXISTS vector",
            "CREATE TABLE users",
            "CREATE TABLE agents",
            "CREATE TABLE runs",
            "CREATE TABLE agent_versions",
            "CREATE TABLE knowledge_chunks",
            "CREATE TABLE quarantined_legacy_rows",
            "CREATE POLICY agents_read",
            "CREATE POLICY runs_read",
            "CREATE POLICY run_events_read",
            "CREATE POLICY versions_read",
            "CREATE POLICY own_workspace_membership",
            "CREATE TRIGGER agent_versions_immutable",
            "CREATE TRIGGER agents_fixed_workspace",
            "CREATE FUNCTION",
        ):
            if expression == "CREATE FUNCTION":
                # CREATE OR REPLACE FUNCTION is equivalent and expected.
                self.assertIn("CREATE OR REPLACE FUNCTION", generated)
            else:
                self.assertIn(expression, generated)
        self.assertGreaterEqual(generated.count("FORCE ROW LEVEL SECURITY"), 9)
        self.assertIn("NOLOGIN NOBYPASSRLS", generated)
        self.assertIn("GRANT SELECT,INSERT", generated)
        # Audit events are append-only even for a tenant member.
        self.assertIn("GRANT SELECT,INSERT ON public.run_events", generated)
        self.assertNotIn("GRANT SELECT,INSERT,UPDATE ON public.run_events", generated)
        self.assertIn("CREATE ROLE agentstudio_runtime NOLOGIN NOBYPASSRLS", generated)
        self.assertIn("REVOKE EXECUTE ON FUNCTION", generated)
        # Text assertions alone previously missed an invalid DO $$ terminator.
        # Parsing the *complete* migration prevents such SQL from passing CI.
        parsed = parse_sql(generated)
        self.assertGreaterEqual(len(parsed), 85)
        self.assertIn("END $$;", generated)
        self.assertNotIn("GRANT SELECT ON public.users", generated)
        self.assertNotIn("GRANT SELECT ON public.sessions", generated)
        self.assertNotIn("GRANT SELECT ON public.quarantined_legacy_rows", generated)
        self.assertNotIn("password-here", generated)

    def test_transaction_helper_rejects_wrong_ids_and_sqlite(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with self.assertRaises(ValueError):
                with workspace_transaction(engine, user_id="not-a-uuid", workspace_id=uuid.uuid4()):
                    pass
            with self.assertRaises(ValueError):
                with workspace_transaction(engine, user_id=uuid.uuid4(), workspace_id=uuid.uuid4()):
                    pass
        finally:
            engine.dispose()

    def test_postgres_switch_cannot_silently_use_sqlite(self):
        with patch.dict(os.environ, {"STUDIO_DATABASE_BACKEND": "postgres"}):
            with self.assertRaisesRegex(RuntimeError, "not switched"):
                with TestClient(app):
                    pass
            worker = subprocess.run(
                [sys.executable, "-m", "app.worker", "--once"],
                cwd=API,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertNotEqual(worker.returncode, 0)
        self.assertIn("not yet validated", worker.stderr)

    def test_online_migration_requires_explicit_administrator_url(self):
        env = os.environ.copy()
        env.pop("STUDIO_POSTGRES_ADMIN_URL", None)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current"],
            cwd=API,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STUDIO_POSTGRES_ADMIN_URL", result.stderr + result.stdout)


class SQLiteMigrationPlannerTests(unittest.TestCase):
    def test_assessment_does_not_copy_unowned_data_or_disclose_content(self):
        module_spec = importlib.util.spec_from_file_location(
            "migration_planner", REPO / "scripts" / "plan_sqlite_migration.py"
        )
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        planner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(planner)

        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "legacy.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """CREATE TABLE agents(id TEXT, spec TEXT);
                       CREATE TABLE runs(id TEXT, agent_id TEXT, input TEXT);"""
                )
                connection.execute(
                    "INSERT INTO agents VALUES (?, ?)",
                    ("record-one", '{"secret":"never-export-this"}'),
                )
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?)",
                    ("old-run", "record-one", "confidential-user-input"),
                )
            connection.close()  # Context commits but does not close on Windows
            plan = planner.assess(path)
            self.assertTrue(plan["assessment_only"])
            self.assertFalse(plan["migration_authorized"])
            self.assertEqual(plan["unowned_agents"], 1)
            self.assertEqual(plan["unowned_runs"], 1)
            self.assertEqual(plan["tables"]["runs"], 1)
            self.assertNotIn("never-export-this", str(plan))
            self.assertNotIn("confidential-user-input", str(plan))
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":
    unittest.main()
