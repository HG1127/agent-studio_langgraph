"""Atomic, immutable AgentSpec version storage (SQLite development adapter)."""

from __future__ import annotations

import json
import sqlite3
import uuid

from ...domain.errors import AgentNotFound, VersionConflict
from ...domain.specs import AgentSpec
from .connection import connect, utcnow


def _agent(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "spec": json.loads(row["spec"]),
        "created_at": row["created_at"],
        "current_version": row["current_version"],
        "workspace_id": row["workspace_id"],
    }


def _version(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "version": row["version"],
        "spec": json.loads(row["spec"]),
        "created_at": row["created_at"],
    }


def add_agent(spec: AgentSpec, workspace_id: str) -> dict:
    agent_id, version_id, created_at = str(uuid.uuid4()), str(uuid.uuid4()), utcnow()
    serialized = spec.model_dump_json()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO agents(id, spec, created_at, current_version, workspace_id)
               VALUES (?, ?, ?, 1, ?)""",
            (agent_id, serialized, created_at, workspace_id),
        )
        connection.execute(
            """INSERT INTO agent_versions(id, agent_id, version, spec, created_at)
               VALUES (?, ?, 1, ?, ?)""",
            (version_id, agent_id, serialized, created_at),
        )
    return {
        "id": agent_id,
        "spec": spec.model_dump(),
        "created_at": created_at,
        "current_version": 1,
        "workspace_id": workspace_id,
    }


def get_agent(agent_id: str, workspace_id: str | None = None) -> dict | None:
    with connect() as connection:
        if workspace_id is None:  # only trusted Worker/tests
            row = connection.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM agents WHERE id=? AND workspace_id=?",
                (agent_id, workspace_id),
            ).fetchone()
    return _agent(row) if row else None


def list_agents(workspace_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM agents WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ).fetchall()
    return [_agent(row) for row in rows]


def list_agent_versions(agent_id: str, workspace_id: str | None = None) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT v.* FROM agent_versions v JOIN agents a ON v.agent_id=a.id
               WHERE a.id=? AND (? IS NULL OR a.workspace_id=?)
               ORDER BY v.version DESC""",
            (agent_id, workspace_id, workspace_id),
        ).fetchall()
    return [_version(row) for row in rows]


def get_agent_version(agent_id: str, version: int, workspace_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """SELECT v.* FROM agent_versions v JOIN agents a ON v.agent_id=a.id
               WHERE a.id=? AND v.version=? AND a.workspace_id=?""",
            (agent_id, version, workspace_id),
        ).fetchone()
    return _version(row) if row else None


def create_agent_version(
    agent_id: str, spec: AgentSpec, expected_version: int, workspace_id: str
) -> dict:
    """Optimistic version check and new snapshot commit form one transaction."""
    version_id, created_at = str(uuid.uuid4()), utcnow()
    serialized = spec.model_dump_json()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT current_version FROM agents WHERE id=? AND workspace_id=?",
            (agent_id, workspace_id),
        ).fetchone()
        if current is None:
            raise AgentNotFound("Agent 不存在")
        if current["current_version"] != expected_version:
            raise VersionConflict(
                f"版本冲突：服务器当前是 v{current['current_version']}，"
                f"你提交的是 v{expected_version}；请先刷新再修改"
            )
        next_version = expected_version + 1
        connection.execute(
            """INSERT INTO agent_versions(id, agent_id, version, spec, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (version_id, agent_id, next_version, serialized, created_at),
        )
        updated = connection.execute(
            """UPDATE agents SET spec=?, current_version=?
               WHERE id=? AND current_version=? AND workspace_id=?""",
            (serialized, next_version, agent_id, expected_version, workspace_id),
        )
        if updated.rowcount != 1:
            raise VersionConflict("并发更新冲突，未写入新版本")
    return {
        "id": version_id,
        "agent_id": agent_id,
        "version": next_version,
        "spec": spec.model_dump(),
        "created_at": created_at,
    }
