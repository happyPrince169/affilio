"""Shared fixtures for distribution tests: approved finals, accounts, links, fake adapter."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import HandoffStatus, MediaAssetKind, Platform, ScanJobStatus, ScanTrigger
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.schemas import HandoffDecision
from affilio.modules.media.service import enqueue_scan, review_handoff
from affilio.modules.publishing.adapters.assisted import AssistedPublishingAdapter
from affilio.modules.publishing.adapters.base import PublishOutcome
from tests.media_helpers import FakeProber, build_settings, write_bytes


def scan_root(session: Session, organization_id, dirs, root_key: str) -> None:
    job = enqueue_scan(
        session, organization_id, dirs.registry, root_key=root_key, trigger=ScanTrigger.CLI
    )
    job.status = ScanJobStatus.RUNNING
    MediaScanner(session, dirs.registry, build_settings(dirs), prober=FakeProber()).run(job)
    job.status = ScanJobStatus.SUCCEEDED
    session.commit()


def make_final(
    session: Session, organization, dirs, name: str = "final_v1.mp4", *, approve: bool = True
) -> MediaAsset:
    write_bytes(dirs.finals / name, 6_000, seed=sum(map(ord, name)))
    scan_root(session, organization.id, dirs, "finals")
    asset = session.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.organization_id == organization.id,
            MediaAsset.asset_kind == MediaAssetKind.FINAL,
        )
        .order_by(MediaAsset.created_at.desc())
    )
    assert asset is not None
    if approve:
        review_handoff(
            session,
            organization.id,
            asset.id,
            HandoffDecision(status=HandoffStatus.APPROVED, note="ok"),
            actor="test",
        )
        session.commit()
    return asset


def make_footage(session: Session, organization, dirs, name: str = "raw.mp4") -> MediaAsset:
    write_bytes(dirs.originals / name, 4_000, seed=sum(map(ord, name)))
    scan_root(session, organization.id, dirs, "originals")
    asset = session.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.organization_id == organization.id,
            MediaAsset.asset_kind == MediaAssetKind.FOOTAGE,
        )
        .order_by(MediaAsset.created_at.desc())
    )
    assert asset is not None
    return asset


def register_page(client: TestClient, name: str = "Deals Page", **extra: Any) -> dict:
    body = {
        "platform": "facebook",
        "account_type": "page",
        "display_name": name,
        "handle": name.replace(" ", ""),
    }
    body.update(extra)
    response = client.post("/api/v1/accounts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_link(client: TestClient, label: str = "Shopee deal", url: str | None = None) -> dict:
    response = client.post(
        "/api/v1/content/links",
        json={
            "platform": "shopee",
            "label": label,
            "url": url or f"https://shopee.vn/product/{uuid.uuid4().hex[:8]}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_campaign(client: TestClient, name: str = "October Reels", **extra: Any) -> dict:
    response = client.post("/api/v1/publishing/campaigns", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def create_publication(
    client: TestClient, campaign_id: str, asset_id: str, account_id: str, **content: Any
) -> dict:
    body = {
        "campaign_id": campaign_id,
        "media_asset_id": asset_id,
        "social_account_id": account_id,
        "caption": "Fresh deal for you",
        "hashtags": ["deal", "#shopee"],
    }
    body.update(content)
    response = client.post("/api/v1/publishing/publications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def act(client: TestClient, job: dict, action: str, expected: int = 200, **body: Any) -> dict:
    payload = {"version": job["version"], **body}
    path = f"/api/v1/publishing/publications/{job['id']}/{action.replace('_', '-')}"
    response = client.post(path, json=payload)
    assert response.status_code == expected, f"{action}: {response.status_code} {response.text}"
    return response.json()


def approve_flow(client: TestClient, job: dict) -> dict:
    job = act(client, job, "submit")
    return act(client, job, "approve")


class FakeNetworkAdapter(AssistedPublishingAdapter):
    """Test double that records calls and never touches the network."""

    name = "fake"
    calls: list[str]

    def __init__(
        self, platform: Platform = Platform.FACEBOOK, outcome: str = "manual_required"
    ) -> None:
        super().__init__(platform)
        self.calls = []
        self.outcome = outcome

    def publish(self, prepared, *, intent_id: str) -> PublishOutcome:
        self.calls.append(intent_id)
        if self.outcome == "manual_required":
            return super().publish(prepared, intent_id=intent_id)
        return PublishOutcome(mode=prepared.mode, status=self.outcome, error="simulated")  # type: ignore[arg-type]
