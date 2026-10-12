"""Synthetic fail-closed evaluation regression contracts; no network/LLM/DB."""

import unittest

from app.domain.evaluation import EvalCase, EvalSummary, evaluate
from app.domain.evaluation_gate import assess_eval_regression


def passed(case_id: str):
    return evaluate(EvalCase(case_id, "ok"), "ok")


def failed(case_id: str):
    return evaluate(EvalCase(case_id, "ok"), "wrong")


def incomparable(case_id: str):
    return evaluate(EvalCase(case_id, None), "ok")


def summary(*results):
    return EvalSummary(results)


class EvaluationGateTests(unittest.TestCase):
    def test_identical_complete_suites_are_eligible(self):
        report = assess_eval_regression(
            summary(passed("one"), passed("two")),
            summary(passed("two"), passed("one")),
        )
        self.assertTrue(report.eligible)
        self.assertEqual(report.blockers, ())
        self.assertEqual(report.regressed_case_ids, ())

    def test_empty_suites_never_authorize(self):
        report = assess_eval_regression(summary(), summary())
        self.assertFalse(report.eligible)
        self.assertEqual(report.blockers, ("empty_suite",))

    def test_missing_or_added_case_ids_block(self):
        report = assess_eval_regression(
            summary(passed("one")), summary(passed("one"), passed("two"))
        )
        self.assertFalse(report.eligible)
        self.assertEqual(report.blockers, ("suite_mismatch",))
        self.assertFalse(assess_eval_regression(summary(passed("one")), summary()).eligible)

    def test_failed_candidate_is_blocked_even_without_regression(self):
        report = assess_eval_regression(summary(failed("x")), summary(failed("x")))
        self.assertFalse(report.eligible)
        self.assertEqual(report.blockers, ("candidate_failed",))
        self.assertEqual(report.failed_case_ids, ("x",))

    def test_failed_baseline_can_be_fixed(self):
        report = assess_eval_regression(summary(failed("x")), summary(passed("x")))
        self.assertTrue(report.eligible)

    def test_regression_is_stable_and_explained(self):
        old = summary(passed("z"), passed("a"))
        new = summary(failed("a"), failed("z"))
        report = assess_eval_regression(old, new)
        self.assertEqual(report.blockers, ("candidate_failed", "case_regression"))
        self.assertEqual(report.regressed_case_ids, ("a", "z"))
        self.assertEqual(report.failed_case_ids, ("a", "z"))
        self.assertEqual(
            report,
            assess_eval_regression(
                summary(passed("a"), passed("z")),
                summary(failed("z"), failed("a")),
            ),
        )

    def test_baseline_missing_oracle_is_a_hard_blocker(self):
        report = assess_eval_regression(summary(incomparable("x")), summary(passed("x")))
        self.assertEqual(report.blockers, ("baseline_incomparable",))
        self.assertFalse(report.eligible)

    def test_candidate_missing_oracle_is_blocked_and_regressed(self):
        report = assess_eval_regression(summary(passed("x")), summary(incomparable("x")))
        self.assertEqual(report.blockers, ("candidate_incomparable", "case_regression"))
        self.assertEqual(report.incomparable_case_ids, ("x",))
        self.assertEqual(report.regressed_case_ids, ("x",))

    def test_all_uncomparable_never_fake_a_release(self):
        report = assess_eval_regression(summary(incomparable("x")), summary(incomparable("x")))
        self.assertFalse(report.eligible)
        self.assertEqual(report.blockers, ("baseline_incomparable", "candidate_incomparable"))

    def test_missing_evidence_fails_candidate(self):
        case = EvalCase("x", "ok", ("doc:v1",))
        report = assess_eval_regression(
            summary(evaluate(case, "ok", ("doc:v1",))),
            summary(evaluate(case, "ok")),
        )
        self.assertEqual(report.blockers, ("candidate_failed", "case_regression"))

    def test_invalid_contract_inputs_rejected(self):
        for old, new in [
            (None, summary(passed("x"))),
            (summary(passed("x")), (passed("x"),)),
            ([], []),
        ]:
            with self.subTest(old=old, new=new), self.assertRaises(TypeError):
                assess_eval_regression(old, new)


if __name__ == "__main__":
    unittest.main()
