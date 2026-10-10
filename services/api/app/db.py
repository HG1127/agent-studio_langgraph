"""SQLite-only development task ledger and single-worker lease queue.

Do not deploy publicly: no identity or tenant isolation. Run checkpoints are kept
in a *separate* LangGraph SQLite database, determined by checkpoint_path().
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schema import AgentSpec

ACTIVE = ("RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED")
TERMINAL = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELED")
LEASE_SECONDS = 150


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def db_path() -> Path:
    return Path(os.getenv("AGENT_STUDIO_DB_PATH", ".local/agent_studio.sqlite3")).resolve()


def checkpoint_path() -> Path:
    explicit = os.getenv("AGENT_STUDIO_CHECKPOINT_PATH")
    path = Path(explicit).resolve() if explicit else db_path().with_name(db_path().stem + ".checkpoints.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, spec TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, input TEXT NOT NULL,
            status TEXT NOT NULL, output TEXT, error TEXT, created_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT,
            FOREIGN KEY (agent_id) REFERENCES agents(id)
        );
        CREATE TABLE IF NOT EXISTS run_events (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, seq INTEGER NOT NULL,
            type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(run_id, seq),
            FOREIGN KEY (run_id) REFERENCES runs(id)
        );
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL
        );
        """)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        for name, definition in {
            "spec_snapshot": "TEXT",
            "lease_until": "TEXT",
            "heartbeat_at": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
        event_columns = {r["name"] for r in conn.execute("PRAGMA table_info(run_events)").fetchall()}
        if "event_key" not in event_columns:
            conn.execute("ALTER TABLE run_events ADD COLUMN event_key TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_event_idempotency ON run_events(run_id, event_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_claim ON runs(status, created_at)")


def add_agent(spec: AgentSpec) -> dict:
    agent_id, created_at = str(uuid.uuid4()), utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO agents (id, spec, created_at) VALUES (?, ?, ?)",
            (agent_id, spec.model_dump_json(), created_at),
        )
    return {"id": agent_id, "spec": spec.model_dump(), "created_at": created_at}


def _agent(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "spec": json.loads(row["spec"]), "created_at": row["created_at"]}


def get_agent(agent_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return _agent(row) if row else None


def list_agents() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    return [_agent(row) for row in rows]


def add_run(agent_id: str, input_text: str) -> dict:
    # Immutable AgentSpec snapshot per Run: editing/deleting Agent cannot change an in-flight execution.
    agent = get_agent(agent_id)
    if agent is None:
        raise KeyError("Agent 不存在")
    run_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO runs
               (id, agent_id, input, status, created_at, spec_snapshot, attempts)
               VALUES (?, ?, ?, 'QUEUED', ?, ?, 0)""",
            (run_id, agent_id, input_text, utcnow(), json.dumps(agent["spec"], ensure_ascii=False)),
        )
    append_event(run_id, "queued", {"message": "任务已排队，等待独立 Worker"}, "queued")
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]


def append_event(run_id: str, event_type: str, payload: dict, key: str | None = None) -> None:
    """Stable event keys suppress duplicates when a node is replayed after a crash."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if key and conn.execute(
            "SELECT 1 FROM run_events WHERE run_id = ? AND event_key = ?", (run_id, key)
        ).fetchone():
            return
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO run_events
               (id, run_id, seq, type, payload, created_at, event_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), run_id, seq, event_type,
             json.dumps(payload, ensure_ascii=False), utcnow(), key),
        )


