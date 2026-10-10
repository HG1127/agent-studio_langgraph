"""Single development Worker queue claims and heartbeat registry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .connection import after, connect, utcnow
from .event_repository import append_event

LEASE_SECONDS = 150


def register_worker(worker_id: str) -> None:
    with connect() as connection:
        connection.execute(
            """INSERT INTO worker_heartbeats(worker_id,seen_at) VALUES (?,?)
               ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at""",
            (worker_id, utcnow()),
        )


def worker_online() -> bool:
    cutoff = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    with connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM worker_heartbeats WHERE seen_at>? LIMIT 1", (cutoff,)
        ).fetchone()
    return bool(row)


def claim_next(worker_id: str = "local-worker") -> str | None:
    """Atomic claim for one local Worker. External writes remain forbidden."""
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = utcnow()
        reclaimed = [
            row["id"]
            for row in connection.execute(
                """SELECT id FROM runs
                   WHERE status='RUNNING' AND lease_until IS NOT NULL
                     AND lease_until<?""",
                (now,),
            ).fetchall()
        ]
        if reclaimed:
            connection.executemany(
                "UPDATE runs SET status='QUEUED',lease_until=NULL WHERE id=?",
                [(run_id,) for run_id in reclaimed],
            )
        connection.execute(
            """UPDATE runs SET status='PAUSED',lease_until=NULL
               WHERE status='PAUSE_REQUESTED' AND lease_until<?""",
            (now,),
        )
        connection.execute(
            """UPDATE runs SET status='CANCELED',lease_until=NULL,completed_at=?
               WHERE status='CANCEL_REQUESTED' AND lease_until<?""",
            (now, now),
        )
        chosen = connection.execute(
            """SELECT id FROM runs WHERE status='QUEUED'
               ORDER BY created_at LIMIT 1"""
        ).fetchone()
        chosen_id = chosen["id"] if chosen else None
        if chosen_id:
            connection.execute(
                """UPDATE runs SET status='RUNNING',started_at=?,
                   heartbeat_at=?,lease_until=?,attempts=attempts+1,
                   error=NULL,output=NULL,completed_at=NULL
                   WHERE id=? AND status='QUEUED'""",
                (now, now, after(LEASE_SECONDS), chosen_id),
            )
    for run_id in reclaimed:
        append_event(run_id, "worker_recovered", {"message": "工作进程超时，准备从检查点恢复"})
    if chosen_id:
        append_event(chosen_id, "worker_claimed", {"worker": worker_id})
    return chosen_id
