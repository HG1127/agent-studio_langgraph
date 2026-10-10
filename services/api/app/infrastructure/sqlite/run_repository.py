"""Run creation and immutable AgentSpec snapshot binding."""

from __future__ import annotations

import uuid

from .connection import connect, utcnow
from .event_repository import append_event

TERMINAL = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELED")


def add_run(agent_id: str, input_text: str) -> dict:
    run_id, created_at = str(uuid.uuid4()), utcnow()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute(
            """SELECT v.id, v.spec FROM agents a JOIN agent_versions v
               ON v.agent_id=a.id AND v.version=a.current_version
               WHERE a.id=?""",
            (agent_id,),
        ).fetchone()
        if version is None:
            raise KeyError("Agent 不存在")
        connection.execute(
            """INSERT INTO runs(
                id,agent_id,input,status,created_at,spec_snapshot,
                attempts,agent_version_id
            ) VALUES (?, ?, ?, 'QUEUED', ?, ?, 0, ?)""",
            (run_id, agent_id, input_text, created_at, version["spec"], version["id"]),
        )
    append_event(run_id, "queued", {"message": "任务已排队，等待独立 Worker"}, key="queued")
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [dict(row) for row in rows]


def heartbeat(run_id: str, lease_seconds: int = 150) -> None:
    from .connection import after

    with connect() as connection:
        connection.execute(
            """UPDATE runs SET heartbeat_at=?,lease_until=?
               WHERE id=? AND status IN
               ('RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')""",
            (utcnow(), after(lease_seconds), run_id),
        )


def request_pause(run_id: str) -> dict | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if current is None:
            return None
        if current["status"] == "QUEUED":
            connection.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run_id,))
        elif current["status"] == "RUNNING":
            connection.execute("UPDATE runs SET status='PAUSE_REQUESTED' WHERE id=?", (run_id,))
        elif current["status"] not in ("PAUSED", "PAUSE_REQUESTED"):
            raise ValueError("当前任务状态无法暂停")
    append_event(run_id, "pause_requested", {"message": "暂停请求已记录"})
    return get_run(run_id)


def request_cancel(run_id: str) -> dict | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if current is None:
            return None
        if current["status"] in ("QUEUED", "PAUSED"):
            connection.execute(
                """UPDATE runs SET status='CANCELED',completed_at=?,
                   lease_until=NULL WHERE id=?""",
                (utcnow(), run_id),
            )
        elif current["status"] in ("RUNNING", "PAUSE_REQUESTED"):
            connection.execute("UPDATE runs SET status='CANCEL_REQUESTED' WHERE id=?", (run_id,))
        elif current["status"] not in ("CANCELED", "CANCEL_REQUESTED"):
            raise ValueError("任务已结束，无法取消")
    append_event(run_id, "cancel_requested", {"message": "取消请求已记录"})
    return get_run(run_id)


def request_resume(run_id: str, retry_failed: bool = False) -> dict | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT status,attempts FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if current is None:
            return None
        required = "FAILED" if retry_failed else "PAUSED"
        if current["status"] != required:
            raise ValueError(f"只允许对 {required} 状态的任务进行此操作")
        if retry_failed and current["attempts"] >= 5:
            raise ValueError("已达到重试次数上限")
        connection.execute(
            """UPDATE runs SET status='QUEUED',started_at=NULL,
               lease_until=NULL,completed_at=NULL,error=NULL WHERE id=?""",
            (run_id,),
        )
    append_event(
        run_id,
        "retry_queued" if retry_failed else "resumed",
        {"message": "重新进入执行队列"},
    )
    return get_run(run_id)


def settle_control(run_id: str) -> str | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in ("PAUSE_REQUESTED", "CANCEL_REQUESTED"):
            return None
        target = "PAUSED" if row["status"] == "PAUSE_REQUESTED" else "CANCELED"
        connection.execute(
            "UPDATE runs SET status=?,lease_until=NULL,completed_at=? WHERE id=?",
            (target, utcnow() if target == "CANCELED" else None, run_id),
        )
    append_event(run_id, target.lower(), {"reason": "user_request"})
    return target


def finalize_run(
    run_id: str,
    status: str,
    output: str | None = None,
    error: str | None = None,
) -> bool:
    if status not in TERMINAL:
        raise ValueError("Invalid terminal status")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            """UPDATE runs SET status=?,output=?,error=?,completed_at=?,
               lease_until=NULL WHERE id=? AND status='RUNNING'""",
            (status, output, error, utcnow(), run_id),
        ).rowcount
    return changed == 1