def get_events(run_id: str, after_seq: int = 0) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT seq, type, payload, created_at FROM run_events
               WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT 500""",
            (run_id, after_seq),
        ).fetchall()
    return [
        {"seq": r["seq"], "type": r["type"],
         "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
        for r in rows
    ]


def register_worker(worker_id: str):
    with connect() as conn:
        conn.execute(
            """INSERT INTO worker_heartbeats(worker_id, seen_at) VALUES (?, ?)
               ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at""",
            (worker_id, utcnow()),
        )


def worker_online() -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM worker_heartbeats WHERE seen_at > ? LIMIT 1", (cutoff,)
        ).fetchone()
    return bool(row)


def claim_next(worker_id: str = "local-worker") -> str | None:
    """Atomic queue claim; one SQLite Worker process is supported in development."""
    reclaimed: list[str] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = utcnow()
        # A dead worker may leave a RUNNING run. A read-only tool is safe to replay
        # from the latest checkpoint; external writes remain forbidden.
        reclaimed = [
            r["id"] for r in conn.execute(
                "SELECT id FROM runs WHERE status = 'RUNNING' AND lease_until IS NOT NULL AND lease_until < ?",
                (now,),
            ).fetchall()
        ]
        if reclaimed:
            conn.executemany(
                "UPDATE runs SET status='QUEUED', lease_until=NULL WHERE id=?",
                [(rid,) for rid in reclaimed],
            )
        # Pending control requests from a crashed worker must become terminal/paused.
        conn.execute(
            "UPDATE runs SET status='PAUSED', lease_until=NULL WHERE status='PAUSE_REQUESTED' AND lease_until < ?",
            (now,),
        )
        conn.execute(
            """UPDATE runs SET status='CANCELED', lease_until=NULL, completed_at=?
               WHERE status='CANCEL_REQUESTED' AND lease_until < ?""",
            (now, now),
        )
        chosen = conn.execute(
            "SELECT id FROM runs WHERE status = 'QUEUED' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not chosen:
            chosen_id = None
        else:
            chosen_id = chosen["id"]
            conn.execute(
                """UPDATE runs SET status='RUNNING', started_at=?, heartbeat_at=?, lease_until=?,
                   attempts=attempts+1, error=NULL, output=NULL, completed_at=NULL
                   WHERE id=? AND status='QUEUED'""",
                (now, now, _after(LEASE_SECONDS), chosen_id),
            )
    for rid in reclaimed:
        append_event(rid, "worker_recovered", {"message": "工作进程超时，准备从检查点恢复"})
    if chosen_id:
        append_event(chosen_id, "worker_claimed", {"worker": worker_id})
    return chosen_id


def heartbeat(run_id: str):
    with connect() as conn:
        conn.execute(
            """UPDATE runs SET heartbeat_at=?, lease_until=?
               WHERE id=? AND status IN ('RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED')""",
            (utcnow(), _after(LEASE_SECONDS), run_id),
        )


def request_pause(run_id: str) -> dict | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not current:
            return None
        if current["status"] == "QUEUED":
            conn.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run_id,))
        elif current["status"] == "RUNNING":
            conn.execute("UPDATE runs SET status='PAUSE_REQUESTED' WHERE id=?", (run_id,))
        elif current["status"] not in ("PAUSED", "PAUSE_REQUESTED"):
            raise ValueError("当前任务状态无法暂停")
    append_event(run_id, "pause_requested", {"message": "暂停请求已记录"})
    return get_run(run_id)


def request_cancel(run_id: str) -> dict | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not current:
            return None
        if current["status"] in ("QUEUED", "PAUSED"):
            conn.execute(
                "UPDATE runs SET status='CANCELED', completed_at=?, lease_until=NULL WHERE id=?",
                (utcnow(), run_id),
            )
        elif current["status"] in ("RUNNING", "PAUSE_REQUESTED"):
            conn.execute("UPDATE runs SET status='CANCEL_REQUESTED' WHERE id=?", (run_id,))
        elif current["status"] not in ("CANCELED", "CANCEL_REQUESTED"):
            raise ValueError("任务已结束，无法取消")
    append_event(run_id, "cancel_requested", {"message": "取消请求已记录"})
    return get_run(run_id)


def request_resume(run_id: str, retry_failed: bool = False) -> dict | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status, attempts FROM runs WHERE id=?", (run_id,)).fetchone()
        if not current:
            return None
        required = "FAILED" if retry_failed else "PAUSED"
        if current["status"] != required:
            raise ValueError(f"只允许对 {required} 状态的任务进行此操作")
        if retry_failed and current["attempts"] >= 5:
            raise ValueError("已达到重试次数上限")
        conn.execute(
            """UPDATE runs SET status='QUEUED', started_at=NULL, lease_until=NULL,
               completed_at=NULL, error=NULL WHERE id=?""",
            (run_id,),
        )
    append_event(run_id, "retry_queued" if retry_failed else "resumed", {"message": "重新进入执行队列"})
    return get_run(run_id)


def settle_control(run_id: str) -> str | None:
    """A worker acknowledges a pause/cancel request between graph steps."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row or row["status"] not in ("PAUSE_REQUESTED", "CANCEL_REQUESTED"):
            return None
        target = "PAUSED" if row["status"] == "PAUSE_REQUESTED" else "CANCELED"
        conn.execute(
            "UPDATE runs SET status=?, lease_until=NULL, completed_at=? WHERE id=?",
            (target, utcnow() if target == "CANCELED" else None, run_id),
        )
    append_event(run_id, target.lower(), {"reason": "user_request"})
    return target


def finalize_run(run_id: str, status: str, output: str | None = None, error: str | None = None) -> bool:
    if status not in TERMINAL:
        raise ValueError("Invalid terminal status")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            """UPDATE runs SET status=?, output=?, error=?, completed_at=?, lease_until=NULL
               WHERE id=? AND status='RUNNING'""",
            (status, output, error, utcnow(), run_id),
        ).rowcount
    return changed == 1
