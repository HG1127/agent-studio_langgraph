"""Offline deterministic evaluation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureReason = Literal["empty_response", "missing_evidence", "answer_mismatch", "not_comparable"]
EvalStatus = Literal["passed", "failed", "incomparable"]


def _text(value: object, name: str, *, nonblank: bool = True) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if nonblank and not value.strip():
        raise ValueError(f"{name} must not be blank")


def _evidence(value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError("evidence must be a tuple of unique nonblank strings")
    for item in value:
        _text(item, "evidence item")
    if len(set(value)) != len(value):
        raise ValueError("evidence must be unique")


@dataclass(frozen=True)
class EvalCase:
    """Exact-text oracle; None means no comparable oracle, never an automatic pass."""

    case_id: str
    expected_answer: str | None
    required_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        if self.expected_answer is not None:
            _text(self.expected_answer, "expected_answer")
        _evidence(self.required_evidence)


@dataclass(frozen=True)
class EvalResult:
    """Validated binary score and ordered, possibly multiple failure causes."""

    case_id: str
    status: EvalStatus
    score: int | None
    reasons: tuple[FailureReason, ...] = ()

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        if not isinstance(self.reasons, tuple):
            raise TypeError("reasons must be a tuple")
        order = ("empty_response", "missing_evidence", "answer_mismatch", "not_comparable")
        if any(reason not in order for reason in self.reasons):
            raise ValueError("unknown failure reason")
        if tuple(reason for reason in order if reason in self.reasons) != self.reasons:
            raise ValueError("reasons must be unique and in canonical order")
        if self.score is not None and type(self.score) is not int:
            raise TypeError("score must be an integer or None")
        valid = (
            (self.status == "passed" and self.score == 1 and not self.reasons)
            or (
                self.status == "failed"
                and self.score == 0
                and bool(self.reasons)
                and "not_comparable" not in self.reasons
                and not {"empty_response", "answer_mismatch"}.issubset(self.reasons)
            )
            or (
                self.status == "incomparable"
                and self.score is None
                and "not_comparable" in self.reasons
                and "answer_mismatch" not in self.reasons
            )
        )
        if not valid:
            raise ValueError("inconsistent evaluation status, score or reasons")


def evaluate(case: EvalCase, answer: str, evidence: tuple[str, ...] = ()) -> EvalResult:
    """Compare stripped, case-sensitive text and exact evidence identifiers, with no I/O."""
    if not isinstance(case, EvalCase):
        raise TypeError("case must be EvalCase")
    _text(answer, "answer", nonblank=False)
    _evidence(evidence)
    reasons: list[FailureReason] = []
    if not answer.strip():
        reasons.append("empty_response")
    if not set(case.required_evidence).issubset(evidence):
        reasons.append("missing_evidence")
    if case.expected_answer is None:
        reasons.append("not_comparable")
        return EvalResult(case.case_id, "incomparable", None, tuple(reasons))
    if answer.strip() and answer.strip() != case.expected_answer.strip():
        reasons.append("answer_mismatch")
    return EvalResult(
        case.case_id, "failed" if reasons else "passed", 0 if reasons else 1, tuple(reasons)
    )


@dataclass(frozen=True)
class EvalSummary:
    """Derived counts; incomparable results are excluded from mean_score, never from the gate."""

    results: tuple[EvalResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple) or any(
            not isinstance(result, EvalResult) for result in self.results
        ):
            raise TypeError("results must be a tuple of EvalResult")
        if len({result.case_id for result in self.results}) != len(self.results):
            raise ValueError("duplicate case_id in summary")

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(result.status == "passed" for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.status == "failed" for result in self.results)

    @property
    def incomparable(self) -> int:
        return self.total - self.passed - self.failed

    @property
    def mean_score(self) -> float | None:
        comparable = self.passed + self.failed
        return self.passed / comparable if comparable else None

    @property
    def hard_pass(self) -> bool:
        return self.total > 0 and self.passed == self.total

    @property
    def failure_counts(self) -> dict[FailureReason, int]:
        counts: dict[FailureReason, int] = {}
        for result in self.results:
            for reason in result.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts
