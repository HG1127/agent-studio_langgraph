"""Transaction-scoped workspace context for future PostgreSQL runtime adapters.

The application obtains user_id from a verified session, never the HTTP body.
RLS operates under the least-privilege agentstudio_runtime DB role; the migrator
account must never be used by regular API or Worker business requests.

The current V0.5 development API still uses SQLite until real PG tests pass.
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
    # SET LOCAL cannot leak into a subsequent request using a pooled connection.
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('agent_studio.user_id', :value, true)"),
            {"value": str(user_id)},
        )
        connection.execute(
            text("SELECT set_config('agent_studio.workspace_id', :value, true)"),
            {"value": str(workspace_id)},
        )
        yield connection
