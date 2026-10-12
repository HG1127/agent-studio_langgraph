"""Offline cross-check of reported evaluations against server-owned run records.

The caller must obtain authoritative data and expected_scope from an
authenticated backend/execution store, never from a client request. Python
objects alone cannot prove provenance or authorize publication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.evaluation import EvalSummary
from app.domain.evaluation_batch import EvalSubmission, replay_eval_batch
from app.domain.evaluation_suite_integrity import EvalSuiteManifest


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")


@dataclass(frozen=True, slots=True)
class EvalRunScope:
    """Exact, authenticated workspace/agent/version/run identity."""

    workspace_id: str
    agent_id: str
    agent_version: int
    run_id: str

    def __post_init__(self) -> None:
        for field in ("workspace_id", "agent_id", "run_id"):
            _identifier(getattr(self, field), field)
        if type(self.agent_version) is not int or self.agent_version <= 0:
            raise ValueError("agent_version must be a positive integer")


@dataclass(frozen=True, slots=True)
class EvalExecutionRecord:
    """Immutable input snapshot; construction does NOT establish its origin."""

    scope: EvalRunScope
    manifest_fingerprint: str
    submissions: tuple[EvalSubmission, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EvalRunScope):
            raise TypeError("scope must be EvalRunScope")
        if (
            not isinstance(self.manifest_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.manifest_fingerprint) is None
        ):
            raise ValueError("manifest_fingerprint must be lowercase SHA-256 hex")
        if not isinstance(self.submissions, tuple) or any(
            not isinstance(item, EvalSubmission) for item in self.submissions
        ):
            raise TypeError("submissions must be a tuple of EvalSubmission")
        if len({item.case_id for item in self.submissions}) != len(self.submissions):
            raise ValueError("duplicate case_id in execution record")


@dataclass(frozen=True, slots=True)
class ReconciledEvalEvidence:
    """Derived data only: never a release token, signature or trust proof."""

    scope: EvalRunScope
    manifest_fingerprint: str
    summary: EvalSummary


def reconcile_eval_execution(
    manifest: EvalSuiteManifest,
    reported: EvalExecutionRecord,
    authoritative: EvalExecutionRecord,
    expected_scope: EvalRunScope,
) -> ReconciledEvalEvidence:
    """Fail closed unless reported answers match the authoritative run snapshot.

    An authenticated application service MUST supply the manifest, the expected
    scope and the authoritative record from its own protected persistent store.
    Supplying both sides from untrusted input defeats this check. This function
    has no I/O, verifies no cryptographic signature, and grants no publish rights.
    """
    if not isinstance(manifest, EvalSuiteManifest):
        raise TypeError("manifest must be EvalSuiteManifest")
    if not isinstance(reported, EvalExecutionRecord):
        raise TypeError("reported must be EvalExecutionRecord")
    if not isinstance(authoritative, EvalExecutionRecord):
        raise TypeError("authoritative must be EvalExecutionRecord")
    if not isinstance(expected_scope, EvalRunScope):
        raise TypeError("expected_scope must be EvalRunScope")

    digest = manifest.fingerprint
    case_ids = {case.case_id for case in manifest.cases}
    for source, record in (("reported", reported), ("authoritative", authoritative)):
        if record.scope != expected_scope:
            raise ValueError(f"{source} scope does not match authenticated run scope")
        if record.manifest_fingerprint != digest:
            raise ValueError(f"{source} suite fingerprint does not match manifest")
        if {item.case_id for item in record.submissions} != case_ids:
            raise ValueError(f"{source} case IDs do not match complete manifest")

    reported_by_id = {item.case_id: item for item in reported.submissions}
    authoritative_by_id = {item.case_id: item for item in authoritative.submissions}
    for case_id in sorted(case_ids):
        supplied = reported_by_id[case_id]
        recorded = authoritative_by_id[case_id]
        if supplied.answer != recorded.answer or sorted(supplied.evidence) != sorted(
            recorded.evidence
        ):
            raise ValueError(f"reported answer or evidence differs for case_id={case_id}")

    # Always compute the summary from the server-sourced record, not client output.
    summary = replay_eval_batch(manifest, authoritative.submissions)
    return ReconciledEvalEvidence(expected_scope, digest, summary)
