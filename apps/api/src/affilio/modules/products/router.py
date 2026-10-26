"""Product HTTP routes (M2). Reads are organization-scoped; writes require the admin gate."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from affilio.api.deps import CurrentOrganization, DbSession, RequireEditor, SettingsDep
from affilio.core.enums import Platform, ProductStatus
from affilio.modules.products import imports, service
from affilio.modules.products.schemas import (
    ImportPreviewResponse,
    ImportRowPreview,
    LinkedProductResponse,
    PaginatedProducts,
    ProductCreate,
    ProductDetail,
    ProductImportResponse,
    ProductMediaLinkCreate,
    ProductMediaLinkResponse,
    ProductMediaLinkUpdate,
    ProductSortKey,
    ProductStats,
    ProductStatusChange,
    ProductStatusEventResponse,
    ProductUpdate,
)
from affilio.modules.products.service import ProductFilters

router = APIRouter(prefix="/products", tags=["products"])


def _thumb_prefix(settings_prefix: str) -> str:
    return f"{settings_prefix}/media/assets"


@router.get("", response_model=PaginatedProducts, summary="List and search products")
def list_products(
    session: DbSession,
    organization: CurrentOrganization,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[ProductStatus | None, Query(alias="status")] = None,
    platform: Platform | None = None,
    category: Annotated[str | None, Query(max_length=120)] = None,
    has_media: bool | None = None,
    sort: ProductSortKey = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 25,
) -> PaginatedProducts:
    filters = ProductFilters(
        q=q,
        status=status_filter,
        platform=platform,
        category=category,
        has_media=has_media,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return service.list_products(session, organization.id, filters)


@router.get("/stats", response_model=ProductStats, summary="Catalog counters")
def read_stats(session: DbSession, organization: CurrentOrganization) -> ProductStats:
    return service.product_stats(session, organization.id)


@router.post(
    "",
    response_model=ProductDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product (editor)",
)
def create_product(
    body: ProductCreate,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> ProductDetail:
    product = service.create_product(session, organization.id, body)
    session.commit()
    return service.product_detail(
        session, service.get_product(session, organization.id, product.id)
    )


def _parse_mapping(raw: str | None) -> dict[str, str] | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise imports.ImportMappingError("mapping must be a JSON object") from exc
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise imports.ImportMappingError("mapping must be a JSON object of field → column")
    return value


async def _read_upload(file: UploadFile) -> bytes:
    raw = await file.read(imports.MAX_FILE_BYTES + 1)
    if len(raw) > imports.MAX_FILE_BYTES:
        raise imports.ImportFileError("File larger than 5 MB")
    return raw


def _preview_response(
    plan: imports.ImportPlan, parsed: imports.ParsedCsv, file_name: str, suggested: dict[str, str]
) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        file_name=file_name,
        file_sha256=parsed.sha256,
        delimiter=parsed.delimiter,
        headers=parsed.headers,
        suggested_mapping=suggested,
        mapping=plan.mapping,
        target_fields=list(imports.TARGET_FIELDS),
        row_count=plan.row_count,
        counts=plan.counts,
        rows=[
            ImportRowPreview(
                row_number=r.row_number,
                action=r.action,
                name=r.name,
                reason=r.reason,
                existing_product_id=r.existing_product_id,
            )
            for r in plan.results[: imports.PREVIEW_ROWS]
        ],
        already_imported=plan.previous_import_id is not None,
        previous_import_id=plan.previous_import_id,
    )


@router.post(
    "/imports/preview",
    response_model=ImportPreviewResponse,
    summary="Parse a MeoScout CSV, suggest a column mapping and dry-run the import",
)
async def preview_import(
    session: DbSession,
    organization: CurrentOrganization,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str | None, Form()] = None,
    on_duplicate: Annotated[imports.OnDuplicate, Form()] = "skip",
) -> ImportPreviewResponse:
    raw = await _read_upload(file)
    parsed = imports.parse_csv(raw, file_name=file.filename or "upload.csv")
    suggested = imports.suggest_mapping(parsed.headers)
    chosen = _parse_mapping(mapping) or suggested
    plan = imports.plan_import(session, organization.id, parsed, chosen, on_duplicate=on_duplicate)
    return _preview_response(plan, parsed, file.filename or "upload.csv", suggested)


@router.post(
    "/imports",
    response_model=ProductImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a MeoScout CSV (editor)",
)
async def run_import(
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str | None, Form()] = None,
    on_duplicate: Annotated[imports.OnDuplicate, Form()] = "skip",
    force: Annotated[bool, Form()] = False,
) -> ProductImportResponse:
    raw = await _read_upload(file)
    parsed = imports.parse_csv(raw, file_name=file.filename or "upload.csv")
    chosen = _parse_mapping(mapping) or imports.suggest_mapping(parsed.headers)
    plan = imports.plan_import(session, organization.id, parsed, chosen, on_duplicate=on_duplicate)
    record = imports.apply_import(
        session,
        organization.id,
        plan,
        file_name=file.filename or "upload.csv",
        on_duplicate=on_duplicate,
        force=force,
    )
    session.commit()
    session.refresh(record)
    return ProductImportResponse.model_validate(record)


@router.get("/imports", response_model=list[ProductImportResponse], summary="Import history")
def list_imports(
    session: DbSession,
    organization: CurrentOrganization,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ProductImportResponse]:
    return [
        ProductImportResponse.model_validate(r)
        for r in service.list_imports(session, organization.id, limit)
    ]


@router.get(
    "/media/{media_asset_id}",
    response_model=list[LinkedProductResponse],
    summary="Products linked to a media asset",
)
def products_for_media(
    media_asset_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> list[LinkedProductResponse]:
    return service.products_for_media(session, organization.id, media_asset_id)


@router.get("/{product_id}", response_model=ProductDetail, summary="Product detail")
def read_product(
    product_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> ProductDetail:
    return service.product_detail(
        session, service.get_product(session, organization.id, product_id)
    )


@router.patch("/{product_id}", response_model=ProductDetail, summary="Edit a product (editor)")
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> ProductDetail:
    service.update_product(session, organization.id, product_id, body)
    session.commit()
    return service.product_detail(
        session, service.get_product(session, organization.id, product_id)
    )


@router.post(
    "/{product_id}/status", response_model=ProductDetail, summary="Change product status (editor)"
)
def change_status(
    product_id: uuid.UUID,
    body: ProductStatusChange,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> ProductDetail:
    service.change_status(session, organization.id, product_id, body)
    session.commit()
    return service.product_detail(
        session, service.get_product(session, organization.id, product_id)
    )


@router.get(
    "/{product_id}/status-history",
    response_model=list[ProductStatusEventResponse],
    summary="Status audit trail",
)
def status_history(
    product_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> list[ProductStatusEventResponse]:
    product = service.get_product(session, organization.id, product_id)
    return [ProductStatusEventResponse.model_validate(e) for e in product.status_events]


@router.get(
    "/{product_id}/media",
    response_model=list[ProductMediaLinkResponse],
    summary="Videos linked to a product",
)
def list_media_links(
    product_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
) -> list[ProductMediaLinkResponse]:
    return service.list_media_links(
        session,
        organization.id,
        product_id,
        thumbnail_url_prefix=_thumb_prefix(settings.api_v1_prefix),
    )


@router.post(
    "/{product_id}/media",
    response_model=ProductMediaLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a video to a product (editor)",
)
def link_media(
    product_id: uuid.UUID,
    body: ProductMediaLinkCreate,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductMediaLinkResponse:
    link = service.link_media(
        session,
        organization.id,
        product_id,
        body,
        thumbnail_url_prefix=_thumb_prefix(settings.api_v1_prefix),
    )
    session.commit()
    return link


@router.patch(
    "/{product_id}/media/{media_asset_id}",
    response_model=ProductMediaLinkResponse,
    summary="Update a product-video link (editor)",
)
def update_media_link(
    product_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    body: ProductMediaLinkUpdate,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductMediaLinkResponse:
    link = service.update_media_link(
        session,
        organization.id,
        product_id,
        media_asset_id,
        body,
        thumbnail_url_prefix=_thumb_prefix(settings.api_v1_prefix),
    )
    session.commit()
    return link


@router.delete(
    "/{product_id}/media/{media_asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a video from a product (editor)",
)
def unlink_media(
    product_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> Response:
    service.unlink_media(session, organization.id, product_id, media_asset_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
