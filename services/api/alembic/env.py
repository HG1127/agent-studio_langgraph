"""Alembic environment for PostgreSQL only.

Offline SQL does not require credentials; online migrations require a dedicated
admin URL supplied only through the runtime environment / secret manager.
Never use the public API runtime role for DDL.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from app.infrastructure.postgres.models import Base
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url="postgresql+psycopg://",  # dialect only, no credentials
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    admin_url = os.environ.get("STUDIO_POSTGRES_ADMIN_URL", "")
    if not admin_url or not admin_url.startswith("postgresql+psycopg://"):
        raise RuntimeError(
            "PostgreSQL migration requires STUDIO_POSTGRES_ADMIN_URL with a "
            "dedicated migrator account; no embedded credentials are allowed"
        )
    engine = create_engine(admin_url, poolclass=pool.NullPool, future=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transactional_ddl=True,
                include_schemas=False,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
