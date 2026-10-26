"""MeoScout CSV import: parse → map → validate → detect duplicates → preview or apply.

MeoScout exposes no API we rely on; operators export a CSV and upload it. Every run is recorded
in `product_imports` with the file's SHA-256 so re-importing the same file is a no-op that
explains itself, and duplicate rows (by platform id or normalized URL) are skipped or updated
according to `on_duplicate`, never inserted twice.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import ImportStatus, Platform, ProductSource, ProductStatus
from affilio.core.errors import AppError
from affilio.modules.products.models import Product, ProductImport, ProductStatusEvent
from affilio.modules.products.schemas import ProductCreate
from affilio.modules.products.urls import normalize_url

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
PREVIEW_ROWS = 50
MAX_ERRORS = 200

TARGET_FIELDS: tuple[str, ...] = (
    "name",
    "product_url",
    "affiliate_url",
    "platform_product_id",
    "seller_name",
    "category",
    "image_url",
    "observed_price",
    "currency",
    "estimated_commission_rate",
    "estimated_commission_amount",
    "source_ref",
    "research_notes",
)
REQUIRED_FIELDS = ("name",)

# Header aliases (lower-cased, punctuation stripped) → target field. Covers MeoScout-style and
# generic exports; the operator can override every mapping in the UI.
HEADER_ALIASES: dict[str, str] = {
    "name": "name",
    "productname": "name",
    "product": "name",
    "title": "name",
    "tensanpham": "name",
    "url": "product_url",
    "producturl": "product_url",
    "link": "product_url",
    "productlink": "product_url",
    "shopeeurl": "product_url",
    "affiliateurl": "affiliate_url",
    "affiliatelink": "affiliate_url",
    "shortlink": "affiliate_url",
    "productid": "platform_product_id",
    "itemid": "platform_product_id",
    "shopeeid": "platform_product_id",
    "id": "source_ref",
    "meoscoutid": "source_ref",
    "shop": "seller_name",
    "shopname": "seller_name",
    "seller": "seller_name",
    "store": "seller_name",
    "category": "category",
    "image": "image_url",
    "imageurl": "image_url",
    "thumbnail": "image_url",
    "price": "observed_price",
    "currentprice": "observed_price",
    "gia": "observed_price",
    "currency": "currency",
    "commission": "estimated_commission_rate",
    "commissionrate": "estimated_commission_rate",
    "commissionpercent": "estimated_commission_rate",
    "hoahong": "estimated_commission_rate",
    "commissionamount": "estimated_commission_amount",
    "notes": "research_notes",
    "note": "research_notes",
    "description": "research_notes",
}

OnDuplicate = Literal["skip", "update"]


class ImportFileError(AppError):
    status_code = 422
    code = "invalid_import_file"


class ImportMappingError(AppError):
    status_code = 422
    code = "invalid_import_mapping"


@dataclass(slots=True)
class RowResult:
    row_number: int
    action: Literal["create", "update", "skip", "error"]
    name: str | None = None
    reason: str | None = None
    existing_product_id: uuid.UUID | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedCsv:
    headers: list[str]
    rows: list[dict[str, str]]
    sha256: str
    delimiter: str


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Map target field → CSV header for every header we recognise (first match wins)."""
    mapping: dict[str, str] = {}
    for header in headers:
        target = HEADER_ALIASES.get(_norm_header(header))
        if target and target not in mapping:
            mapping[target] = header
    return mapping


