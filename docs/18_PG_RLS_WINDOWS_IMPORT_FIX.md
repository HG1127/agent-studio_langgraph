# Agent Studio PostgreSQL RLS smoke script import fix

Updated: 2026-10-10

## Verified current database status

The user successfully ran the **real Alembic V0.6 initial migration** through a Windows SSH tunnel into the dedicated Baota container running PostgreSQL 17 and pgvector 0.8.7. The post-migration read-only preflight reported:

- database: `agent_studio`
- schema_state: `already_migrated`
- alembic_revision: `0001_pg17_workspace`
- business_table_count: 12

The next command `& .\scripts\pg_staging.ps1 -Action RlsTest` passed the database preflight but **failed before executing any RLS checks** with `ModuleNotFoundError: No module named 'app'`. This reflects Python import-path resolution, **not** a failed RLS policy or incomplete Alembic migration.

## Fix in this increment

File `scripts/smoke_postgres_rls.py` is executed as a file, while `app` lives under `services/api/app`. Python's `sys.path[0]` for a script file points at `scripts`, not necessarily at the caller's working directory. The script now verifies that the expected `services/api/app/__init__.py` exists, adds that local source root to `sys.path`, and imports domain/SQLAlchemy repository objects **inside `main()` after validating that the test target is the dedicated `agent_studio` localhost database**.

No production API, Worker, RLS policy, schema, credentials, or SQLite data were modified. The PowerShell launcher and Linux CI invoke the same script path and continue to work without custom `PYTHONPATH` settings.

Four new offline regression tests cover direct file invocation, local package root resolution, mocked valid-target startup without connecting to a database, and early rejection of an invalid database URL. The real PostgreSQL DB2 integration test has **not** been rerun at the time of writing.

## User next command (Windows PowerShell)

Keep the existing SSH tunnel terminal running, and in a second terminal:

```powershell
cd "D:\工作项目\agent studio"
& .\scripts\pg_staging.ps1 -Action RlsTest
```

The script prompts for the database migrator password (never send it to chat). No Alembic migration or `MIGRATE` confirmation is needed again. The RLS script creates synthetic users, workspaces, Agents and Runs **inside one PostgreSQL transaction that is rolled back at the end**.

If the next run fails, preserve the error's last section and stop; don't drop tables, weaken RLS policies, reset DB passwords, or turn on `STUDIO_DATABASE_BACKEND=postgres`. A code-level RLS failure is possible now that the test can actually start, and should be fixed based on the real output.

## Remaining gate

Production-safe PostgreSQL activation additionally requires separate least-privilege accounts, authenticated workspace identity flow, LangGraph PostgresSaver integration, crash/recovery tests, controlled migration of SQLite records, and full multi-user QA. A successful synthetic RLS test is one necessary checkpoint, not completion of the entire migration.
