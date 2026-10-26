"""Engine and session management.

M0 uses the synchronous SQLAlchemy engine with psycopg 3. FastAPI runs `def` endpoints in a
thread pool, which is sufficient for the MVP's traffic profile and keeps Alembic, seeding, and
tests on one code path. See docs/decisions/0003-sync-database-access.md.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass
from threading import Lock

import structlog
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from affilio.core.config import Settings, get_settings
from affilio.db import registry as _registry  # noqa: F401 - configure every mapper up front

log = structlog.get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = Lock()


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )


def get_engine() -> Engine:
    """Lazily create the process-wide engine so importing the app never opens connections."""
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = build_engine(get_settings())
                _session_factory = sessionmaker(
                    bind=_engine, autoflush=False, expire_on_commit=False
                )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    if _session_factory is None:  # pragma: no cover - established by get_engine()
        msg = "Session factory not initialised"
        raise RuntimeError(msg)
    return _session_factory


def dispose_engine() -> None:
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True, slots=True)
class DatabaseProbe:
    ok: bool
    dialect: str
    latency_ms: float | None
    error: str | None = None


def probe_database(engine: Engine | None = None) -> DatabaseProbe:
    """Run `SELECT 1` and report latency. Never raises; failures are reported, not propagated."""
    engine = engine or get_engine()
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.warning("database.probe_failed", error=exc.__class__.__name__)
        return DatabaseProbe(ok=False, dialect=engine.dialect.name, latency_ms=None, error=str(exc))
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return DatabaseProbe(ok=True, dialect=engine.dialect.name, latency_ms=latency_ms)
