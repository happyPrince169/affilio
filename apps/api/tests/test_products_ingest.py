"""`POST /products/ingest` (M3.2-B.2 P1): identity, dedupe, idempotency, conflicts, bounds.
Needs TEST_DATABASE_URL."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole, ProductStatus
from affilio.db.session import dispose_engine
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import AuditEvent
from affilio.modules.procurement.models import PurchaseRequest
from affilio.modules.products import ingest, service
from affilio.modules.products.models import (
    Product,
    ProductIngestBatch,
    ProductIngestItem,
    ProductStatusEvent,
)
from affilio.modules.products.schemas import ProductCreate, ProductStatusChange
from tests.auth_helpers import sign_in
from tests.media_helpers import create_organization

pytestmark = pytest.mark.database

INGEST = "/api/v1/products/ingest"


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()


@pytest.fixture
def key(db_session: Session, organization) -> dict[str, str]:
    _, secret = identity_service.create_api_key(
        db_session,
        organization,
        name="MeoScout bridge",
        scopes=["products:ingest"],
        created_by=None,
    )
    db_session.commit()
    return {"Authorization": f"Bearer {secret}"}


def item(ref: str, **overrides) -> dict:
    base = {
        "client_ref": f"shopee:{ref}",
        "name": f"Product {ref}",
        "platform": "shopee",
        "platform_product_id": ref,
        "product_url": f"https://shopee.vn/product/{ref.replace('_', '/')}",
        "affiliate_url": f"https://s.shopee.vn/{ref}",
        "seller_name": "KRISTY Official",
        "category": "Women's Fashion",
        "image_url": f"https://cf.shopee.vn/file/{ref}.webp",
        "currency": "VND",
        "observed_price": "239000",
        "estimated_commission_rate": "0.125",
        "estimated_commission_amount": "29875",
        "research_notes": "MeoScore 82 TAKE",
        "evidence": {"meoscore": 82, "decision": "TAKE", "link_source": "BATCH_CSV_IMPORT"},
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def batch(items: list[dict], *, batch_id: str | None = None, on_conflict: str = "keep") -> dict:
    return {
        "contract": "meoscout.affilio.candidates",
        "version": "2.0.0",
        "batch_id": batch_id or f"msb_{uuid.uuid4().hex}",
        "sent_at": "2026-11-09T03:10:22Z",
        "source": {"tool": "meoscout", "build": "c73db53", "extension_version": "0.1.0"},
        "on_conflict": on_conflict,
        "items": items,
    }


def by_ref(response) -> dict[str, dict]:
    assert response.status_code == 200, response.text
    return {r["client_ref"]: r for r in response.json()["results"]}


def product_count(session: Session, organization_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count(Product.id)).where(Product.organization_id == organization_id)
    )


def batch_count(session: Session, organization_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count(ProductIngestBatch.id)).where(
            ProductIngestBatch.organization_id == organization_id
        )
    )


def status_event_count(session: Session, organization_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count(ProductStatusEvent.id))
        .join(Product, Product.id == ProductStatusEvent.product_id)
        .where(Product.organization_id == organization_id)
    )


def purchase_requests(session: Session, organization_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count(PurchaseRequest.id)).where(
            PurchaseRequest.organization_id == organization_id
        )
    )


def audit_events(session: Session, organization_id: uuid.UUID, action: str) -> list[AuditEvent]:
    return session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == action, AuditEvent.organization_id == organization_id
        )
    ).all()


def one_batch(session: Session, organization_id: uuid.UUID) -> ProductIngestBatch:
    rows = session.scalars(
        select(ProductIngestBatch)
        .where(ProductIngestBatch.organization_id == organization_id)
        .order_by(ProductIngestBatch.created_at)
    ).all()
    assert len(rows) == 1, [r.batch_id for r in rows]
    return rows[0]


# ------------------------------------------------------------------ happy path


def test_valid_batch_creates_discovered_products_and_audits(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = batch([item("987654_123456789"), item("111_222", affiliate_url=None)])
    response = api.post(INGEST, json=payload, headers=key)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch_id"] == payload["batch_id"] and body["replayed"] is False
    assert body["status"] == "completed" and body["on_conflict"] == "keep"
    assert body["counts"] == {
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "conflict": 0,
        "rejected": 0,
    }
    results = by_ref(response)
    first = results["shopee:987654_123456789"]
    assert first["status"] == "created" and first["product_status"] == "discovered"
    assert first["code"] is None and first["changed_fields"] == [] and first["conflicts"] == []

    product = db_session.get(Product, uuid.UUID(first["product_id"]))
    assert product is not None
    assert product.organization_id == organization.id
    assert product.status == ProductStatus.DISCOVERED
    assert product.source == "meoscout" and product.source_ref == "shopee:987654_123456789"
    assert str(product.ingest_batch_id) == body["id"] and product.import_id is None
    assert product.platform_product_id == "987654_123456789"
    assert product.product_url_normalized == "https://shopee.vn/product/987654/123456789"
    assert product.affiliate_url == "https://s.shopee.vn/987654_123456789"
    assert product.images == ["https://cf.shopee.vn/file/987654_123456789.webp"]
    assert product.observed_price == Decimal("239000") and product.price_observed_at is not None
    assert product.estimated_commission_rate == Decimal("0.125")
    assert product.currency == "VND" and product.research_notes == "MeoScore 82 TAKE"
    events = product.status_events
    assert len(events) == 1 and events[0].to_status == ProductStatus.DISCOVERED
    assert events[0].actor == "integration:MeoScout bridge"
    assert payload["batch_id"] in (events[0].note or "")

    # Evidence lives on the ingest item, not on the product.
    stored = db_session.scalars(
        select(ProductIngestItem).where(ProductIngestItem.ingest_batch_id == uuid.UUID(body["id"]))
    ).all()
    assert {i.client_ref: i.evidence["meoscore"] for i in stored} == {
        "shopee:987654_123456789": 82,
        "shopee:111_222": 82,
    }
    assert product.research_notes is not None and "82" not in str(product.images)

    # Nothing downstream happened.
    assert purchase_requests(db_session, organization.id) == 0

    # Audited as a service actor named after the key, without the secret.
    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "product.ingest_batch",
            AuditEvent.organization_id == organization.id,
        )
    ).all()
    assert len(audit) == 1
    event = audit[0]
    assert event.actor_kind == "service" and event.actor_label == "integration:MeoScout bridge"
    assert event.data["batch_id"] == payload["batch_id"]
    assert event.data["counts"]["created"] == 2
    assert event.data["key_prefix"].startswith("afk_") and len(event.data["key_prefix"]) == 12
    assert "afk_" not in json.dumps(event.data).replace(event.data["key_prefix"], "")
    assert event.request_id is not None
    stored_batch = db_session.get(ProductIngestBatch, uuid.UUID(body["id"]))
    assert stored_batch is not None and stored_batch.api_key_id is not None
    assert stored_batch.request_id == response.headers["x-request-id"]
    assert stored_batch.source == {
        "tool": "meoscout",
        "build": "c73db53",
        "extension_version": "0.1.0",
    }


def test_editor_session_may_ingest_viewer_may_not(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization, role=MembershipRole.VIEWER)
    denied = api.post(INGEST, json=batch([item("1_1")]))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    api.cookies.clear()
    sign_in(api, db_session, organization, role=MembershipRole.EDITOR)
    ok = api.post(INGEST, json=batch([item("1_1")]))
    assert ok.status_code == 200, ok.text
    assert by_ref(ok)["shopee:1_1"]["status"] == "created"
    batch_row = one_batch(db_session, organization.id)
    assert batch_row.api_key_id is None
    assert "@" in batch_row.actor


def test_key_lands_products_in_its_own_organization_only(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    other = create_organization(db_session)
    db_session.commit()
    # The header that selects organizations for users is ignored for keys.
    response = api.post(
        INGEST, json=batch([item("5_5")]), headers={**key, "X-Organization-Slug": other.slug}
    )
    assert response.status_code == 200
    assert product_count(db_session, organization.id) == 1
    assert product_count(db_session, other.id) == 0


# ------------------------------------------------------------------ auth and scope


def test_missing_invalid_and_unscoped_keys(
    api: TestClient, db_session: Session, organization
) -> None:
    payload = batch([item("1_1")])
    assert api.post(INGEST, json=payload).status_code == 401
    bad = api.post(INGEST, json=payload, headers={"Authorization": "Bearer afk_nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "authentication_required"

    unscoped, secret = identity_service.create_api_key(
        db_session, organization, name="health only", scopes=[], created_by=None
    )
    db_session.commit()
    headers = {"Authorization": f"Bearer {secret}"}
    denied = api.post(INGEST, json=payload, headers=headers)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "integration_scope_denied"
    assert denied.json()["error"]["details"] == {"required": "products:ingest", "scopes": []}
    assert api.get(f"{INGEST}/msb_anything", headers=headers).status_code == 403
    # The connectivity check still works for a scope-less key.
    assert api.get("/api/v1/integrations/me", headers=headers).status_code == 200

    identity_service.revoke_api_key(unscoped, revoked_by=None)
    db_session.commit()
    assert api.post(INGEST, json=payload, headers=headers).status_code == 401
    assert product_count(db_session, organization.id) == 0


# ------------------------------------------------------------------ validation


def test_envelope_errors_fail_the_whole_batch(api: TestClient, key: dict, db_session, organization):
    good = batch([item("1_1")])
    cases = [
        ({**good, "contract": "something.else"}, "contract"),
        ({**good, "version": "1.0.0"}, "version"),
        ({**good, "version": "2"}, "version"),
        ({**good, "batch_id": "short"}, "batch_id"),
        ({**good, "batch_id": "has spaces in it"}, "batch_id"),
        ({**good, "on_conflict": "overwrite"}, "on_conflict"),
        ({**good, "items": []}, "items"),
        ({**good, "items": [item("1_1"), item("1_1")]}, "client_ref"),
        ({**good, "items": [{"name": "no ref"}]}, "client_ref"),
        ({**good, "unexpected": 1}, "unexpected"),
        ({**good, "items": [item(str(i)) for i in range(201)]}, "items"),
    ]
    for payload, needle in cases:
        response = api.post(INGEST, json=payload, headers=key)
        assert response.status_code == 422, (needle, response.text)
        assert response.json()["error"]["code"] == "validation_error"
        assert needle in json.dumps(response.json()["error"]["details"]), needle
    not_json = api.post(
        INGEST, content=b"{not json", headers={**key, "Content-Type": "application/json"}
    )
    assert not_json.status_code == 422
    empty = api.post(INGEST, content=b"", headers={**key, "Content-Type": "application/json"})
    assert empty.status_code == 422
    assert product_count(db_session, organization.id) == 0
    assert batch_count(db_session, organization.id) == 0


def test_item_errors_reject_the_item_and_land_the_rest(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = batch(
        [
            item("ok_1", platform_product_id="10_1"),
            item("bad_url", product_url="javascript:alert(1)", platform_product_id=None),
            {
                "client_ref": "shopee:fp:9a",
                "name": "Fingerprint only",
                "affiliate_url": "https://s.shopee.vn/x",
            },
            item("percent", estimated_commission_rate="12.5"),
            item("blank_name", name="   "),
            item("huge_evidence", evidence={"blob": "x" * 9000}),
            item("extra_field", unknown_field="x"),
        ]
    )
    results = by_ref(api.post(INGEST, json=payload, headers=key))
    assert results["shopee:ok_1"]["status"] == "created"
    for ref, code, needle in (
        ("shopee:bad_url", "invalid_item", "http"),
        ("shopee:fp:9a", "no_canonical_identity", "platform_product_id or product_url"),
        ("shopee:percent", "invalid_item", "estimated_commission_rate"),
        ("shopee:blank_name", "invalid_item", "name"),
        ("shopee:huge_evidence", "invalid_item", "evidence"),
        ("shopee:extra_field", "invalid_item", "unknown_field"),
    ):
        assert results[ref]["status"] == "rejected", ref
        assert results[ref]["code"] == code, ref
        assert needle in (results[ref]["reason"] or ""), (ref, results[ref]["reason"])
        assert results[ref]["product_id"] is None
    assert product_count(db_session, organization.id) == 1
    stored = one_batch(db_session, organization.id)
    assert (stored.created_count, stored.rejected_count, stored.item_count) == (1, 6, 7)


def test_duplicate_candidates_within_a_batch(api: TestClient, key: dict, db_session, organization):
    payload = batch(
        [
            item("7_7"),
            item("7_7_again", platform_product_id="7_7", product_url="https://shopee.vn/other"),
            item(
                "url_twin",
                platform_product_id=None,
                product_url="HTTPS://SHOPEE.VN/product/7/7/",  # same normalized URL as 7_7
            ),
            item("8_8"),
        ]
    )
    results = by_ref(api.post(INGEST, json=payload, headers=key))
    assert results["shopee:7_7"]["status"] == "created"
    assert results["shopee:8_8"]["status"] == "created"
    for ref in ("shopee:7_7_again", "shopee:url_twin"):
        assert results[ref]["status"] == "rejected" and results[ref]["code"] == "duplicate_in_batch"
        assert "shopee:7_7" in results[ref]["reason"]
    assert product_count(db_session, organization.id) == 2


# ------------------------------------------------------------------ idempotency


def test_batch_replay_returns_stored_results_and_writes_nothing(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = batch([item("1_1"), item("fp", platform_product_id=None, product_url=None)])
    first = api.post(INGEST, json=payload, headers=key)
    assert first.status_code == 200 and first.json()["replayed"] is False
    replay = api.post(INGEST, json=payload, headers=key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["results"] == first.json()["results"]
    assert replay.json()["id"] == first.json()["id"]
    assert product_count(db_session, organization.id) == 1
    assert batch_count(db_session, organization.id) == 1
    assert status_event_count(db_session, organization.id) == 1

    # The stored results are also readable on their own.
    fetched = api.get(f"{INGEST}/{payload['batch_id']}", headers=key)
    assert fetched.status_code == 200 and fetched.json()["results"] == first.json()["results"]
    assert api.get(f"{INGEST}/msb_unknown", headers=key).status_code == 404

    # Same batch id, different content: refused, nothing written.
    changed = {**payload, "items": [item("1_1", observed_price="1")]}
    mismatch = api.post(INGEST, json=changed, headers=key)
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "batch_mismatch"
    # A different on_conflict is a different payload too.
    assert (
        api.post(INGEST, json={**payload, "on_conflict": "replace"}, headers=key).status_code == 409
    )
    assert batch_count(db_session, organization.id) == 1
    replays = audit_events(db_session, organization.id, "product.ingest_replayed")
    assert len(replays) == 1 and replays[0].data["batch_id"] == payload["batch_id"]


def test_resend_in_a_new_batch_is_unchanged_then_updated(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    api.post(INGEST, json=batch([item("1_1")]), headers=key)
    again = by_ref(api.post(INGEST, json=batch([item("1_1")]), headers=key))
    assert (
        again["shopee:1_1"]["status"] == "unchanged" and again["shopee:1_1"]["changed_fields"] == []
    )
    assert product_count(db_session, organization.id) == 1

    refreshed = by_ref(
        api.post(
            INGEST,
            json=batch(
                [
                    item(
                        "1_1",
                        observed_price="219000",
                        research_notes="MeoScore 85 TAKE",
                        seller_name=None,  # empty incoming values never erase stored ones
                        category="",
                        estimated_commission_rate="0.1250",  # same value, different spelling
                    )
                ]
            ),
            headers=key,
        )
    )
    result = refreshed["shopee:1_1"]
    assert result["status"] == "updated"
    assert sorted(result["changed_fields"]) == ["observed_price", "research_notes"]
    product = db_session.get(Product, uuid.UUID(result["product_id"]))
    db_session.refresh(product)
    assert product.observed_price == Decimal("219000")
    assert product.research_notes == "MeoScore 85 TAKE"
    assert product.seller_name == "KRISTY Official" and product.category == "Women's Fashion"
    assert product.status == ProductStatus.DISCOVERED
    # Updates leave the creating batch and the status trail alone.
    assert status_event_count(db_session, organization.id) == 1
    assert product.ingest_batch_id is not None
    first_batch = db_session.scalar(
        select(ProductIngestBatch)
        .where(ProductIngestBatch.organization_id == organization.id)
        .order_by(ProductIngestBatch.created_at)
    )
    assert product.ingest_batch_id == first_batch.id


def test_item_level_retry_after_partial_batch(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    first = by_ref(
        api.post(
            INGEST,
            json=batch(
                [item("1_1"), item("bad", platform_product_id="12_12", product_url="ftp://x")]
            ),
            headers=key,
        )
    )
    assert first["shopee:1_1"]["status"] == "created"
    assert first["shopee:bad"]["status"] == "rejected"
    # The client fixes the rejected item and resends only it, under a new batch id.
    retry = by_ref(
        api.post(INGEST, json=batch([item("bad", platform_product_id="12_12")]), headers=key)
    )
    assert retry["shopee:bad"]["status"] == "created"
    assert product_count(db_session, organization.id) == 2


# ------------------------------------------------------------------ conflicts and ownership


def test_affiliate_url_conflict_is_kept_by_default_and_replaced_on_request(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    product = service.create_product(
        db_session,
        organization.id,
        ProductCreate(
            name="Hand-entered",
            platform_product_id="9_9",
            affiliate_url="https://s.shopee.vn/operator-link",
        ),
    )
    db_session.commit()
    conflict = by_ref(api.post(INGEST, json=batch([item("9_9")]), headers=key))["shopee:9_9"]
    assert conflict["status"] == "conflict" and conflict["code"] == "affiliate_url_differs"
    assert conflict["product_id"] == str(product.id)
    assert conflict["conflicts"] == [
        {"field": "affiliate_url", "code": "affiliate_url_differs", "resolution": "kept"}
    ]
    assert "kept" in conflict["reason"]
    # Research fields still landed alongside the kept link; product_url was enriched.
    assert "product_url" in conflict["changed_fields"]
    assert "seller_name" in conflict["changed_fields"]
    db_session.refresh(product)
    assert product.affiliate_url == "https://s.shopee.vn/operator-link"
    assert product.source == "manual" and product.source_ref == "shopee:9_9"
    audited = audit_events(db_session, organization.id, "product.ingest_conflict")
    assert len(audited) == 1 and audited[0].entity_id == product.id
    assert audited[0].data["code"] == "affiliate_url_differs"

    replaced = by_ref(
        api.post(INGEST, json=batch([item("9_9")], on_conflict="replace"), headers=key)
    )["shopee:9_9"]
    assert replaced["status"] == "updated" and replaced["changed_fields"] == ["affiliate_url"]
    assert replaced["conflicts"] == [
        {"field": "affiliate_url", "code": "affiliate_url_differs", "resolution": "replaced"}
    ]
    db_session.refresh(product)
    assert product.affiliate_url == "https://s.shopee.vn/9_9"
    # Nothing left to replace: unchanged, even with replace.
    again = by_ref(api.post(INGEST, json=batch([item("9_9")], on_conflict="replace"), headers=key))
    assert again["shopee:9_9"]["status"] == "unchanged"


def test_identity_conflicts_never_change_stored_identity(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    api.post(INGEST, json=batch([item("1_1")]), headers=key)  # pid 1_1, url …/1/1
    # Same id, different URL → conflict, URL kept.
    moved = by_ref(
        api.post(
            INGEST, json=batch([item("1_1", product_url="https://shopee.vn/moved")]), headers=key
        )
    )["shopee:1_1"]
    assert moved["status"] == "conflict" and moved["code"] == "product_url_differs"
    product = db_session.get(Product, uuid.UUID(moved["product_id"]))
    assert product.product_url == "https://shopee.vn/product/1/1"

    # URL-only product gets its id enriched once, then a different id is a conflict.
    url_only = service.create_product(
        db_session, organization.id, ProductCreate(name="By URL", product_url="https://shopee.vn/u")
    )
    db_session.commit()
    enriched = by_ref(
        api.post(
            INGEST,
            json=batch([item("u_1", platform_product_id="2_2", product_url="https://shopee.vn/u")]),
            headers=key,
        )
    )["shopee:u_1"]
    assert enriched["status"] == "updated" and "platform_product_id" in enriched["changed_fields"]
    db_session.refresh(url_only)
    assert url_only.platform_product_id == "2_2"
    differs = by_ref(
        api.post(
            INGEST,
            json=batch([item("u_2", platform_product_id="3_3", product_url="https://shopee.vn/u")]),
            headers=key,
        )
    )["shopee:u_2"]
    assert differs["status"] == "conflict" and differs["code"] == "identity_differs"
    db_session.refresh(url_only)
    assert url_only.platform_product_id == "2_2"

    # Id of product A with the URL of product B → conflict on A, no merge, no duplicate.
    two_in_one = by_ref(
        api.post(
            INGEST,
            json=batch([item("mix", platform_product_id="1_1", product_url="https://shopee.vn/u")]),
            headers=key,
        )
    )["shopee:mix"]
    assert two_in_one["status"] == "conflict" and two_in_one["code"] == "identity_differs"
    assert two_in_one["product_id"] == str(product.id)
    assert product_count(db_session, organization.id) == 2


def test_affilio_owned_fields_are_preserved(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    created = by_ref(api.post(INGEST, json=batch([item("1_1")]), headers=key))["shopee:1_1"]
    product_id = uuid.UUID(created["product_id"])
    service.change_status(
        db_session,
        organization.id,
        product_id,
        ProductStatusChange(status=ProductStatus.SHORTLISTED, note="operator decision"),
        actor="owner@test",
    )
    db_session.commit()
    product = db_session.get(Product, product_id)
    status_changed_at = product.status_changed_at
    events_before = status_event_count(db_session, organization.id)

    result = by_ref(
        api.post(INGEST, json=batch([item("1_1", observed_price="100000")]), headers=key)
    )["shopee:1_1"]
    assert result["status"] == "updated" and result["product_status"] == "shortlisted"
    db_session.refresh(product)
    assert product.status == ProductStatus.SHORTLISTED
    assert product.status_changed_at == status_changed_at
    assert status_event_count(db_session, organization.id) == events_before
    assert purchase_requests(db_session, organization.id) == 0
    assert product.observed_price == Decimal("100000")


# ------------------------------------------------------------------ bounds


def test_rate_limit_per_key(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    for name, value in media_dirs.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("INGEST_MAX_BATCHES_PER_WINDOW", "2")
    monkeypatch.setenv("INGEST_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()
    _, secret = identity_service.create_api_key(
        db_session, organization, name="k1", scopes=["products:ingest"], created_by=None
    )
    _, other_secret = identity_service.create_api_key(
        db_session, organization, name="k2", scopes=["products:ingest"], created_by=None
    )
    db_session.commit()
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.post(INGEST, json=batch([item("1_1")]), headers=headers).status_code == 200
        assert client.post(INGEST, json=batch([item("2_2")]), headers=headers).status_code == 200
        blocked = client.post(INGEST, json=batch([item("3_3")]), headers=headers)
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["error"]["code"] == "too_many_requests"
        assert int(blocked.headers["Retry-After"]) >= 1
        # Per credential: another key is unaffected.
        other = client.post(
            INGEST, json=batch([item("3_3")]), headers={"Authorization": f"Bearer {other_secret}"}
        )
        assert other.status_code == 200
    dispose_engine()
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()
    assert product_count(db_session, organization.id) == 3


def test_payload_too_large_is_refused_before_parsing(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    for name, value in media_dirs.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("INGEST_MAX_BODY_BYTES", "600")
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()
    _, secret = identity_service.create_api_key(
        db_session, organization, name="k", scopes=["products:ingest"], created_by=None
    )
    db_session.commit()
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        big = client.post(INGEST, json=batch([item("1_1"), item("2_2")]), headers=headers)
        assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"
        small = client.post(
            INGEST,
            json=batch([{"client_ref": "s", "name": "Small", "platform_product_id": "1_1"}]),
            headers=headers,
        )
        assert small.status_code == 200, small.text
    dispose_engine()
    get_settings.cache_clear()
    ingest.reset_ingest_limiter()


# ------------------------------------------------------------------ failure and concurrency


def test_failed_batch_is_recorded_and_the_retry_reruns_it(
    api: TestClient, key: dict, db_session: Session, organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ingest._store_items
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "disk on fire at /volume1/docker/affilio/secret-token=abc"
            raise RuntimeError(msg)
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest, "_store_items", boom)
    payload = batch([item("1_1")])
    failed = api.post(INGEST, json=payload, headers=key)
    assert failed.status_code == 500
    assert product_count(db_session, organization.id) == 0  # rolled back
    row = one_batch(db_session, organization.id)
    assert row.status == "failed" and row.item_count == 1
    assert "RuntimeError" in (row.error or "") and "/volume1" not in row.error
    assert "abc" not in row.error
    assert len(audit_events(db_session, organization.id, "product.ingest_failed")) == 1

    retried = api.post(INGEST, json=payload, headers=key)
    assert retried.status_code == 200 and retried.json()["replayed"] is False
    assert by_ref(retried)["shopee:1_1"]["status"] == "created"
    db_session.expire_all()
    assert one_batch(db_session, organization.id).status == "completed"


def test_concurrent_duplicate_submissions_create_one_product(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = batch([item("1_1"), item("2_2")])

    def send(_: int):
        return api.post(INGEST, json=payload, headers=key)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(4)))
    codes = sorted(r.status_code for r in responses)
    assert all(c in (200, 409) for c in codes), codes
    ok = [r for r in responses if r.status_code == 200]
    assert ok, codes
    assert sum(1 for r in ok if r.json()["replayed"] is False) == 1
    for r in ok:
        assert r.json()["results"] == ok[0].json()["results"]
    for r in responses:
        if r.status_code == 409:
            assert r.json()["error"]["code"] == "batch_in_progress"
    assert product_count(db_session, organization.id) == 2
    assert batch_count(db_session, organization.id) == 1

    # Different batches racing on the same product: one creates, the others update/unchanged.
    def send_new(i: int):
        return api.post(INGEST, json=batch([item("3_3")]), headers=key)

    with ThreadPoolExecutor(max_workers=4) as pool:
        racing = list(pool.map(send_new, range(4)))
    assert all(r.status_code == 200 for r in racing), [r.text for r in racing]
    statuses = sorted(by_ref(r)["shopee:3_3"]["status"] for r in racing)
    assert statuses.count("created") == 1 and set(statuses) <= {"created", "unchanged"}
    assert product_count(db_session, organization.id) == 3
