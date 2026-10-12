"""Pure offline contract tests for evaluation-batch input replay."""

import unittest
from dataclasses import FrozenInstanceError

from app.domain.evaluation import EvalCase, EvalSummary
from app.domain.evaluation_batch import EvalSubmission, replay_eval_batch
from app.domain.evaluation_suite_integrity import EvalSuiteManifest


def suite(*cases):
    return EvalSuiteManifest("offline-replay", 1, cases)


class BatchReplayTests(unittest.TestCase):
    def test_replay_is_deterministic_across_submission_and_case_order(self):
        a, b = EvalCase("a", "one"), EvalCase("b", "two", ("doc:2",))
        first = replay_eval_batch(
            suite(b, a), (EvalSubmission("b", "two", ("doc:2",)), EvalSubmission("a", "one"))
        )
        second = replay_eval_batch(
            suite(a, b), (EvalSubmission("a", "one"), EvalSubmission("b", "two", ("doc:2",)))
        )
        self.assertEqual(first, second)
        self.assertEqual(tuple(item.case_id for item in first.results), ("a", "b"))
        self.assertTrue(first.hard_pass)

    def test_missing_evidence_and_wrong_answer_have_both_failures(self):
        result = replay_eval_batch(
            suite(EvalCase("a", "ok", ("doc:1",))),
            (EvalSubmission("a", "wrong", ("doc:2",)),),
        ).results[0]
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reasons, ("missing_evidence", "answer_mismatch"))

    def test_blank_answer_is_scored_as_failure_not_structural_error(self):
        result = replay_eval_batch(
            suite(EvalCase("a", "ok")), (EvalSubmission("a", "  "),)
        ).results[0]
        self.assertEqual(result.reasons, ("empty_response",))

    def test_missing_oracle_never_passes(self):
        result = replay_eval_batch(
            suite(EvalCase("a", None)), (EvalSubmission("a", "answer"),)
        ).results[0]
        self.assertEqual(result.status, "incomparable")
        self.assertIsNone(result.score)

    def test_missing_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replay_eval_batch(
                suite(EvalCase("a", "ok"), EvalCase("b", "ok")),
                (EvalSubmission("a", "ok"),),
            )

    def test_unknown_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replay_eval_batch(
                suite(EvalCase("a", "ok")),
                (EvalSubmission("a", "ok"), EvalSubmission("extra", "ok")),
            )

    def test_duplicate_submission_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate submission"):
            replay_eval_batch(
                suite(EvalCase("a", "ok")),
                (EvalSubmission("a", "ok"), EvalSubmission("a", "ok")),
            )

    def test_empty_submission_is_never_silent_success(self):
        with self.assertRaises(ValueError):
            replay_eval_batch(suite(EvalCase("a", "ok")), ())

    def test_wrong_manifest_and_submission_collection_types_rejected(self):
        good = suite(EvalCase("a", "ok"))
        for manifest, submissions in [
            (None, (EvalSubmission("a", "ok"),)),
            (good, [EvalSubmission("a", "ok")]),
            (good, (None,)),
        ]:
            with self.subTest(manifest=manifest, submissions=submissions):
                with self.assertRaises(TypeError):
                    replay_eval_batch(manifest, submissions)

    def test_replay_returns_existing_immutable_summary_contract(self):
        output = replay_eval_batch(suite(EvalCase("a", "ok")), (EvalSubmission("a", "ok"),))
        self.assertIsInstance(output, EvalSummary)
        with self.assertRaises(FrozenInstanceError):
            output.results = ()

    def test_invalid_submission_identifiers_rejected(self):
        for case_id in ("", "   ", 42, None):
            with self.subTest(case_id=case_id):
                with self.assertRaises((ValueError, TypeError)):
                    EvalSubmission(case_id, "ok")

    def test_invalid_answer_type_is_rejected(self):
        with self.assertRaises(TypeError):
            EvalSubmission("a", None)

    def test_invalid_evidence_contract_rejected(self):
        for evidence in (["doc"], ("doc", "doc"), ("",), (3,), None):
            with self.subTest(evidence=evidence):
                with self.assertRaises((ValueError, TypeError)):
                    EvalSubmission("a", "ok", evidence)

    def test_submission_is_immutable(self):
        submission = EvalSubmission("a", "ok")
        with self.assertRaises(FrozenInstanceError):
            submission.answer = "forged"


if __name__ == "__main__":
    unittest.main()
