r"""Real PostgreSQL row-level-security smoke test.

This is deliberately opt-in, LOCALHOST ONLY, transactionally rolled back.
It requires a running pgvector/pgvector:pg17 instance migrated with Alembic.
No production/cloud DSN is accepted. The admin connection is used ONLY for
test fixture setup and SET LOCAL ROLE; regular requests must not use it.

Run from services/api:
  $env:STUDIO_POSTGRES_TEST_ADMIN_URL = "postgresql+psycopg://...@127.0.0.1:15429/agent_studio"
  .\.venv\Scripts\python.exe ..\..\scripts\smoke_postgres_rls.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError


def _enable_backend_imports() -> None:
    """Locate the repository's API package when called as scripts/*.py.

    Python sets sys.path[0] to the script directory rather than the caller's
    working directory. Both the Windows PowerShell launcher and Linux CI
    invoke this file by path, so the backend needs an explicit import root.
    """
    api_root = Path(__file__).resolve().parents[1] / "services" / "api"
    if not (api_root / "app" / "__init__.py").is_file():
        raise RuntimeError("Expected services/api/app package was not found")
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))


def _verify_local_test_url(value: str) -> None:
    if not value:
        raise RuntimeError("STUDIO_POSTGRES_TEST_ADMIN_URL required; no real PG was contacted")
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in ("127.0.0.1", "localhost")
        or url.port not in (15429, 54329)
        or url.database != "agent_studio"
        or url.username != "studio_migrator"
    ):
        raise RuntimeError(
            "Refusing non-local/unexpected DB URL. "
            "Only localhost:15429 (SSH tunnel) or :54329 (same-host) "
            "and database agent_studio are allowed for this test."
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
    _enable_backend_imports()
    from app.domain.errors import VersionConflict
    from app.domain.specs import AgentSpec, Goal
    from app.infrastructure.postgres.scoped_run_queue import PgScopedRunQueue
    from app.infrastructure.postgres.scoped_store import PgScopedStore

    engine = create_engine(
        value, pool_pre_ping=True, future=True, connect_args={"connect_timeout": 5}
    )
    a_user, b_user, viewer_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
                for user in (a_user, b_user, viewer_user):
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
                connection.execute(
                    text(
                        """INSERT INTO workspace_members(
                             workspace_id,user_id,role,created_at
                           ) VALUES(:ws,:actor,'viewer',now())"""
                    ),
                    {"ws": a_ws, "actor": viewer_user},
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
                try:
                    PgScopedStore(connection, a_ws)
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Store accepted missing authenticated transaction scope")

                _set_scope(connection, a_user, a_ws)
                visible = connection.execute(text("SELECT id FROM agents")).scalars().all()
                assert visible == [a_agent], visible
                store_a = PgScopedStore(connection, a_ws)
                assert store_a.get_agent(a_agent)["id"] == str(a_agent)
                assert len(store_a.list_agents()) == 1

                created = store_a.create_agent(
                    AgentSpec(
                        name="真实 PostgreSQL 适配测试助手",
                        goal=Goal(task="执行工作空间作用域隔离与版本控制验收"),
                    )
                )
                assert created["current_version"] == 1
                original = store_a.list_versions(created["id"])
                assert len(original) == 1 and original[0]["version"] == 1
                updated = store_a.create_version(
                    created["id"],
                    AgentSpec(
                        name="升级版本验证助手",
                        goal=Goal(task="验证 PostgreSQL 事务和版本更新正确性"),
                    ),
                    expected_version=1,
                )
                assert updated["version"] == 2
                try:
                    store_a.create_version(
                        created["id"],
                        AgentSpec(
                            name="冲突请求",
                            goal=Goal(task="此版本更新必须被严格拒绝并保持历史记录"),
                        ),
                        expected_version=1,
                    )
                except VersionConflict:
                    pass
                else:
                    raise AssertionError("PostgreSQL version CAS accepted stale update")
                run_a = store_a.queue_run(created["id"], "测试持久化与队列初始事件")
                assert run_a["agent_version_id"] == updated["id"]
                assert store_a.get_run(run_a["id"])["status"] == "QUEUED"
                events = store_a.get_events(run_a["id"])
                assert len(events) == 1 and events[0]["type"] == "queued"
                assert len(store_a.get_events(run_a["id"], after_seq=1)) == 0
                version_count = connection.execute(
                    text("SELECT count(*) FROM agent_versions")
                ).scalar_one()
                assert version_count == 3, version_count
                print("PASS: real PostgreSQL scoped Agent v1→v2→Run & atomic initial event")
                queue_a = PgScopedRunQueue(store_a)
                claim = queue_a.claim_next(worker_id="rls-worker", lease_seconds=90)
                assert claim is not None and claim["run_id"] == run_a["id"]
                assert claim["attempt"] == 1
                assert queue_a.heartbeat(run_a["id"], attempt=1, lease_seconds=90)
                assert queue_a.finalize(
                    run_a["id"], attempt=1, status="SUCCEEDED", output="verified"
                )
                assert store_a.get_run(run_a["id"])["status"] == "SUCCEEDED"
                final_events = store_a.get_events(run_a["id"])
                assert [event["type"] for event in final_events] == [
                    "queued",
                    "worker_claimed",
                    "worker_finished",
                ]
                assert not queue_a.finalize(run_a["id"], attempt=1, status="FAILED")

                # Crash/lease expiry fencing: an expired worker must never
                # finalize after recovery and another worker's claim.
                waiting = store_a.queue_run(created["id"], "测试 Worker 崩溃恢复")
                first = queue_a.claim_next(worker_id="worker-before-crash")
                assert first and first["run_id"] == waiting["id"]
                connection.execute(
                    text("UPDATE runs SET lease_until=now()-interval '5 seconds' WHERE id=:id"),
                    {"id": uuid.UUID(waiting["id"])},
                )
                recovered = queue_a.recover_expired()
                assert len(recovered) == 1
                assert recovered[0]["status"] == "QUEUED"
                assert not queue_a.finalize(
                    waiting["id"], attempt=first["attempt"], status="SUCCEEDED"
                )
                second = queue_a.claim_next(worker_id="worker-after-crash")
                assert second and second["attempt"] == first["attempt"] + 1
                assert queue_a.finalize(
                    waiting["id"], attempt=second["attempt"], status="SUCCEEDED"
                )
                print("PASS: PostgreSQL scoped queue claim/lease/fencing/reclaim lifecycle")

                # A different actor cannot impersonate a workspace member
                # merely by setting X-Workspace-ID.
                _set_scope(connection, b_user, a_ws)
                assert connection.execute(text("SELECT count(*) FROM agents")).scalar_one() == 0
                assert PgScopedStore(connection, a_ws).get_agent(created["id"]) is None

                _set_scope(connection, b_user, b_ws)
                visible_b = connection.execute(text("SELECT id FROM agents")).scalars().all()
                assert visible_b == [b_agent], visible_b
                store_b = PgScopedStore(connection, b_ws)
                assert store_b.get_agent(created["id"]) is None
                assert store_b.get_run(run_a["id"]) is None
                assert store_b.get_events(run_a["id"]) == []
                _set_scope(connection, viewer_user, a_ws)
                store_viewer = PgScopedStore(connection, a_ws)
                assert store_viewer.get_agent(created["id"]) is not None
                # A viewer is allowed to read but not to insert Agents or Runs.
                nested = connection.begin_nested()
                try:
                    try:
                        store_viewer.create_agent(
                            AgentSpec(
                                name="越权操作应拒绝",
                                goal=Goal(task="尝试使用只读角色创建新的 Agent 配置"),
                            )
                        )
                    except DBAPIError:
                        nested.rollback()
                    else:
                        nested.rollback()
                        raise AssertionError("Viewer bypassed database RLS write policy")
                finally:
                    if nested.is_active:
                        nested.rollback()
                print("PASS: PostgreSQL viewer reads own workspace but cannot create Agent")
                _set_scope(connection, b_user, b_ws)
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