def parse_csv(raw: bytes, *, file_name: str) -> ParsedCsv:
    if not raw:
        raise ImportFileError("The uploaded file is empty")
    if len(raw) > MAX_FILE_BYTES:
        raise ImportFileError(f"File larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ImportFileError("File must be UTF-8 (or Windows-1252) encoded text") from exc
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise ImportFileError(f"{file_name}: no header row found")
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        if len(rows) >= MAX_ROWS:
            raise ImportFileError(f"More than {MAX_ROWS} rows; split the export")
        cleaned = {
            (k or "").strip(): (v or "").strip() if isinstance(v, str) else ""
            for k, v in raw_row.items()
            if k is not None
        }
        if any(cleaned.values()):
            rows.append(cleaned)
    return ParsedCsv(headers=headers, rows=rows, sha256=sha256, delimiter=delimiter)


def validate_mapping(mapping: dict[str, str], headers: list[str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for target, header in mapping.items():
        if target not in TARGET_FIELDS:
            raise ImportMappingError(f"Unknown target field '{target}'")
        if not header:
            continue
        if header not in headers:
            raise ImportMappingError(f"Column '{header}' not found in the file")
        cleaned[target] = header
    for required in REQUIRED_FIELDS:
        if required not in cleaned:
            raise ImportMappingError(f"A column must be mapped to '{required}'")
    return cleaned


def _parse_decimal(value: str) -> Decimal | None:
    text = value.strip().replace(" ", "")
    if not text:
        return None
    text = re.sub(r"[^\d.,-]", "", text)
    if text.count(",") and text.count("."):
        text = (
            text.replace(",", "")
            if text.rfind(".") > text.rfind(",")
            else text.replace(".", "").replace(",", ".")
        )
    elif text.count(",") == 1 and len(text.split(",")[1]) <= 2:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _row_to_payload(
    row: dict[str, str], mapping: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    get = lambda field: row.get(mapping[field], "") if field in mapping else ""  # noqa: E731
    payload: dict[str, Any] = {"name": get("name")}
    for text_field in (
        "product_url",
        "affiliate_url",
        "platform_product_id",
        "seller_name",
        "category",
        "research_notes",
    ):
        value = get(text_field)
        if value:
            payload[text_field] = value
    image = get("image_url")
    if image:
        payload["images"] = [image]
    currency = get("currency")
    if currency:
        payload["currency"] = currency
    price = get("observed_price")
    if price:
        parsed = _parse_decimal(price)
        if parsed is None:
            problems.append(f"observed_price '{price}' is not a number")
        else:
            payload["observed_price"] = parsed.quantize(Decimal("0.01"))
            payload["price_observed_at"] = datetime.now(UTC)
    rate = get("estimated_commission_rate")
    if rate:
        parsed = _parse_decimal(rate)
        if parsed is None:
            problems.append(f"commission '{rate}' is not a number")
        else:
            if parsed > 1:  # given as percent
                parsed = parsed / 100
            payload["estimated_commission_rate"] = parsed.quantize(Decimal("0.0001"))
    amount = get("estimated_commission_amount")
    if amount:
        parsed = _parse_decimal(amount)
        if parsed is None:
            problems.append(f"commission amount '{amount}' is not a number")
        else:
            payload["estimated_commission_amount"] = parsed.quantize(Decimal("0.01"))
    return payload, problems


@dataclass(slots=True)
class ImportPlan:
    headers: list[str]
    mapping: dict[str, str]
    sha256: str
    row_count: int
    results: list[RowResult]
    counts: dict[str, int]
    previous_import_id: uuid.UUID | None = None


def plan_import(
    session: Session,
    organization_id: uuid.UUID,
    parsed: ParsedCsv,
    mapping: dict[str, str],
    *,
    on_duplicate: OnDuplicate,
) -> ImportPlan:
    """Dry-run every row: validate, detect duplicates (catalog + within file), decide action."""
    mapping = validate_mapping(mapping, parsed.headers)
    existing_by_pid = {
        (p.platform, p.platform_product_id): p
        for p in session.scalars(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.platform_product_id.is_not(None),
            )
        )
    }
    existing_by_url = {
        p.product_url_normalized: p
        for p in session.scalars(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.product_url_normalized.is_not(None),
            )
        )
    }
    seen_in_file: dict[str, int] = {}
    results: list[RowResult] = []
    counts = {"create": 0, "update": 0, "skip": 0, "error": 0}

    for index, row in enumerate(parsed.rows, start=2):  # header is line 1
        payload, problems = _row_to_payload(row, mapping)
        source_ref = row.get(mapping["source_ref"], "") if "source_ref" in mapping else ""
        name = payload.get("name") or None
        try:
            validated = ProductCreate(**payload)
        except ValidationError as exc:
            problems.extend(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            validated = None
        except AppError as exc:
            problems.append(exc.message)
            validated = None
        if validated is None or problems:
            results.append(RowResult(index, "error", name, "; ".join(problems) or "invalid row"))
            counts["error"] += 1
            continue

        keys: list[str] = []
        if validated.platform_product_id:
            keys.append(f"pid:{validated.platform}:{validated.platform_product_id}")
        if validated.product_url:
            keys.append(f"url:{normalize_url(validated.product_url)}")
        dup_row = next((seen_in_file[k] for k in keys if k in seen_in_file), None)
        if dup_row is not None:
            results.append(
                RowResult(index, "skip", validated.name, f"duplicate of row {dup_row} in this file")
            )
            counts["skip"] += 1
            continue
        for k in keys:
            seen_in_file[k] = index

        existing = None
        if validated.platform_product_id:
            existing = existing_by_pid.get((validated.platform, validated.platform_product_id))
        if existing is None and validated.product_url:
            existing = existing_by_url.get(normalize_url(validated.product_url))
        data = validated.model_dump(exclude={"status"})
        data["source_ref"] = source_ref or None
        if existing is not None:
            action: Literal["update", "skip"] = "update" if on_duplicate == "update" else "skip"
            results.append(
                RowResult(index, action, validated.name, "already in catalog", existing.id, data)
            )
            counts[action] += 1
        else:
            results.append(RowResult(index, "create", validated.name, None, None, data))
            counts["create"] += 1

    previous = session.scalar(
        select(ProductImport)
        .where(
            ProductImport.organization_id == organization_id,
            ProductImport.file_sha256 == parsed.sha256,
            ProductImport.status == ImportStatus.COMPLETED,
        )
        .order_by(ProductImport.created_at.desc())
    )
    return ImportPlan(
        headers=parsed.headers,
        mapping=mapping,
        sha256=parsed.sha256,
        row_count=len(parsed.rows),
        results=results,
        counts=counts,
        previous_import_id=previous.id if previous else None,
    )


def apply_import(
    session: Session,
    organization_id: uuid.UUID,
    plan: ImportPlan,
    *,
    file_name: str,
    on_duplicate: OnDuplicate,
    force: bool = False,
    actor: str = "api",
) -> ProductImport:
    """Execute a plan. Idempotent: the same file (by hash) is not imported twice unless forced."""
    record = ProductImport(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source=ProductSource.MEOSCOUT,
        status=ImportStatus.COMPLETED,
        file_name=file_name[:255],
        file_sha256=plan.sha256,
        mapping=plan.mapping,
        on_duplicate=on_duplicate,
        row_count=plan.row_count,
        actor=actor,
        errors=[],
    )
    if plan.previous_import_id and not force:
        record.status = ImportStatus.SKIPPED
        record.duplicate_of_import_id = plan.previous_import_id
        record.skipped_count = plan.row_count
        record.errors = [
            {"row": 0, "message": "This exact file was already imported; pass force=true to re-run"}
        ]
        session.add(record)
        session.flush()
        return record

    session.add(record)
    session.flush()
    errors: list[dict[str, Any]] = []
    for result in plan.results:
        if result.action == "error":
            record.failed_count += 1
            if len(errors) < MAX_ERRORS:
                errors.append(
                    {"row": result.row_number, "name": result.name, "message": result.reason}
                )
            continue
        if result.action == "skip":
            record.skipped_count += 1
            continue
        data = dict(result.data)
        source_ref = data.pop("source_ref", None)
        if result.action == "update" and result.existing_product_id:
            product = session.get(Product, result.existing_product_id)
            if product is None:
                record.failed_count += 1
                continue
            for key, value in data.items():
                if value is not None and value != [] and value != "":
                    setattr(product, key, value)
            if product.product_url:
                product.product_url_normalized = normalize_url(product.product_url)
            product.source_ref = source_ref or product.source_ref
            record.updated_count += 1
            continue
        product = Product(
            id=uuid.uuid4(),
            organization_id=organization_id,
            **data,
            status=ProductStatus.DISCOVERED,
            status_changed_at=datetime.now(UTC),
            source=ProductSource.MEOSCOUT,
            source_ref=source_ref,
            import_id=record.id,
        )
        product.product_url_normalized = (
            normalize_url(product.product_url) if product.product_url else None
        )
        session.add(product)
        session.add(
            ProductStatusEvent(
                product=product,
                from_status=None,
                to_status=ProductStatus.DISCOVERED,
                actor=actor,
                note=f"imported from MeoScout ({file_name})",
            )
        )
        record.created_count += 1
    record.errors = errors
    session.flush()
    return record


def platform_default() -> Platform:  # pragma: no cover - convenience for future platform mapping
    return Platform.SHOPEE
