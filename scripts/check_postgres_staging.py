"""Read-only preflight for the dedicated Agent Studio PostgreSQL 17 test DB.

No changes are made. Password and connection URL are never printed. The URL must
be supplied as STUDIO_POSTGRES_ADMIN_URL from the secure PowerShell launcher.
The script fails closed on unknown tables/revisions or wrong host/user/database.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy.exc import OperationalError

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
ALLOWED_PORTS = {15429, 54329}
EXPECTED_DATABASE = "agent_studio"
EXPECTED_USER = "studio_migrator"
EXPECTED_REVISION = "0001_pg17_workspace"

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "sessions",
    "workspaces",
    "workspace_members",
    "agents",
    "agent_versions",
    "runs",
    "run_events",
    "knowledge_sources",
    "knowledge_chunks",
    "memory_records",
    "quarantined_legacy_rows",
}


def validate_url(raw: str) -> None:
    if not raw:
        raise RuntimeError("STUDIO_POSTGRES_ADMIN_URL is missing (no database contacted)")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in ALLOWED_HOSTS
        or url.port not in ALLOWED_PORTS
        or url.database != EXPECTED_DATABASE
        or url.username != EXPECTED_USER
    ):
        raise RuntimeError(
            "Refusing unexpected database target or role. Expected dedicated "
            "local SSH tunnel: 127.0.0.1:15429/agent_studio, studio_migrator"
        )
    if url.query.get("sslmode") != "disable":
        raise RuntimeError(
            "Expected sslmode=disable ONLY over the already verified local SSH tunnel"
        )


def inspect_database() -> dict:
    raw = os.environ.get("STUDIO_POSTGRES_ADMIN_URL", "")
    validate_url(raw)
    engine = create_engine(
        raw,
        future=True,
        poolclass=NullPool,
        connect_args={"connect_timeout": 8},
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT current_user, current_database(), "
                    "current_setting('server_version_num')::int AS version"
                )
            ).one()
            if row[0] != EXPECTED_USER or row[1] != EXPECTED_DATABASE:
                raise RuntimeError("Connected server identity does not match requested DB/user")
            if not 170000 <= row[2] < 180000:
                raise RuntimeError("Only PostgreSQL 17 is accepted for this migration")

            pgvector = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one_or_none()
            if pgvector is None:
                raise RuntimeError("pgvector is not installed in this database")

            tables = {
                str(item)
                for item in conn.execute(
                    text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")
                ).scalars()
            }
            unexpected = tables - EXPECTED_TABLES
            if unexpected:
                raise RuntimeError(
                    "Unexpected public tables detected; migration BLOCKED: "
                    + ", ".join(sorted(unexpected))
                )

            revision = None
            if "alembic_version" in tables:
                revision_rows = (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
                )
                if len(revision_rows) != 1 or revision_rows[0] != EXPECTED_REVISION:
                    raise RuntimeError(
                        "Unknown/incomplete Alembic migration version; manual review required"
                    )
                revision = revision_rows[0]
                if tables != EXPECTED_TABLES:
                    raise RuntimeError(
                        "Alembic revision is recorded but expected tables are missing"
                    )
            elif tables:
                raise RuntimeError(
                    "Existing public tables found without migration ledger; "
                    "refusing to modify an unverified database"
                )

            return {
                "connected": True,
                "server_major_version": 17,
                "role": EXPECTED_USER,
                "database": EXPECTED_DATABASE,
                "pgvector_version": pgvector,
                "schema_state": "initial_empty" if not tables else "already_migrated",
                "alembic_revision": revision,
                "business_table_count": len(tables - {"alembic_version"}),
                "safe_for_baseline": not tables,
            }
    finally:
        engine.dispose()


def main() -> int:
    try:
        result = inspect_database()
    except OperationalError as exc:
        # SQLAlchemy wraps PostgreSQL errors in OperationalError. Exposing the
        # full traceback adds noise and may disclose connection metadata.
        # Print only safe, actionable troubleshooting text.
        detail = str(exc.orig).lower() if exc.orig else ""
        if "password authentication failed" in detail or "invalid password" in detail:
            print(
                "AUTH FAILED: The studio_migrator password was rejected. "
                "Check /opt/agent-studio-pg/secrets/admin_password on Baota. "
                "The original generated secret has 64 hexadecimal characters. "
                "Copy the entire secret into the hidden PowerShell prompt; "
                "do not type a literal '*' and do not send the password to chat. "
                "No schema changes were attempted.",
                file=sys.stderr,
            )
        elif "connection refused" in detail or "connection closed" in detail:
            print(
                "CONNECTION FAILED: Check that the SSH tunnel on "
                "127.0.0.1:15429 remains active. No schema changes were attempted.",
                file=sys.stderr,
            )
        else:
            print(
                "PostgreSQL connection/authentication failed. "
                "Verify the SSH tunnel, database account and PostgreSQL logs. "
                "No schema changes were attempted.",
                file=sys.stderr,
            )
        return 3
    except RuntimeError as exc:
        print(f"Preflight blocked: {exc}", file=sys.stderr)
        return 2

    print("PostgreSQL staging preflight (READ ONLY):")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
