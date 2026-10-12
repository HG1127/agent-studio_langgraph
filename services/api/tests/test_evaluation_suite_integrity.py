"""Offline tests for Eval Suite manifest identity and release gating."""

import unittest

from app.domain.evaluation import EvalCase, EvalSummary, evaluate
from app.domain.evaluation_suite_integrity import (
    EvalSuiteManifest,
    assess_versioned_eval_gate,
)


def manifest(*cases, version=1):
    return EvalSuiteManifest("contract-v1", version, cases)


def summary(*cases):
    return EvalSummary(
        tuple(evaluate(case, case.expected_answer or "", case.required_evidence) for case in cases)
    )


class SuiteIntegrityTests(unittest.TestCase):
    def test_exact_matching_suite_is_eligible(self):
        a, b = EvalCase("a", "yes"), EvalCase("b", "done")
        report = assess_versioned_eval_gate(
            manifest(a, b), summary(a, b), manifest(b, a), summary(b, a)
        )
        self.assertTrue(report.eligible)
        self.assertEqual(report.blockers, ())

    def test_changed_oracle_is_blocked_even_if_both_pass(self):
        a, b = EvalCase("a", "yes"), EvalCase("a", "no")
        report = assess_versioned_eval_gate(manifest(a), summary(a), manifest(b), summary(b))
        self.assertFalse(report.eligible)
        self.assertEqual(report.blockers, ("suite_fingerprint_mismatch",))
        self.assertTrue(report.regression.eligible)

    def test_changed_version_blocks(self):
        a = EvalCase("a", "yes")
        report = assess_versioned_eval_gate(
            manifest(a), summary(a), manifest(a, version=2), summary(a)
        )
        self.assertIn("suite_fingerprint_mismatch", report.blockers)

    def test_missing_result_blocks(self):
        a = EvalCase("a", "yes")
        report = assess_versioned_eval_gate(manifest(a), EvalSummary(()), manifest(a), summary(a))
        self.assertIn("baseline_manifest_mismatch", report.blockers)

    def test_invalid_manifest_rejected(self):
        a = EvalCase("a", "yes")
        with self.assertRaises(ValueError):
            manifest(a, a)
        with self.assertRaises(ValueError):
            EvalSuiteManifest("suite", 1, ())

    def test_candidate_failure_not_authorized(self):
        a = EvalCase("a", "yes")
        failed = EvalSummary((evaluate(a, "wrong"),))
        report = assess_versioned_eval_gate(manifest(a), summary(a), manifest(a), failed)
        self.assertEqual(report.blockers, ("candidate_failed", "case_regression"))

    def test_reordered_evidence_is_same_suite(self):
        first = EvalCase("a", "yes", ("doc:2", "doc:1"))
        second = EvalCase("a", "yes", ("doc:1", "doc:2"))
        self.assertEqual(manifest(first).fingerprint, manifest(second).fingerprint)
        report = assess_versioned_eval_gate(
            manifest(first), summary(first), manifest(second), summary(second)
        )
        self.assertTrue(report.eligible)

    def test_changed_evidence_requirement_is_blocked(self):
        before = EvalCase("a", "yes", ("doc:1",))
        after = EvalCase("a", "yes", ("doc:2",))
        report = assess_versioned_eval_gate(
            manifest(before), summary(before), manifest(after), summary(after)
        )
        self.assertEqual(report.blockers, ("suite_fingerprint_mismatch",))

    def test_candidate_result_subset_is_blocked(self):
        a, b = EvalCase("a", "yes"), EvalCase("b", "done")
        report = assess_versioned_eval_gate(
            manifest(a, b), summary(a, b), manifest(a, b), summary(a)
        )
        self.assertFalse(report.eligible)
        self.assertIn("candidate_manifest_mismatch", report.blockers)
        self.assertIn("suite_mismatch", report.blockers)

    def test_incomparable_result_still_blocks(self):
        case = EvalCase("a", "yes")
        unknown = EvalCase("a", None)
        report = assess_versioned_eval_gate(
            manifest(case),
            summary(case),
            manifest(case),
            EvalSummary((evaluate(unknown, "yes"),)),
        )
        self.assertEqual(report.blockers, ("candidate_incomparable", "case_regression"))

    def test_invalid_manifest_and_summary_contracts(self):
        case = EvalCase("a", "yes")
        with self.assertRaises(ValueError):
            EvalSuiteManifest(" ", 1, (case,))
        with self.assertRaises(ValueError):
            EvalSuiteManifest("id", 0, (case,))
        with self.assertRaises(ValueError):
            EvalSuiteManifest("id", True, (case,))
        with self.assertRaises(TypeError):
            EvalSuiteManifest("id", 1, ("a",))
        with self.assertRaises(TypeError):
            assess_versioned_eval_gate(None, summary(case), manifest(case), summary(case))


if __name__ == "__main__":
    unittest.main()
