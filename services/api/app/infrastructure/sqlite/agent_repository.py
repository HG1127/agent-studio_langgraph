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
    }


def _version(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "version": row["version"],
        "spec": json.loads(row["spec"]),
        "created_at": row["created_at"],
    }


def add_agent(spec: AgentSpec) -> dict:
    agent_id, version_id, created_at = str(uuid.uuid4()), str(uuid.uuid4()), utcnow()
    serialized = spec.model_dump_json()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO agents(id, spec, created_at, current_version)
               VALUES (?, ?, ?, 1)""",
            (agent_id, serialized, created_at),
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
    }


def get_agent(agent_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return _agent(row) if row else None


def list_agents() -> list[dict]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    return [_agent(row) for row in rows]


def list_agent_versions(agent_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT * FROM agent_versions WHERE agent_id=?
               ORDER BY version DESC""",
            (agent_id,),
        ).fetchall()
    return [_version(row) for row in rows]


def get_agent_version(agent_id: str, version: int) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM agent_versions WHERE agent_id=? AND version=?",
            (agent_id, version),
        ).fetchone()
    return _version(row) if row else None


def create_agent_version(agent_id: str, spec: AgentSpec, expected_version: int) -> dict:
    """Optimistic version check and new snapshot commit form one transaction."""
    version_id, created_at = str(uuid.uuid4()), utcnow()
    serialized = spec.model_dump_json()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT current_version FROM agents WHERE id=?", (agent_id,)
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
               WHERE id=? AND current_version=?""",
            (serialized, next_version, agent_id, expected_version),
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
