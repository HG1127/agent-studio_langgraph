"""SQLite connection lifecycle and development paths."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def db_path() -> Path:
    return Path(os.getenv("AGENT_STUDIO_DB_PATH", ".local/agent_studio.sqlite3")).resolve()


def checkpoint_path() -> Path:
    explicit = os.getenv("AGENT_STUDIO_CHECKPOINT_PATH")
    path = (
        Path(explicit).resolve()
        if explicit
        else db_path().with_name(db_path().stem + ".checkpoints.sqlite3")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
