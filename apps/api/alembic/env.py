"""Alembic environment. Uses the application's metadata and settings."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from affilio.core.config import get_settings
from affilio.core.logging import configure_logging
from affilio.db.registry import Base

config = context.config
configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("url")
        or config.get_main_option("sqlalchemy.url")
        or os.environ.get("ALEMBIC_DATABASE_URL")
        or get_settings().database_url.get_secret_value()
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
