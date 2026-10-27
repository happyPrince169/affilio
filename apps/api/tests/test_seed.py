"""Seed script tests. Require TEST_DATABASE_URL (skipped otherwise)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from affilio.core.config import Settings
from affilio.core.enums import Platform, SocialAccountStatus
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.identity.models import Organization
from affilio.scripts import seed_dev

API_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.database


@pytest.fixture(scope="module", autouse=True)
def _fresh_schema(test_database_url: str) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_seed_is_idempotent(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        first = seed_dev.seed(session)
    with Session(engine) as session, session.begin():
        second = seed_dev.seed(session)

    assert first.created_anything
    assert not second.created_anything

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Organization)) == 1
        account = session.scalar(select(SocialAccount))
        assert account is not None
        assert account.platform == Platform.SHOPEE
        assert account.status == SocialAccountStatus.PENDING
        assert account.external_account_id is None


def test_seed_refuses_non_development_environments(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = seed_dev.main(Settings(app_env="production", meta_provider_mode="live"))

    assert exit_code == 2
    assert "Refusing to seed" in capsys.readouterr().err
