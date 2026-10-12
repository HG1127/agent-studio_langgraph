"""Offline authorization/read-port contract tests; no DB, network or LLM."""

import unittest

from app.application.evaluation_read_service import (
    AuthoritativeEvalSnapshot,
    read_reconciled_evaluation,
)
from app.domain.evaluation import EvalCase
from app.domain.evaluation_batch import EvalSubmission
from app.domain.evaluation_execution_binding import EvalExecutionRecord, EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest


def scope(**changes):
    values = {"workspace_id": "ws-1", "agent_id": "agent-1", "agent_version": 2, "run_id": "run-1"}
    values.update(changes)
    return EvalRunScope(**values)


class FakeStore:
    def __init__(self, snapshot, *, role="viewer"):
        self.snapshot = snapshot
        self.role = role
        self.calls = []

    def member_role(self, authenticated_user_id, workspace_id):
        self.calls.append(("member", authenticated_user_id, workspace_id))
        return self.role

    def load_snapshot(self, authenticated_user_id, workspace_id, run_id):
        self.calls.append(("snapshot", authenticated_user_id, workspace_id, run_id))
        return self.snapshot


class AuthorizedEvalReadTests(unittest.TestCase):
    def setUp(self):
        self.manifest = EvalSuiteManifest("suite-v1", 1, (EvalCase("case-a", "answer"),))
        self.submission = EvalSubmission("case-a", "answer")
        self.canonical = EvalExecutionRecord(scope(), self.manifest.fingerprint, (self.submission,))
        self.snapshot = AuthoritativeEvalSnapshot(scope(), self.manifest, self.canonical)

    def read(self, *, role="viewer", requested=None, reported=None, snapshot=None, actor="user-1"):
        store = FakeStore(self.snapshot if snapshot is None else snapshot, role=role)
        result = read_reconciled_evaluation(
            actor,
            scope() if requested is None else requested,
            self.canonical if reported is None else reported,
            store,
        )
        return result, store

    def test_authorized_roles_can_read_scoped_immutable_results(self):
        for role in ("viewer", "member", "admin", "owner"):
            with self.subTest(role=role):
                result, store = self.read(role=role)
                self.assertTrue(result.summary.hard_pass)
                self.assertEqual(result.scope, scope())
                self.assertEqual(
                    store.calls,
                    [("member", "user-1", "ws-1"), ("snapshot", "user-1", "ws-1", "run-1")],
                )

    def test_nonmembers_and_unknown_roles_fail_before_run_access(self):
        for role in (None, "", "guest", "editor", "VIEWER"):
            with self.subTest(role=role):
                store = FakeStore(self.snapshot, role=role)
                with self.assertRaisesRegex(PermissionError, "access denied"):
                    read_reconciled_evaluation("user-1", scope(), self.canonical, store)
                self.assertEqual(store.calls, [("member", "user-1", "ws-1")])

    def test_missing_authenticated_actor_rejected_without_store_calls(self):
        for actor in (None, "", " ", 7):
            with self.subTest(actor=actor):
                store = FakeStore(self.snapshot)
                with self.assertRaises(PermissionError):
                    read_reconciled_evaluation(actor, scope(), self.canonical, store)
                self.assertEqual(store.calls, [])

    def test_invalid_inputs_rejected_without_read(self):
        for requested, receipt in ((None, self.canonical), (scope(), None)):
            with self.subTest(requested=requested, receipt=receipt):
                store = FakeStore(self.snapshot)
                with self.assertRaises(TypeError):
                    read_reconciled_evaluation("user-1", requested, receipt, store)
                self.assertEqual(store.calls, [])

    def test_missing_store_rejected(self):
        with self.assertRaises(TypeError):
            read_reconciled_evaluation("user-1", scope(), self.canonical, None)

    def test_revoked_between_checks_fails_closed(self):
        class RevokedAtSnapshot(FakeStore):
            def load_snapshot(self, authenticated_user_id, workspace_id, run_id):
                self.calls.append(("snapshot", authenticated_user_id, workspace_id, run_id))
                return None  # Role was revoked before the authoritative scoped read.

        store = RevokedAtSnapshot(self.snapshot)
        with self.assertRaises(LookupError):
            read_reconciled_evaluation("user-1", scope(), self.canonical, store)
        self.assertEqual(len(store.calls), 2)

    def test_missing_snapshot_denied(self):
        store = FakeStore(None)
        with self.assertRaisesRegex(LookupError, "not found"):
            read_reconciled_evaluation("user-1", scope(), self.canonical, store)

    def test_incorrect_snapshot_type_denied(self):
        store = FakeStore({"untrusted": "data"})
        with self.assertRaisesRegex(LookupError, "not found"):
            read_reconciled_evaluation("user-1", scope(), self.canonical, store)

    def test_requested_other_workspace_denied_even_for_member(self):
        store = FakeStore(self.snapshot)
        wrong = scope(workspace_id="ws-2")
        with self.assertRaises(LookupError):
            read_reconciled_evaluation("user-1", wrong, self.canonical, store)
        self.assertEqual(store.calls[-1], ("snapshot", "user-1", "ws-2", "run-1"))

    def test_requested_other_agent_version_or_run_denied(self):
        for changed in ({"agent_id": "agent-2"}, {"agent_version": 3}, {"run_id": "run-2"}):
            with self.subTest(changed=changed):
                store = FakeStore(self.snapshot)
                with self.assertRaises(LookupError):
                    read_reconciled_evaluation("user-1", scope(**changed), self.canonical, store)

    def test_snapshot_run_scope_mismatch_denied(self):
        poisoned = AuthoritativeEvalSnapshot(
            scope(),
            self.manifest,
            EvalExecutionRecord(
                scope(run_id="run-2"), self.manifest.fingerprint, (self.submission,)
            ),
        )
        store = FakeStore(poisoned)
        with self.assertRaises(LookupError):
            read_reconciled_evaluation("user-1", scope(), self.canonical, store)

    def test_reported_data_tampering_denied(self):
        forged = EvalExecutionRecord(
            scope(), self.manifest.fingerprint, (EvalSubmission("case-a", "forged"),)
        )
        store = FakeStore(self.snapshot)
        with self.assertRaisesRegex(ValueError, "differs"):
            read_reconciled_evaluation("user-1", scope(), forged, store)

    def test_reported_fingerprint_tampering_denied(self):
        forged = EvalExecutionRecord(scope(), "a" * 64, (self.submission,))
        store = FakeStore(self.snapshot)
        with self.assertRaisesRegex(ValueError, "suite fingerprint"):
            read_reconciled_evaluation("user-1", scope(), forged, store)

    def test_server_snapshot_fingerprint_mismatch_denied(self):
        bad = AuthoritativeEvalSnapshot(
            scope(), self.manifest, EvalExecutionRecord(scope(), "b" * 64, (self.submission,))
        )
        store = FakeStore(bad)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            read_reconciled_evaluation("user-1", scope(), self.canonical, store)

    def test_empty_or_malformed_snapshot_data_never_scores(self):
        with self.assertRaises(TypeError):
            AuthoritativeEvalSnapshot(None, self.manifest, self.canonical)
        with self.assertRaises(TypeError):
            AuthoritativeEvalSnapshot(scope(), None, self.canonical)
        with self.assertRaises(TypeError):
            AuthoritativeEvalSnapshot(scope(), self.manifest, None)

    def test_scored_failure_is_not_promoted_to_success(self):
        submitted = EvalExecutionRecord(
            scope(), self.manifest.fingerprint, (EvalSubmission("case-a", "wrong"),)
        )
        store = FakeStore(AuthoritativeEvalSnapshot(scope(), self.manifest, submitted))
        output = read_reconciled_evaluation("user-1", scope(), submitted, store)
        self.assertFalse(output.summary.hard_pass)
        self.assertEqual(output.summary.failed, 1)


if __name__ == "__main__":
    unittest.main()
