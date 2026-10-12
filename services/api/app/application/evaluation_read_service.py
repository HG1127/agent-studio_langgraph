"""Application boundary for authorized, read-only S2 evaluation reconciliation.

This is a *port contract*, not a database-backed or production-ready service.
An authenticated caller must supply the actor ID; a trusted implementation
must check membership against the authoritative identity store and retrieve
the immutable snapshot in a consistent, workspace-scoped read transaction.
No publish capability is granted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.evaluation_execution_binding import (
    EvalExecutionRecord,
    EvalRunScope,
    ReconciledEvalEvidence,
    reconcile_eval_execution,
)
from app.domain.evaluation_suite_integrity import EvalSuiteManifest

_READ_ROLES = frozenset({"owner", "admin", "member", "viewer"})


@dataclass(frozen=True, slots=True)
class AuthoritativeEvalSnapshot:
    """Single logical read of a run, frozen suite and server-collected outputs.

    Freezing a Python object does not authenticate its source or assure DB
    transaction atomicity; those are obligations of EvalReadStore.
    """

    scope: EvalRunScope
    manifest: EvalSuiteManifest
    execution: EvalExecutionRecord

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EvalRunScope):
            raise TypeError("scope must be EvalRunScope")
        if not isinstance(self.manifest, EvalSuiteManifest):
            raise TypeError("manifest must be EvalSuiteManifest")
        if not isinstance(self.execution, EvalExecutionRecord):
            raise TypeError("execution must be EvalExecutionRecord")


class EvalReadStore(Protocol):
    """Server-controlled read port; do not implement from client-supplied data."""

    def member_role(self, authenticated_user_id: str, workspace_id: str) -> str | None:
        """Look up current membership, never trust request-provided role."""
        ...

    def load_snapshot(
        self, authenticated_user_id: str, workspace_id: str, run_id: str
    ) -> AuthoritativeEvalSnapshot | None:
        """Recheck membership and read a scoped snapshot in one transaction."""
        ...


def read_reconciled_evaluation(
    authenticated_user_id: str,
    requested_scope: EvalRunScope,
    reported: EvalExecutionRecord,
    store: EvalReadStore,
) -> ReconciledEvalEvidence:
    """Authorize read first; reject mismatched scoped runs without leaking data.

    Only a trusted authentication dependency may supply authenticated_user_id.
    Only a server-owned repository may implement store. Do not expose this
    method directly with a user-selected store/identity. This method is NOT
    a release approval or a guarantee of trusted execution.
    """
    if not isinstance(authenticated_user_id, str) or not authenticated_user_id.strip():
        raise PermissionError("authenticated identity required")
    if not isinstance(requested_scope, EvalRunScope):
        raise TypeError("requested_scope must be EvalRunScope")
    if not isinstance(reported, EvalExecutionRecord):
        raise TypeError("reported must be EvalExecutionRecord")
    if store is None:
        raise TypeError("trusted eval read store required")

    # Membership must be checked before any run lookup, including failure paths.
    if store.member_role(authenticated_user_id, requested_scope.workspace_id) not in _READ_ROLES:
        raise PermissionError("workspace access denied")

    snapshot = store.load_snapshot(
        authenticated_user_id, requested_scope.workspace_id, requested_scope.run_id
    )
    if snapshot is None or not isinstance(snapshot, AuthoritativeEvalSnapshot):
        raise LookupError("evaluation run not found")
    if snapshot.scope != requested_scope or snapshot.execution.scope != snapshot.scope:
        raise LookupError("evaluation run not found")

    return reconcile_eval_execution(snapshot.manifest, reported, snapshot.execution, snapshot.scope)
