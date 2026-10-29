"""Analytics routes. Production reporting (upload track U6) is the first area; affiliate
performance analytics (M4) is still planned.

Reporting is admin-only, organization-scoped and read-only: it never changes an asset, an
upload or a review. Rows are built by `media.reporting` from existing tables.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from affilio.api.deps import DbSession, RequireAdmin
from affilio.core.enums import HandoffStatus
from affilio.modules.analytics.schemas import (
    ProductionPeriod,
    ProductionRow,
    ProductionRowsPage,
    ProductionSummary,
)
from affilio.modules.media import reporting

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_PAGE_SIZE = 200


def _filters(
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    owner_id: uuid.UUID | None = None,
    editor_id: uuid.UUID | None = None,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    attribution: Annotated[str | None, Query(pattern=r"^(upload|historical)$")] = None,
    product_category_id: uuid.UUID | None = None,
    content_type_id: uuid.UUID | None = None,
    unknown_editor: bool | None = None,
) -> reporting.ReportFilters:
    return reporting.ReportFilters(
        period=period,
        owner_id=owner_id,
        editor_id=editor_id,
        status=status_filter,
        attribution=attribution,
        product_category_id=product_category_id,
        content_type_id=content_type_id,
        unknown_editor=unknown_editor,
    )


@router.get(
    "/production/summary",
    response_model=ProductionSummary,
    summary="Production KPIs and breakdowns by owner and editor (admin)",
)
def production_summary(
    session: DbSession,
    identity: RequireAdmin,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    owner_id: uuid.UUID | None = None,
    editor_id: uuid.UUID | None = None,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    attribution: Annotated[str | None, Query(pattern=r"^(upload|historical)$")] = None,
    product_category_id: uuid.UUID | None = None,
    content_type_id: uuid.UUID | None = None,
    unknown_editor: bool | None = None,
) -> ProductionSummary:
    flt = _filters(
        period,
        owner_id,
        editor_id,
        status_filter,
        attribution,
        product_category_id,
        content_type_id,
        unknown_editor,
    )
    all_rows = reporting.load_rows(session, identity.organization.id)
    rows = reporting.apply_filters(all_rows, flt)
    return ProductionSummary.model_validate(reporting.summarize(rows, all_rows))


@router.get(
    "/production/rows",
    response_model=ProductionRowsPage,
    summary="Per-video production rows with lineage, paginated (admin)",
)
def production_rows(
    session: DbSession,
    identity: RequireAdmin,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    owner_id: uuid.UUID | None = None,
    editor_id: uuid.UUID | None = None,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    attribution: Annotated[str | None, Query(pattern=r"^(upload|historical)$")] = None,
    product_category_id: uuid.UUID | None = None,
    content_type_id: uuid.UUID | None = None,
    unknown_editor: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> ProductionRowsPage:
    flt = _filters(
        period,
        owner_id,
        editor_id,
        status_filter,
        attribution,
        product_category_id,
        content_type_id,
        unknown_editor,
    )
    rows = reporting.apply_filters(reporting.load_rows(session, identity.organization.id), flt)
    rows.sort(key=lambda r: (r.submitted_at, str(r.asset_id)), reverse=True)
    start = (page - 1) * page_size
    return ProductionRowsPage(
        items=[ProductionRow.model_validate(r) for r in rows[start : start + page_size]],
        total=len(rows),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/production/rows.csv",
    summary="CSV export of the filtered production rows (admin)",
    response_class=Response,
)
def production_rows_csv(
    session: DbSession,
    identity: RequireAdmin,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    owner_id: uuid.UUID | None = None,
    editor_id: uuid.UUID | None = None,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
    attribution: Annotated[str | None, Query(pattern=r"^(upload|historical)$")] = None,
    product_category_id: uuid.UUID | None = None,
    content_type_id: uuid.UUID | None = None,
    unknown_editor: bool | None = None,
) -> Response:
    flt = _filters(
        period,
        owner_id,
        editor_id,
        status_filter,
        attribution,
        product_category_id,
        content_type_id,
        unknown_editor,
    )
    rows = reporting.apply_filters(reporting.load_rows(session, identity.organization.id), flt)
    rows.sort(key=lambda r: (r.submitted_at, str(r.asset_id)), reverse=True)
    name = f"production-{period or 'all'}.csv"
    return Response(
        content=reporting.rows_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get(
    "/production/periods",
    response_model=list[ProductionPeriod],
    summary="Production periods present in the library with their counts (admin)",
)
def production_periods(session: DbSession, identity: RequireAdmin) -> list[ProductionPeriod]:
    rows = reporting.load_rows(session, identity.organization.id)
    counts: dict[str, ProductionPeriod] = {}
    for r in rows:
        key = r.production_period or "unknown"
        entry = counts.setdefault(key, ProductionPeriod(period=key, files=0, submissions=0))
        entry.files += 1
        if r.ingest_source == "upload":
            entry.submissions += 1
    return sorted(counts.values(), key=lambda p: p.period, reverse=True)
