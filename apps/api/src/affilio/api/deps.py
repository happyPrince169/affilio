"""Request-scoped dependencies shared by routers.

Organization resolution is a stop-gap until authentication (M3): the header
`X-Organization-Slug` selects an organization, otherwise `DEFAULT_ORGANIZATION_SLUG` is used.
Mutating endpoints additionally require `X-Affilio-Admin-Token` outside development.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings, get_settings
from affilio.core.errors import AppError, NotFoundError
from affilio.db.session import get_db
from affilio.modules.identity.models import Organization
from affilio.modules.media.roots import MediaRootRegistry

DbSession = Annotated[Session, Depends(get_db)]


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


class AdminTokenRequiredError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "admin_token_required"


class AdminEndpointsDisabledError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "admin_endpoints_disabled"


def get_current_organization(
    session: DbSession,
    settings: SettingsDep,
    x_organization_slug: Annotated[str | None, Header()] = None,
) -> Organization:
    slug = (x_organization_slug or settings.default_organization_slug).strip().lower()
    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        raise NotFoundError("Organization not found", details={"slug": slug})
    return organization


CurrentOrganization = Annotated[Organization, Depends(get_current_organization)]


def require_admin(
    settings: SettingsDep,
    x_affilio_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    """Gate for mutating media endpoints until real authentication exists.

    - development/test with no token configured: allowed (local workstation).
    - token configured (any env): header must match (constant-time compare).
    - production with no token configured: refused outright.
    """
    expected = settings.media_admin_token.get_secret_value() if settings.media_admin_token else ""
    if not expected:
        if settings.is_development:
            return
        raise AdminEndpointsDisabledError(
            "Administrative endpoints are disabled until MEDIA_ADMIN_TOKEN is configured"
        )
    if not x_affilio_admin_token or not hmac.compare_digest(x_affilio_admin_token, expected):
        raise AdminTokenRequiredError("A valid X-Affilio-Admin-Token header is required")


AdminRequired = Annotated[None, Depends(require_admin)]


def get_media_registry(settings: SettingsDep) -> MediaRootRegistry:
    return MediaRootRegistry.from_settings(settings)


MediaRegistry = Annotated[MediaRootRegistry, Depends(get_media_registry)]
