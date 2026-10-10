"""SQLite development storage; replace with PostgreSQL and scoped authorization before launch."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import AgentSpec


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    path = Path(os.getenv("AGENT_STUDIO_DB_PATH", ".local/agent_studio.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.executescript("""
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
        """)


def add_agent(spec: AgentSpec) -> dict:
    agent_id = str(uuid.uuid4())
    created_at = utcnow()
    with connect() as db:
        db.execute(
            "INSERT INTO agents (id, spec, created_at) VALUES (?, ?, ?)",
            (agent_id, spec.model_dump_json(), created_at),
        )
    return {"id": agent_id, "spec": spec.model_dump(), "created_at": created_at}


def get_agent(agent_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return {"id": row["id"], "spec": json.loads(row["spec"]), "created_at": row["created_at"]} if row else None


def list_agents() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    return [{"id": r["id"], "spec": json.loads(r["spec"]), "created_at": r["created_at"]} for r in rows]


def add_run(agent_id: str, input_text: str) -> dict:
    run_id = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO runs (id, agent_id, input, status, created_at) VALUES (?, ?, ?, 'QUEUED', ?)",
            (run_id, agent_id, input_text, utcnow()),
        )
    append_event(run_id, "queued", {"message": "任务已创建"})
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs() -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 100").fetchall()]


def append_event(run_id: str, event_type: str, payload: dict):
    with connect() as db:
        seq = db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), run_id, seq, event_type, json.dumps(payload, ensure_ascii=False), utcnow()),
        )


def get_events(run_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT seq, type, payload, created_at FROM run_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [
        {"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
        for r in rows
    ]


def update_run(run_id: str, status: str, output: str | None = None, error: str | None = None):
    with connect() as db:
        if status == "RUNNING":
            db.execute("UPDATE runs SET status = ?, started_at = ? WHERE id = ?",
                       (status, utcnow(), run_id))
        else:
            db.execute(
                "UPDATE runs SET status = ?, output = ?, error = ?, completed_at = ? WHERE id = ?",
                (status, output, error, utcnow(), run_id),
            )
