"""Tenant-scoped PostgreSQL Agent/Version/Run repository (staged V0.6 adapter).

This adapter is NOT wired to the live API or Worker yet. The caller must first
authenticate an actor with a trusted identity service and open a transaction
with `workspace_transaction` using a least-privileged DB login.

Every method uses PostgreSQL's RLS *and* an explicit workspace predicate. The
version update and Run+first event insert are atomic within the caller's
transaction; no hidden commit or implicit connection is created here.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select, text, update

from ...domain.errors import AgentNotFound, VersionConflict
from ...domain.specs import AgentSpec
from .models import Agent, AgentVersion, Run, RunEvent

EXPECTED_ROLE = "agentstudio_runtime"


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def _now() -> datetime:
    return datetime.now(UTC)


def _public_agent(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "spec": row["spec"],
        "created_at": row["created_at"].isoformat(),
        "current_version": row["current_version"],
    }


def _public_version(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "agent_id": str(row["agent_id"]),
        "version": row["version"],
        "spec": row["spec"],
        "created_at": row["created_at"].isoformat(),
    }


def _public_run(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "agent_id": str(row["agent_id"]),
        "workspace_id": str(row["workspace_id"]),
        "agent_version_id": str(row["agent_version_id"]) if row["agent_version_id"] else None,
        "input": row["input"],
        "status": row["status"],
        "output": row["output"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat(),
    }


class PgScopedStore:
    """Use only from within an already-opened RLS-scoped transaction.

    Constructor checks PostgreSQL role and scope to catch accidental
    bypass via a superuser/migrator connection or forgotten SET LOCAL.
    """

    def __init__(self, connection: Connection, workspace_id: uuid.UUID):
        if connection.dialect.name != "postgresql":
            raise ValueError("PgScopedStore requires PostgreSQL")
        if not isinstance(workspace_id, uuid.UUID):
            raise ValueError("Workspace ID must be a verified UUID")
        current = connection.execute(
            text(
                """SELECT current_user,
                         current_setting('agent_studio.user_id', true) AS actor_id,
                         current_setting('agent_studio.workspace_id', true) AS scope_id"""
            )
        ).one()
        if current[0] != EXPECTED_ROLE:
            raise PermissionError("Data access must use agentstudio_runtime (RLS) role")
        if current[2] != str(workspace_id) or not current[1]:
            raise PermissionError("Verified user/workspace transaction scope required")
        try:
            uuid.UUID(str(current[1]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise PermissionError("Invalid actor identifier in RLS transaction scope") from exc
        self.connection = connection
        self.workspace_id = workspace_id

    def create_agent(self, spec: AgentSpec) -> dict:
        agent_id, version_id, timestamp = uuid.uuid4(), uuid.uuid4(), _now()
        body = spec.model_dump(mode="json")
        self.connection.execute(
            insert(Agent).values(
                id=agent_id,
                workspace_id=self.workspace_id,
                spec=body,
                current_version=1,
                created_at=timestamp,
            )
        )
        self.connection.execute(
            insert(AgentVersion).values(
                id=version_id,
                agent_id=agent_id,
                version=1,
                spec=body,
                created_at=timestamp,
            )
        )
        return {
            "id": str(agent_id),
            "workspace_id": str(self.workspace_id),
            "spec": body,
            "created_at": timestamp.isoformat(),
            "current_version": 1,
        }

    def get_agent(self, agent_id: str | uuid.UUID) -> dict | None:
        row = (
            self.connection.execute(
                select(Agent).where(
                    Agent.id == _as_uuid(agent_id), Agent.workspace_id == self.workspace_id
                )
            )
            .mappings()
            .one_or_none()
        )
        return _public_agent(row) if row else None

    def list_agents(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 500:
            raise ValueError("Agent listing limit must be between 1 and 500")
        rows = self.connection.execute(
            select(Agent)
            .where(Agent.workspace_id == self.workspace_id)
            .order_by(Agent.created_at.desc(), Agent.id.desc())
            .limit(limit)
        ).mappings()
        return [_public_agent(agent) for agent in rows]

    def list_versions(self, agent_id: str | uuid.UUID) -> list[dict]:
        uid = _as_uuid(agent_id)
        rows = self.connection.execute(
            select(AgentVersion)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(
                Agent.id == uid,
                Agent.workspace_id == self.workspace_id,
            )
            .order_by(AgentVersion.version.desc())
        ).mappings()
        return [_public_version(version) for version in rows]

    def create_version(
        self, agent_id: str | uuid.UUID, spec: AgentSpec, *, expected_version: int
    ) -> dict:
        if expected_version < 1:
            raise ValueError("Expected version must be positive")
        uid = _as_uuid(agent_id)
        agent = (
            self.connection.execute(
                select(Agent)
                .where(Agent.id == uid, Agent.workspace_id == self.workspace_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if agent is None:
            raise AgentNotFound("Agent 不存在")
        if agent["current_version"] != expected_version:
            raise VersionConflict(
                f"版本冲突：服务器当前是 v{agent['current_version']}；"
                f"客户端使用 v{expected_version}"
            )
        next_version = expected_version + 1
        version_id = uuid.uuid4()
        now = _now()
        body = spec.model_dump(mode="json")
        self.connection.execute(
            insert(AgentVersion).values(
                id=version_id,
                agent_id=uid,
                version=next_version,
                spec=body,
                created_at=now,
            )
        )
        updated = self.connection.execute(
            update(Agent)
            .where(
                Agent.id == uid,
                Agent.workspace_id == self.workspace_id,
                Agent.current_version == expected_version,
            )
            .values(spec=body, current_version=next_version)
        )
        if updated.rowcount != 1:
            raise VersionConflict("并发版本更新失败，必须回滚整个事务")
        return {
            "id": str(version_id),
            "agent_id": str(uid),
            "version": next_version,
            "spec": body,
            "created_at": now.isoformat(),
        }

    def queue_run(self, agent_id: str | uuid.UUID, user_input: str) -> dict:
        if not 1 <= len(user_input) <= 4000:
            raise ValueError("Run input must contain 1 to 4000 characters")
        uid = _as_uuid(agent_id)
        row = self.connection.execute(
            select(AgentVersion.id, AgentVersion.spec)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(
                Agent.id == uid,
                Agent.workspace_id == self.workspace_id,
                AgentVersion.version == Agent.current_version,
            )
        ).one_or_none()
        if row is None:
            raise AgentNotFound("Agent 不存在")
        run_id, event_id, timestamp = uuid.uuid4(), uuid.uuid4(), _now()
        self.connection.execute(
            insert(Run).values(
                id=run_id,
                workspace_id=self.workspace_id,
                agent_id=uid,
                agent_version_id=row[0],
                input=user_input,
                spec_snapshot=row[1],
                status="QUEUED",
                attempts=0,
                created_at=timestamp,
            )
        )
        self.connection.execute(
            insert(RunEvent).values(
                id=event_id,
                run_id=run_id,
                workspace_id=self.workspace_id,
                seq=1,
                type="queued",
                payload={"message": "Run persisted in PostgreSQL; Worker adapter not activated"},
                event_key="queued",
                created_at=timestamp,
            )
        )
        return {
            "id": str(run_id),
            "agent_id": str(uid),
            "workspace_id": str(self.workspace_id),
            "agent_version_id": str(row[0]),
            "input": user_input,
            "status": "QUEUED",
            "output": None,
            "error": None,
            "created_at": timestamp.isoformat(),
        }

    def get_run(self, run_id: str | uuid.UUID) -> dict | None:
        run = (
            self.connection.execute(
                select(Run).where(Run.id == _as_uuid(run_id), Run.workspace_id == self.workspace_id)
            )
            .mappings()
            .one_or_none()
        )
        return _public_run(run) if run else None

    def list_runs(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 500:
            raise ValueError("Run listing limit must be between 1 and 500")
        rows = self.connection.execute(
            select(Run)
            .where(Run.workspace_id == self.workspace_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(limit)
        ).mappings()
        return [_public_run(run) for run in rows]

    def get_events(self, run_id: str | uuid.UUID, *, after_seq: int = 0) -> list[dict]:
        if after_seq < 0:
            raise ValueError("Event cursor must not be negative")
        run = self.connection.execute(
            select(Run.id).where(Run.id == _as_uuid(run_id), Run.workspace_id == self.workspace_id)
        ).scalar_one_or_none()
        if run is None:
            return []
        events = self.connection.execute(
            select(RunEvent)
            .where(
                RunEvent.run_id == run,
                RunEvent.workspace_id == self.workspace_id,
                RunEvent.seq > after_seq,
            )
            .order_by(RunEvent.seq)
            .limit(500)
        ).mappings()
        return [
            {
                "seq": event["seq"],
                "type": event["type"],
                "payload": event["payload"],
                "created_at": event["created_at"].isoformat(),
            }
            for event in events
        ]
