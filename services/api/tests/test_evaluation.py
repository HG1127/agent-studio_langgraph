"""Synthetic offline evaluation contracts; no model, database or network fixtures."""

import unittest
from dataclasses import FrozenInstanceError

from app.domain.evaluation import EvalCase, EvalResult, EvalSummary, evaluate


class EvaluationTests(unittest.TestCase):
    def test_pass_is_repeatable_and_trims_only_outer_whitespace(self):
        case = EvalCase("c1", " Yes ", ("doc:v1",))
        result = evaluate(case, "Yes\n", ("extra", "doc:v1"))
        self.assertEqual(result, EvalResult("c1", "passed", 1))
        self.assertEqual(result, evaluate(case, "Yes\n", ("doc:v1", "extra")))
        self.assertEqual(evaluate(case, "yes", ("doc:v1",)).reasons, ("answer_mismatch",))

    def test_multiple_failures_and_exact_evidence_ids(self):
        case = EvalCase("c1", "answer", ("doc:v1",))
        self.assertEqual(
            evaluate(case, "wrong", ("doc:v2",)).reasons, ("missing_evidence", "answer_mismatch")
        )
        self.assertEqual(evaluate(case, " \n").reasons, ("empty_response", "missing_evidence"))
        self.assertEqual(evaluate(case, "answer").score, 0)

    def test_incomparable_preserves_other_causes(self):
        result = evaluate(EvalCase("c1", None, ("doc",)), "")
        self.assertEqual(result.status, "incomparable")
        self.assertIsNone(result.score)
        self.assertEqual(result.reasons, ("empty_response", "missing_evidence", "not_comparable"))

    def test_input_validation(self):
        for args in [
            ("", "a"),
            ("c", " "),
            (1, "a"),
            ("c", 1),
            ("c", "a", ["doc"]),
            ("c", "a", ("",)),
            ("c", "a", ("doc", "doc")),
        ]:
            with self.subTest(args=args), self.assertRaises((TypeError, ValueError)):
                EvalCase(*args)
        for answer, evidence in [
            (None, ()),
            (3, ()),
            ("a", ["doc"]),
            ("a", (3,)),
            ("a", ("doc", "doc")),
        ]:
            with self.subTest(answer=answer), self.assertRaises((TypeError, ValueError)):
                evaluate(EvalCase("c", "a"), answer, evidence)
        with self.assertRaises(TypeError):
            evaluate(None, "a")
        with self.assertRaises(TypeError):
            EvalCase("c", "a", unknown=True)

    def test_result_rejects_forged_or_inconsistent_output(self):
        for status, score, reasons in [
            ("passed", 0, ()),
            ("passed", True, ()),
            ("passed", 1.0, ()),
            ("failed", 0, ()),
            ("failed", 0, ("not_comparable",)),
            ("failed", 0, ("empty_response", "answer_mismatch")),
            ("incomparable", 0, ("not_comparable",)),
            ("unknown", 1, ()),
            ("failed", 0, ("unknown",)),
            ("failed", 0, ("answer_mismatch", "missing_evidence")),
            ("failed", 0, ("empty_response", "empty_response")),
            ("failed", 0, ["empty_response"]),
        ]:
            with self.subTest(status=status, reasons=reasons):
                with self.assertRaises((TypeError, ValueError)):
                    EvalResult("c", status, score, reasons)

    def test_summary_counts_denominators_and_gate(self):
        results = (
            evaluate(EvalCase("a", "ok"), "ok"),
            evaluate(EvalCase("b", "ok", ("doc",)), "bad"),
            evaluate(EvalCase("c", None), "ok"),
        )
        summary = EvalSummary(results)
        self.assertEqual(
            (summary.total, summary.passed, summary.failed, summary.incomparable), (3, 1, 1, 1)
        )
        self.assertEqual(summary.mean_score, 0.5)
        self.assertFalse(summary.hard_pass)
        self.assertEqual(
            summary.failure_counts,
            {"missing_evidence": 1, "answer_mismatch": 1, "not_comparable": 1},
        )
        self.assertTrue(EvalSummary(results[:1]).hard_pass)
        self.assertEqual(EvalSummary(tuple(reversed(results))).mean_score, 0.5)
        summary.failure_counts.clear()
        self.assertEqual(len(summary.failure_counts), 3)

    def test_empty_all_incomparable_and_duplicate_summary(self):
        for results in [(), (evaluate(EvalCase("c", None), "ok"),)]:
            summary = EvalSummary(results)
            self.assertIsNone(summary.mean_score)
            self.assertFalse(summary.hard_pass)
        result = EvalResult("c", "passed", 1)
        with self.assertRaises(ValueError):
            EvalSummary((result, result))
        for results in [[], (None,)]:
            with self.assertRaises(TypeError):
                EvalSummary(results)

    def test_contracts_are_immutable(self):
        case = EvalCase("c", "ok")
        with self.assertRaises(FrozenInstanceError):
            case.case_id = "changed"


if __name__ == "__main__":
    unittest.main()
