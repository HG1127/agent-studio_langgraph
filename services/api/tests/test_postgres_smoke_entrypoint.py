"""Regression for direct Windows/Linux invocation of real PostgreSQL RLS smoke.

All cases are offline and must not connect to any database.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = PROJECT_ROOT / "services" / "api"
SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "smoke_postgres_rls.py"


def load_smoke():
    spec = importlib.util.spec_from_file_location("pg_rls_smoke_entrypoint", SMOKE_SCRIPT)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


class SmokeEntrypointTests(unittest.TestCase):
    def test_direct_script_without_test_url_exits_before_import_or_db(self):
        """Run exactly as the PowerShell wizard used to run it."""
        environment = os.environ.copy()
        environment.pop("STUDIO_POSTGRES_TEST_ADMIN_URL", None)
        process = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT)],
            cwd=API_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=25,
            check=False,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("STUDIO_POSTGRES_TEST_ADMIN_URL required", process.stderr)
        self.assertNotIn("No module named 'app'", process.stderr)
        self.assertNotIn("psycopg.OperationalError", process.stderr)

    def test_backend_path_can_be_resolved_by_file_location(self):
        script = load_smoke()
        with patch.object(sys, "path", [str(PROJECT_ROOT / "scripts")]):
            script._enable_backend_imports()
            self.assertEqual(sys.path[0], str(API_ROOT))
            self.assertTrue((API_ROOT / "app" / "__init__.py").exists())

    def test_authenticated_target_reaches_mock_engine_after_import(self):
        script = load_smoke()
        url = (
            "postgresql+psycopg://studio_migrator:fake-pw@"
            "127.0.0.1:15429/agent_studio?sslmode=disable"
        )

        class ReachedEngine(Exception):
            pass

        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_TEST_ADMIN_URL": url}),
            patch.object(script, "create_engine", side_effect=ReachedEngine),
        ):
            with self.assertRaises(ReachedEngine):
                script.main()

    def test_unknown_database_is_rejected_before_any_backend_import(self):
        script = load_smoke()
        url = "postgresql+psycopg://studio_migrator:fake@127.0.0.1:15429/untrusted"
        with (
            patch.dict(os.environ, {"STUDIO_POSTGRES_TEST_ADMIN_URL": url}),
            patch.object(script, "_enable_backend_imports") as imports,
            patch.object(script, "create_engine") as engine,
        ):
            with self.assertRaisesRegex(RuntimeError, "Refusing non-local/unexpected"):
                script.main()
        imports.assert_not_called()
        engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
