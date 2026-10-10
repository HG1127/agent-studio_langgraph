"""Regression: architecture rules reject forbidden imports, not just a green tree."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_architecture.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("agent_studio_architecture", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load architecture checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArchitectureContractTests(unittest.TestCase):
    def test_current_project_boundaries_pass(self):
        checker = load_checker()
        self.assertEqual(checker.check_python(), [])
        self.assertEqual(checker.check_next_entrypoint(), [])

    def test_domain_to_http_dependency_is_rejected(self):
        checker = load_checker()
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            domain = root / "app" / "domain"
            domain.mkdir(parents=True)
            (domain / "bad_import.py").write_text(
                "from app.api.routes import runs\n",
                encoding="utf-8",
            )
            with (
                patch.object(checker, "APP", root / "app"),
                patch.object(checker, "ROOT", root),
            ):
                findings = checker.check_python()
            self.assertEqual(len(findings), 1)
            self.assertIn("domain cannot import app.api.routes", findings[0])


if __name__ == "__main__":
    unittest.main()
