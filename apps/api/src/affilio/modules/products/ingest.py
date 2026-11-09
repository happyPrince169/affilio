"""MeoScout → AFFILIO product ingestion (M3.2-B.2 P1, ADR 0013).

`POST /products/ingest` receives a batch of approved MeoScout candidates from an integration
key and lands them in the product registry with the same validation and duplicate rules as
the CSV import (`imports.py`), plus the rules that make a server-to-server handoff safe:

- **Identity.** A candidate is matched by `(platform, platform_product_id)`, then by the
  normalized product URL. Candidates with neither are `rejected` (`no_canonical_identity`);
  the second candidate of a batch that resolves to the same catalog key is `rejected`
  (`duplicate_in_batch`). Existing identities never change: a differing platform id or
  product URL is a `conflict` (`identity_differs`, `product_url_differs`), kept as stored.
- **Ownership.** Only research-derived fields are written, and only when the incoming value
  is non-empty and differs. `affiliate_url` differing is a `conflict` (`affiliate_url_differs`)
  kept by default and replaced only when the batch says `on_conflict = "replace"`. Status,
  procurement, production, media links, channels and publishing are never touched: new
  products are `DISCOVERED`, matched products keep their lifecycle.
- **Idempotency.** `(organization, batch_id)` is unique. The batch row is flushed before any
  product is written, so a concurrent duplicate blocks on the unique index and, once the
  first request commits, receives the stored results (`replayed = true`). The same batch id
  with a different payload is refused (`batch_mismatch`). Items are stored with the batch so a
  replay never recomputes anything. Within the batch, every product write is a savepoint:
  a unique-index race with another batch degrades to the update path, never to a duplicate.
- **Failure.** If processing raises, the transaction rolls back (no product, no item) and a
  `failed` batch row with a redacted error is recorded separately; the next retry with the
  same batch id re-runs it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.audit import record_audit
from affilio.core.config import Settings
from affilio.core.enums import IngestBatchStatus, IngestItemStatus, ProductSource, ProductStatus
from affilio.core.errors import AppError, NotFoundError
from affilio.core.ratelimit import SlidingWindowLimiter
from affilio.core.redaction import redact_message
from affilio.modules.products.models import (
    Product,
    ProductIngestBatch,
    ProductIngestItem,
    ProductStatusEvent,
)
from affilio.modules.products.schemas import (
    IngestCandidate,
    IngestConflict,
    IngestCounts,
    IngestItemResult,
    IngestRequest,
    IngestResponse,
)
from affilio.modules.products.urls import normalize_url

if TYPE_CHECKING:
    from affilio.api.deps import Identity

log = structlog.get_logger(__name__)

# Research-derived scalar fields MeoScout may refresh (non-empty and different → written).
RESEARCH_FIELDS: tuple[str, ...] = (
    "name",
    "seller_name",
    "category",
    "currency",
    "observed_price",
    "estimated_commission_rate",
    "estimated_commission_amount",
    "research_notes",
)
# MeoScout identities that are not canonical: a later canonical client_ref replaces them.
FINGERPRINT_REF_MARKERS = (":fp:", ":url:")
MAX_REASON_LENGTH = 1000

# Item codes (also listed in docs/api/README.md).
CODE_INVALID_ITEM = "invalid_item"
CODE_NO_IDENTITY = "no_canonical_identity"
CODE_DUPLICATE_IN_BATCH = "duplicate_in_batch"
CODE_IDENTITY_DIFFERS = "identity_differs"
CODE_PRODUCT_URL_DIFFERS = "product_url_differs"
CODE_AFFILIATE_URL_DIFFERS = "affiliate_url_differs"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"


class BatchInProgressError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "batch_in_progress"


class BatchMismatchError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "batch_mismatch"


class IngestRateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"


# ------------------------------------------------------------------ request handling

_limiter: SlidingWindowLimiter | None = None


def limiter_for(settings: Settings) -> SlidingWindowLimiter:
    global _limiter
    if (
        _limiter is None
        or _limiter.limit != settings.ingest_max_batches_per_window
        or _limiter.window != float(max(1, settings.ingest_window_seconds))
    ):
        _limiter = SlidingWindowLimiter(
            limit=settings.ingest_max_batches_per_window,
            window_seconds=settings.ingest_window_seconds,
        )
    return _limiter


def reset_ingest_limiter() -> None:
    """Test hook."""
    if _limiter is not None:
        _limiter.clear()


def rate_limit_key(identity: Identity) -> str:
    if identity.api_key is not None:
        return f"key:{identity.api_key.id}"
    if identity.user is not None:
        return f"user:{identity.user.id}"
    return f"org:{identity.organization.id}"


def check_rate_limit(identity: Identity, settings: Settings) -> None:
    wait = limiter_for(settings).check(rate_limit_key(identity))
    if wait is not None:
        raise IngestRateLimitedError(
            "Too many ingest batches; try again later",
            details={"retry_after_seconds": int(wait)},
            headers={"Retry-After": str(int(wait))},
        )


def record_rate_limit_hit(identity: Identity, settings: Settings) -> None:
    limiter_for(settings).hit(rate_limit_key(identity))


async def read_body(request: Request, *, limit: int) -> bytes:
    """Read the request body up to `limit` bytes; one byte more is 413 (nothing is parsed)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError(
            f"Request body larger than {limit} bytes", details={"limit_bytes": limit}
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLargeError(
                f"Request body larger than {limit} bytes", details={"limit_bytes": limit}
            )
        chunks.append(chunk)
    return b"".join(chunks)


def parse_request(raw: bytes, *, max_items: int) -> IngestRequest:
    """Validate the envelope. Errors surface through the standard 422 envelope."""
    try:
        parsed = IngestRequest.model_validate_json(raw or b"null")
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_input=False)) from exc
    if len(parsed.items) > max_items:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "items"),
                    "msg": f"List should have at most {max_items} items",
                    "type": "too_long",
                }
            ]
        )
    return parsed


