"""Offline S2 C08 trust-boundary contract tests; no LLM/network/database."""

import unittest
from dataclasses import FrozenInstanceError

from app.domain.evaluation import EvalCase
from app.domain.evaluation_batch import EvalSubmission
from app.domain.evaluation_execution_binding import (
    EvalExecutionRecord,
    EvalRunScope,
    ReconciledEvalEvidence,
    reconcile_eval_execution,
)
from app.domain.evaluation_suite_integrity import EvalSuiteManifest


def suite(*cases, version=1):
    return EvalSuiteManifest("offline-bound", version, cases)


def scope(**overrides):
    fields = {
        "workspace_id": "ws-1",
        "agent_id": "agent-1",
        "agent_version": 3,
        "run_id": "run-1",
    }
    fields.update(overrides)
    return EvalRunScope(**fields)


def record(manifest, *submissions, bound_scope=None, fingerprint=None):
    return EvalExecutionRecord(
        scope() if bound_scope is None else bound_scope,
        manifest.fingerprint if fingerprint is None else fingerprint,
        submissions,
    )


class EvalExecutionBindingTests(unittest.TestCase):
    def setUp(self):
        self.a = EvalCase("a", "ok", ("doc:1", "doc:2"))
        self.b = EvalCase("b", "ready")
        self.manifest = suite(self.a, self.b)
        self.outputs = (
            EvalSubmission("a", "ok", ("doc:1", "doc:2")),
            EvalSubmission("b", "ready"),
        )
        self.canonical = record(self.manifest, *self.outputs)

    def reconcile(self, reported=None, authoritative=None, manifest=None, expected=None):
        return reconcile_eval_execution(
            self.manifest if manifest is None else manifest,
            self.canonical if reported is None else reported,
            self.canonical if authoritative is None else authoritative,
            scope() if expected is None else expected,
        )

    def test_reconciles_matching_complete_records(self):
        output = self.reconcile()
        self.assertIsInstance(output, ReconciledEvalEvidence)
        self.assertTrue(output.summary.hard_pass)
        self.assertEqual(output.summary.total, 2)
        self.assertEqual(output.scope, scope())
        self.assertEqual(output.manifest_fingerprint, self.manifest.fingerprint)

    def test_submission_order_and_evidence_order_are_semantically_stable(self):
        rearranged = record(
            self.manifest,
            self.outputs[1],
            EvalSubmission("a", "ok", ("doc:2", "doc:1")),
        )
        self.assertEqual(self.reconcile(reported=rearranged).summary, self.reconcile().summary)

    def test_failed_candidate_does_not_become_success_due_to_reconciliation(self):
        wrong = record(
            self.manifest,
            EvalSubmission("a", "bad", ("doc:1", "doc:2")),
            self.outputs[1],
        )
        output = self.reconcile(reported=wrong, authoritative=wrong)
        self.assertFalse(output.summary.hard_pass)
        self.assertEqual(output.summary.failed, 1)
        self.assertEqual(output.summary.failure_counts, {"answer_mismatch": 1})

    def test_missing_oracle_stays_incomparable(self):
        manifest = suite(EvalCase("z", None))
        result = reconcile_eval_execution(
            manifest,
            record(manifest, EvalSubmission("z", "anything")),
            record(manifest, EvalSubmission("z", "anything")),
            scope(),
        )
        self.assertEqual(result.summary.incomparable, 1)
        self.assertFalse(result.summary.hard_pass)

    def test_blank_answer_remains_scored_failure(self):
        blank = record(
            self.manifest,
            EvalSubmission("a", " ", ("doc:1", "doc:2")),
            self.outputs[1],
        )
        self.assertEqual(
            self.reconcile(reported=blank, authoritative=blank).summary.failure_counts,
            {"empty_response": 1},
        )

    def test_reported_answer_tamper_rejected(self):
        forged = record(
            self.manifest,
            EvalSubmission("a", "forged", ("doc:1", "doc:2")),
            self.outputs[1],
        )
        with self.assertRaisesRegex(ValueError, "differs"):
            self.reconcile(reported=forged)

    def test_reported_evidence_tamper_rejected(self):
        forged = record(
            self.manifest,
            EvalSubmission("a", "ok", ("doc:1",)),
            self.outputs[1],
        )
        with self.assertRaisesRegex(ValueError, "differs"):
            self.reconcile(reported=forged)

    def test_missing_or_additional_reported_cases_rejected(self):
        for submissions in [
            (self.outputs[0],),
            (*self.outputs, EvalSubmission("unknown", "yes")),
        ]:
            with (
                self.subTest(submissions=submissions),
                self.assertRaisesRegex(ValueError, "reported case IDs"),
            ):
                self.reconcile(reported=record(self.manifest, *submissions))

    def test_missing_or_additional_authoritative_cases_rejected(self):
        for submissions in [
            (self.outputs[1],),
            (*self.outputs, EvalSubmission("unknown", "yes")),
        ]:
            with (
                self.subTest(submissions=submissions),
                self.assertRaisesRegex(ValueError, "authoritative case IDs"),
            ):
                self.reconcile(authoritative=record(self.manifest, *submissions))

    def test_empty_case_submissions_are_never_accepted(self):
        with self.assertRaisesRegex(ValueError, "case IDs"):
            self.reconcile(reported=record(self.manifest))

    def test_duplicate_case_id_cannot_enter_execution_record(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            record(self.manifest, self.outputs[0], self.outputs[0])

    def test_cross_workspace_is_denied_on_both_sides(self):
        wrong = record(self.manifest, *self.outputs, bound_scope=scope(workspace_id="ws-2"))
        for kind in ("reported", "authoritative"):
            with self.subTest(side=kind), self.assertRaisesRegex(ValueError, "scope"):
                self.reconcile(**{kind: wrong})

    def test_agent_identity_mismatch_denied(self):
        wrong = record(self.manifest, *self.outputs, bound_scope=scope(agent_id="agent-2"))
        with self.assertRaisesRegex(ValueError, "scope"):
            self.reconcile(authoritative=wrong)

    def test_agent_version_mismatch_denied(self):
        wrong = record(self.manifest, *self.outputs, bound_scope=scope(agent_version=4))
        with self.assertRaisesRegex(ValueError, "scope"):
            self.reconcile(reported=wrong)

    def test_run_id_mismatch_denied(self):
        wrong = record(self.manifest, *self.outputs, bound_scope=scope(run_id="run-2"))
        with self.assertRaisesRegex(ValueError, "scope"):
            self.reconcile(authoritative=wrong)

    def test_caller_expected_scope_cannot_be_overridden_by_matching_bad_receipts(self):
        wrong_scope = scope(workspace_id="ws-2")
        matching_wrong = record(self.manifest, *self.outputs, bound_scope=wrong_scope)
        with self.assertRaisesRegex(ValueError, "scope"):
            self.reconcile(reported=matching_wrong, authoritative=matching_wrong)

    def test_submitted_fingerprint_tamper_denied(self):
        wrong = record(self.manifest, *self.outputs, fingerprint="a" * 64)
        with self.assertRaisesRegex(ValueError, "reported suite fingerprint"):
            self.reconcile(reported=wrong)

    def test_authoritative_fingerprint_tamper_denied(self):
        wrong = record(self.manifest, *self.outputs, fingerprint="b" * 64)
        with self.assertRaisesRegex(ValueError, "authoritative suite fingerprint"):
            self.reconcile(authoritative=wrong)

    def test_changed_oracle_suite_evidence_or_version_denied(self):
        for changed in (
            suite(EvalCase("a", "different", ("doc:1", "doc:2")), self.b),
            suite(EvalCase("a", "ok", ("doc:9",)), self.b),
            suite(self.a, self.b, version=2),
        ):
            with (
                self.subTest(digest=changed.fingerprint),
                self.assertRaisesRegex(ValueError, "suite fingerprint"),
            ):
                self.reconcile(manifest=changed)

    def test_invalid_scope_identifiers_and_versions_rejected(self):
        for field in ("workspace_id", "agent_id", "run_id"):
            for invalid in ("", "   ", None, 8):
                with (
                    self.subTest(field=field, invalid=invalid),
                    self.assertRaises((ValueError, TypeError)),
                ):
                    scope(**{field: invalid})
        for invalid in (0, -1, True, 3.5, "1", None):
            with self.subTest(version=invalid), self.assertRaises(ValueError):
                scope(agent_version=invalid)

    def test_invalid_receipt_type_fingerprint_and_collection_rejected(self):
        with self.assertRaises(TypeError):
            EvalExecutionRecord(None, self.manifest.fingerprint, self.outputs)
        for invalid in ("", "A" * 64, "z" * 64, None):
            with self.subTest(fingerprint=invalid), self.assertRaises(ValueError):
                EvalExecutionRecord(scope(), invalid, self.outputs)
        for invalid in (list(self.outputs), (None,), None):
            with self.subTest(submissions=invalid), self.assertRaises(TypeError):
                EvalExecutionRecord(scope(), self.manifest.fingerprint, invalid)

    def test_invalid_comparison_inputs_fail_closed(self):
        for field in ("manifest", "reported", "authoritative", "expected_scope"):
            values = {
                "manifest": self.manifest,
                "reported": self.canonical,
                "authoritative": self.canonical,
                "expected_scope": scope(),
            }
            values[field] = None
            with self.subTest(field=field), self.assertRaises(TypeError):
                reconcile_eval_execution(**values)

    def test_self_declared_trusted_flag_is_not_supported(self):
        with self.assertRaises(TypeError):
            EvalExecutionRecord(scope(), self.manifest.fingerprint, self.outputs, trusted=True)

    def test_receipt_and_result_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.canonical.scope = scope(agent_id="forged")
        with self.assertRaises(FrozenInstanceError):
            self.reconcile().summary = None


if __name__ == "__main__":
    unittest.main()
