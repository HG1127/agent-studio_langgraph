"""Real PostgreSQL row-level-security smoke test.

This is deliberately opt-in, LOCALHOST ONLY, transactionally rolled back.
It requires a running pgvector/pgvector:pg17 instance migrated with Alembic.
No production/cloud DSN is accepted. The admin connection is used ONLY for
test fixture setup and SET LOCAL ROLE; regular requests must not use it.

Run from services/api:
  $env:STUDIO_POSTGRES_TEST_ADMIN_URL = "postgresql+psycopg://...@127.0.0.1:54329/agent_studio"
  .\.venv\Scripts\python.exe ..\..\scripts\smoke_postgres_rls.py
"""

from __future__ import annotations

import json
import os
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError


def _verify_local_test_url(value: str) -> None:
    if not value:
        raise RuntimeError("STUDIO_POSTGRES_TEST_ADMIN_URL required; no real PG was contacted")
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in ("127.0.0.1", "localhost")
        or url.port != 54329
        or url.database != "agent_studio"
    ):
        raise RuntimeError(
            "Refusing non-local/unexpected DB URL. "
            "Only 127.0.0.1:54329/agent_studio is allowed for this test."
        )


def _set_scope(connection, actor: uuid.UUID, workspace: uuid.UUID) -> None:
    connection.execute(
        text("SELECT set_config('agent_studio.user_id', :value, true)"),
        {"value": str(actor)},
    )
    connection.execute(
        text("SELECT set_config('agent_studio.workspace_id', :value, true)"),
        {"value": str(workspace)},
    )


def _must_fail(connection, sql: str, parameters: dict) -> None:
    # A failed statement must not abort the outer fixture transaction.
    nested = connection.begin_nested()
    try:
        try:
            connection.execute(text(sql), parameters)
        except DBAPIError:
            nested.rollback()
        else:
            nested.rollback()
            raise AssertionError("Expected PostgreSQL privilege/RLS rejection")
    finally:
        if nested.is_active:
            nested.rollback()


def main() -> int:
    value = os.environ.get("STUDIO_POSTGRES_TEST_ADMIN_URL", "")
    _verify_local_test_url(value)
    engine = create_engine(
        value, pool_pre_ping=True, future=True, connect_args={"connect_timeout": 5}
    )
    a_user, b_user = uuid.uuid4(), uuid.uuid4()
    a_ws, b_ws = uuid.uuid4(), uuid.uuid4()
    a_agent, b_agent = uuid.uuid4(), uuid.uuid4()
    version = uuid.uuid4()
    spec = json.dumps({"schema_version": "1.0", "name": "local-schema-check"}, ensure_ascii=False)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                found = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                if found != "0001_pg17_workspace":
                    raise RuntimeError("Apply Alembic baseline to localhost PG first")
                for user in (a_user, b_user):
                    connection.execute(
                        text(
                            """INSERT INTO users(id,email,password_hash,created_at)
                               VALUES(:id,:email,:hash,now())"""
                        ),
                        {"id": user, "email": str(user) + "@example.test", "hash": "test-only"},
                    )
                for user, ws in ((a_user, a_ws), (b_user, b_ws)):
                    connection.execute(
                        text(
                            """INSERT INTO workspaces(id,name,created_by,created_at)
                               VALUES(:id,'RLS smoke',:creator,now())"""
                        ),
                        {"id": ws, "creator": user},
                    )
                    connection.execute(
                        text(
                            """INSERT INTO workspace_members(
                                 workspace_id,user_id,role,created_at
                               ) VALUES(:ws,:actor,'owner',now())"""
                        ),
                        {"ws": ws, "actor": user},
                    )
                for agent, ws in ((a_agent, a_ws), (b_agent, b_ws)):
                    connection.execute(
                        text(
                            """INSERT INTO agents(
                                 id,workspace_id,spec,current_version,created_at
                               ) VALUES(:id,:ws,CAST(:spec AS jsonb),1,now())"""
                        ),
                        {"id": agent, "ws": ws, "spec": spec},
                    )
                connection.execute(
                    text(
                        """INSERT INTO agent_versions(
                             id,agent_id,version,spec,created_at
                           ) VALUES(:id,:agent,1,CAST(:spec AS jsonb),now())"""
                    ),
                    {"id": version, "agent": a_agent, "spec": spec},
                )

                # Switching to a non-login role proves RLS is enforced at the
                # DB permission layer, not merely filtered by a Python WHERE.
                connection.execute(text("SET LOCAL ROLE agentstudio_runtime"))
                count = connection.execute(text("SELECT count(*) FROM agents")).scalar_one()
                assert count == 0, f"Unscoped RLS failed: {count}"

                _set_scope(connection, a_user, a_ws)
                visible = connection.execute(text("SELECT id FROM agents")).scalars().all()
                assert visible == [a_agent], visible
                version_count = connection.execute(
                    text("SELECT count(*) FROM agent_versions")
                ).scalar_one()
                assert version_count == 1, version_count

                # A different actor cannot impersonate a workspace member
                # merely by setting X-Workspace-ID.
                _set_scope(connection, b_user, a_ws)
                assert connection.execute(text("SELECT count(*) FROM agents")).scalar_one() == 0

                _set_scope(connection, b_user, b_ws)
                visible_b = connection.execute(text("SELECT id FROM agents")).scalars().all()
                assert visible_b == [b_agent], visible_b
                _must_fail(
                    connection,
                    """INSERT INTO agents(
                         id,workspace_id,spec,current_version,created_at
                       ) VALUES(:id,:ws,CAST(:spec AS jsonb),1,now())""",
                    {"id": uuid.uuid4(), "ws": a_ws, "spec": spec},
                )
                _must_fail(connection, "SELECT * FROM sessions", {})
                _must_fail(connection, "SELECT * FROM quarantined_legacy_rows", {})
                _must_fail(
                    connection,
                    "UPDATE agent_versions SET spec=CAST(:spec AS jsonb) WHERE id=:id",
                    {"spec": spec, "id": version},
                )
                print("PASS: real PostgreSQL RLS denies unscoped and cross-workspace reads/writes")
                print("PASS: session/quarantine permission denied, versions cannot be mutated")
                print("All synthetic fixture rows rolled back; no production data used.")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
