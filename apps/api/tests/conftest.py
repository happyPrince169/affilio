"""Shared pytest fixtures.

Environment is pinned *before* `affilio` is imported so cached settings reflect the test
configuration. Database-backed tests run only when TEST_DATABASE_URL is set and reachable;
otherwise they are skipped so the unit suite stays runnable without Docker.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Unreachable by construction: port 1 refuses connections immediately. Guarantees that tests
# never touch the development database unless TEST_DATABASE_URL is provided explicitly.
_UNREACHABLE_URL = "postgresql+psycopg://affilio:affilio@127.0.0.1:1/affilio_never"

os.environ["APP_ENV"] = "test"
os.environ["LOG_FORMAT"] = "json"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", _UNREACHABLE_URL)
os.environ["DATABASE_CONNECT_TIMEOUT_SECONDS"] = "2"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from affilio.app import create_app  # noqa: E402
from affilio.core.config import get_settings  # noqa: E402
from affilio.db.session import dispose_engine  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "database: needs a reachable TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; skipping database-backed test")
    try:
        probe = create_engine(url, connect_args={"connect_timeout": 2})
        with probe.connect() as connection:
            connection.execute(text("SELECT 1"))
        probe.dispose()
    except SQLAlchemyError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"TEST_DATABASE_URL not reachable: {exc.__class__.__name__}")
    return url


@pytest.fixture(scope="session")
def engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def app() -> Iterator[FastAPI]:
    get_settings.cache_clear()
    application = create_app()
    yield application
    dispose_engine()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
