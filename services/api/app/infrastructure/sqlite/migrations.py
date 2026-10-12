"""Idempotent SQLite migrations for the *local development* database.

Includes upgrade/backfill for the original single-spec Agent records.
Production PostgreSQL migrations and RLS are separate future deliverables.
"""

from __future__ import annotations

import uuid

from .connection import connect


def _columns(connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def initialize() -> None:
    with connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                spec TEXT NOT NULL,
                created_at TEXT NOT NULL
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
                worker_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL
            );
            """
        )
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces(
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS workspace_members(
                workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('owner','admin','member','viewer')),
                created_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,user_id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        # Previously unauthenticated data remains NULL (quarantined).
        # No automatic ownership assignment is possible or safe.
        if "workspace_id" not in _columns(connection, "agents"):
            connection.execute("ALTER TABLE agents ADD COLUMN workspace_id TEXT")
        if "workspace_id" not in _columns(connection, "runs"):
            connection.execute("ALTER TABLE runs ADD COLUMN workspace_id TEXT")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS model_connections(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                provider TEXT NOT NULL,
                name TEXT NOT NULL,
                protocol TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
                last_status TEXT,
                last_latency_ms INTEGER,
                last_tokens_per_second REAL,
                last_tested_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ix_model_default_workspace
               ON model_connections(workspace_id) WHERE is_default=1"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_model_workspace ON model_connections(workspace_id)"
        )
        for column, definition in {
            "model_connection_id": "TEXT",
            "model_name": "TEXT",
            "spec_snapshot": "TEXT",
            "lease_until": "TEXT",
            "heartbeat_at": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "agent_version_id": "TEXT",
        }.items():
            if column not in _columns(connection, "runs"):
                connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")

        if "current_version" not in _columns(connection, "agents"):
            connection.execute(
                "ALTER TABLE agents ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1"
            )
        if "event_key" not in _columns(connection, "run_events"):
            connection.execute("ALTER TABLE run_events ADD COLUMN event_key TEXT")

        connection.execute(
            """CREATE TABLE IF NOT EXISTS agent_versions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version >= 1),
                spec TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(agent_id, version),
                FOREIGN KEY(agent_id) REFERENCES agents(id)
            )"""
        )
        # Only legacy Agents without any version need backfill.
        # Never relabel an old Run to v1 unless its old snapshot is proven identical.
        for legacy in connection.execute(
            """SELECT id, spec, created_at FROM agents a
               WHERE NOT EXISTS (
                   SELECT 1 FROM agent_versions v WHERE v.agent_id=a.id
               )"""
        ).fetchall():
            connection.execute(
                """INSERT INTO agent_versions(id, agent_id, version, spec, created_at)
                   VALUES (?, ?, 1, ?, ?)""",
                (str(uuid.uuid4()), legacy["id"], legacy["spec"], legacy["created_at"]),
            )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ix_event_idempotency
               ON run_events(run_id, event_key)"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_runs_claim ON runs(status, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_runs_version ON runs(agent_version_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_agent_workspace ON agents(workspace_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_runs_workspace ON runs(workspace_id)")
        # Only owned Agent rows are eligible for derivation.
        # Unowned legacy records must remain hidden and never be claimed.
        connection.execute(
            """UPDATE runs SET workspace_id=(
                 SELECT a.workspace_id FROM agents a WHERE a.id=runs.agent_id
               ) WHERE workspace_id IS NULL AND EXISTS(
                 SELECT 1 FROM agents a
                 WHERE a.id=runs.agent_id AND a.workspace_id IS NOT NULL
               )"""
        )

        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_agent_version_update
               BEFORE UPDATE ON agent_versions
               BEGIN SELECT RAISE(ABORT, 'agent_versions are immutable'); END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_agent_version_delete
               BEFORE DELETE ON agent_versions
               BEGIN SELECT RAISE(ABORT, 'agent_versions are immutable'); END"""
        )
