"""Offline integrity contract for immutable evaluation-suite manifests.

No I/O, model inference, persistence, or publishing authority is provided here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.domain.evaluation import EvalCase, EvalSummary
from app.domain.evaluation_gate import EvalGateReport, assess_eval_regression


@dataclass(frozen=True)
class EvalSuiteManifest:
    """Canonical suite definition for comparison across candidate versions."""

    suite_id: str
    version: int
    cases: tuple[EvalCase, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.suite_id, str) or not self.suite_id.strip():
            raise ValueError("suite_id must be a nonblank string")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("cases must be a nonempty tuple")
        if any(not isinstance(case, EvalCase) for case in self.cases):
            raise TypeError("every case must be EvalCase")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case_id in suite")

    @property
    def fingerprint(self) -> str:
        """SHA-256 of deterministic UTF-8 JSON, independent of case ordering."""
        payload = {
            "suite_id": self.suite_id,
            "version": self.version,
            "cases": [
                {
                    "case_id": case.case_id,
                    "expected_answer": case.expected_answer,
                    "required_evidence": sorted(case.required_evidence),
                }
                for case in sorted(self.cases, key=lambda case: case.case_id)
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VersionedEvalGateReport:
    """Read-only evidence for an upper-layer authorized release decision."""

    eligible: bool
    blockers: tuple[str, ...]
    baseline_fingerprint: str
    candidate_fingerprint: str
    regression: EvalGateReport


def assess_versioned_eval_gate(
    baseline_manifest: EvalSuiteManifest,
    baseline: EvalSummary,
    candidate_manifest: EvalSuiteManifest,
    candidate: EvalSummary,
) -> VersionedEvalGateReport:
    """Fail closed on changed or incomplete suites, then apply regression gate.

    A matching manifest does NOT prove that summaries were genuinely produced
    by a trusted evaluator. An actual publisher must independently verify
    immutable results, workspace scope, approvals and version identity.
    """
    if not isinstance(baseline_manifest, EvalSuiteManifest) or not isinstance(
        candidate_manifest, EvalSuiteManifest
    ):
        raise TypeError("both manifests must be EvalSuiteManifest")
    if not isinstance(baseline, EvalSummary) or not isinstance(candidate, EvalSummary):
        raise TypeError("baseline and candidate must be EvalSummary")

    old_digest = baseline_manifest.fingerprint
    new_digest = candidate_manifest.fingerprint
    old_ids = {case.case_id for case in baseline_manifest.cases}
    new_ids = {case.case_id for case in candidate_manifest.cases}
    old_results = {result.case_id for result in baseline.results}
    new_results = {result.case_id for result in candidate.results}

    blockers: list[str] = []
    if old_digest != new_digest:
        blockers.append("suite_fingerprint_mismatch")
    if old_ids != old_results:
        blockers.append("baseline_manifest_mismatch")
    if new_ids != new_results:
        blockers.append("candidate_manifest_mismatch")

    regression = assess_eval_regression(baseline, candidate)
    blockers.extend(regression.blockers)
    return VersionedEvalGateReport(
        eligible=not blockers,
        blockers=tuple(blockers),
        baseline_fingerprint=old_digest,
        candidate_fingerprint=new_digest,
        regression=regression,
    )
