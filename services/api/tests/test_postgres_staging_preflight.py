"""Offline safety tests for the dedicated SSH-tunneled PostgreSQL preflight."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_script():
    file = PROJECT_ROOT / "scripts" / "check_postgres_staging.py"
    spec = importlib.util.spec_from_file_location("pg_staging_preflight_test", file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeScalars:
    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def all(self):
        return list(self._items)


class StagingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script()
        self.admin_url = (
            "postgresql+psycopg://studio_migrator:dummy%40test"
            "@127.0.0.1:15429/agent_studio?sslmode=disable"
        )

    def fake_engine(self, *, tables=None, revision=None, server_version=170011):
        table_set = set(tables or [])
        connection = MagicMock()
        executed = []

        def fake_execute(statement):
            sql = str(statement)
            executed.append(sql)
            result = MagicMock()
            if "current_user" in sql:
                result.one.return_value = ("studio_migrator", "agent_studio", server_version)
            elif "pg_extension" in sql:
                result.scalar_one_or_none.return_value = "0.8.7"
            elif "pg_catalog.pg_tables" in sql:
                result.scalars.return_value = FakeScalars(table_set)
            elif "SELECT version_num" in sql:
                result.scalars.return_value = FakeScalars([revision] if revision else [])
            else:
                raise AssertionError("Unexpected preflight SQL: " + sql)
            return result

        connection.execute.side_effect = fake_execute
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        return engine, connection, executed

    def test_allows_verified_local_ssh_tunnel_and_same_host_port(self):
        for port in (15429, 54329):
            with self.subTest(port=port):
                url = self.admin_url.replace(":15429/", f":{port}/")
                self.module.validate_url(url)

    def test_rejects_remote_or_wrong_role_db_or_port(self):
        disallowed = (
            self.admin_url.replace("127.0.0.1", "192.168.1.133"),
            self.admin_url.replace("15429", "5432"),
            self.admin_url.replace("/agent_studio?", "/production?"),
            self.admin_url.replace("studio_migrator", "postgres"),
            self.admin_url.replace("sslmode=disable", "sslmode=prefer"),
            self.admin_url.replace("?sslmode=disable", ""),
        )
        for url in disallowed:
            with self.subTest(url=url.replace("dummy%40test", "[redacted]")):
                with self.assertRaises(RuntimeError):
                    self.module.validate_url(url)

    def test_rls_smoke_uses_only_dedicated_local_database_and_admin(self):
        script = PROJECT_ROOT / "scripts" / "smoke_postgres_rls.py"
        spec = importlib.util.spec_from_file_location("rls_staging_test", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for port in (15429, 54329):
            module._verify_local_test_url(self.admin_url.replace(":15429/", f":{port}/"))
        for unsafe in (
            self.admin_url.replace("studio_migrator", "some_other_role"),
            self.admin_url.replace("127.0.0.1", "192.168.1.133"),
            self.admin_url.replace(":15429/", ":5432/"),
        ):
            with self.assertRaises(RuntimeError):
                module._verify_local_test_url(unsafe)

    def test_empty_database_preflight_is_read_only_and_passes(self):
        engine, connection, executed = self.fake_engine()
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            result = self.module.inspect_database()
        self.assertTrue(result["safe_for_baseline"])
        self.assertEqual(result["schema_state"], "initial_empty")
        self.assertEqual(result["pgvector_version"], "0.8.7")
        self.assertEqual(len(executed), 3)
        self.assertTrue(all(sql.strip().upper().startswith("SELECT") for sql in executed))
        connection.commit.assert_not_called()
        engine.dispose.assert_called_once()

    def test_unknown_tables_fail_without_writes(self):
        engine, connection, executed = self.fake_engine(tables={"other_app_users"})
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            with self.assertRaisesRegex(RuntimeError, "Unexpected public tables"):
                self.module.inspect_database()
        self.assertTrue(all(sql.strip().upper().startswith("SELECT") for sql in executed))
        connection.commit.assert_not_called()

    def test_unverified_existing_schema_is_blocked(self):
        engine, _, _ = self.fake_engine(tables={"users", "agents"})
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            with self.assertRaisesRegex(RuntimeError, "without migration ledger"):
                self.module.inspect_database()

    def test_previously_migrated_baseline_requires_all_expected_tables(self):
        engine, _, _ = self.fake_engine(
            tables=self.module.EXPECTED_TABLES,
            revision="0001_pg17_workspace",
        )
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            result = self.module.inspect_database()
        self.assertFalse(result["safe_for_baseline"])
        self.assertEqual(result["schema_state"], "already_migrated")
        self.assertEqual(result["alembic_revision"], "0001_pg17_workspace")

    def test_incomplete_migration_version_refuses(self):
        engine, _, _ = self.fake_engine(
            tables={"alembic_version", "users"}, revision="wrong_revision"
        )
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            with self.assertRaisesRegex(RuntimeError, "Unknown/incomplete"):
                self.module.inspect_database()

    def test_postgres_16_cannot_pass_preflight(self):
        engine, _, _ = self.fake_engine(server_version=160015)
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_ADMIN_URL": self.admin_url}),
            patch.object(self.module, "create_engine", return_value=engine),
        ):
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL 17"):
                self.module.inspect_database()


if __name__ == "__main__":
    unittest.main()