def payload_fingerprint(request: IngestRequest) -> str:
    canonical = json.dumps(
        {"on_conflict": request.on_conflict, "items": request.items},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ outcomes


@dataclass(slots=True)
class ItemOutcome:
    client_ref: str
    status: IngestItemStatus
    product: Product | None = None
    code: str | None = None
    reason: str | None = None
    changed_fields: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None


def _rejected(client_ref: str, code: str, reason: str, evidence: Any = None) -> ItemOutcome:
    return ItemOutcome(
        client_ref,
        IngestItemStatus.REJECTED,
        code=code,
        reason=reason[:MAX_REASON_LENGTH],
        evidence=evidence if isinstance(evidence, dict) else None,
    )


def _pid_key(candidate: IngestCandidate) -> tuple[str, str] | None:
    if not candidate.platform_product_id:
        return None
    return (candidate.platform.value, candidate.platform_product_id)


def _url_key(candidate: IngestCandidate) -> str | None:
    return normalize_url(candidate.product_url) if candidate.product_url else None


def _is_fingerprint_ref(ref: str | None) -> bool:
    return ref is not None and any(marker in ref for marker in FINGERPRINT_REF_MARKERS)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


# ------------------------------------------------------------------ catalog lookups


def _lookup_existing(
    session: Session,
    organization_id: uuid.UUID,
    pid_keys: set[tuple[str, str]],
    url_keys: set[str],
) -> tuple[dict[tuple[str, str], Product], dict[str, Product]]:
    by_pid: dict[tuple[str, str], Product] = {}
    by_url: dict[str, Product] = {}
    if pid_keys:
        pids = {pid for _, pid in pid_keys}
        for product in session.scalars(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.platform_product_id.in_(pids),
            )
        ):
            by_pid[(product.platform.value, product.platform_product_id or "")] = product
    if url_keys:
        for product in session.scalars(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.product_url_normalized.in_(url_keys),
            )
        ):
            by_url[product.product_url_normalized or ""] = product
    return by_pid, by_url


def _refetch(
    session: Session, organization_id: uuid.UUID, candidate: IngestCandidate
) -> Product | None:
    """After a unique-index race: the product another transaction just committed."""
    pid_key, url_key = _pid_key(candidate), _url_key(candidate)
    if pid_key is not None:
        found = session.scalar(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.platform == candidate.platform,
                Product.platform_product_id == pid_key[1],
            )
        )
        if found is not None:
            return found
    if url_key is not None:
        return session.scalar(
            select(Product).where(
                Product.organization_id == organization_id,
                Product.product_url_normalized == url_key,
            )
        )
    return None


# ------------------------------------------------------------------ writes


