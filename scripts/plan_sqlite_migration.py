"""Read-only SQLite-to-PostgreSQL migration assessment.

The tool prints counts only, never names, emails, prompts, passwords, token
hashes or document content. It does not connect to or write to PostgreSQL.
The source SQLite path remains untouched; no unverified legacy ownership
is silently mapped to a newly registered user.

Usage:
  python scripts/plan_sqlite_migration.py --sqlite-path services/api/.local/agent_studio.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

TABLES = (
    "users",
    "sessions",
    "workspaces",
    "workspace_members",
    "agents",
    "agent_versions",
    "runs",
    "run_events",
)


def assess(source: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("SQLite source must be an existing file")
    uri = "file:" + quote(source.as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        present = {
            item["name"]
            for item in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts: dict[str, int] = {}
        for name in TABLES:
            counts[name] = (
                int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
                if name in present
                else 0
            )

        def unowned(table: str) -> int:
            if table not in present:
                return 0
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "workspace_id" not in columns:
                return counts[table]
            return int(
                connection.execute(
                    f"SELECT count(*) FROM {table} WHERE workspace_id IS NULL"
                ).fetchone()[0]
            )

        agent_unowned = unowned("agents")
        run_unowned = unowned("runs")
        workspace_links_inconsistent = 0
        if "agents" in present and "runs" in present:
            agent_cols = {row["name"] for row in connection.execute("PRAGMA table_info(agents)")}
            run_cols = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if "workspace_id" in agent_cols and "workspace_id" in run_cols:
                workspace_links_inconsistent = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM runs r
                           LEFT JOIN agents a ON a.id=r.agent_id
                           WHERE a.id IS NULL
                              OR (r.workspace_id IS NOT NULL
                                  AND a.workspace_id IS NOT r.workspace_id)"""
                    ).fetchone()[0]
                )
        return {
            "assessment_only": True,
            "source_modified": False,
            "tables": counts,
            "unowned_agents": agent_unowned,
            "unowned_runs": run_unowned,
            "mismatched_or_missing_agent_links": workspace_links_inconsistent,
            "migration_authorized": False,
            "action": (
                "Import requires explicit verified workspace mapping and reviewed "
                "backup/rollback procedure. Unowned rows must remain quarantined."
            ),
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only legacy SQLite migration assessment")
    parser.add_argument("--sqlite-path", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assess(args.sqlite_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
