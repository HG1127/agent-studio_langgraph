"""SQLite staging EvalReadStore tests; isolated temporary DB, no network/LLM."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.application.evaluation_read_service import read_reconciled_evaluation
from app.domain.evaluation import EvalCase
from app.domain.evaluation_batch import EvalSubmission
from app.domain.evaluation_execution_binding import EvalExecutionRecord, EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest
from app.infrastructure.sqlite.evaluation_read_repository import SqliteEvalReadStore, _decode


class SqliteEvalReadStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "eval.sqlite"
        self.scope = EvalRunScope("ws-1", "agent-1", 2, "run-1")
        self.suite = EvalSuiteManifest(
            "suite-v1", 1, (EvalCase("case-a", "answer", ("source:1",)),)
        )
        self.record = EvalExecutionRecord(
            self.scope,
            self.suite.fingerprint,
            (EvalSubmission("case-a", "answer", ("source:1",)),),
        )
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE workspace_members (
                    workspace_id TEXT, user_id TEXT, role TEXT,
                    PRIMARY KEY(workspace_id, user_id)
                );
                CREATE TABLE agents (
                    id TEXT PRIMARY KEY, workspace_id TEXT
                );
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
            db.execute("INSERT INTO workspace_members VALUES ('ws-1', 'user-1', 'viewer')")
            db.execute("INSERT INTO agents VALUES ('agent-1', 'ws-1')")
            db.execute("INSERT INTO agent_versions VALUES ('version-2', 'agent-1', 2)")
            db.execute(
                "INSERT INTO runs VALUES ('run-1', 'ws-1', 'agent-1', 'version-2', 'SUCCEEDED')"
            )
            self.insert_snapshot(db)
        self.store = SqliteEvalReadStore(self.connection)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def insert_snapshot(self, db):
        manifest = {
            "suite_id": self.suite.suite_id,
            "version": self.suite.version,
            "cases": [
                {
                    "case_id": case.case_id,
                    "expected_answer": case.expected_answer,
                    "required_evidence": list(case.required_evidence),
                }
                for case in self.suite.cases
            ],
        }
        submissions = [
            {"case_id": item.case_id, "answer": item.answer, "evidence": list(item.evidence)}
            for item in self.record.submissions
        ]
        db.execute(
            "INSERT INTO evaluation_snapshots VALUES (?, ?, ?, ?, ?)",
            (
                self.scope.run_id,
                self.scope.workspace_id,
                json.dumps(manifest),
                json.dumps(submissions),
                self.suite.fingerprint,
            ),
        )

    def execute(self, query: str, params: tuple = ()) -> None:
        with self.connection() as db:
            db.execute(query, params)

    def test_reads_persisted_authoritative_snapshot_and_reconciles(self):
        self.assertEqual(self.store.member_role("user-1", "ws-1"), "viewer")
        snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
        self.assertEqual(snapshot.scope, self.scope)
        self.assertEqual(snapshot.manifest.fingerprint, self.suite.fingerprint)
        self.assertEqual(snapshot.execution, self.record)
        result = read_reconciled_evaluation("user-1", self.scope, self.record, self.store)
        self.assertTrue(result.summary.hard_pass)

    def test_multi_case_snapshot_replay_is_order_invariant_and_fails_gate(self):
        cases = (
            EvalCase("case-b", "B", ("source:b",)),
            EvalCase("case-a", "A", ("source:a",)),
        )
        submissions = (
            EvalSubmission("case-a", "wrong", ("source:a",)),
            EvalSubmission("case-b", "B", ("source:b",)),
        )
        fingerprints = []
        summaries = []
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.suite = EvalSuiteManifest("suite-v1", 1, cases[::-1] if reverse else cases)
                self.record = EvalExecutionRecord(
                    self.scope,
                    self.suite.fingerprint,
                    submissions[::-1] if reverse else submissions,
                )
                with self.connection() as db:
                    db.execute("DELETE FROM evaluation_snapshots")
                    self.insert_snapshot(db)
                snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.execution, self.record)
                result = read_reconciled_evaluation("user-1", self.scope, self.record, self.store)
                fingerprints.append(self.suite.fingerprint)
                summaries.append(result.summary)
                self.assertEqual(
                    tuple(r.case_id for r in result.summary.results), ("case-a", "case-b")
                )
                self.assertEqual((result.summary.failed, result.summary.passed), (1, 1))
                self.assertEqual(result.summary.failure_counts, {"answer_mismatch": 1})
                self.assertFalse(result.summary.hard_pass)
        self.assertEqual(fingerprints[0], fingerprints[1])
        self.assertEqual(summaries[0], summaries[1])

    def test_read_role_allowlist_is_rechecked_after_role_changes(self):
        for role in ("owner", "admin", "member", "viewer"):
            with self.subTest(role=role):
                self.execute(
                    "UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?",
                    (role, "ws-1", "user-1"),
                )
                self.assertEqual(self.store.member_role("user-1", "ws-1"), role)
                snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.execution, self.record)
                self.assertTrue(
                    read_reconciled_evaluation(
                        "user-1", self.scope, self.record, self.store
                    ).summary.hard_pass
                )
        self.execute(
            "UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?",
            ("guest", "ws-1", "user-1"),
        )
        self.assertIsNone(self.store.member_role("user-1", "ws-1"))
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))
        with self.assertRaisesRegex(PermissionError, "workspace access denied"):
            read_reconciled_evaluation("user-1", self.scope, self.record, self.store)

    def test_unknown_member_rejected_without_snapshot(self):
        self.assertIsNone(self.store.member_role("stranger", "ws-1"))
        self.assertIsNone(self.store.load_snapshot("stranger", "ws-1", "run-1"))
        with self.assertRaises(PermissionError):
            read_reconciled_evaluation("stranger", self.scope, self.record, self.store)

    def test_membership_revoked_between_port_calls_is_rechecked(self):
        self.assertEqual(self.store.member_role("user-1", "ws-1"), "viewer")
        self.execute("DELETE FROM workspace_members WHERE workspace_id='ws-1' AND user_id='user-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_corrupt_manifest_not_exposed_after_membership_revocation(self):
        self.execute("UPDATE evaluation_snapshots SET manifest_json=?", ("{oops",))
        self.assertEqual(self.store.member_role("user-1", "ws-1"), "viewer")
        self.execute("DELETE FROM workspace_members WHERE user_id='user-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))
        with self.assertRaisesRegex(PermissionError, "workspace access denied"):
            read_reconciled_evaluation("user-1", self.scope, self.record, self.store)

    def test_corrupt_manifest_not_exposed_to_foreign_workspace_member(self):
        self.execute("UPDATE evaluation_snapshots SET manifest_json=?", ("{oops",))
        self.execute("DELETE FROM workspace_members WHERE user_id='user-1'")
        self.execute("INSERT INTO workspace_members VALUES ('ws-2', 'user-1', 'viewer')")
        self.assertIsNone(self.store.member_role("user-1", "ws-1"))
        self.assertEqual(self.store.member_role("user-1", "ws-2"), "viewer")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-2", "run-1"))

    def test_role_revoked_after_service_precheck_is_denied(self):
        store = self.store
        outer = self

        class RevokeBetweenChecks:
            def member_role(self, actor, workspace):
                role = store.member_role(actor, workspace)
                outer.execute("DELETE FROM workspace_members WHERE user_id=?", (actor,))
                return role

            def load_snapshot(self, actor, workspace, run):
                return store.load_snapshot(actor, workspace, run)

        with self.assertRaises(LookupError):
            read_reconciled_evaluation("user-1", self.scope, self.record, RevokeBetweenChecks())

    def test_cross_workspace_membership_cannot_read_foreign_run(self):
        self.execute("INSERT INTO workspace_members VALUES ('ws-2', 'user-1', 'viewer')")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-2", "run-1"))
        bad_scope = EvalRunScope("ws-2", "agent-1", 2, "run-1")
        with self.assertRaises(LookupError):
            read_reconciled_evaluation("user-1", bad_scope, self.record, self.store)

    def test_forged_evaluation_workspace_binding_rejected(self):
        self.execute("UPDATE evaluation_snapshots SET workspace_id='ws-2' WHERE run_id='run-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_legacy_unowned_run_not_exposed(self):
        self.execute("UPDATE runs SET workspace_id=NULL WHERE id='run-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_mismatched_agent_or_version_ownership_rejected(self):
        for changed, restored in (
            (
                "UPDATE agents SET workspace_id='ws-2' WHERE id='agent-1'",
                "UPDATE agents SET workspace_id='ws-1' WHERE id='agent-1'",
            ),
            (
                "UPDATE agent_versions SET agent_id='other' WHERE id='version-2'",
                "UPDATE agent_versions SET agent_id='agent-1' WHERE id='version-2'",
            ),
            (
                "UPDATE runs SET agent_version_id='other' WHERE id='run-1'",
                "UPDATE runs SET agent_version_id='version-2' WHERE id='run-1'",
            ),
        ):
            with self.subTest(changed=changed):
                self.execute(changed)  # Commit before opening the reader connection.
                try:
                    self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))
                finally:
                    self.execute(restored)

    def test_only_terminal_runs_are_eligible(self):
        self.execute("UPDATE runs SET status='RUNNING' WHERE id='run-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_partial_and_failed_runs_remain_evaluable(self):
        for status in ("PARTIAL", "FAILED"):
            with self.subTest(status=status):
                self.execute("UPDATE runs SET status=? WHERE id='run-1'", (status,))
                try:
                    snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
                    self.assertIsNotNone(snapshot)
                    self.assertEqual(snapshot.scope, self.scope)
                    self.assertEqual(snapshot.execution, self.record)
                finally:
                    self.execute("UPDATE runs SET status='SUCCEEDED' WHERE id='run-1'")

    def test_non_evaluable_run_states_fail_closed(self):
        for status in ("DRAFT", "QUEUED", "RUNNING", "CANCELLED", "SUCCEEDED "):
            with self.subTest(status=status):
                self.execute("UPDATE runs SET status=? WHERE id='run-1'", (status,))
                try:
                    self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))
                finally:
                    self.execute("UPDATE runs SET status='SUCCEEDED' WHERE id='run-1'")

    def test_unknown_role_is_rejected(self):
        self.execute("UPDATE workspace_members SET role='guest' WHERE user_id='user-1'")
        self.assertIsNone(self.store.member_role("user-1", "ws-1"))
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_invalid_identifier_rejected(self):
        for value in (None, "", "  ", 3):
            with self.subTest(value=value):
                self.assertIsNone(self.store.member_role(value, "ws-1"))
                self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", value))

    def test_manifest_payload_tampering_is_rejected_with_original_fingerprint(self):
        with self.connection() as db:
            original = db.execute(
                "SELECT manifest_json FROM evaluation_snapshots WHERE run_id=?", ("run-1",)
            ).fetchone()[0]
        for field, changed in (
            ("suite_id", "other-suite"),
            ("version", 2),
            ("expected_answer", "tampered"),
            ("required_evidence", ["source:other"]),
        ):
            with self.subTest(field=field):
                payload = json.loads(original)
                target = payload if field in ("suite_id", "version") else payload["cases"][0]
                target[field] = changed
                self.execute(
                    "UPDATE evaluation_snapshots SET manifest_json=? WHERE run_id=?",
                    (json.dumps(payload), "run-1"),
                )
                try:
                    with self.assertRaisesRegex(
                        LookupError, "^evaluation run not found$"
                    ) as rejected:
                        self.store.load_snapshot("user-1", "ws-1", "run-1")
                    self.assertIsInstance(rejected.exception.__cause__, ValueError)
                    self.assertEqual(
                        str(rejected.exception.__cause__), "suite fingerprint mismatch"
                    )
                finally:
                    self.execute(
                        "UPDATE evaluation_snapshots SET manifest_json=? WHERE run_id=?",
                        (original, "run-1"),
                    )
        snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
        self.assertEqual(snapshot.manifest.fingerprint, self.suite.fingerprint)

    def test_manifest_version_json_type_confusion_fails_closed(self):
        # bool subclasses int in Python, but a suite version must be a JSON integer.
        with self.connection() as db:
            original = db.execute(
                "SELECT manifest_json FROM evaluation_snapshots WHERE run_id='run-1'"
            ).fetchone()[0]
        for invalid_version in (True, 1.0, "1", None):
            with self.subTest(invalid_version=repr(invalid_version)):
                payload = json.loads(original)
                payload["version"] = invalid_version
                self.execute(
                    "UPDATE evaluation_snapshots SET manifest_json=? WHERE run_id='run-1'",
                    (json.dumps(payload),),
                )
                try:
                    with self.assertRaisesRegex(
                        LookupError, "^evaluation run not found$"
                    ) as rejected:
                        self.store.load_snapshot("user-1", "ws-1", "run-1")
                    self.assertIsInstance(rejected.exception.__cause__, ValueError)
                    self.assertEqual(
                        str(rejected.exception.__cause__), "version must be a positive integer"
                    )
                finally:
                    self.execute(
                        "UPDATE evaluation_snapshots SET manifest_json=? WHERE run_id='run-1'",
                        (original,),
                    )
        self.assertEqual(
            self.store.load_snapshot("user-1", "ws-1", "run-1").manifest,
            self.suite,
        )

    def test_changed_fingerprint_fails_closed(self):
        self.execute("UPDATE evaluation_snapshots SET manifest_fingerprint=?", ("0" * 64,))
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_invalid_json_and_duplicate_keys_fail_closed(self):
        for value in ('{"bad":true}', '{"suite_id":"x","suite_id":"y"}', "[]", "{oops"):
            with self.subTest(value=value):
                self.execute("UPDATE evaluation_snapshots SET manifest_json=?", (value,))
                with self.assertRaises(LookupError):
                    self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_missing_or_extra_submission_fails_closed(self):
        for submissions in (
            "[]",
            '[{"case_id":"case-a","answer":"answer","evidence":[]},'
            '{"case_id":"extra","answer":"yes","evidence":[]}]',
        ):
            with self.subTest(submissions=submissions):
                self.execute("UPDATE evaluation_snapshots SET submissions_json=?", (submissions,))
                with self.assertRaises(LookupError):
                    self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_duplicate_submission_entries_fail_closed(self):
        # Replaying an identical submission twice must not create two votes.
        with self.connection() as db:
            original = db.execute(
                "SELECT submissions_json FROM evaluation_snapshots WHERE run_id=?",
                ("run-1",),
            ).fetchone()[0]
        entries = json.loads(original)
        self.execute(
            "UPDATE evaluation_snapshots SET submissions_json=? WHERE run_id=?",
            (json.dumps([*entries, entries[0]]), "run-1"),
        )
        try:
            with self.assertRaisesRegex(LookupError, "^evaluation run not found$") as rejected:
                self.store.load_snapshot("user-1", "ws-1", "run-1")
            self.assertIsInstance(rejected.exception.__cause__, ValueError)
            self.assertEqual(
                str(rejected.exception.__cause__), "duplicate case_id in execution record"
            )
        finally:
            self.execute(
                "UPDATE evaluation_snapshots SET submissions_json=? WHERE run_id=?",
                (original, "run-1"),
            )
        self.assertEqual(
            self.store.load_snapshot("user-1", "ws-1", "run-1").execution,
            self.record,
        )

    def test_missing_snapshot_denied(self):
        self.execute("DELETE FROM evaluation_snapshots WHERE run_id='run-1'")
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_untrusted_identifiers_cannot_change_sql_scope(self):
        attack = "' OR 1=1 --"
        self.assertIsNone(self.store.member_role(attack, "ws-1"))
        self.assertIsNone(self.store.member_role("user-1", attack))
        self.assertIsNone(self.store.load_snapshot(attack, "ws-1", "run-1"))
        self.assertIsNone(self.store.load_snapshot("user-1", attack, "run-1"))
        self.assertIsNone(self.store.load_snapshot("user-1", "ws-1", attack))
        self.assertIsNotNone(self.store.load_snapshot("user-1", "ws-1", "run-1"))

    def test_json_size_limit_counts_utf8_bytes_not_characters(self):
        exact_limit = '"' + "a" * (1_048_576 - 2) + '"'
        self.assertEqual(len(exact_limit.encode("utf-8")), 1_048_576)
        self.assertEqual(_decode(exact_limit), "a" * (1_048_576 - 2))

        multibyte_payload = '"' + "é" * 524_288 + '"'
        self.assertLess(len(multibyte_payload), 1_048_576)
        self.assertGreater(len(multibyte_payload.encode("utf-8")), 1_048_576)
        with self.assertRaisesRegex(ValueError, "invalid snapshot payload size"):
            _decode(multibyte_payload)
        self.execute("UPDATE evaluation_snapshots SET manifest_json=?", (multibyte_payload,))
        with self.assertRaisesRegex(LookupError, "evaluation run not found"):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_oversized_persisted_json_is_rejected(self):
        too_large = " " * 1_048_577
        self.execute(
            "UPDATE evaluation_snapshots SET manifest_json=? WHERE run_id='run-1'",
            (too_large,),
        )
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_duplicate_fields_in_submission_json_are_rejected(self):
        duplicate = (
            '[{"case_id":"case-a","case_id":"case-a","answer":"answer","evidence":["source:1"]}]'
        )
        self.execute(
            "UPDATE evaluation_snapshots SET submissions_json=? WHERE run_id='run-1'",
            (duplicate,),
        )
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_unicode_escaped_duplicate_json_fields_fail_closed(self):
        # Escaped names must collide with their decoded counterparts, even
        # when both fields have the same valid value and shape.
        for column, payload in (
            (
                "manifest_json",
                r'{"suite_id":"suite-v1","\u0073uite_id":"suite-v1","version":1,"cases":[{"case_id":"case-a","expected_answer":"answer","required_evidence":["source:1"]}]}',
            ),
            (
                "submissions_json",
                r'[{"case_id":"case-a","\u0063ase_id":"case-a","answer":"answer","evidence":["source:1"]}]',
            ),
        ):
            with self.subTest(column=column):
                self.execute(
                    f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                    (payload,),
                )
                with self.assertRaisesRegex(LookupError, "^evaluation run not found$"):
                    self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_invalid_evidence_items_in_persisted_json_fail_closed(self):
        # Array shape alone is insufficient: items must be unique, nonblank strings.
        for column, evidence_key in (
            ("manifest_json", "required_evidence"),
            ("submissions_json", "evidence"),
        ):
            with self.connection() as db:
                original = db.execute(
                    f"SELECT {column} FROM evaluation_snapshots WHERE run_id='run-1'"
                ).fetchone()[0]
            for evidence in ([None], [3], [""], ["source:1", "source:1"]):
                with self.subTest(column=column, evidence=evidence):
                    payload = json.loads(original)
                    entry = payload["cases"][0] if column == "manifest_json" else payload[0]
                    entry[evidence_key] = evidence
                    self.execute(
                        f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                        (json.dumps(payload),),
                    )
                    with self.assertRaisesRegex(LookupError, "^evaluation run not found$"):
                        self.store.load_snapshot("user-1", "ws-1", "run-1")
            self.execute(
                f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                (original,),
            )

    def test_submission_evidence_must_be_json_array(self):
        malformed = '[{"case_id":"case-a","answer":"answer","evidence":"source:1"}]'
        self.execute(
            "UPDATE evaluation_snapshots SET submissions_json=? WHERE run_id='run-1'",
            (malformed,),
        )
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_excessive_manifest_json_nesting_fails_closed(self):
        nested = "[" * 2_000 + "]" * 2_000
        self.execute("UPDATE evaluation_snapshots SET manifest_json=?", (nested,))
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_excessive_submissions_json_nesting_fails_closed(self):
        nested = "[" * 2_000 + "]" * 2_000
        self.execute("UPDATE evaluation_snapshots SET submissions_json=?", (nested,))
        with self.assertRaises(LookupError):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_decoder_recursion_error_maps_to_scoped_lookup_failure(self):
        with patch(
            "app.infrastructure.sqlite.evaluation_read_repository.json.loads",
            side_effect=RecursionError("too deeply nested"),
        ):
            with self.assertRaisesRegex(LookupError, "evaluation run not found"):
                self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_nonfinite_json_numbers_rejected_at_decoder(self):
        # Python's default json.loads silently accepts these non-JSON constants.
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                    _decode(f'{{"value": {constant}}}')

    def test_nonfinite_submission_json_fails_scoped_read(self):
        self.execute(
            "UPDATE evaluation_snapshots SET submissions_json=? WHERE run_id='run-1'",
            ('[{"case_id":"case-a","answer":"answer","evidence":[NaN]}]',),
        )
        with self.assertRaisesRegex(LookupError, "evaluation run not found"):
            self.store.load_snapshot("user-1", "ws-1", "run-1")

    def test_unpaired_surrogates_rejected_at_decoder(self):
        for surrogate in ("\ud800", "\udfff"):
            for payload in (
                f'"{surrogate}"',
                json.dumps(surrogate),
                json.dumps({surrogate: ["valid"]}),
                json.dumps({"value": [surrogate]}),
            ):
                with self.subTest(payload=repr(payload)):
                    with self.assertRaisesRegex(ValueError, "invalid snapshot Unicode"):
                        _decode(payload)

    def test_persisted_surrogates_fail_scoped_read(self):
        for column in ("manifest_json", "submissions_json"):
            with self.connection() as db:
                original = db.execute(
                    f"SELECT {column} FROM evaluation_snapshots WHERE run_id='run-1'"
                ).fetchone()[0]
            for surrogate in ("\ud800", "\udfff"):
                with self.subTest(column=column, surrogate=repr(surrogate)):
                    self.execute(
                        f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                        (original.replace('"answer"', json.dumps(surrogate), 1),),
                    )
                    try:
                        with self.assertRaisesRegex(
                            LookupError, "evaluation run not found"
                        ) as raised:
                            self.store.load_snapshot("user-1", "ws-1", "run-1")
                        self.assertIsInstance(raised.exception.__cause__, ValueError)
                        self.assertEqual(
                            str(raised.exception.__cause__), "invalid snapshot Unicode"
                        )
                    finally:
                        self.execute(
                            f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                            (original,),
                        )

    def test_valid_non_ascii_unicode_round_trip(self):
        answer = "答案 café 😀"
        value = {"证据": [answer]}
        for ensure_ascii in (False, True):
            with self.subTest(ensure_ascii=ensure_ascii):
                self.assertEqual(_decode(json.dumps(value, ensure_ascii=ensure_ascii)), value)
        self.suite = EvalSuiteManifest("suite-v1", 1, (EvalCase("case-a", answer, ("source:1",)),))
        self.record = EvalExecutionRecord(
            self.scope,
            self.suite.fingerprint,
            (EvalSubmission("case-a", answer, ("source:1",)),),
        )
        with self.connection() as db:
            db.execute("DELETE FROM evaluation_snapshots")
            self.insert_snapshot(db)
        snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
        self.assertEqual(snapshot.manifest, self.suite)
        self.assertEqual(snapshot.execution, self.record)

    def test_rebound_run_agent_in_same_workspace_rejected_against_original_scope(self):
        self.execute("INSERT INTO agents VALUES ('agent-2', 'ws-1')")
        self.execute("INSERT INTO agent_versions VALUES ('version-3', 'agent-2', 1)")
        self.execute(
            "UPDATE runs SET agent_id='agent-2', agent_version_id='version-3' WHERE id='run-1'"
        )
        snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.scope, EvalRunScope("ws-1", "agent-2", 1, "run-1"))
        with self.assertRaisesRegex(LookupError, "evaluation run not found"):
            read_reconciled_evaluation("user-1", self.scope, self.record, self.store)

    def test_rebound_run_version_in_same_agent_rejected_against_original_scope(self):
        self.execute("INSERT INTO agent_versions VALUES ('version-3', 'agent-1', 3)")
        self.execute("UPDATE runs SET agent_version_id='version-3' WHERE id='run-1'")
        snapshot = self.store.load_snapshot("user-1", "ws-1", "run-1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.scope, EvalRunScope("ws-1", "agent-1", 3, "run-1"))
        with self.assertRaisesRegex(LookupError, "evaluation run not found"):
            read_reconciled_evaluation("user-1", self.scope, self.record, self.store)

    def test_caller_report_tampering_rejected(self):
        forged = EvalExecutionRecord(
            self.scope,
            self.suite.fingerprint,
            (EvalSubmission("case-a", "altered", ("source:1",)),),
        )
        with self.assertRaisesRegex(ValueError, "differs"):
            read_reconciled_evaluation("user-1", self.scope, forged, self.store)

    def test_exponent_overflow_rejected_at_decoder(self):
        for number in ("1e999", "-1e999", "1E309", "-1E309"):
            for payload in (number, f'{{"value": [{number}]}}'):
                with self.subTest(payload=payload):
                    with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                        _decode(payload)

    def test_nonfinite_error_never_echoes_untrusted_numeric_payload(self):
        number = "1e" + "9" * 200
        with self.assertRaises(ValueError) as rejected:
            _decode(f'{{"value": {number}}}')
        self.assertEqual(str(rejected.exception), "non-finite JSON number")

        for column in ("manifest_json", "submissions_json"):
            with self.subTest(column=column), self.connection() as db:
                original = db.execute(
                    f"SELECT {column} FROM evaluation_snapshots WHERE run_id='run-1'"
                ).fetchone()[0]
            self.execute(
                f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                (original.replace(': "answer"', f": {number}", 1),),
            )
            try:
                with self.assertRaisesRegex(LookupError, "evaluation run not found") as raised:
                    self.store.load_snapshot("user-1", "ws-1", "run-1")
                self.assertEqual(str(raised.exception.__cause__), "non-finite JSON number")
            finally:
                self.execute(
                    f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                    (original,),
                )

    def test_decoder_preserves_finite_numeric_parsing(self):
        values = _decode('[0, 42, -7, 1.25, -2.5e2, 1e308, 1e-999, "1e999"]')
        self.assertEqual(values, [0, 42, -7, 1.25, -250.0, 1e308, 0.0, "1e999"])
        self.assertTrue(all(type(value) is int for value in values[:3]))
        self.assertTrue(all(type(value) is float for value in values[3:7]))

    def test_exponent_overflow_in_persisted_snapshot_fails_scoped_read(self):
        for column in ("manifest_json", "submissions_json"):
            with self.connection() as db:
                original = db.execute(
                    f"SELECT {column} FROM evaluation_snapshots WHERE run_id='run-1'"
                ).fetchone()[0]
            for number in ("1e999", "-1e999"):
                with self.subTest(column=column, number=number):
                    self.execute(
                        f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                        (original.replace(': "answer"', f": {number}", 1),),
                    )
                    try:
                        with self.assertRaisesRegex(
                            LookupError, "evaluation run not found"
                        ) as raised:
                            self.store.load_snapshot("user-1", "ws-1", "run-1")
                        self.assertIsInstance(raised.exception.__cause__, ValueError)
                        self.assertIn("non-finite JSON number", str(raised.exception.__cause__))
                    finally:
                        self.execute(
                            f"UPDATE evaluation_snapshots SET {column}=? WHERE run_id='run-1'",
                            (original,),
                        )


if __name__ == "__main__":
    unittest.main()
