"""M3.2-B.2 P1.1: cross-repository contract, replay, ownership and identity safety for
`POST /products/ingest`. The golden payload is MeoScout's own fixture (`fixtures/
meoscout_handoff_v2.json`, copied byte-for-byte from MeoScout 8bc741d). Needs TEST_DATABASE_URL."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from affilio.core.enums import ProductStatus
from affilio.db.base import Base
from affilio.modules.content import service as content_service
from affilio.modules.content.schemas import AffiliateLinkCreate
from affilio.modules.media.models import MediaAsset
from affilio.modules.procurement import service as procurement_service
from affilio.modules.procurement.schemas import PurchaseRequestCreate
from affilio.modules.production import service as production_service
from affilio.modules.production.schemas import ProductionJobCreate
from affilio.modules.products import imports, ingest, service
from affilio.modules.products.models import Product, ProductIngestItem, ProductMediaLink
from affilio.modules.products.schemas import ProductCreate, ProductStatusChange, ProductUpdate
from tests import test_products_ingest as base
from tests.test_products_ingest import INGEST, batch_count, by_ref, product_count

pytestmark = pytest.mark.database

# Shared fixtures: the app client and a `products:ingest` key.
api = base.api
key = base.key

FIXTURE = Path(__file__).parent / "fixtures" / "meoscout_handoff_v2.json"
GOLDEN_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
GOLDEN: dict = GOLDEN_DOC["payload"]
REFS = [i["client_ref"] for i in GOLDEN["items"]]
SHIRT, JOGGER, BOTTLE = REFS  # fixture order (MeoScout sorts by item key)


def golden(**changes) -> dict:
    payload = copy.deepcopy(GOLDEN)
    payload.update(changes)
    return payload


def fresh_id() -> str:
    return f"msb_{uuid.uuid4().hex}"


def products_by_ref(session: Session, organization_id: uuid.UUID) -> dict[str, Product]:
    session.expire_all()
    return {
        p.source_ref or "": p
        for p in session.scalars(select(Product).where(Product.organization_id == organization_id))
    }


# ------------------------------------------------------------------ task 1: the golden payload


def test_fixture_is_meoscouts_golden_payload() -> None:
    """The copy matches MeoScout's fixture when the sibling checkout is available."""
    source = GOLDEN_DOC["_source"]
    assert source["commit"] == "8bc741d" and source["pointer"] == "/expected/payload"
    repo = Path(os.environ.get("MEOSCOUT_REPO", Path(__file__).parents[4] / "meoscout"))
    original = repo / source["path"]
    if not original.is_file():
        pytest.skip("MeoScout checkout not available; the vendored copy is authoritative here")
    raw = original.read_bytes()
    current = json.loads(raw)["expected"]["payload"]
    if hashlib.sha256(raw).hexdigest() != source["file_sha256"]:
        # MeoScout moved on: the payload itself must still be the one we test.
        assert current == GOLDEN, "MeoScout's golden payload changed; re-vendor and re-verify"
    else:
        assert current == GOLDEN


