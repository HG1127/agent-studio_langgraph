"""Isolated local Run/Run Event -> single-case Eval staging contracts."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.application.evaluation_read_service import read_reconciled_evaluation
from app.domain.evaluation import EvalCase
from app.domain.evaluation_execution_binding import EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest
from app.infrastructure.sqlite.evaluation_read_repository import SqliteEvalReadStore
from app.infrastructure.sqlite.evaluation_run_projection import append_single_case_run_evaluation


class RunEvalProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run_eval.sqlite"
        self.suite = EvalSuiteManifest("suite-v1", 1, (EvalCase("case-1", "answer"),))
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE workspace_members(
                    workspace_id TEXT,user_id TEXT,role TEXT,
                    PRIMARY KEY(workspace_id,user_id)
                );
                CREATE TABLE agents(id TEXT PRIMARY KEY, workspace_id TEXT);
                CREATE TABLE agent_versions(
                    id TEXT PRIMARY KEY, agent_id TEXT, version INTEGER
                );
                CREATE TABLE runs(
                    id TEXT PRIMARY KEY,workspace_id TEXT,agent_id TEXT,
                    agent_version_id TEXT,status TEXT,output TEXT
                );
                CREATE TABLE run_events(
                    id TEXT PRIMARY KEY,run_id TEXT,seq INTEGER,type TEXT,payload TEXT,
                    UNIQUE(run_id,seq)
                );
                CREATE TABLE evaluation_snapshots(
                    run_id TEXT PRIMARY KEY,workspace_id TEXT,
                    manifest_json TEXT,submissions_json TEXT,manifest_fingerprint TEXT
                );
                """
            )
            db.execute("INSERT INTO workspace_members VALUES ('ws-1','user-1','viewer')")
            db.execute("INSERT INTO agents VALUES ('agent-1','ws-1')")
            db.execute("INSERT INTO agent_versions VALUES ('version-2','agent-1',2)")
            db.execute(
                "INSERT INTO runs VALUES ('run-1','ws-1','agent-1','version-2','SUCCEEDED','answer')"
            )
            self.event(db, 1, "queued", {"message": "queued"})
            self.event(db, 2, "started", {"checkpoint_resume": False})
            self.event(db, 3, "model_step", {"round": 1, "has_text": True})
            self.event(db, 4, "completed", self.completion())
        self.reader = SqliteEvalReadStore(self.connection)

    @staticmethod
    def completion():
        return {"reason": "model_final_response", "quality_evaluated": False}

    @staticmethod
    def event(db, seq, kind, payload):
        db.execute(
            "INSERT INTO run_events VALUES (?, 'run-1', ?, ?, ?)",
            (f"event-{seq}", seq, kind, json.dumps(payload)),
        )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def stage(self, manifest=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return append_single_case_run_evaluation(
                db, "run-1", self.suite if manifest is None else manifest
            )

    def no_snapshot(self):
        with self.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM evaluation_snapshots").fetchone()[0], 0
            )

    def test_run_output_is_authoritative_and_scoped(self):
        result = self.stage()
        scope = EvalRunScope("ws-1", "agent-1", 2, "run-1")
        self.assertEqual(result.scope, scope)
        self.assertEqual(result.submissions[0].answer, "answer")
        self.assertEqual(result.submissions[0].evidence, ())
        snapshot = self.reader.load_snapshot("user-1", "ws-1", "run-1")
        self.assertEqual(snapshot.execution, result)
        self.assertTrue(
            read_reconciled_evaluation("user-1", scope, result, self.reader).summary.hard_pass
        )
        self.assertIsNone(self.reader.load_snapshot("outsider", "ws-1", "run-1"))

    def test_wrong_answer_is_not_promoted_to_quality_pass(self):
        with self.connection() as db:
            db.execute("UPDATE runs SET output='wrong'")
        record = self.stage()
        self.assertEqual(record.submissions[0].answer, "wrong")
        self.assertFalse(
            read_reconciled_evaluation(
                "user-1", record.scope, record, self.reader
            ).summary.hard_pass
        )

    def test_tool_completed_yields_event_id_not_rejected_tool_evidence(self):
        with self.connection() as db:
            db.execute("DELETE FROM run_events WHERE seq=4")
            self.event(db, 4, "tool_completed", {"tool": "local_read", "result": {}})
            self.event(db, 5, "tool_rejected", {"tool": "external_write", "reason": "denied"})
            self.event(db, 6, "model_step", {"round": 2, "has_text": True})
            self.event(db, 7, "completed", self.completion())
        manifest = EvalSuiteManifest(
            "suite-v1", 1, (EvalCase("case-1", "answer", ("run-event:4",)),)
        )
        record = self.stage(manifest)
        self.assertEqual(record.submissions[0].evidence, ("run-event:4",))
        self.assertTrue(
            read_reconciled_evaluation(
                "user-1", record.scope, record, self.reader
            ).summary.hard_pass
        )

    def test_failed_partial_and_nonterminal_runs_are_rejected(self):
        for status in ("FAILED", "PARTIAL", "CANCELED", "QUEUED", "RUNNING", "SUCCEEDED "):
            with self.subTest(status=status), self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE runs SET status=?", (status,))
                with self.assertRaisesRegex(LookupError, "not found"):
                    append_single_case_run_evaluation(db, "run-1", self.suite)
                db.rollback()
        self.no_snapshot()

    def test_missing_or_blank_answer_is_rejected(self):
        for output in (None, "", "  "):
            with self.subTest(output=output), self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE runs SET output=?", (output,))
                with self.assertRaisesRegex(ValueError, "no final answer"):
                    append_single_case_run_evaluation(db, "run-1", self.suite)
                db.rollback()
        self.no_snapshot()

    def test_missing_terminal_event_is_rejected(self):
        with self.connection() as db:
            db.execute("DELETE FROM run_events WHERE seq=4")
        with self.assertRaisesRegex(ValueError, "completion event"):
            self.stage()
        self.no_snapshot()

    def test_completion_must_be_unique_and_last(self):
        with self.connection() as db:
            self.event(db, 5, "completed", self.completion())
        with self.assertRaisesRegex(ValueError, "completion event"):
            self.stage()
        self.no_snapshot()

    def test_model_step_is_required(self):
        with self.connection() as db:
            db.execute("UPDATE run_events SET type='tool_rejected' WHERE seq=3")
        with self.assertRaisesRegex(ValueError, "model execution"):
            self.stage()
        self.no_snapshot()

    def test_event_sequence_gap_is_rejected(self):
        with self.connection() as db:
            db.execute("DELETE FROM run_events WHERE seq=2")
        with self.assertRaisesRegex(ValueError, "event history"):
            self.stage()
        self.no_snapshot()

    def test_invalid_terminal_payload_and_type_confusion_rejected(self):
        invalid = (
            '{"reason":"other","quality_evaluated":false}',
            '{"oops":',
            '{"reason":"model_final_response","quality_evaluated":false,"quality_evaluated":false}',
            '{"reason":"model_final_response","quality_evaluated":0}',
            '{"reason":"model_final_response","quality_evaluated":NaN}',
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE run_events SET payload=? WHERE seq=4", (payload,))
                with self.assertRaisesRegex(ValueError, "terminal event payload"):
                    append_single_case_run_evaluation(db, "run-1", self.suite)
                db.rollback()
        self.no_snapshot()

    def test_foreign_or_missing_agent_binding_rejected(self):
        with self.connection() as db:
            db.execute("UPDATE agents SET workspace_id='ws-2'")
        with self.assertRaisesRegex(LookupError, "not found"):
            self.stage()
        self.no_snapshot()

    def test_multi_case_suite_does_not_invent_case_outputs(self):
        manifest = EvalSuiteManifest(
            "suite-v1", 1, (EvalCase("case-1", "answer"), EvalCase("case-2", "another"))
        )
        with self.assertRaisesRegex(ValueError, "one evaluation case"):
            self.stage(manifest)
        self.no_snapshot()

    def test_duplicate_append_is_rejected_without_placement(self):
        record = self.stage()
        with self.assertRaisesRegex(ValueError, "insert rejected"):
            self.stage()
        self.assertEqual(self.reader.load_snapshot("user-1", "ws-1", "run-1").execution, record)

    def test_run_event_count_limit_rejects_unbounded_history(self):
        with self.connection() as db:
            db.executemany(
                "INSERT INTO run_events VALUES (?, ?, ?, ?, ?)",
                [(f"extra-{seq}", "run-1", seq, "noop", "{}") for seq in range(5, 502)],
            )
        with self.assertRaisesRegex(ValueError, "event history"):
            self.stage()
        self.no_snapshot()

    def test_oversized_terminal_payload_is_rejected_even_if_valid_json(self):
        # json.loads normally accepts trailing whitespace; bound it before parsing.
        body = json.dumps(self.completion()) + " " * 4096
        with self.connection() as db:
            db.execute("UPDATE run_events SET payload=? WHERE seq=4", (body,))
        with self.assertRaisesRegex(ValueError, "terminal event payload"):
            self.stage()
        self.no_snapshot()

    def test_unknown_run_never_creates_a_snapshot(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(LookupError, "not found"):
                append_single_case_run_evaluation(db, "run-absent", self.suite)
            db.rollback()
        self.no_snapshot()

    def test_transaction_is_required(self):
        with self.connection() as db:
            with self.assertRaisesRegex(RuntimeError, "explicit transaction"):
                append_single_case_run_evaluation(db, "run-1", self.suite)
        self.no_snapshot()


if __name__ == "__main__":
    unittest.main()
