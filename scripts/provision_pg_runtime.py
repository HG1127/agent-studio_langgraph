"""Guarded staging-only PostgreSQL runtime login role setup and verification.

Admin DSN is inherited from pg_staging.ps1; never pass passwords as CLI flags.
This script can CREATE only a fixed role on the dedicated agent_studio test DB.
It does not activate PostgreSQL as the application's default backend.
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import os
import sys
from typing import Any

import psycopg
from check_postgres_staging import validate_url
from psycopg import sql
from psycopg.errors import Error as PgError
from sqlalchemy.engine.url import make_url

DB_NAME = "agent_studio"
MIGRATOR = "studio_migrator"
RUNTIME_LOGIN = "agentstudio_app"
RUNTIME_RLS_ROLE = "agentstudio_runtime"
BASELINE = "0001_pg17_workspace"


def admin_connection_settings(raw: str) -> dict[str, Any]:
    validate_url(raw)
    url = make_url(raw)
    return {
        "host": url.host,
        "port": url.port,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
        "sslmode": "disable",  # Only via verified localhost SSH tunnel.
        "connect_timeout": 8,
    }


def _role_details(conn: psycopg.Connection, name: str) -> dict | None:
    row = conn.execute(
        """SELECT rolname, rolcanlogin, rolinherit, rolsuper,
                  rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
           FROM pg_catalog.pg_roles WHERE rolname=%s""",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return dict(
        zip(
            (
                "name",
                "login",
                "inherit",
                "superuser",
                "create_role",
                "create_db",
                "replication",
                "bypass_rls",
            ),
            row,
            strict=True,
        )
    )


def validate_staging(conn: psycopg.Connection) -> None:
    row = conn.execute(
        """SELECT current_user,current_database(),
                  current_setting('server_version_num')::int"""
    ).fetchone()
    if row is None or row[0] != MIGRATOR or row[1] != DB_NAME or not 170000 <= row[2] < 180000:
        raise RuntimeError("Refusing non-staging database, wrong admin role or non-PG17 server")
    found = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
    if found is None or found[0] != BASELINE:
        raise RuntimeError("Expected PostgreSQL baseline migration not applied")
    rls = _role_details(conn, RUNTIME_RLS_ROLE)
    if rls is None or rls["login"] or rls["bypass_rls"] or rls["superuser"]:
        raise RuntimeError("Expected NOLOGIN/NOBYPASSRLS runtime RLS role was not found")


def role_status(conn: psycopg.Connection) -> dict | None:
    role = _role_details(conn, RUNTIME_LOGIN)
    if role is None:
        return None
    role["may_set_rls_role"] = bool(
        conn.execute(
            "SELECT pg_has_role(%s,%s,'SET')",
            (RUNTIME_LOGIN, RUNTIME_RLS_ROLE),
        ).fetchone()[0]
    )
    memberships = conn.execute(
        """SELECT granted_role.rolname
           FROM pg_catalog.pg_auth_members m
           JOIN pg_catalog.pg_roles member_role ON member_role.oid = m.member
           JOIN pg_catalog.pg_roles granted_role ON granted_role.oid = m.roleid
           WHERE member_role.rolname=%s
           ORDER BY granted_role.rolname""",
        (RUNTIME_LOGIN,),
    ).fetchall()
    role["memberships"] = tuple(item[0] for item in memberships)
    return role


def require_expected_runtime(role: dict) -> None:
    if not (
        role["name"] == RUNTIME_LOGIN
        and role["login"]
        and not role["inherit"]
        and not role["superuser"]
        and not role["create_role"]
        and not role["create_db"]
        and not role["replication"]
        and not role["bypass_rls"]
        and role.get("memberships") == (RUNTIME_RLS_ROLE,)
        and role["may_set_rls_role"]
    ):
        raise RuntimeError(
            "Database role has unexpected security flags. Stop; do not rewrite existing role."
        )


def _new_password(admin_password: str | None) -> str:
    print(
        "Create a NEW strong runtime password (recommended 64 hex characters). "
        "Save it in your own password manager. It is NOT printed or saved by this script."
    )
    chosen = getpass.getpass("New agentstudio_app password (hidden): ")
    verify = getpass.getpass("Repeat agentstudio_app password (hidden): ")
    if not chosen or not hmac.compare_digest(chosen, verify):
        raise RuntimeError("Runtime password confirmation failed; no database changes made")
    if len(chosen) < 40 or len(chosen) > 256 or chosen.strip() != chosen:
        raise RuntimeError("Runtime password must be 40-256 characters without edge whitespace")
    if admin_password and hmac.compare_digest(chosen, admin_password):
        raise RuntimeError("Runtime password must be different from migrator password")
    return chosen


def provision(conn: psycopg.Connection, *, admin_password: str | None) -> None:
    # Fail closed, including when another party already created the login.
    if role_status(conn) is not None:
        raise RuntimeError("agentstudio_app already exists; refusing to overwrite its password")
    password = _new_password(admin_password)
    with conn.transaction():
        # Runtime login has no inherited privileges. Explicit SET LOCAL ROLE is
        # mandatory for each verified workspace transaction.
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                "CONNECTION LIMIT 20 PASSWORD {}"
            ).format(sql.Identifier(RUNTIME_LOGIN), sql.Literal(password))
        )
        conn.execute(
            sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
                sql.Identifier(RUNTIME_RLS_ROLE), sql.Identifier(RUNTIME_LOGIN)
            )
        )
        # Grant the intended database explicitly. This does NOT globally revoke
        # PostgreSQL's default PUBLIC CONNECT on other databases.
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DB_NAME), sql.Identifier(RUNTIME_LOGIN)
            )
        )
    role = role_status(conn)
    if role is None:
        raise RuntimeError("Login not visible after provision")
    require_expected_runtime(role)
    print(
        "PROVISIONED: agentstudio_app has LOGIN/NOINHERIT/NOBYPASSRLS and "
        "may SET ROLE agentstudio_runtime. "
        "No application backend switchover occurred."
    )


def check_role(conn: psycopg.Connection) -> None:
    role = role_status(conn)
    if role is None:
        print("RUNTIME_ROLE_STATUS: not_provisioned")
    else:
        require_expected_runtime(role)
        print("RUNTIME_ROLE_STATUS: valid_least_privilege_role")
        print("RUNTIME_ROLE_NAME: agentstudio_app")
        print("RUNTIME_RLS_ROLE: agentstudio_runtime")
        print("RUNTIME_LOGIN_PASSWORD: never printed")


def verify_login(admin_options: dict[str, Any]) -> None:
    password = getpass.getpass("agentstudio_app password for login verification (hidden): ")
    if not password:
        raise RuntimeError("Runtime password is missing")
    options = {**admin_options, "user": RUNTIME_LOGIN, "password": password}
    with psycopg.connect(**options) as conn:
        with conn.transaction():
            user = conn.execute("SELECT current_user").fetchone()[0]
            if user != RUNTIME_LOGIN:
                raise RuntimeError("Runtime login unexpectedly changed roles")
            # The login itself must not inherit any direct data privileges.
            if conn.execute(
                "SELECT has_table_privilege(current_user, 'public.sessions', 'SELECT')"
            ).fetchone()[0]:
                raise RuntimeError("Runtime login can read sessions; security regression")
            if conn.execute(
                "SELECT has_table_privilege(current_user, 'public.agents', 'SELECT')"
            ).fetchone()[0]:
                raise RuntimeError("Runtime login inherited Agent table privileges")
            conn.execute("SET LOCAL ROLE agentstudio_runtime")
            if conn.execute("SELECT current_user").fetchone()[0] != RUNTIME_RLS_ROLE:
                raise RuntimeError("Runtime login cannot SET LOCAL ROLE as required")
            if conn.execute("SELECT count(*) FROM public.agents").fetchone()[0] != 0:
                raise RuntimeError("Unscoped runtime role can see Agent data")
    print("PASS: real runtime LOGIN and SET LOCAL ROLE; unscoped rows hidden.")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--provision", action="store_true")
    group.add_argument("--verify-login", action="store_true")
    args = parser.parse_args()

    raw = os.environ.get("STUDIO_POSTGRES_ADMIN_URL", "")
    settings = admin_connection_settings(raw)
    if args.verify_login:
        # Privilege metadata is checked using the existing admin credentials,
        # but credentials are never used as the runtime login.
        with psycopg.connect(**settings) as admin:
            validate_staging(admin)
            role = role_status(admin)
            if role is None:
                raise RuntimeError("Runtime login has not been provisioned")
            require_expected_runtime(role)
        verify_login(settings)
        return 0

    with psycopg.connect(**settings) as conn:
        validate_staging(conn)
        if args.check:
            check_role(conn)
        else:
            if os.environ.get("STUDIO_RUNTIME_PROVISION_APPROVED") != "PROVISION":
                raise RuntimeError("Explicit staging provisioning approval missing")
            provision(conn, admin_password=settings.get("password"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        # Runtime errors are authored here and never contain secrets.
        print(f"RUNTIME ROLE BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except PgError:
        # A provider exception may carry SQL or connection parameters.
        print(
            "PostgreSQL role operation failed; no application backend switchover. "
            "Check the database server logs without sharing credentials.",
            file=sys.stderr,
        )
        raise SystemExit(3) from None
