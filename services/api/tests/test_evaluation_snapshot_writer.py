"""Staging snapshot writer contracts: ephemeral SQLite, no network or LLM."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.domain.evaluation import EvalCase
from app.domain.evaluation_batch import EvalSubmission
from app.domain.evaluation_execution_binding import EvalExecutionRecord, EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest
from app.infrastructure.sqlite.evaluation_read_repository import SqliteEvalReadStore
from app.infrastructure.sqlite.evaluation_snapshot_writer import append_terminal_eval_snapshot


class SqliteEvalSnapshotWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_file = Path(temporary.name) / "eval_writer.sqlite"
        self.scope = EvalRunScope("ws-1", "agent-1", 2, "run-1")
        self.manifest = EvalSuiteManifest(
            "suite-v1", 1, (EvalCase("case-a", "answer", ("source:1",)),)
        )
        self.record = EvalExecutionRecord(
            self.scope,
            self.manifest.fingerprint,
            (EvalSubmission("case-a", "answer", ("source:1",)),),
        )
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE workspace_members (
                    workspace_id TEXT, user_id TEXT, role TEXT,
                    PRIMARY KEY(workspace_id, user_id)
                );
                CREATE TABLE agents (id TEXT PRIMARY KEY, workspace_id TEXT);
                CREATE TABLE agent_versions (
                    id TEXT PRIMARY KEY, agent_id TEXT, version INTEGER
                );
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY, workspace_id TEXT, agent_id TEXT,
                    agent_version_id TEXT, status TEXT
                );
                CREATE TABLE evaluation_snapshots (
                    run_id TEXT PRIMARY KEY, workspace_id TEXT,
                    manifest_json TEXT, submissions_json TEXT,
                    manifest_fingerprint TEXT
                );
                """
            )
            db.execute("INSERT INTO workspace_members VALUES ('ws-1','user-1','viewer')")
            db.execute("INSERT INTO agents VALUES ('agent-1','ws-1')")
            db.execute("INSERT INTO agent_versions VALUES ('version-2','agent-1',2)")
            db.execute("INSERT INTO runs VALUES ('run-1','ws-1','agent-1','version-2','SUCCEEDED')")
        self.reader = SqliteEvalReadStore(self.connection)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_file)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, *, manifest=None, record=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            append_terminal_eval_snapshot(
                db,
                self.manifest if manifest is None else manifest,
                self.record if record is None else record,
            )

    def test_valid_append_round_trips_existing_scoped_reader(self):
        self.append()
        snapshot = self.reader.load_snapshot("user-1", "ws-1", "run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.scope, self.scope)
        self.assertEqual(snapshot.manifest, self.manifest)
        self.assertEqual(snapshot.execution, self.record)
        self.assertIsNone(self.reader.load_snapshot("outsider", "ws-1", "run-1"))

    def test_duplicate_write_is_rejected_without_replacing_snapshot(self):
        self.append()
        with self.assertRaisesRegex(ValueError, "insert rejected"):
            self.append()
        self.assertEqual(
            self.reader.load_snapshot("user-1", "ws-1", "run-1").execution,
            self.record,
        )

    def test_nonterminal_and_missing_runs_fail_closed(self):
        for state in ("DRAFT", "QUEUED", "RUNNING", "CANCELLED", "SUCCEEDED "):
            with self.subTest(state=state), self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE runs SET status=?", (state,))
                with self.assertRaisesRegex(LookupError, "not found"):
                    append_terminal_eval_snapshot(db, self.manifest, self.record)
                db.rollback()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM runs")
            with self.assertRaisesRegex(LookupError, "not found"):
                append_terminal_eval_snapshot(db, self.manifest, self.record)
            db.rollback()
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_partial_and_failed_runs_are_eligible_to_record_evidence(self):
        for state in ("PARTIAL", "FAILED"):
            with self.subTest(state=state), self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE runs SET status=?", (state,))
                append_terminal_eval_snapshot(db, self.manifest, self.record)
                db.rollback()
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_cross_workspace_agent_version_and_run_scope_rejected(self):
        for changed in (
            {"workspace_id": "ws-2"},
            {"agent_id": "agent-2"},
            {"agent_version": 3},
            {"run_id": "run-2"},
        ):
            with self.subTest(changed=changed):
                kwargs = dict(
                    workspace_id="ws-1", agent_id="agent-1", agent_version=2, run_id="run-1"
                )
                kwargs.update(changed)
                forged = EvalExecutionRecord(
                    EvalRunScope(**kwargs), self.manifest.fingerprint, self.record.submissions
                )
                with self.assertRaisesRegex(LookupError, "not found"):
                    self.append(record=forged)
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_fingerprint_and_case_count_must_match(self):
        for record, error in (
            (EvalExecutionRecord(self.scope, "a" * 64, self.record.submissions), "fingerprint"),
            (EvalExecutionRecord(self.scope, self.manifest.fingerprint, ()), "case IDs"),
        ):
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    self.append(record=record)
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_multibyte_size_limit_is_checked_before_persist(self):
        manifest = EvalSuiteManifest("suite-v1", 1, (EvalCase("case-a", "汉" * 350_000),))
        record = EvalExecutionRecord(
            self.scope, manifest.fingerprint, (EvalSubmission("case-a", "answer"),)
        )
        with self.assertRaisesRegex(ValueError, "size limit"):
            self.append(manifest=manifest, record=record)
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_no_transaction_means_no_insert(self):
        with self.connection() as db:
            with self.assertRaisesRegex(RuntimeError, "explicit transaction"):
                append_terminal_eval_snapshot(db, self.manifest, self.record)
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))

    def test_caller_controls_commit_or_rollback(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            append_terminal_eval_snapshot(db, self.manifest, self.record)
            db.rollback()
        self.assertIsNone(self.reader.load_snapshot("user-1", "ws-1", "run-1"))


if __name__ == "__main__":
    unittest.main()