def _create_product(
    session: Session,
    organization_id: uuid.UUID,
    batch: ProductIngestBatch,
    candidate: IngestCandidate,
    *,
    actor: str,
) -> Product | None:
    """Insert in a savepoint. Returns None when a concurrent transaction won the identity."""
    data = candidate.model_dump(exclude={"client_ref", "image_url", "evidence", "images"})
    now = datetime.now(UTC)
    if data.get("observed_price") is not None and data.get("price_observed_at") is None:
        data["price_observed_at"] = now
    product = Product(
        id=uuid.uuid4(),
        organization_id=organization_id,
        **data,
        images=candidate.all_images(),
        status=ProductStatus.DISCOVERED,
        status_changed_at=now,
        source=ProductSource.MEOSCOUT,
        source_ref=candidate.client_ref,
        ingest_batch_id=batch.id,
    )
    product.product_url_normalized = _url_key(candidate)
    try:
        with session.begin_nested():
            session.add(product)
            session.add(
                ProductStatusEvent(
                    product=product,
                    from_status=None,
                    to_status=ProductStatus.DISCOVERED,
                    actor=actor,
                    note=f"ingested from MeoScout (batch {batch.batch_id})",
                )
            )
            session.flush()
    except IntegrityError:
        return None
    return product


def _apply_update(
    product: Product, candidate: IngestCandidate, *, on_conflict: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Merge research fields into an existing product under the ownership rules above."""
    changed: list[str] = []
    conflicts: list[dict[str, str]] = []

    # Identity: enrich when empty, never change.
    if candidate.platform_product_id:
        if _empty(product.platform_product_id):
            product.platform_product_id = candidate.platform_product_id
            changed.append("platform_product_id")
        elif product.platform_product_id != candidate.platform_product_id:
            conflicts.append(
                {
                    "field": "platform_product_id",
                    "code": CODE_IDENTITY_DIFFERS,
                    "resolution": "kept",
                }
            )
    incoming_url = _url_key(candidate)
    if incoming_url is not None:
        if _empty(product.product_url):
            product.product_url = candidate.product_url
            product.product_url_normalized = incoming_url
            changed.append("product_url")
        elif (product.product_url_normalized or normalize_url(product.product_url or "")) != (
            incoming_url
        ):
            conflicts.append(
                {"field": "product_url", "code": CODE_PRODUCT_URL_DIFFERS, "resolution": "kept"}
            )

    # Affiliate link: the one field MeoScout may replace, and only on explicit request.
    if candidate.affiliate_url:
        if _empty(product.affiliate_url):
            product.affiliate_url = candidate.affiliate_url
            changed.append("affiliate_url")
        elif product.affiliate_url != candidate.affiliate_url:
            if on_conflict == "replace":
                product.affiliate_url = candidate.affiliate_url
                changed.append("affiliate_url")
                resolution = "replaced"
            else:
                resolution = "kept"
            conflicts.append(
                {
                    "field": "affiliate_url",
                    "code": CODE_AFFILIATE_URL_DIFFERS,
                    "resolution": resolution,
                }
            )

    # Research fields: written when non-empty and different; never erased.
    for name in RESEARCH_FIELDS:
        value = getattr(candidate, name)
        if _empty(value):
            continue
        current = getattr(product, name)
        if isinstance(value, Decimal) and isinstance(current, Decimal):
            same = value == current
        else:
            same = current == value
        if same:
            continue
        setattr(product, name, value)
        changed.append(name)
        if name == "observed_price":
            product.price_observed_at = candidate.price_observed_at or datetime.now(UTC)
    images = candidate.all_images()
    if images and images != list(product.images or []):
        product.images = images
        changed.append("images")

    # Provenance: fill an empty reference, upgrade a fingerprint id to a canonical one.
    upgradable = _is_fingerprint_ref(product.source_ref) and not _is_fingerprint_ref(
        candidate.client_ref
    )
    if (_empty(product.source_ref) or upgradable) and product.source_ref != candidate.client_ref:
        product.source_ref = candidate.client_ref
        changed.append("source_ref")
    return changed, conflicts


def _outcome_for_update(
    product: Product, candidate: IngestCandidate, *, on_conflict: str
) -> ItemOutcome:
    changed, conflicts = _apply_update(product, candidate, on_conflict=on_conflict)
    kept = [c for c in conflicts if c["resolution"] == "kept"]
    if kept:
        first = kept[0]
        return ItemOutcome(
            candidate.client_ref,
            IngestItemStatus.CONFLICT,
            product=product,
            code=first["code"],
            reason=(
                f"AFFILIO holds a different {first['field']}; kept"
                + (" (on_conflict=keep)" if first["field"] == "affiliate_url" else "")
            ),
            changed_fields=changed,
            conflicts=conflicts,
            evidence=candidate.evidence,
        )
    if changed:
        return ItemOutcome(
            candidate.client_ref,
            IngestItemStatus.UPDATED,
            product=product,
            changed_fields=changed,
            conflicts=conflicts,
            evidence=candidate.evidence,
        )
    return ItemOutcome(
        candidate.client_ref,
        IngestItemStatus.UNCHANGED,
        product=product,
        evidence=candidate.evidence,
    )


# ------------------------------------------------------------------ batch processing


def _validate_candidate(raw: dict[str, Any]) -> tuple[IngestCandidate | None, str | None]:
    try:
        return IngestCandidate.model_validate(raw), None
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in e['loc']) or 'item'}: {e['msg']}" for e in exc.errors()
        )
        return None, problems or "invalid item"
    except AppError as exc:
        return None, exc.message


def _process_items(
    session: Session,
    organization_id: uuid.UUID,
    batch: ProductIngestBatch,
    request: IngestRequest,
    *,
    actor: str,
) -> list[ItemOutcome]:
    validated: list[tuple[str, IngestCandidate | None, str | None, Any]] = []
    pid_keys: set[tuple[str, str]] = set()
    url_keys: set[str] = set()
    for raw in request.items:
        client_ref = str(raw.get("client_ref", "")).strip()
        candidate, problem = _validate_candidate(raw)
        validated.append((client_ref, candidate, problem, raw.get("evidence")))
        if candidate is not None:
            if (pid := _pid_key(candidate)) is not None:
                pid_keys.add(pid)
            if (url := _url_key(candidate)) is not None:
                url_keys.add(url)
    by_pid, by_url = _lookup_existing(session, organization_id, pid_keys, url_keys)

    seen: dict[str, str] = {}
    outcomes: list[ItemOutcome] = []
    for client_ref, candidate, problem, raw_evidence in validated:
        if candidate is None:
            outcomes.append(_rejected(client_ref, CODE_INVALID_ITEM, problem or "", raw_evidence))
            continue
        if not candidate.has_canonical_identity():
            outcomes.append(
                _rejected(
                    client_ref,
                    CODE_NO_IDENTITY,
                    "platform_product_id or product_url is required",
                    candidate.evidence,
                )
            )
            continue
        pid_key, url_key = _pid_key(candidate), _url_key(candidate)
        keys: list[str] = []
        if pid_key is not None:
            keys.append(f"pid:{pid_key[0]}:{pid_key[1]}")
        if url_key is not None:
            keys.append(f"url:{url_key}")
        duplicate_of = next((seen[k] for k in keys if k in seen), None)
        if duplicate_of is not None:
            outcomes.append(
                _rejected(
                    client_ref,
                    CODE_DUPLICATE_IN_BATCH,
                    f"same product as '{duplicate_of}' in this batch",
                    candidate.evidence,
                )
            )
            continue
        for key in keys:
            seen[key] = client_ref

        matched_pid = by_pid.get(pid_key) if pid_key else None
        matched_url = by_url.get(url_key) if url_key else None
        if matched_pid is not None and matched_url is not None and matched_pid.id != matched_url.id:
            outcomes.append(
                ItemOutcome(
                    client_ref,
                    IngestItemStatus.CONFLICT,
                    product=matched_pid,
                    code=CODE_IDENTITY_DIFFERS,
                    reason="platform_product_id and product_url resolve to two different products",
                    conflicts=[
                        {
                            "field": "product_url",
                            "code": CODE_IDENTITY_DIFFERS,
                            "resolution": "kept",
                        }
                    ],
                    evidence=candidate.evidence,
                )
            )
            continue
        existing = matched_pid or matched_url
        if existing is None:
            created = _create_product(session, organization_id, batch, candidate, actor=actor)
            if created is not None:
                if pid_key:
                    by_pid[pid_key] = created
                if url_key:
                    by_url[url_key] = created
                outcomes.append(
                    ItemOutcome(
                        client_ref,
                        IngestItemStatus.CREATED,
                        product=created,
                        evidence=candidate.evidence,
                    )
                )
                continue
            existing = _refetch(session, organization_id, candidate)
            if existing is None:  # pragma: no cover - the race partner rolled back
                outcomes.append(
                    _rejected(
                        client_ref,
                        CODE_INVALID_ITEM,
                        "concurrent write detected; retry this item",
                        candidate.evidence,
                    )
                )
                continue
        outcomes.append(_outcome_for_update(existing, candidate, on_conflict=request.on_conflict))
    return outcomes


def _store_items(
    session: Session,
    organization_id: uuid.UUID,
    batch: ProductIngestBatch,
    outcomes: list[ItemOutcome],
) -> None:
    counts = IngestCounts()
    for position, outcome in enumerate(outcomes):
        session.add(
            ProductIngestItem(
                id=uuid.uuid4(),
                ingest_batch_id=batch.id,
                organization_id=organization_id,
                position=position,
                client_ref=outcome.client_ref,
                status=outcome.status,
                code=outcome.code,
                reason=outcome.reason,
                product_id=outcome.product.id if outcome.product is not None else None,
                changed_fields=outcome.changed_fields,
                conflicts=outcome.conflicts,
                evidence=outcome.evidence,
            )
        )
        setattr(counts, outcome.status.value, getattr(counts, outcome.status.value) + 1)
    batch.item_count = len(outcomes)
    batch.created_count = counts.created
    batch.updated_count = counts.updated
    batch.unchanged_count = counts.unchanged
    batch.conflict_count = counts.conflict
    batch.rejected_count = counts.rejected


def _find_batch(
    session: Session, organization_id: uuid.UUID, batch_id: str
) -> ProductIngestBatch | None:
    return session.scalar(
        select(ProductIngestBatch)
        .options(selectinload(ProductIngestBatch.items))
        .where(
            ProductIngestBatch.organization_id == organization_id,
            ProductIngestBatch.batch_id == batch_id,
        )
    )


def _new_batch(
    organization_id: uuid.UUID, request: IngestRequest, fingerprint: str, identity: Identity
) -> ProductIngestBatch:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return ProductIngestBatch(
        id=uuid.uuid4(),
        organization_id=organization_id,
        batch_id=request.batch_id,
        contract=request.contract,
        contract_version=request.version,
        payload_sha256=fingerprint,
        source=request.source.model_dump(exclude_none=True) if request.source else {},
        on_conflict=request.on_conflict,
        status=IngestBatchStatus.COMPLETED,
        api_key_id=identity.api_key.id if identity.api_key is not None else None,
        actor=identity.label[:255],
        request_id=str(request_id)[:128] if request_id else None,
        sent_at=request.sent_at,
    )


def _audit_data(batch: ProductIngestBatch, identity: Identity) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "contract_version": batch.contract_version,
        "on_conflict": batch.on_conflict,
        "source": batch.source,
        "key_prefix": identity.api_key.prefix if identity.api_key is not None else None,
        "counts": {
            "created": batch.created_count,
            "updated": batch.updated_count,
            "unchanged": batch.unchanged_count,
            "conflict": batch.conflict_count,
            "rejected": batch.rejected_count,
        },
    }


def _record_failure(
    session: Session,
    organization_id: uuid.UUID,
    request: IngestRequest,
    fingerprint: str,
    identity: Identity,
    exc: Exception,
) -> None:
    """After a rollback: keep a `failed` row so operators see it and a retry can re-run."""
    try:
        existing = _find_batch(session, organization_id, request.batch_id)
        if existing is not None:
            return  # the race partner completed it; the retry will replay
        batch = _new_batch(organization_id, request, fingerprint, identity)
        batch.status = IngestBatchStatus.FAILED
        batch.item_count = len(request.items)
        batch.error = redact_message(f"{exc.__class__.__name__}: {exc}", limit=500)
        session.add(batch)
        record_audit(
            session,
            organization_id=organization_id,
            identity=identity,
            action="product.ingest_failed",
            entity_type="product_ingest_batch",
            entity_id=batch.id,
            data={**_audit_data(batch, identity), "error": batch.error},
        )
        session.commit()
    except Exception:  # pragma: no cover - best effort; the original error is re-raised
        session.rollback()
        log.exception("products.ingest.failure_not_recorded", batch_id=request.batch_id)


def ingest_batch(
    session: Session, identity: Identity, request: IngestRequest
) -> tuple[ProductIngestBatch, bool]:
    """Process (or replay) one batch. Returns (batch, replayed). Commits on success."""
    organization_id = identity.organization.id
    fingerprint = payload_fingerprint(request)

    existing = _find_batch(session, organization_id, request.batch_id)
    if existing is not None:
        if existing.payload_sha256 != fingerprint:
            raise BatchMismatchError(
                "This batch_id was already used with a different payload; send a new batch_id",
                details={"batch_id": request.batch_id},
            )
        if existing.status == IngestBatchStatus.COMPLETED:
            _audit_replay(session, existing, identity)
            return existing, True
        # A failed batch: forget it and re-run under the same id (same transaction).
        session.delete(existing)
        session.flush()

    batch = _new_batch(organization_id, request, fingerprint, identity)
    session.add(batch)
    try:
        # Takes the (organization, batch_id) unique slot. A concurrent duplicate blocks here
        # until this transaction ends, then fails and is answered with the stored results.
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = _find_batch(session, organization_id, request.batch_id)
        if winner is not None and winner.payload_sha256 != fingerprint:
            raise BatchMismatchError(
                "This batch_id was already used with a different payload; send a new batch_id",
                details={"batch_id": request.batch_id},
            ) from None
        if winner is not None and winner.status == IngestBatchStatus.COMPLETED:
            _audit_replay(session, winner, identity)
            return winner, True
        raise BatchInProgressError(
            "This batch is being processed by another request; retry shortly",
            details={"batch_id": request.batch_id},
            headers={"Retry-After": "5"},
        ) from None

    try:
        outcomes = _process_items(session, organization_id, batch, request, actor=identity.label)
        _store_items(session, organization_id, batch, outcomes)
        batch.completed_at = datetime.now(UTC)
        record_audit(
            session,
            organization_id=organization_id,
            identity=identity,
            action="product.ingest_batch",
            entity_type="product_ingest_batch",
            entity_id=batch.id,
            data=_audit_data(batch, identity),
        )
        for outcome in outcomes:
            if outcome.status == IngestItemStatus.CONFLICT and outcome.product is not None:
                record_audit(
                    session,
                    organization_id=organization_id,
                    identity=identity,
                    action="product.ingest_conflict",
                    entity_type="product",
                    entity_id=outcome.product.id,
                    data={
                        "batch_id": batch.batch_id,
                        "client_ref": outcome.client_ref,
                        "code": outcome.code,
                        "conflicts": outcome.conflicts,
                        "changed_fields": outcome.changed_fields,
                    },
                )
        session.commit()
    except Exception as exc:
        session.rollback()
        _record_failure(session, organization_id, request, fingerprint, identity, exc)
        raise
    log.info(
        "products.ingest",
        batch_id=batch.batch_id,
        actor=identity.label,
        key_prefix=identity.api_key.prefix if identity.api_key is not None else None,
        **_audit_data(batch, identity)["counts"],
    )
    return _find_batch(session, organization_id, request.batch_id) or batch, False


def _audit_replay(session: Session, batch: ProductIngestBatch, identity: Identity) -> None:
    record_audit(
        session,
        organization_id=batch.organization_id,
        identity=identity,
        action="product.ingest_replayed",
        entity_type="product_ingest_batch",
        entity_id=batch.id,
        data={"batch_id": batch.batch_id},
    )
    session.commit()


def get_batch(session: Session, organization_id: uuid.UUID, batch_id: str) -> ProductIngestBatch:
    batch = _find_batch(session, organization_id, batch_id)
    if batch is None:
        raise NotFoundError("Ingest batch not found", details={"batch_id": batch_id})
    return batch


# ------------------------------------------------------------------ responses


def batch_response(batch: ProductIngestBatch, *, replayed: bool) -> IngestResponse:
    product_status: dict[uuid.UUID, ProductStatus] = {}
    products = {item.product_id for item in batch.items if item.product_id is not None}
    if products:
        session = Session.object_session(batch)
        if session is not None:
            for product in session.scalars(select(Product).where(Product.id.in_(products))):
                product_status[product.id] = product.status
    return IngestResponse(
        id=batch.id,
        batch_id=batch.batch_id,
        contract=batch.contract,
        version=batch.contract_version,
        status=batch.status.value,  # type: ignore[arg-type]
        replayed=replayed,
        on_conflict=batch.on_conflict,  # type: ignore[arg-type]
        received_at=batch.created_at,
        completed_at=batch.completed_at,
        counts=IngestCounts(
            created=batch.created_count,
            updated=batch.updated_count,
            unchanged=batch.unchanged_count,
            conflict=batch.conflict_count,
            rejected=batch.rejected_count,
        ),
        results=[
            IngestItemResult(
                client_ref=item.client_ref,
                status=item.status.value,  # type: ignore[arg-type]
                product_id=item.product_id,
                product_status=(
                    product_status.get(item.product_id) if item.product_id is not None else None
                ),
                code=item.code,
                reason=item.reason,
                changed_fields=list(item.changed_fields or []),
                conflicts=[IngestConflict(**c) for c in (item.conflicts or [])],
            )
            for item in batch.items
        ],
    )
