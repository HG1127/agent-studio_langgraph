"""Deterministic, offline version regression gate for evaluation summaries.

This domain-only report does not authorize publishing and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.evaluation import EvalSummary

GateBlocker = Literal[
    "empty_suite",
    "suite_mismatch",
    "baseline_incomparable",
    "candidate_incomparable",
    "candidate_failed",
    "case_regression",
]


@dataclass(frozen=True)
class EvalGateReport:
    """Release eligibility *evidence*, not a permission or release action."""

    eligible: bool
    blockers: tuple[GateBlocker, ...]
    regressed_case_ids: tuple[str, ...]
    failed_case_ids: tuple[str, ...]
    incomparable_case_ids: tuple[str, ...]


def assess_eval_regression(baseline: EvalSummary, candidate: EvalSummary) -> EvalGateReport:
    """Fail closed on empty/drifting suites, missing oracles and any failed case.

    Compare by case_id, never position. Case IDs and blocker order are stable.
    Baseline failures can improve; a baseline pass becoming non-pass is a regression.
    """
    if not isinstance(baseline, EvalSummary) or not isinstance(candidate, EvalSummary):
        raise TypeError("baseline and candidate must be EvalSummary")

    old = {item.case_id: item for item in baseline.results}
    new = {item.case_id: item for item in candidate.results}
    old_ids, new_ids = set(old), set(new)
    failed = tuple(sorted(key for key, item in new.items() if item.status == "failed"))
    unknown = tuple(sorted(key for key, item in new.items() if item.status == "incomparable"))
    regressed = tuple(
        sorted(
            key
            for key in old_ids & new_ids
            if old[key].status == "passed" and new[key].status != "passed"
        )
    )

    blockers: list[GateBlocker] = []
    if not old or not new:
        blockers.append("empty_suite")
    if old_ids != new_ids:
        blockers.append("suite_mismatch")
    if baseline.incomparable:
        blockers.append("baseline_incomparable")
    if candidate.incomparable:
        blockers.append("candidate_incomparable")
    if candidate.failed:
        blockers.append("candidate_failed")
    if regressed:
        blockers.append("case_regression")

    return EvalGateReport(
        eligible=not blockers,
        blockers=tuple(blockers),
        regressed_case_ids=regressed,
        failed_case_ids=failed,
        incomparable_case_ids=unknown,
    )
