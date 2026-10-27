"""Request-scoped dependencies: identity, organization context, roles.

Identity resolution (fail closed):
1. Session cookie → user. Cookie-authenticated unsafe requests must come from an allowed
   Origin (CSRF defence on top of SameSite=Lax). The organization is the one named by
   `X-Organization-Slug` if the user is a member of it, else the user's first membership.
2. `X-Affilio-Admin-Token` → *service* identity (CLI, cron). It may only call the internal
   operations listed in `SERVICE_TOKEN_ALLOWED` (scan triggers, scan status, release-due,
   its own identity); every other route answers 403 `service_scope_denied`. Within that
   allow-list it acts with the OWNER role for the organization named by the header or
   `DEFAULT_ORGANIZATION_SLUG`. Never used by browsers.
3. Otherwise 401 `authentication_required`.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings, get_settings
from affilio.core.enums import MembershipRole
from affilio.core.errors import AppError, NotFoundError
from affilio.db.session import get_db
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import Organization, User, UserSession
from affilio.modules.media.roots import MediaRootRegistry

DbSession = Annotated[Session, Depends(get_db)]


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Internal operations the service token may perform (method, path regex relative to the API
# prefix). Everything else is refused: the token is for cron/CLI automation, not a user.
SERVICE_TOKEN_ALLOWED: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = (
    (frozenset({"GET"}), re.compile(r"^/auth/me$")),
    (frozenset({"GET"}), re.compile(r"^/media/roots$")),
    (frozenset({"GET", "POST"}), re.compile(r"^/media/scans(/[^/]+)?(/status)?$")),
    (frozenset({"POST"}), re.compile(r"^/publishing/queue/release-due$")),
)


class AuthenticationRequiredError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_required"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class CsrfRejectedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "csrf_rejected"


class AdminEndpointsDisabledError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "admin_endpoints_disabled"


class ServiceScopeDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "service_scope_denied"


def service_token_allows(method: str, path: str, api_prefix: str) -> bool:
    relative = path[len(api_prefix) :] if path.startswith(api_prefix) else path
    return any(
        method.upper() in methods and pattern.match(relative)
        for methods, pattern in SERVICE_TOKEN_ALLOWED
    )


@dataclass(slots=True)
class Identity:
    kind: Literal["user", "service"]
    organization: Organization
    role: MembershipRole
    user: User | None = None
    session: UserSession | None = None

    @property
    def label(self) -> str:
        return self.user.email if self.user is not None else "service"

    @property
    def user_id(self):
        return self.user.id if self.user is not None else None

    def has_role(self, minimum: MembershipRole) -> bool:
        return identity_service.role_at_least(self.role, minimum)

    def permissions(self) -> dict[str, bool]:
        return {
            "view": True,
            "edit": self.has_role(MembershipRole.EDITOR),
            "approve": self.has_role(MembershipRole.ADMIN),
            "manage_accounts": self.has_role(MembershipRole.ADMIN),
            "manage_members": self.has_role(MembershipRole.ADMIN),
        }


def _origin_allowed(origin: str, request: Request, settings: Settings) -> bool:
    if origin in settings.cors_origins:
        return True
    # Same-origin deployments (web proxy): compare host of Origin with the request Host.
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return bool(parts.netloc) and parts.netloc.lower() == host.lower()


def _check_csrf(request: Request, settings: Settings) -> None:
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin:
        if not _origin_allowed(origin, request, settings):
            raise CsrfRejectedError("Cross-site request rejected")
        return
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site.lower() == "cross-site":
        raise CsrfRejectedError("Cross-site request rejected")


def get_identity(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    x_organization_slug: Annotated[str | None, Header()] = None,
    x_affilio_admin_token: Annotated[str | None, Header()] = None,
) -> Identity:
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if raw_cookie:
        resolved = identity_service.resolve_session(
            session, raw_cookie, ttl=timedelta(days=settings.session_ttl_days)
        )
        if resolved is None:
            raise AuthenticationRequiredError("Session expired or invalid")
        user_session, user = resolved
        _check_csrf(request, settings)
        slug = (x_organization_slug or "").strip().lower() or None
        membership = identity_service.membership_for(user, slug)
        if membership is None:
            raise ForbiddenError(
                "No organization membership" + (f" for '{slug}'" if slug else ""),
                details={"slug": slug},
            )
        identity = Identity(
            kind="user",
            organization=membership.organization,
            role=membership.role,
            user=user,
            session=user_session,
        )
        request.state.identity = identity
        return identity

    expected = settings.media_admin_token.get_secret_value() if settings.media_admin_token else ""
    if x_affilio_admin_token:
        if not expected:
            raise AdminEndpointsDisabledError("Service token is not configured on this server")
        if not hmac.compare_digest(x_affilio_admin_token, expected):
            raise AuthenticationRequiredError("Invalid service token")
        if not service_token_allows(request.method, request.url.path, settings.api_v1_prefix):
            raise ServiceScopeDeniedError(
                "The service token may only trigger scans and release due publications",
                details={"method": request.method, "path": request.url.path},
            )
        slug = (x_organization_slug or settings.default_organization_slug).strip().lower()
        organization = session.scalar(select(Organization).where(Organization.slug == slug))
        if organization is None:
            raise NotFoundError("Organization not found", details={"slug": slug})
        identity = Identity(kind="service", organization=organization, role=MembershipRole.OWNER)
        request.state.identity = identity
        return identity

    raise AuthenticationRequiredError("Sign in to continue")


CurrentIdentity = Annotated[Identity, Depends(get_identity)]


def get_current_organization(identity: CurrentIdentity) -> Organization:
    return identity.organization


CurrentOrganization = Annotated[Organization, Depends(get_current_organization)]


def require_role(minimum: MembershipRole):
    def dependency(identity: CurrentIdentity) -> Identity:
        if not identity.has_role(minimum):
            raise ForbiddenError(
                f"This action requires the {minimum.value} role",
                details={"required": minimum.value, "role": identity.role.value},
            )
        return identity

    return dependency


RequireViewer = Annotated[Identity, Depends(require_role(MembershipRole.VIEWER))]
RequireEditor = Annotated[Identity, Depends(require_role(MembershipRole.EDITOR))]
RequireAdmin = Annotated[Identity, Depends(require_role(MembershipRole.ADMIN))]
# Compatibility aliases for routers written before M3A.
AdminRequired = RequireAdmin
EditorRequired = RequireEditor


def get_media_registry(settings: SettingsDep) -> MediaRootRegistry:
    return MediaRootRegistry.from_settings(settings)


MediaRegistry = Annotated[MediaRootRegistry, Depends(get_media_registry)]
