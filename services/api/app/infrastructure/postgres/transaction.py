"""Transaction-scoped workspace context for future PostgreSQL runtime adapters.

The application obtains user_id from a verified session, never the HTTP body.
RLS operates under the least-privilege agentstudio_runtime DB role; the migrator
account must never be used by regular API or Worker business requests.

The current development API still uses SQLite; do not bypass the rollout gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text


@contextmanager
def workspace_transaction(
    engine: Engine, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> Iterator[Connection]:
    if not isinstance(user_id, uuid.UUID) or not isinstance(workspace_id, uuid.UUID):
        raise ValueError("Workspace and actor identifiers must be verified UUIDs")
    if engine.dialect.name != "postgresql":
        raise ValueError("RLS workspace context requires PostgreSQL; SQLite is unsupported")
    # SET LOCAL ROLE + GUC context cannot leak into another pooled connection.
    # The login role must be provisioned separately as a non-superuser member of
    # agentstudio_runtime. The migrator/admin DSN is explicitly rejected.
    with engine.begin() as connection:
        principal = connection.execute(
            text(
                """SELECT current_user,
                          COALESCE(r.rolsuper, false) AS is_superuser,
                          COALESCE(r.rolbypassrls, false) AS bypass_rls
                   FROM pg_roles r WHERE r.rolname = current_user"""
            )
        ).one()
        if (
            principal[1]
            or principal[2]
            or principal[0] in ("studio_migrator", "agentstudio_runtime")
        ):
            raise PermissionError(
                "Unprivileged login role required; never use migrator/superuser "
                "for runtime Agent data"
            )
        connection.execute(text("SET LOCAL ROLE agentstudio_runtime"))
        connection.execute(
            text("SELECT set_config('agent_studio.user_id', :value, true)"),
            {"value": str(user_id)},
        )
        connection.execute(
            text("SELECT set_config('agent_studio.workspace_id', :value, true)"),
            {"value": str(workspace_id)},
        )
        yield connection
