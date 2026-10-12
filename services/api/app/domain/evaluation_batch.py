"""Deterministic evaluation-batch replay over an immutable suite.

This validates supplied inputs but cannot attest to their origin or authorize release.
No model, database, filesystem, or network I/O is performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.evaluation import EvalSummary, evaluate
from app.domain.evaluation_suite_integrity import EvalSuiteManifest


@dataclass(frozen=True)
class EvalSubmission:
    """One reported answer and set of exact evidence identifiers."""

    case_id: str
    answer: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a nonblank string")
        if not isinstance(self.answer, str):
            raise TypeError("answer must be a string")
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("evidence must contain nonblank strings")
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("duplicate evidence identifier")


def replay_eval_batch(
    manifest: EvalSuiteManifest,
    submissions: tuple[EvalSubmission, ...],
) -> EvalSummary:
    """Replay every defined case exactly once; reject missing, extra or duplicate IDs.

    A returned summary is only deterministic derived data. Production publishers
    must separately verify trusted execution, tenant/agent/version binding,
    identity, approvals and immutable result storage.
    """
    if not isinstance(manifest, EvalSuiteManifest):
        raise TypeError("manifest must be EvalSuiteManifest")
    if not isinstance(submissions, tuple) or any(
        not isinstance(item, EvalSubmission) for item in submissions
    ):
        raise TypeError("submissions must be a tuple of EvalSubmission")

    by_id: dict[str, EvalSubmission] = {}
    for item in submissions:
        if item.case_id in by_id:
            raise ValueError("duplicate submission case_id")
        by_id[item.case_id] = item

    cases = {case.case_id: case for case in manifest.cases}
    if by_id.keys() != cases.keys():
        raise ValueError("submission case IDs must exactly match the suite manifest")

    return EvalSummary(
        tuple(
            evaluate(case, by_id[case.case_id].answer, by_id[case.case_id].evidence)
            for case in sorted(manifest.cases, key=lambda item: item.case_id)
        )
    )