def test_exact_golden_payload_is_accepted_and_mapped(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    response = api.post(INGEST, content=json.dumps(GOLDEN, ensure_ascii=False), headers=key)
    assert response.status_code == 200, response.text
    body = response.json()

    # Response: every field MeoScout's AffilioIngestResponse reads, with the same meaning.
    assert body["batch_id"] == GOLDEN["batch_id"] and body["replayed"] is False
    assert body["contract"] == "meoscout.affilio.candidates" and body["version"] == "2.0.0"
    assert datetime.fromisoformat(body["received_at"]).tzinfo is not None
    assert body["counts"] == {
        "created": 3,
        "updated": 0,
        "unchanged": 0,
        "conflict": 0,
        "rejected": 0,
    }
    assert [r["client_ref"] for r in body["results"]] == REFS  # request order, refs verbatim
    for result in body["results"]:
        assert result["status"] == "created" and result["product_status"] == "discovered"
        assert uuid.UUID(result["product_id"])
        assert result["code"] is None and result["reason"] is None
        assert result["changed_fields"] == [] and result["preserved_fields"] == []

    products = products_by_ref(db_session, organization.id)
    assert set(products) == set(REFS)
    by_item = {i["client_ref"]: i for i in GOLDEN["items"]}
    for ref, product in products.items():
        sent = by_item[ref]
        assert product.name == sent["name"] and product.platform == "shopee"
        assert product.status == ProductStatus.DISCOVERED and product.source == "meoscout"
        assert product.product_url == sent["product_url"]  # stored as sent
        assert product.affiliate_url == sent["affiliate_url"]
        assert product.images == [sent["image_url"]]  # image_url → images[0]
        assert product.currency == "VND"
        assert product.observed_price == Decimal(sent["observed_price"])
        assert product.estimated_commission_rate == Decimal(sent["estimated_commission_rate"])
        assert product.estimated_commission_amount == Decimal(sent["estimated_commission_amount"])
        assert product.research_notes == sent["research_notes"]
        assert product.category == sent["category"]

    shirt, jogger, bottle = products[SHIRT], products[JOGGER], products[BOTTLE]
    assert (
        shirt.platform_product_id == "987654_123456789" and shirt.seller_name == "KRISTY Official"
    )
    assert jogger.platform_product_id == "111_222"
    # Commission is a fraction on the wire and in storage (12.5 % → 0.125, 8 % → 0.08).
    assert (shirt.estimated_commission_rate, jogger.estimated_commission_rate) == (
        Decimal("0.125"),
        Decimal("0.08"),
    )
    # URL identity: the URL states only the item id, so no platform id is invented.
    assert bottle.platform_product_id is None
    assert bottle.product_url_normalized == "https://shopee.vn/some-page?itemid=55667788&x=1"

    # Evidence is kept verbatim on the ingest item (nested meoscore, nulls, ISO dates).
    stored = {
        i.client_ref: i.evidence
        for i in db_session.scalars(
            select(ProductIngestItem).where(
                ProductIngestItem.ingest_batch_id == uuid.UUID(body["id"])
            )
        )
    }
    assert stored == {ref: by_item[ref]["evidence"] for ref in REFS}
    assert max(len(json.dumps(e).encode()) for e in stored.values()) < 1024


def test_golden_payload_resent_in_a_new_batch_is_unchanged_then_updated(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    by_ref(api.post(INGEST, json=GOLDEN, headers=key))
    again = by_ref(api.post(INGEST, json=golden(batch_id=fresh_id()), headers=key))
    assert {r: again[r]["status"] for r in REFS} == dict.fromkeys(REFS, "unchanged")

    changed = golden(batch_id=fresh_id())
    changed["items"][0]["observed_price"] = "219000"
    changed["items"][0]["research_notes"] = "MeoScore 85 TAKE"
    result = by_ref(api.post(INGEST, json=changed, headers=key))
    assert result[SHIRT]["status"] == "updated"
    assert sorted(result[SHIRT]["changed_fields"]) == ["observed_price", "research_notes"]
    assert result[JOGGER]["status"] == "unchanged" and result[BOTTLE]["status"] == "unchanged"
    assert product_count(db_session, organization.id) == 3


def test_commission_rate_beyond_storage_precision_is_rounded_not_rejected(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = golden(batch_id=fresh_id())
    payload["items"][0]["estimated_commission_rate"] = "0.12345"  # MeoScout: 12.345 %
    payload["items"][1]["estimated_commission_rate"] = "0.021249"
    results = by_ref(api.post(INGEST, json=payload, headers=key))
    assert results[SHIRT]["status"] == "created" and results[JOGGER]["status"] == "created"
    products = products_by_ref(db_session, organization.id)
    assert products[SHIRT].estimated_commission_rate == Decimal("0.1235")
    assert products[JOGGER].estimated_commission_rate == Decimal("0.0212")
    # Still a fraction: a percent is refused, not divided.
    bad = golden(batch_id=fresh_id())
    bad["items"][0]["estimated_commission_rate"] = "12.5"
    assert by_ref(api.post(INGEST, json=bad, headers=key))[SHIRT]["code"] == "invalid_item"


@pytest.mark.parametrize("version", ["2.1.0", "3.0.0", "2.0"])
def test_unknown_contract_versions_fail_the_batch(
    api: TestClient, key: dict, db_session: Session, organization, version: str
) -> None:
    response = api.post(INGEST, json=golden(version=version), headers=key)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert batch_count(db_session, organization.id) == 0


def test_patch_versions_of_the_pinned_minor_are_accepted(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    response = api.post(INGEST, json=golden(version="2.0.7"), headers=key)
    assert response.status_code == 200 and response.json()["version"] == "2.0.7"


# ------------------------------------------------------------------ task 2: replay safety


def test_retry_with_a_new_sent_at_is_a_replay(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    first = api.post(INGEST, json=GOLDEN, headers=key)
    assert first.status_code == 200
    for variant in (
        golden(sent_at="2027-01-16T08:00:05.000Z"),  # the relay's next attempt
        golden(sent_at=None),
        golden(source={"tool": "meoscout", "build": "next", "extension_version": "0.2.0"}),
    ):
        replay = api.post(INGEST, json=variant, headers=key)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["results"] == first.json()["results"]
        assert replay.json()["id"] == first.json()["id"]
    # Key order and whitespace do not matter either: the fingerprint is over parsed JSON.
    reordered = {k: GOLDEN[k] for k in reversed(list(GOLDEN))}
    reordered["items"] = [{k: i[k] for k in sorted(i, reverse=True)} for i in GOLDEN["items"]]
    raw = json.dumps(reordered, indent=3, ensure_ascii=True)
    assert api.post(INGEST, content=raw, headers=key).json()["replayed"] is True
    assert product_count(db_session, organization.id) == 3
    assert batch_count(db_session, organization.id) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["items"][0].update(research_notes="MeoScore 90 TAKE"),
        lambda p: p["items"][1]["evidence"].update(approved_at="2027-01-16T00:00:00.000Z"),
        lambda p: p["items"][2].update(observed_price="98000"),
        lambda p: p["items"].pop(),
        lambda p: p.update(on_conflict="replace"),
    ],
    ids=["notes", "evidence", "price", "fewer-items", "on_conflict"],
)
def test_same_batch_id_with_different_content_is_a_mismatch(
    api: TestClient, key: dict, db_session: Session, organization, mutate
) -> None:
    assert api.post(INGEST, json=GOLDEN, headers=key).status_code == 200
    before = {r: p.updated_at for r, p in products_by_ref(db_session, organization.id).items()}
    changed = golden(sent_at="2027-01-16T09:00:00.000Z")
    mutate(changed)
    response = api.post(INGEST, json=changed, headers=key)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "batch_mismatch"
    after = {r: p.updated_at for r, p in products_by_ref(db_session, organization.id).items()}
    assert after == before and batch_count(db_session, organization.id) == 1


def test_failed_batch_retried_with_a_new_sent_at_reruns(
    api: TestClient, key: dict, db_session: Session, organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ingest._store_items
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "transient"
            raise RuntimeError(msg)
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest, "_store_items", flaky)
    assert api.post(INGEST, json=GOLDEN, headers=key).status_code == 500
    assert product_count(db_session, organization.id) == 0
    retry = api.post(INGEST, json=golden(sent_at="2027-01-16T08:00:02.000Z"), headers=key)
    assert retry.status_code == 200 and retry.json()["replayed"] is False
    assert retry.json()["counts"]["created"] == 3
    # And now it is a normal replay.
    assert api.post(INGEST, json=GOLDEN, headers=key).json()["replayed"] is True


def test_partial_batch_then_item_level_retry(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = golden(batch_id=fresh_id())
    payload["items"][1]["product_url"] = "https://shopee.vn/Quan-jogger-i.111.999"  # wrong item
    first = by_ref(api.post(INGEST, json=payload, headers=key))
    assert first[SHIRT]["status"] == "created" and first[BOTTLE]["status"] == "created"
    assert first[JOGGER]["status"] == "rejected" and first[JOGGER]["code"] == "identity_mismatch"
    assert first[JOGGER]["product_id"] is None
    # MeoScout fixes the item and resends the whole selection: only the missing one is created.
    retry = by_ref(api.post(INGEST, json=golden(batch_id=fresh_id()), headers=key))
    assert retry[JOGGER]["status"] == "created"
    assert retry[SHIRT]["status"] == "unchanged" and retry[BOTTLE]["status"] == "unchanged"
    # Or resends just that item.
    single = golden(batch_id=fresh_id())
    single["items"] = [single["items"][1]]
    assert by_ref(api.post(INGEST, json=single, headers=key))[JOGGER]["status"] == "unchanged"
    assert product_count(db_session, organization.id) == 3


def test_concurrent_golden_submissions_with_different_sent_at(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    def send(i: int):
        return api.post(INGEST, json=golden(sent_at=f"2027-01-16T08:00:0{i}.000Z"), headers=key)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(4)))
    codes = [r.status_code for r in responses]
    assert set(codes) <= {200, 409}, codes
    ok = [r for r in responses if r.status_code == 200]
    assert sum(1 for r in ok if not r.json()["replayed"]) == 1
    for r in responses:
        if r.status_code == 409:
            assert r.json()["error"]["code"] == "batch_in_progress"
    assert product_count(db_session, organization.id) == 3
    assert batch_count(db_session, organization.id) == 1


# ------------------------------------------------------------------ task 3: data ownership


def test_human_edited_research_fields_are_preserved(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    created = by_ref(api.post(INGEST, json=GOLDEN, headers=key))
    shirt_id = uuid.UUID(created[SHIRT]["product_id"])
    service.update_product(
        db_session,
        organization.id,
        shirt_id,
        ProductUpdate(
            name="Áo sơ mi KRISTY (tên do biên tập đặt)",
            research_notes="Editor: test with the white variant first.",
            category="Thời trang nữ",
            images=["https://cf.shopee.vn/file/curated-1.webp"],
        ),
    )
    db_session.commit()

    refreshed = golden(batch_id=fresh_id())
    shirt = refreshed["items"][0]
    shirt.update(
        name="Áo sơ mi nữ công sở tay dài 2027",
        research_notes="MeoScore 88 TAKE",
        category="Women's Fashion",
        image_url="https://cf.shopee.vn/file/new_tn.webp",
        observed_price="219000",  # untouched by the editor: still MeoScout's value
        seller_name="KRISTY Official Store",
    )
    result = by_ref(api.post(INGEST, json=refreshed, headers=key))[SHIRT]
    assert result["status"] == "updated"
    assert sorted(result["changed_fields"]) == ["observed_price", "seller_name"]
    assert sorted(result["preserved_fields"]) == ["category", "images", "name", "research_notes"]
    assert result["conflicts"] == []

    product = db_session.get(Product, shirt_id)
    db_session.refresh(product)
    assert product.name == "Áo sơ mi KRISTY (tên do biên tập đặt)"
    assert product.research_notes == "Editor: test with the white variant first."
    assert product.category == "Thời trang nữ"
    assert product.images == ["https://cf.shopee.vn/file/curated-1.webp"]
    assert product.observed_price == Decimal("219000")
    assert product.seller_name == "KRISTY Official Store"

    # The editor's ownership sticks: later batches keep preserving, and say so on replay too.
    later = golden(batch_id=fresh_id())
    later["items"][0]["name"] = "Yet another title"
    response = api.post(INGEST, json=later, headers=key)
    assert "name" in by_ref(response)[SHIRT]["preserved_fields"]
    replay = api.post(INGEST, json=later, headers=key)
    assert replay.json()["replayed"] is True
    assert replay.json()["results"] == response.json()["results"]


def test_products_ingest_did_not_create_are_fill_only(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    manual = service.create_product(
        db_session,
        organization.id,
        ProductCreate(
            name="Áo sơ mi (nhập tay)",
            platform_product_id="987654_123456789",
            observed_price=Decimal("250000"),
            currency="VND",
            research_notes="Operator notes",
        ),
    )
    db_session.commit()
    result = by_ref(api.post(INGEST, json=GOLDEN, headers=key))[SHIRT]
    assert result["status"] == "updated" and result["product_id"] == str(manual.id)
    # Empty fields are filled; every value the operator set is kept.
    assert sorted(result["preserved_fields"]) == ["name", "observed_price", "research_notes"]
    assert {"seller_name", "category", "images", "affiliate_url", "product_url"} <= set(
        result["changed_fields"]
    )
    db_session.refresh(manual)
    assert manual.name == "Áo sơ mi (nhập tay)" and manual.observed_price == Decimal("250000")
    assert manual.research_notes == "Operator notes" and manual.source == "manual"
    assert manual.seller_name == "KRISTY Official"


def test_csv_imported_values_are_owned_by_affilio(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    csv_text = (
        "name,product_url,platform_product_id,price,commission\n"
        "CSV shirt,https://shopee.vn/product/987654/123456789,987654_123456789,245000,10\n"
    )
    parsed = imports.parse_csv(csv_text.encode(), file_name="meoscout.csv")
    mapping = imports.suggest_mapping(parsed.headers)
    plan = imports.plan_import(db_session, organization.id, parsed, mapping, on_duplicate="skip")
    imports.apply_import(
        db_session, organization.id, plan, file_name="meoscout.csv", on_duplicate="skip"
    )
    db_session.commit()
    result = by_ref(api.post(INGEST, json=GOLDEN, headers=key))[SHIRT]
    assert result["status"] == "updated"
    assert {"name", "observed_price", "estimated_commission_rate"} <= set(
        result["preserved_fields"]
    )
    product = products_by_ref(db_session, organization.id)
    shirt = next(p for p in product.values() if p.platform_product_id == "987654_123456789")
    assert shirt.name == "CSV shirt" and shirt.estimated_commission_rate == Decimal("0.1")


def _rows_outside_ingest(session: Session) -> dict[str, list]:
    """Every row of every table ingest must not write, as plain tuples."""
    allowed = {
        "products",
        "product_status_events",
        "product_ingest_batches",
        "product_ingest_items",
        "audit_events",
        "integration_api_keys",  # last_used_at
        "auth_sessions",
    }
    snapshot: dict[str, list] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in allowed:
            continue
        query = f'SELECT * FROM "{table.name}" ORDER BY 1'  # noqa: S608 - names from ORM metadata
        rows = session.execute(text(query)).all()
        snapshot[table.name] = [tuple(r) for r in rows]
    return snapshot


def test_affilio_owned_data_is_never_touched(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    created = by_ref(api.post(INGEST, json=GOLDEN, headers=key))
    shirt_id = uuid.UUID(created[SHIRT]["product_id"])
    # Lifecycle, procurement, production, links, media: everything a person does after intake.
    service.change_status(
        db_session,
        organization.id,
        shirt_id,
        ProductStatusChange(status=ProductStatus.SHORTLISTED, note="operator"),
        actor="owner@test",
    )
    procurement_service.create_request(
        db_session, organization.id, PurchaseRequestCreate(product_id=shirt_id, quantity=2)
    )
    production_service.create_job(
        db_session, organization.id, ProductionJobCreate(title="Shoot", product_ids=[shirt_id])
    )
    content_service.create_link(
        db_session,
        organization.id,
        AffiliateLinkCreate(product_id=shirt_id, label="Bio link", url="https://s.shopee.vn/bio"),
        actor="owner@test",
    )
    now = datetime.now(UTC)
    asset = MediaAsset(
        id=uuid.uuid4(),
        organization_id=organization.id,
        title="final",
        status="active",
        asset_kind="final",
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        ProductMediaLink(
            id=uuid.uuid4(),
            organization_id=organization.id,
            product_id=shirt_id,
            media_asset_id=asset.id,
            is_primary=True,
            notes="hero",
        )
    )
    db_session.commit()
    product = db_session.get(Product, shirt_id)
    status_changed_at, events = product.status_changed_at, len(product.status_events)
    before = _rows_outside_ingest(db_session)

    changed = golden(batch_id=fresh_id(), on_conflict="keep")
    for candidate in changed["items"]:
        candidate.update(observed_price="1000", affiliate_url="https://s.shopee.vn/other")
    results = by_ref(api.post(INGEST, json=changed, headers=key))
    assert {r["status"] for r in results.values()} == {"conflict"}  # links kept

    db_session.expire_all()
    assert _rows_outside_ingest(db_session) == before
    product = db_session.get(Product, shirt_id)
    assert product.status == ProductStatus.SHORTLISTED
    assert product.status_changed_at == status_changed_at
    assert len(product.status_events) == events
    assert product.affiliate_url == "https://s.shopee.vn/AB1cdE"


# ------------------------------------------------------------------ task 4: identity safety


@pytest.mark.parametrize(
    ("pid", "url", "code"),
    [
        ("987654_123456789", "https://shopee.vn/product/987654/999", "identity_mismatch"),
        ("987654_123456789", "https://shopee.vn/product/111/123456789", "identity_mismatch"),
        ("987654_123456789", "https://shopee.vn/Ao-so-mi-i.987654.1", "identity_mismatch"),
        ("987654_123456789", "https://shopee.vn/x?itemid=5", "identity_mismatch"),
        (
            "987654_123456789",
            "https://shopee.vn/a-i.987654.123456789?itemid=7",
            "identity_mismatch",
        ),
        ("123456789", "https://shopee.vn/product/987654/123456789", "invalid_item"),
        ("abc_def", None, "invalid_item"),
        ("987654_123456789", "https://lazada.vn/product/987654/123456789", "invalid_item"),
        ("987654_123456789", "https://shopee.vn.evil.example/x", "invalid_item"),
    ],
)
def test_identity_mismatches_are_rejected_and_write_nothing(
    api: TestClient, key: dict, db_session: Session, organization, pid, url, code
) -> None:
    payload = golden(batch_id=fresh_id())
    payload["items"] = [payload["items"][0]]
    payload["items"][0].update(platform_product_id=pid, product_url=url)
    result = by_ref(api.post(INGEST, json=payload, headers=key))[SHIRT]
    assert result["status"] == "rejected" and result["code"] == code, result
    assert result["product_id"] is None
    assert product_count(db_session, organization.id) == 0


def test_consistent_identity_forms_are_accepted(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = golden(batch_id=fresh_id())
    payload["items"][0]["product_url"] = "https://shopee.vn/x?shopid=987654&itemid=123456789"
    payload["items"][1]["product_url"] = "https://SHOPEE.vn/Quan-jogger-i.111.222/"
    results = by_ref(api.post(INGEST, json=payload, headers=key))
    assert {r["status"] for r in results.values()} == {"created"}


def test_url_stating_both_ids_supplies_the_platform_id_and_prevents_duplicates(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    url_only = golden(batch_id=fresh_id())
    url_only["items"] = [url_only["items"][1]]
    url_only["items"][0].pop("platform_product_id")  # URL …-i.111.222 only
    created = by_ref(api.post(INGEST, json=url_only, headers=key))[JOGGER]
    assert created["status"] == "created"
    product = db_session.get(Product, uuid.UUID(created["product_id"]))
    assert product.platform_product_id == "111_222"

    # The same product later arrives with its id and the other URL form: matched, not duplicated.
    other_form = golden(batch_id=fresh_id())
    other_form["items"] = [other_form["items"][1]]
    other_form["items"][0]["product_url"] = "https://shopee.vn/product/111/222"
    matched = by_ref(api.post(INGEST, json=other_form, headers=key))[JOGGER]
    assert matched["product_id"] == created["product_id"]
    assert matched["status"] == "conflict" and matched["code"] == "product_url_differs"
    assert product_count(db_session, organization.id) == 1


def test_manual_product_known_only_by_url_is_matched_by_the_ids_it_states(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    manual = service.create_product(
        db_session,
        organization.id,
        ProductCreate(name="Jogger", product_url="https://shopee.vn/Quan-jogger-i.111.222"),
    )
    db_session.commit()
    payload = golden(batch_id=fresh_id())
    payload["items"] = [payload["items"][1]]
    payload["items"][0]["product_url"] = "https://shopee.vn/product/111/222"
    result = by_ref(api.post(INGEST, json=payload, headers=key))[JOGGER]
    assert result["product_id"] == str(manual.id)
    assert "platform_product_id" in result["changed_fields"]
    assert result["code"] == "product_url_differs"  # the stored URL form is kept
    db_session.refresh(manual)
    assert manual.platform_product_id == "111_222"
    assert manual.product_url == "https://shopee.vn/Quan-jogger-i.111.222"
    assert product_count(db_session, organization.id) == 1


def test_weaker_identities_are_ambiguous_conflicts_not_merges(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    # A product stored with a bare item id (generic CSV) and one known by a full id.
    bare = service.create_product(
        db_session, organization.id, ProductCreate(name="Bare id", platform_product_id="123456789")
    )
    full = service.create_product(
        db_session,
        organization.id,
        ProductCreate(name="Full id", platform_product_id="4242_55667788"),
    )
    db_session.commit()
    before = {p.id: (p.name, p.updated_at) for p in (bare, full)}

    results = by_ref(api.post(INGEST, json=golden(batch_id=fresh_id()), headers=key))
    shirt, bottle = results[SHIRT], results[BOTTLE]
    assert shirt["status"] == "conflict" and shirt["code"] == "identity_ambiguous"
    assert shirt["product_id"] == str(bare.id) and shirt["changed_fields"] == []
    # The bottle's URL states only item 55667788; a product with that item id exists.
    assert bottle["status"] == "conflict" and bottle["code"] == "identity_ambiguous"
    assert bottle["product_id"] == str(full.id)
    assert results[JOGGER]["status"] == "created"
    db_session.expire_all()
    assert {p.id: (p.name, p.updated_at) for p in (bare, full)} == before
    assert product_count(db_session, organization.id) == 3


def test_ambiguity_is_detected_within_one_batch(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    payload = golden(batch_id=fresh_id())
    full_id = copy.deepcopy(payload["items"][0])
    full_id.update(
        client_ref="shopee:4242_55667788",
        platform_product_id="4242_55667788",
        product_url="https://shopee.vn/product/4242/55667788",
        affiliate_url="https://s.shopee.vn/full",
    )
    payload["items"].insert(0, full_id)  # processed before the URL-only bottle
    results = by_ref(api.post(INGEST, json=payload, headers=key))
    assert results["shopee:4242_55667788"]["status"] == "created"
    assert results[BOTTLE]["status"] == "conflict"
    assert results[BOTTLE]["code"] == "identity_ambiguous"
    assert results[BOTTLE]["product_id"] == results["shopee:4242_55667788"]["product_id"]
    assert product_count(db_session, organization.id) == 3


def test_meoscout_keeps_on_conflict_keep_by_default(
    api: TestClient, key: dict, db_session: Session, organization
) -> None:
    assert GOLDEN["on_conflict"] == "keep"
    payload = golden(batch_id=fresh_id())
    payload.pop("on_conflict")
    assert api.post(INGEST, json=payload, headers=key).json()["on_conflict"] == "keep"
