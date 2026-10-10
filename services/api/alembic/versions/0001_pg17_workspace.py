"""Initial PostgreSQL 17 / pgvector schema, RLS and immutable version ledger.

Revision ID: 0001_pg17_workspace
Revises:
Create Date: 2026-10-10

Migration must execute using a dedicated DBA/migrator account with CREATEROLE
and CREATE EXTENSION privileges. The no-login runtime role is strictly RLS bound.

IMPORTANT: There is deliberately no SQLite import in this migration. Legacy
records of uncertain ownership must be quarantined by a separate reviewed tool.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001_pg17_workspace"
down_revision = None
branch_labels = None
depends_on = None


def _uuid(name, *, nullable=False, primary_key=False):
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), nullable=nullable, primary_key=primary_key
    )


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _tenant_policy(table: str, *, allow_updates: bool = True):
    """For each tenant table, missing scope or membership yields zero rows."""
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_read ON public.{table} FOR SELECT "
        f"TO agentstudio_runtime USING (public.studio_can_read(workspace_id))"
    )
    op.execute(
        f"CREATE POLICY {table}_insert ON public.{table} FOR INSERT "
        f"TO agentstudio_runtime WITH CHECK (public.studio_can_write(workspace_id))"
    )
    if allow_updates:
        op.execute(
            f"CREATE POLICY {table}_update ON public.{table} FOR UPDATE "
            "TO agentstudio_runtime "
            "USING (public.studio_can_write(workspace_id)) "
            "WITH CHECK (public.studio_can_write(workspace_id))"
        )


def upgrade() -> None:
    # Supplied by the locally pinned pgvector/pgvector:pg17 image.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """DO $$
           BEGIN
             IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='agentstudio_runtime')
             THEN
               CREATE ROLE agentstudio_runtime NOLOGIN NOBYPASSRLS;
             ELSIF EXISTS (
               SELECT 1 FROM pg_roles WHERE rolname='agentstudio_runtime'
                 AND (rolcanlogin OR rolbypassrls OR rolsuper)
             ) THEN
               RAISE EXCEPTION 'agentstudio_runtime role must be NOLOGIN and NOBYPASSRLS';
             END IF;
           END $;"""
    )

    op.create_table(
        "users",
        _uuid("id", primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        _created(),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_table(
        "workspaces",
        _uuid("id", primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        _uuid("created_by"),
        _created(),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_table(
        "workspace_members",
        _uuid("workspace_id", primary_key=True),
        _uuid("user_id", primary_key=True),
        sa.Column("role", sa.String(12), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')",
            name="valid_member_role",
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        _uuid("user_id"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_sessions_expiry", "sessions", ["expires_at"])

    op.create_table(
        "agents",
        _uuid("id", primary_key=True),
        _uuid("workspace_id"),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agents_workspace"),
        sa.CheckConstraint("current_version >= 1", name="agent_version_positive"),
    )
    op.create_index("ix_agents_workspace_created", "agents", ["workspace_id", "created_at"])
    op.create_table(
        "agent_versions",
        _uuid("id", primary_key=True),
        _uuid("agent_id"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_number"),
        sa.UniqueConstraint("id", "agent_id", name="uq_agent_version_same_agent"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_table(
        "runs",
        _uuid("id", primary_key=True),
        _uuid("workspace_id"),
        _uuid("agent_id"),
        _uuid("agent_version_id", nullable=True),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("spec_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("output", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _created(),
        sa.UniqueConstraint("id", "workspace_id", name="uq_run_workspace"),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            ["agents.id", "agents.workspace_id"],
            name="fk_run_same_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "agent_id"],
            ["agent_versions.id", "agent_versions.agent_id"],
            name="fk_run_version_same_agent",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','PAUSE_REQUESTED','PAUSED',"
            "'CANCEL_REQUESTED','CANCELED','SUCCEEDED','PARTIAL','FAILED')",
            name="valid_run_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )
    op.create_index("ix_runs_workspace_created", "runs", ["workspace_id", "created_at"])
    op.create_index("ix_runs_lease", "runs", ["status", "lease_until"])
    op.create_table(
        "run_events",
        _uuid("id", primary_key=True),
        _uuid("run_id"),
        _uuid("workspace_id"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("event_key", sa.String(255)),
        _created(),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["runs.id", "runs.workspace_id"],
            name="fk_event_same_workspace",
        ),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_run_event_idempotency"),
    )
    op.create_index("ix_events_workspace_created", "run_events", ["workspace_id", "created_at"])

    # Future knowledge+memory storage structures, not an active RAG implementation.
    op.create_table(
        "knowledge_sources",
        _uuid("id", primary_key=True),
        _uuid("workspace_id"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.UniqueConstraint("id", "workspace_id", name="uq_knowledge_workspace"),
    )
    op.create_table(
        "knowledge_chunks",
        _uuid("id", primary_key=True),
        _uuid("workspace_id"),
        _uuid("source_id"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["knowledge_sources.id", "knowledge_sources.workspace_id"],
            name="fk_chunk_same_workspace",
        ),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunk_position"),
    )
    op.create_table(
        "memory_records",
        _uuid("id", primary_key=True),
        _uuid("workspace_id"),
        _uuid("agent_id", nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            ["agents.id", "agents.workspace_id"],
            name="fk_memory_agent_same_workspace",
        ),
    )
    # No runtime grants; unowned legacy rows are *never* auto-claimed.
    op.create_table(
        "quarantined_legacy_rows",
        _uuid("id", primary_key=True),
        sa.Column("source_table", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _created(),
        sa.UniqueConstraint("source_table", "source_id", name="uq_quarantine_source_row"),
    )

    op.execute(
        """CREATE OR REPLACE FUNCTION public.studio_workspace_id()
           RETURNS uuid LANGUAGE sql STABLE SECURITY INVOKER
           SET search_path = pg_catalog, public AS $$
             SELECT NULLIF(current_setting('agent_studio.workspace_id', true),'')::uuid
           $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.studio_user_id()
           RETURNS uuid LANGUAGE sql STABLE SECURITY INVOKER
           SET search_path = pg_catalog, public AS $$
             SELECT NULLIF(current_setting('agent_studio.user_id', true),'')::uuid
           $$"""
    )
    # Own membership is the only available membership record to the RLS role.
    op.execute("ALTER TABLE public.workspace_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.workspace_members FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY own_workspace_membership ON public.workspace_members
           FOR SELECT TO agentstudio_runtime USING (
               workspace_id = public.studio_workspace_id()
               AND user_id = public.studio_user_id()
           )"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.studio_can_read(target_ws uuid)
           RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER
           SET search_path = pg_catalog, public AS $$
             SELECT target_ws = public.studio_workspace_id()
               AND EXISTS (
                 SELECT 1 FROM public.workspace_members m
                 WHERE m.workspace_id = target_ws AND m.user_id = public.studio_user_id()
               )
           $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.studio_can_write(target_ws uuid)
           RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER
           SET search_path = pg_catalog, public AS $$
             SELECT target_ws = public.studio_workspace_id()
               AND EXISTS (
                 SELECT 1 FROM public.workspace_members m
                 WHERE m.workspace_id=target_ws AND m.user_id=public.studio_user_id()
                   AND m.role IN ('owner','admin','member')
               )
           $$"""
    )
    op.execute("ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.workspaces FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY workspaces_read ON public.workspaces FOR SELECT
           TO agentstudio_runtime USING (public.studio_can_read(id))"""
    )

    tenant_tables = (
        "agents",
        "runs",
        "run_events",
        "knowledge_sources",
        "knowledge_chunks",
        "memory_records",
    )
    for table in tenant_tables:
        _tenant_policy(table, allow_updates=(table != "run_events"))
    # Agent versions do not have a redundant workspace_id: scope through their Agent.
    op.execute("ALTER TABLE public.agent_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.agent_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY versions_read ON public.agent_versions FOR SELECT
           TO agentstudio_runtime USING (
             EXISTS (
               SELECT 1 FROM public.agents a
               WHERE a.id=agent_id AND public.studio_can_read(a.workspace_id)
             )
           )"""
    )
    op.execute(
        """CREATE POLICY versions_insert ON public.agent_versions FOR INSERT
           TO agentstudio_runtime WITH CHECK (
             EXISTS (
               SELECT 1 FROM public.agents a
               WHERE a.id=agent_id AND public.studio_can_write(a.workspace_id)
             )
           )"""
    )

    op.execute(
        """CREATE OR REPLACE FUNCTION public.reject_agent_version_mutation()
           RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN RAISE EXCEPTION 'agent_versions are immutable'; END
           $$"""
    )
    op.execute(
        """CREATE TRIGGER agent_versions_immutable
           BEFORE UPDATE OR DELETE ON public.agent_versions
           FOR EACH ROW EXECUTE FUNCTION public.reject_agent_version_mutation()"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.reject_workspace_reparenting()
           RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN
             IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
               RAISE EXCEPTION 'workspace_id is immutable';
             END IF;
             RETURN NEW;
           END
           $$"""
    )
    for table in tenant_tables:
        op.execute(
            f"CREATE TRIGGER {table}_fixed_workspace BEFORE UPDATE "
            f"ON public.{table} FOR EACH ROW "
            "EXECUTE FUNCTION public.reject_workspace_reparenting()"
        )

    # Users/sessions/quarantine are *not* granted to agentstudio_runtime. A separate
    # audited identity service must own those capabilities in production.
    for routine in (
        "studio_workspace_id()",
        "studio_user_id()",
        "studio_can_read(uuid)",
        "studio_can_write(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{routine} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{routine} TO agentstudio_runtime")

    op.execute("GRANT USAGE ON SCHEMA public TO agentstudio_runtime")
    op.execute(
        """GRANT SELECT ON public.workspaces, public.workspace_members
           TO agentstudio_runtime"""
    )
    op.execute("""GRANT SELECT,INSERT ON public.agent_versions TO agentstudio_runtime""")
    for table in tenant_tables:
        operations = "SELECT,INSERT" if table == "run_events" else "SELECT,INSERT,UPDATE"
        op.execute(f"GRANT {operations} ON public.{table} TO agentstudio_runtime")


def downgrade() -> None:
    # Reverting the initial baseline destructively would remove user data.
    # Restoration must use a reviewed backup/restore procedure instead.
    raise RuntimeError(
        "Refusing destructive initial PostgreSQL schema downgrade; "
        "restore from a verified backup instead."
    )
