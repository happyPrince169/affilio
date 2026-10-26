"""Authentication helpers for API tests: real users, real sessions, real cookies."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.core.enums import MembershipRole
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import Organization, User

TEST_PASSWORD = "correct-horse-battery"


def create_member(
    session: Session,
    organization: Organization,
    *,
    role: MembershipRole = MembershipRole.OWNER,
    email: str | None = None,
    password: str = TEST_PASSWORD,
) -> User:
    user = identity_service.create_user(
        session,
        email=email or f"{role.value}-{uuid.uuid4().hex[:8]}@test.affilio.local",
        password=password,
        display_name=f"{role.value.title()} Tester",
    )
    identity_service.add_membership(session, organization, user, role)
    session.commit()
    return user


def login(
    client: TestClient,
    user: User,
    *,
    password: str = TEST_PASSWORD,
    organization_slug: str | None = None,
) -> dict:
    payload: dict = {"email": user.email, "password": password}
    if organization_slug:
        payload["organization_slug"] = organization_slug
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def sign_in(
    client: TestClient,
    session: Session,
    organization: Organization,
    *,
    role: MembershipRole = MembershipRole.OWNER,
) -> User:
    user = create_member(session, organization, role=role)
    login(client, user)
    return user
