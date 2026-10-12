"""Workspace-scoped SQLite model connection persistence (local development)."""

from __future__ import annotations

import sqlite3
import uuid

from ..credentials import decrypt_key, encrypt_key
from .connection import connect, utcnow


def _public(row: dict) -> dict:
    return {
        key: value for key, value in row.items() if key not in ("api_key_encrypted", "workspace_id")
    } | {"has_api_key": bool(row["api_key_encrypted"]), "is_default": bool(row["is_default"])}


def _fetch(connection: sqlite3.Connection, connection_id: str, workspace_id: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM model_connections WHERE id=? AND workspace_id=?",
        (connection_id, workspace_id),
    ).fetchone()
    return dict(row) if row else None


def list_connections(workspace_id: str) -> list[dict]:
    with connect() as database:
        rows = database.execute(
            """SELECT * FROM model_connections WHERE workspace_id=?
               ORDER BY is_default DESC,updated_at DESC,id""",
            (workspace_id,),
        ).fetchall()
    return [_public(dict(row)) for row in rows]


def get_connection(connection_id: str, workspace_id: str) -> dict | None:
    with connect() as database:
        return _fetch(database, connection_id, workspace_id)


def get_default(workspace_id: str) -> dict | None:
    with connect() as database:
        row = database.execute(
            "SELECT * FROM model_connections WHERE workspace_id=? AND is_default=1",
            (workspace_id,),
        ).fetchone()
    return dict(row) if row else None


def get_runtime_connection(connection_id: str, workspace_id: str) -> dict | None:
    row = get_connection(connection_id, workspace_id)
    if row is None:
        return None
    return {**row, "api_key": decrypt_key(row["api_key_encrypted"])}


def add_connection(workspace_id: str, data: dict) -> dict:
    connection_id = str(uuid.uuid4())
    encrypted = encrypt_key(data.get("api_key", ""))
    now = utcnow()
    with connect() as database:
        database.execute("BEGIN IMMEDIATE")
        existing = database.execute(
            "SELECT 1 FROM model_connections WHERE workspace_id=? LIMIT 1",
            (workspace_id,),
        ).fetchone()
        is_default = not existing or data.get("is_default", False)
        if is_default:
            database.execute(
                "UPDATE model_connections SET is_default=0 WHERE workspace_id=?", (workspace_id,)
            )
        database.execute(
            """INSERT INTO model_connections
            (id,workspace_id,provider,name,protocol,base_url,api_key_encrypted,
             model_id,is_default,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                connection_id,
                workspace_id,
                data["provider"],
                data["name"],
                data["protocol"],
                data["base_url"],
                encrypted,
                data.get("model_id", ""),
                int(is_default),
                now,
                now,
            ),
        )
        result = _fetch(database, connection_id, workspace_id)
    return _public(result)


def update_connection(connection_id: str, workspace_id: str, changes: dict) -> dict | None:
    with connect() as database:
        database.execute("BEGIN IMMEDIATE")
        current = _fetch(database, connection_id, workspace_id)
        if current is None:
            return None
        fields = {key: value for key, value in changes.items() if value is not None}
        if "api_key" in fields:
            if fields["api_key"]:
                fields["api_key_encrypted"] = encrypt_key(fields.pop("api_key"))
            else:
                fields.pop("api_key")  # Empty means keep existing secret.
        if fields.pop("is_default", False):
            database.execute(
                "UPDATE model_connections SET is_default=0 WHERE workspace_id=?", (workspace_id,)
            )
            fields["is_default"] = 1
        if any(
            field in fields and fields[field] != current.get(field)
            for field in ("base_url", "model_id", "protocol", "api_key_encrypted")
        ):
            fields.update(
                {
                    "last_status": None,
                    "last_latency_ms": None,
                    "last_tokens_per_second": None,
                    "last_tested_at": None,
                }
            )
        fields["updated_at"] = utcnow()
        allowed = {
            "name",
            "base_url",
            "model_id",
            "protocol",
            "api_key_encrypted",
            "is_default",
            "updated_at",
            "last_status",
            "last_latency_ms",
            "last_tokens_per_second",
            "last_tested_at",
        }
        fields = {key: value for key, value in fields.items() if key in allowed}
        updates = ", ".join(f"{name}=?" for name in fields)
        database.execute(
            f"UPDATE model_connections SET {updates} WHERE id=? AND workspace_id=?",
            (*fields.values(), connection_id, workspace_id),
        )
        return _public(_fetch(database, connection_id, workspace_id))


def delete_connection(connection_id: str, workspace_id: str) -> bool:
    with connect() as database:
        database.execute("BEGIN IMMEDIATE")
        current = _fetch(database, connection_id, workspace_id)
        if current is None:
            return False
        database.execute(
            "DELETE FROM model_connections WHERE id=? AND workspace_id=?",
            (connection_id, workspace_id),
        )
        if current["is_default"]:
            other = database.execute(
                "SELECT id FROM model_connections WHERE workspace_id=? ORDER BY created_at LIMIT 1",
                (workspace_id,),
            ).fetchone()
            if other:
                database.execute(
                    "UPDATE model_connections SET is_default=1 WHERE id=? AND workspace_id=?",
                    (other["id"], workspace_id),
                )
    return True


def record_test(
    connection_id: str,
    workspace_id: str,
    *,
    status: str,
    latency_ms: int | None = None,
    tokens_per_second: float | None = None,
) -> None:
    with connect() as database:
        database.execute(
            """UPDATE model_connections
               SET last_status=?,last_latency_ms=?,last_tokens_per_second=?,last_tested_at=?
               WHERE id=? AND workspace_id=?""",
            (status, latency_ms, tokens_per_second, utcnow(), connection_id, workspace_id),
        )
