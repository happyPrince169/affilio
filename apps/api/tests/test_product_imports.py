"""MeoScout CSV import. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.db.session import dispose_engine
from affilio.modules.products import imports
from affilio.modules.products.imports import (
    ImportFileError,
    ImportMappingError,
    parse_csv,
    suggest_mapping,
)
from affilio.modules.products.models import Product

pytestmark = pytest.mark.database

CSV = (
    b"ID,Product Name,Link,Shop,Category,Price,Commission,Image\n"
    b"m1,Portable Blender,https://shopee.vn/blender-i.1.11,KitchenMate,Kitchen,189000,8%,https://cf.shopee.vn/a.jpg\n"
    b'm2,Yoga Mat,https://shopee.vn/yoga-i.2.22,FitCo,Fitness,"245,000",0.05,\n'
    b"m3,,https://shopee.vn/noname-i.3.33,X,Misc,1000,5,\n"  # missing name → error
    b"m4,Bad URL,javascript:alert(1),X,Misc,1000,5,\n"  # unsafe url → error
    # duplicate of row 2 within the file (same URL, different case/trailing slash)
    b"m5,Blender again,HTTPS://SHOPEE.VN/blender-i.1.11/,KitchenMate,Kitchen,189000,8%,\n"
)


def test_parse_and_suggest_mapping() -> None:
    parsed = parse_csv(CSV, file_name="meoscout.csv")
    assert parsed.delimiter == ","
    assert parsed.headers[:3] == ["ID", "Product Name", "Link"]
    assert len(parsed.rows) == 5
    suggested = suggest_mapping(parsed.headers)
    assert suggested["name"] == "Product Name"
    assert suggested["product_url"] == "Link"
    assert suggested["seller_name"] == "Shop"
    assert suggested["observed_price"] == "Price"
    assert suggested["estimated_commission_rate"] == "Commission"
    assert suggested["image_url"] == "Image"
    assert suggested["source_ref"] == "ID"


def test_parse_rejects_empty_and_semicolon_is_detected() -> None:
    with pytest.raises(ImportFileError):
        parse_csv(b"", file_name="x.csv")
    parsed = parse_csv(b"name;url\nA;https://shopee.vn/a\n", file_name="x.csv")
    assert parsed.delimiter == ";" and parsed.rows[0]["name"] == "A"


def test_mapping_validation(db_session: Session, organization) -> None:
    parsed = parse_csv(CSV, file_name="m.csv")
    with pytest.raises(ImportMappingError, match="name"):
        imports.plan_import(
            db_session, organization.id, parsed, {"product_url": "Link"}, on_duplicate="skip"
        )
    with pytest.raises(ImportMappingError, match="not found"):
        imports.plan_import(
            db_session, organization.id, parsed, {"name": "Nope"}, on_duplicate="skip"
        )
    with pytest.raises(ImportMappingError, match="Unknown target"):
        imports.plan_import(
            db_session,
            organization.id,
            parsed,
            {"name": "Product Name", "price": "Price"},
            on_duplicate="skip",
        )


def test_partial_import_then_idempotent_reimport(db_session: Session, organization) -> None:
    parsed = parse_csv(CSV, file_name="meoscout.csv")
    mapping = suggest_mapping(parsed.headers)
    plan = imports.plan_import(db_session, organization.id, parsed, mapping, on_duplicate="skip")
    assert plan.counts == {"create": 2, "update": 0, "skip": 1, "error": 2}
    by_row = {r.row_number: r for r in plan.results}
    assert "name" in (by_row[4].reason or "")
    assert "http" in (by_row[5].reason or "").lower()
    assert "duplicate of row 2" in (by_row[6].reason or "")

    record = imports.apply_import(
        db_session, organization.id, plan, file_name="meoscout.csv", on_duplicate="skip"
    )
    db_session.commit()
    assert (record.created_count, record.skipped_count, record.failed_count) == (2, 1, 2)
    assert len(record.errors) == 2 and record.status == "completed"
    products = {
        p.name: p
        for p in db_session.scalars(
            select(Product).where(Product.organization_id == organization.id)
        )
    }
    blender = products["Portable Blender"]
    assert (
        blender.source == "meoscout"
        and blender.source_ref == "m1"
        and blender.import_id == record.id
    )
    assert (
        str(blender.estimated_commission_rate) == "0.0800"
        and str(blender.observed_price) == "189000.00"
    )
    assert (
        products["Yoga Mat"].observed_price == 245000
        and str(products["Yoga Mat"].estimated_commission_rate) == "0.0500"
    )
    assert blender.images == ["https://cf.shopee.vn/a.jpg"]

    # Same file again: nothing created, recorded as skipped with a pointer to the first run.
    plan2 = imports.plan_import(db_session, organization.id, parsed, mapping, on_duplicate="skip")
    assert plan2.previous_import_id == record.id
    assert plan2.counts["create"] == 0 and plan2.counts["skip"] == 3
    record2 = imports.apply_import(
        db_session, organization.id, plan2, file_name="meoscout.csv", on_duplicate="skip"
    )
    assert (
        record2.status == "skipped"
        and record2.duplicate_of_import_id == record.id
        and record2.created_count == 0
    )
    assert (
        db_session.scalar(
            select(Product).where(
                Product.organization_id == organization.id, Product.name == "Portable Blender"
            )
        )
        is blender
    )

    # Forced re-run with update: existing rows are updated, still no duplicates.
    updated_csv = CSV.replace(b"KitchenMate,Kitchen,189000", b"KitchenMate,Kitchen,179000")
    parsed3 = parse_csv(updated_csv, file_name="meoscout-v2.csv")
    plan3 = imports.plan_import(
        db_session, organization.id, parsed3, mapping, on_duplicate="update"
    )
    assert plan3.counts["update"] == 2 and plan3.counts["create"] == 0
    record3 = imports.apply_import(
        db_session, organization.id, plan3, file_name="meoscout-v2.csv", on_duplicate="update"
    )
    db_session.commit()
    db_session.refresh(blender)
    assert record3.updated_count == 2 and str(blender.observed_price) == "179000.00"
    assert (
        db_session.scalar(
            select(Product)
            .where(Product.organization_id == organization.id)
            .with_only_columns(Product.id)
            .limit(100)
            .order_by(None)
        )
        is not None
    )
    assert (
        len(
            list(
                db_session.scalars(
                    select(Product).where(Product.organization_id == organization.id)
                )
            )
        )
        == 2
    )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_import_api_preview_and_run(api: TestClient) -> None:
    files = {"file": ("meoscout.csv", io.BytesIO(CSV), "text/csv")}
    preview = api.post("/api/v1/products/imports/preview", files=files)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["suggested_mapping"]["name"] == "Product Name"
    assert body["counts"] == {"create": 2, "update": 0, "skip": 1, "error": 2}
    assert body["already_imported"] is False and len(body["rows"]) == 5
    assert "name" in body["target_fields"]

    bad_mapping = api.post(
        "/api/v1/products/imports/preview",
        files={"file": ("m.csv", io.BytesIO(CSV), "text/csv")},
        data={"mapping": '{"name": "Missing"}'},
    )
    assert (
        bad_mapping.status_code == 422
        and bad_mapping.json()["error"]["code"] == "invalid_import_mapping"
    )

    run = api.post(
        "/api/v1/products/imports", files={"file": ("meoscout.csv", io.BytesIO(CSV), "text/csv")}
    )
    assert run.status_code == 201, run.text
    result = run.json()
    assert (
        result["status"] == "completed"
        and result["created_count"] == 2
        and result["failed_count"] == 2
    )
    assert api.get("/api/v1/products").json()["total"] == 2
    assert (
        api.get("/api/v1/products", params={"q": "Blender"}).json()["items"][0]["source"]
        == "meoscout"
    )

    again = api.post(
        "/api/v1/products/imports", files={"file": ("meoscout.csv", io.BytesIO(CSV), "text/csv")}
    )
    assert again.status_code == 201 and again.json()["status"] == "skipped"
    assert again.json()["duplicate_of_import_id"] == result["id"]
    history = api.get("/api/v1/products/imports").json()
    assert [h["status"] for h in history] == ["skipped", "completed"]

    empty = api.post(
        "/api/v1/products/imports/preview", files={"file": ("e.csv", io.BytesIO(b""), "text/csv")}
    )
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "invalid_import_file"
