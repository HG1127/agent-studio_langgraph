"""Offline security contracts for the dedicated PostgreSQL staging runtime login.

No tests use the real Baota database or user's credentials. Each DB call is mocked.
"""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from pglast import parse_sql
from psycopg import sql

PROJECT = Path(__file__).resolve().parents[3]
SCRIPTS = PROJECT / "scripts"
PATH = SCRIPTS / "provision_pg_runtime.py"


def load_script():
    spec = importlib.util.spec_from_file_location("staging_runtime_setup_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
        spec.loader.exec_module(module)
    return module


def expected_role():
    return {
        "name": "agentstudio_app",
        "login": True,
        "inherit": False,
        "superuser": False,
        "create_role": False,
        "create_db": False,
        "replication": False,
        "bypass_rls": False,
        "may_set_rls_role": True,
        "memberships": ("agentstudio_runtime",),
    }


class RuntimeProvisionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script()
        self.admin_url = (
            "postgresql+psycopg://studio_migrator:fake-pw"
            "@127.0.0.1:15429/agent_studio?sslmode=disable"
        )

    def test_admin_url_must_be_local_scoped_migrator(self):
        settings = self.module.admin_connection_settings(self.admin_url)
        self.assertEqual(settings["dbname"], "agent_studio")
        self.assertEqual(settings["user"], "studio_migrator")
        self.assertEqual(settings["port"], 15429)
        self.assertEqual(settings["sslmode"], "disable")
        invalid = (
            self.admin_url.replace("127.0.0.1", "192.168.1.133"),
            self.admin_url.replace("15429", "5432"),
            self.admin_url.replace("studio_migrator", "postgres"),
            self.admin_url.replace("agent_studio?", "other_db?"),
            self.admin_url.replace("sslmode=disable", "sslmode=require"),
        )
        for value in invalid:
            with self.subTest(unsafe=value):
                with self.assertRaises(RuntimeError):
                    self.module.admin_connection_settings(value)

    def test_role_has_no_superuser_or_inherited_access(self):
        self.module.require_expected_runtime(expected_role())
        for key in ("inherit", "superuser", "create_role", "create_db", "bypass_rls"):
            with self.subTest(key=key):
                role = expected_role()
                role[key] = True
                with self.assertRaises(RuntimeError):
                    self.module.require_expected_runtime(role)
        for key in ("login", "may_set_rls_role"):
            with self.subTest(key=key):
                role = expected_role()
                role[key] = False
                with self.assertRaises(RuntimeError):
                    self.module.require_expected_runtime(role)

    def test_unexpected_additional_group_membership_is_rejected(self):
        role = expected_role()
        role["memberships"] = ("agentstudio_runtime", "extra_privileged_group")
        with self.assertRaisesRegex(RuntimeError, "security flags"):
            self.module.require_expected_runtime(role)

    def test_weak_repeated_or_admin_password_is_rejected(self):
        with patch.object(self.module.getpass, "getpass", side_effect=["x" * 64, "y" * 64]):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                self.module._new_password("admin-secret")
        with patch.object(self.module.getpass, "getpass", side_effect=["weak", "weak"]):
            with self.assertRaisesRegex(RuntimeError, "40-256"):
                self.module._new_password("admin-secret")
        with patch.object(self.module.getpass, "getpass", side_effect=["a" * 64] * 2):
            with self.assertRaisesRegex(RuntimeError, "different"):
                self.module._new_password("a" * 64)

    def test_provision_uses_fixed_role_and_safe_pg17_grants_without_printing_secret(self):
        conn = MagicMock()
        conn.transaction.return_value.__enter__.return_value = None
        secret = "a" * 64
        statements = []

        def fake_execute(statement, params=None):
            content = (
                statement.as_string() if isinstance(statement, sql.Composable) else str(statement)
            )
            statements.append(content)
            return MagicMock()

        conn.execute.side_effect = fake_execute
        status = expected_role()
        out = io.StringIO()
        with (
            patch.object(self.module, "role_status", side_effect=[None, status]),
            patch.object(self.module, "_new_password", return_value=secret),
            redirect_stdout(out),
        ):
            self.module.provision(conn, admin_password="different-admin")
        self.assertEqual(len(statements), 3)
        self.assertIn('CREATE ROLE "agentstudio_app"', statements[0])
        self.assertIn("NOINHERIT", statements[0])
        self.assertIn("NOBYPASSRLS", statements[0])
        self.assertIn("CONNECTION LIMIT 20", statements[0])
        self.assertIn("INHERIT FALSE, SET TRUE", statements[1])
        self.assertIn('GRANT CONNECT ON DATABASE "agent_studio"', statements[2])
        for statement in statements:
            self.assertEqual(len(parse_sql(statement + ";")), 1)
        self.assertNotIn(secret, out.getvalue())
        conn.commit.assert_not_called()

    def test_existing_runtime_role_is_never_modified(self):
        connection = MagicMock()
        with patch.object(self.module, "role_status", return_value=expected_role()):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                self.module.provision(connection, admin_password="admin")
        connection.execute.assert_not_called()

    def test_no_approval_prevents_all_role_writes(self):
        connection = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = connection
        with (
            patch.dict(
                os.environ,
                {
                    "STUDIO_POSTGRES_ADMIN_URL": self.admin_url,
                    "STUDIO_RUNTIME_PROVISION_APPROVED": "",
                },
            ),
            patch.object(sys, "argv", ["provision_pg_runtime.py", "--provision"]),
            patch.object(self.module.psycopg, "connect", return_value=context),
            patch.object(self.module, "validate_staging"),
            patch.object(self.module, "provision") as provision,
        ):
            with self.assertRaisesRegex(RuntimeError, "approval missing"):
                self.module.main()
        provision.assert_not_called()

    def test_real_runtime_login_verify_checks_noninherited_rights_and_rls_role(self):
        runtime_conn = MagicMock()
        results = {
            "SELECT current_user": [
                "agentstudio_app",
                "agentstudio_runtime",
            ],
            "has_table_privilege(current_user, 'public.sessions'": [False],
            "has_table_privilege(current_user, 'public.agents'": [False],
            "SELECT count(*) FROM public.agents": [0],
        }

        def fake_execute(statement, params=None):
            sql_query = str(statement)
            result = MagicMock()
            if sql_query.startswith("SELECT current_user"):
                result.fetchone.return_value = (results["SELECT current_user"].pop(0),)
            elif "public.sessions" in sql_query:
                result.fetchone.return_value = (False,)
            elif "public.agents', 'SELECT'" in sql_query:
                result.fetchone.return_value = (False,)
            elif "SELECT count(*) FROM public.agents" in sql_query:
                result.fetchone.return_value = (0,)
            elif sql_query == "SET LOCAL ROLE agentstudio_runtime":
                pass
            else:
                raise AssertionError("Unexpected query: " + sql_query)
            return result

        runtime_conn.execute.side_effect = fake_execute
        context = MagicMock()
        context.__enter__.return_value = runtime_conn
        with (
            patch.object(self.module.getpass, "getpass", return_value="runtime-secret"),
            patch.object(self.module.psycopg, "connect", return_value=context) as connect,
            redirect_stdout(io.StringIO()),
        ):
            self.module.verify_login(self.module.admin_connection_settings(self.admin_url))
        self.assertEqual(connect.call_args.kwargs["user"], "agentstudio_app")
        self.assertEqual(connect.call_args.kwargs["password"], "runtime-secret")
        self.assertEqual(runtime_conn.execute.call_count, 6)

    def test_direct_script_requires_admin_url_and_does_not_try_network(self):
        environment = os.environ.copy()
        environment.pop("STUDIO_POSTGRES_ADMIN_URL", None)
        process = subprocess.run(
            [sys.executable, str(PATH), "--check"],
            cwd=PROJECT / "services" / "api",
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("STUDIO_POSTGRES_ADMIN_URL is missing", process.stderr)
        self.assertNotIn("Traceback", process.stderr)


if __name__ == "__main__":
    unittest.main()
