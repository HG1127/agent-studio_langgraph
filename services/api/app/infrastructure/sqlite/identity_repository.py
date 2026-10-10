"""Local-only identity, workspace and hashed session storage."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from ...domain.errors import VersionConflict
from .connection import connect, utcnow

SESSION_HOURS = 12


def create_user_with_personal_workspace(email: str, password_hash: str) -> tuple[dict, dict]:
    import sqlite3

    user_id, space_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = utcnow()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                (user_id, email, password_hash, now),
            )
        except sqlite3.IntegrityError as exc:
            raise VersionConflict("该邮箱已经注册") from exc
        connection.execute(
            "INSERT INTO workspaces(id,name,created_by,created_at) VALUES (?,?,?,?)",
            (space_id, "我的工作空间", user_id, now),
        )
        connection.execute(
            "INSERT INTO workspace_members(workspace_id,user_id,role,created_at) VALUES (?,?,?,?)",
            (space_id, user_id, "owner", now),
        )
    return {"id": user_id, "email": email}, {
        "id": space_id,
        "name": "我的工作空间",
        "role": "owner",
    }


def get_user_by_email(email: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(hours=SESSION_HOURS)).isoformat()
    with connect() as connection:
        connection.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES (?,?,?,?)",
            (session_digest(token), user_id, expires, utcnow()),
        )
    return token


def resolve_session(token: str) -> dict | None:
    if not token or len(token) > 256:
        return None
    with connect() as connection:
        row = connection.execute(
            """SELECT u.id,u.email FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>?""",
            (session_digest(token), utcnow()),
        ).fetchone()
    return dict(row) if row else None


def revoke_session(token: str) -> None:
    if token:
        with connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (session_digest(token),))


def list_workspaces(user_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT w.id,w.name,m.role FROM workspaces w
               JOIN workspace_members m ON w.id=m.workspace_id
               WHERE m.user_id=? ORDER BY w.created_at,w.id""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_membership(user_id: str, workspace_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """SELECT w.id,w.name,m.role FROM workspaces w
               JOIN workspace_members m ON m.workspace_id=w.id
               WHERE w.id=? AND m.user_id=?""",
            (workspace_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def create_workspace(user_id: str, name: str) -> dict:
    space_id, now = str(uuid.uuid4()), utcnow()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO workspaces(id,name,created_by,created_at) VALUES (?,?,?,?)",
            (space_id, name, user_id, now),
        )
        connection.execute(
            "INSERT INTO workspace_members(workspace_id,user_id,role,created_at) VALUES (?,?,?,?)",
            (space_id, user_id, "owner", now),
        )
    return {"id": space_id, "name": name, "role": "owner"}


def list_members(workspace_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT u.id,u.email,m.role FROM workspace_members m
               JOIN users u ON u.id=m.user_id
               WHERE m.workspace_id=? ORDER BY m.created_at,u.id""",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_member(workspace_id: str, user_email: str, role: str) -> dict | None:
    user = get_user_by_email(user_email)
    if user is None:
        return None
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",
            (workspace_id, user["id"]),
        ).fetchone()
        if existing and existing["role"] == "owner":
            raise VersionConflict("禁止修改工作空间所有者权限")
        connection.execute(
            """INSERT INTO workspace_members(workspace_id,user_id,role,created_at)
               VALUES (?,?,?,?) ON CONFLICT(workspace_id,user_id)
               DO UPDATE SET role=excluded.role""",
            (workspace_id, user["id"], role, utcnow()),
        )
    return {"id": user["id"], "email": user["email"], "role": role}
