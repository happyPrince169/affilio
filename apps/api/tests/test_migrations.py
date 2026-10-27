"""Migration smoke tests. Require TEST_DATABASE_URL (skipped otherwise)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from affilio.db.registry import Base

API_DIR = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "organizations",
    "users",
    "organization_memberships",
    "social_accounts",
    "media_roots",
    "media_assets",
    "media_file_variants",
    "media_scan_jobs",
    "products",
    "product_status_events",
    "product_media_links",
    "product_imports",
    "purchase_requests",
    "purchase_orders",
    "purchase_order_items",
    "production_jobs",
    "production_job_products",
    "production_job_media",
    "user_sessions",
    "audit_events",
    "affiliate_links",
    "campaigns",
    "campaign_media",
    "campaign_targets",
    "publication_jobs",
    "publication_links",
    "publication_snapshots",
    "publication_attempts",
    "content_categories",
    "channel_profiles",
    "channel_category_rules",
    "media_content_categories",
    "folder_category_rules",
    "platform_connections",
    "account_credentials",
    "oauth_states",
}

pytestmark = pytest.mark.database


@pytest.fixture(scope="module")
def alembic_config(test_database_url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="module")
def migrated(alembic_config: Config) -> Config:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    return alembic_config


def test_upgrade_creates_foundation_tables(migrated: Config, engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())

    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables


def test_models_match_migrations(migrated: Config, engine: Engine) -> None:
    """Autogenerate must find nothing to do: the migration and the ORM models agree."""
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": False},
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"Schema drift between models and migrations: {diff}"


def test_constraints_and_indexes_present(migrated: Config, engine: Engine) -> None:
    inspector = inspect(engine)

    uniques = {c["name"] for c in inspector.get_unique_constraints("social_accounts")}
    assert "uq_social_accounts_org_platform_external_id" in uniques

    indexes = {i["name"] for i in inspector.get_indexes("social_accounts")}
    assert "ix_social_accounts_organization_id_platform" in indexes

    checks = {c["name"] for c in inspector.get_check_constraints("users")}
    assert "ck_users_user_status" in checks

    fks = inspector.get_foreign_keys("organization_memberships")
    assert all(fk["options"].get("ondelete") == "CASCADE" for fk in fks)


def test_downgrade_then_upgrade_roundtrip(migrated: Config, engine: Engine) -> None:
    command.downgrade(migrated, "base")
    assert not (EXPECTED_TABLES & set(inspect(engine).get_table_names()))

    command.upgrade(migrated, "head")
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
