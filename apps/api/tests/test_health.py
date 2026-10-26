from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from affilio import __version__


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
def test_health_endpoints_return_ok(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "AFFILIO API"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["timestamp"].endswith("Z") or "+" in body["timestamp"]


def test_response_carries_generated_request_id(client: TestClient) -> None:
    response = client.get("/health")

    request_id = response.headers.get("x-request-id")
    assert request_id
    assert len(request_id) == 32  # uuid4().hex


def test_incoming_request_id_is_echoed(client: TestClient) -> None:
    supplied = f"test-{uuid.uuid4()}"

    response = client.get("/health", headers={"X-Request-ID": supplied})

    assert response.headers["x-request-id"] == supplied


def test_cors_preflight_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_actual_response_exposes_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "http://evil.test"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
