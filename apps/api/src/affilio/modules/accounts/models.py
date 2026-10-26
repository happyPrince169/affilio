"""Social account table."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import Platform, SocialAccountStatus
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum

if TYPE_CHECKING:
    from affilio.modules.identity.models import Organization


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An authorized (or to-be-authorized) account on an external platform.

    Credentials/tokens are deliberately NOT on this table; they will live in a dedicated,
    encrypted credential store introduced with the first official integration (M3).
    """

    __tablename__ = "social_accounts"
    __table_args__ = (
        # Explicit name: the convention-generated one would hit PostgreSQL's 63-char limit.
        UniqueConstraint(
            "organization_id",
            "platform",
            "external_account_id",
            name="uq_social_accounts_org_platform_external_id",
        ),
        Index("ix_social_accounts_organization_id_platform", "organization_id", "platform"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(str_enum(Platform, "platform"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Platform-issued identifier; unknown until an official connection flow completes.
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SocialAccountStatus] = mapped_column(
        str_enum(SocialAccountStatus, "social_account_status"),
        nullable=False,
        default=SocialAccountStatus.PENDING,
    )

    organization: Mapped[Organization] = relationship(back_populates="social_accounts")

    def __repr__(self) -> str:
        return f"<SocialAccount platform={self.platform} name={self.display_name!r}>"
