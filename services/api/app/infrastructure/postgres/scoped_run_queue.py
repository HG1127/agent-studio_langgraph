"""Staged PostgreSQL tenant-scoped durable Run queue; NOT connected to live Worker.

Every call must occur inside one caller-managed PostgreSQL transaction using
an already-verified PgScopedStore (RLS role + actor + workspace). The database
row lock + attempt number implement a fencing token. This module never uses the
migration administrator account or bypasses workspace RLS.

Before activating this adapter, a separate authenticated *service identity*,
LangGraph Postgres checkpointing and real PostgreSQL concurrency/failure tests
are required. The current SQLite Worker remains the only live executor.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update

from .models import Run, RunEvent
from .scoped_store import PgScopedStore

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED"})


class LeaseConflict(RuntimeError):
    """The worker's lease expired, was replaced, or the Run changed state."""


class PgScopedRunQueue:
    def __init__(self, store: PgScopedStore):
        if not isinstance(store, PgScopedStore):
            raise TypeError("A validated PgScopedStore is required")
        self.connection = store.connection
        self.workspace_id = store.workspace_id

    @staticmethod
    def _lease_seconds(seconds: int) -> None:
        if not isinstance(seconds, int) or not 30 <= seconds <= 3600:
            raise ValueError("Lease seconds must be in [30, 3600]")

    def _append_event(
        self, run_id: uuid.UUID, event_type: str, payload: dict[str, Any], key: str
    ) -> None:
        """Caller must hold the same Run row lock until transaction commit."""
        existing = self.connection.execute(
            select(RunEvent.id).where(
                RunEvent.run_id == run_id,
                RunEvent.workspace_id == self.workspace_id,
                RunEvent.event_key == key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        next_seq = self.connection.execute(
            select(func.coalesce(func.max(RunEvent.seq), 0) + 1).where(
                RunEvent.run_id == run_id, RunEvent.workspace_id == self.workspace_id
            )
        ).scalar_one()
        self.connection.execute(
            insert(RunEvent).values(
                id=uuid.uuid4(),
                run_id=run_id,
                workspace_id=self.workspace_id,
                seq=next_seq,
                type=event_type,
                payload=payload,
                event_key=key,
                created_at=datetime.now(UTC),
            )
        )

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 150,
        max_attempts: int = 5,
    ) -> dict[str, Any] | None:
        """Claim one queued Run in this workspace, never a global cross-tenant job.

        Competing claimers skip each other's row locks. The returned attempt
        number is a fencing token for heartbeat/finish; a stale worker cannot
        finish a later claim after its lease was replaced.
        """
        self._lease_seconds(lease_seconds)
        if not 1 <= max_attempts <= 10:
            raise ValueError("Maximum attempts must be between 1 and 10")
        if not worker_id or len(worker_id) > 128:
            raise ValueError("Worker identifier is required and <=128 chars")

        row = self.connection.execute(
            select(Run.id, Run.attempts)
            .where(
                Run.workspace_id == self.workspace_id,
                Run.status == "QUEUED",
                Run.attempts < max_attempts,
            )
            .order_by(Run.created_at, Run.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        run_id, old_attempt = row
        attempt = old_attempt + 1
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        changed = self.connection.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.workspace_id == self.workspace_id,
                Run.status == "QUEUED",
                Run.attempts == old_attempt,
            )
            .values(
                status="RUNNING",
                attempts=attempt,
                started_at=now,
                heartbeat_at=now,
                lease_until=lease_until,
                output=None,
                error=None,
                completed_at=None,
            )
        )
        if changed.rowcount != 1:
            raise LeaseConflict("Run was claimed by another worker")
        self._append_event(
            run_id,
            "worker_claimed",
            {"worker_id": worker_id, "attempt": attempt},
            f"worker_claimed:{attempt}",
        )
        return {
            "run_id": str(run_id),
            "workspace_id": str(self.workspace_id),
            "attempt": attempt,
            "lease_until": lease_until.isoformat(),
        }

    def heartbeat(self, run_id: str | uuid.UUID, *, attempt: int, lease_seconds: int = 150) -> bool:
        """Extend only the currently owned, non-expired lease."""
        self._lease_seconds(lease_seconds)
        if attempt < 1:
            raise ValueError("Attempt must be positive")
        now = datetime.now(UTC)
        changed = self.connection.execute(
            update(Run)
            .where(
                Run.id == uuid.UUID(str(run_id)),
                Run.workspace_id == self.workspace_id,
                Run.status == "RUNNING",
                Run.attempts == attempt,
                Run.lease_until > now,
            )
            .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
        )
        return changed.rowcount == 1

    def finalize(
        self,
        run_id: str | uuid.UUID,
        *,
        attempt: int,
        status: str,
        output: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Stale workers cannot finish runs after ownership changed/expired."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("Invalid terminal status")
        if attempt < 1:
            raise ValueError("Attempt must be positive")
        now = datetime.now(UTC)
        uid = uuid.UUID(str(run_id))
        # Locking the Run row serializes next event seq allocation with
        # recovery, claim and other queue lifecycle writes.
        locked = self.connection.execute(
            select(Run.id)
            .where(
                Run.id == uid,
                Run.workspace_id == self.workspace_id,
                Run.status == "RUNNING",
                Run.attempts == attempt,
                Run.lease_until > now,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            return False
        updated = self.connection.execute(
            update(Run)
            .where(
                Run.id == uid,
                Run.workspace_id == self.workspace_id,
                Run.status == "RUNNING",
                Run.attempts == attempt,
                Run.lease_until > now,
            )
            .values(
                status=status,
                output=output,
                error=error,
                completed_at=now,
                lease_until=None,
                heartbeat_at=now,
            )
        )
        if updated.rowcount != 1:
            raise LeaseConflict("Run was changed during finalization")
        self._append_event(
            uid,
            "worker_finished",
            {"status": status, "attempt": attempt},
            f"worker_finished:{attempt}",
        )
        return True

    def recover_expired(
        self, *, max_count: int = 20, max_attempts: int = 5
    ) -> list[dict[str, Any]]:
        """Requeue expired work or permanently fail exhausted attempts.

        SELECT FOR UPDATE SKIP LOCKED prevents competing recovery workers from
        requeueing the same row concurrently within the same transaction.
        """
        if not 1 <= max_count <= 100 or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid reclaim limits")
        now = datetime.now(UTC)
        rows = self.connection.execute(
            select(Run.id, Run.attempts)
            .where(
                Run.workspace_id == self.workspace_id,
                Run.status == "RUNNING",
                Run.lease_until <= now,
            )
            .order_by(Run.lease_until, Run.id)
            .with_for_update(skip_locked=True)
            .limit(max_count)
        ).all()
        recovered = []
        for run_id, attempt in rows:
            terminal = attempt >= max_attempts
            status = "FAILED" if terminal else "QUEUED"
            self.connection.execute(
                update(Run)
                .where(
                    Run.id == run_id,
                    Run.workspace_id == self.workspace_id,
                    Run.status == "RUNNING",
                    Run.attempts == attempt,
                    Run.lease_until <= now,
                )
                .values(
                    status=status,
                    lease_until=None,
                    heartbeat_at=None,
                    error="max_worker_attempts" if terminal else None,
                    completed_at=now if terminal else None,
                )
            )
            self._append_event(
                run_id,
                "worker_failed" if terminal else "worker_requeued",
                {"attempt": attempt, "reason": "lease_expired"},
                f"lease_expired:{attempt}",
            )
            recovered.append({"run_id": str(run_id), "status": status, "attempt": attempt})
        return recovered
