"""Execution event append/read API with idempotency keys."""

from __future__ import annotations

import json
import uuid

from .connection import connect, utcnow


def append_event(run_id: str, event_type: str, payload: dict, key: str | None = None) -> None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            key
            and connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_key=?",
                (run_id, key),
            ).fetchone()
        ):
            return
        sequence = connection.execute(
            "SELECT COALESCE(MAX(seq), 0)+1 FROM run_events WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO run_events
               (id, run_id, seq, type, payload, created_at, event_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                run_id,
                sequence,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                utcnow(),
                key,
            ),
        )


def get_events(run_id: str, after_seq: int = 0) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT seq,type,payload,created_at FROM run_events
               WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500""",
            (run_id, after_seq),
        ).fetchall()
    return [
        {
            "seq": row["seq"],
            "type": row["type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
