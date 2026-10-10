"""Compatibility facade for persistence operations.

Existing callers can continue to import `app.db`. New code should import
the specific repository and avoid calling SQL from API/routes directly.
"""

from .infrastructure.sqlite.agent_repository import (
    add_agent,
    create_agent_version,
    get_agent,
    get_agent_version,
    list_agent_versions,
    list_agents,
)
from .infrastructure.sqlite.connection import checkpoint_path, connect, db_path, utcnow
from .infrastructure.sqlite.event_repository import append_event, get_events
from .infrastructure.sqlite.migrations import initialize
from .infrastructure.sqlite.run_repository import (
    add_run,
    finalize_run,
    get_run,
    heartbeat,
    list_runs,
    request_cancel,
    request_pause,
    request_resume,
    settle_control,
)
from .infrastructure.sqlite.worker_repository import claim_next, register_worker, worker_online

__all__ = [
    "add_agent",
    "add_run",
    "append_event",
    "checkpoint_path",
    "claim_next",
    "connect",
    "create_agent_version",
    "db_path",
    "finalize_run",
    "get_agent",
    "get_agent_version",
    "get_events",
    "get_run",
    "heartbeat",
    "initialize",
    "list_agent_versions",
    "list_agents",
    "list_runs",
    "register_worker",
    "request_cancel",
    "request_pause",
    "request_resume",
    "settle_control",
    "utcnow",
    "worker_online",
]
