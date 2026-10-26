from __future__ import annotations

import json

from fastapi.testclient import TestClient

from affilio.modules import DOMAIN_MODULES

SENSITIVE_FRAGMENTS = ("password", "secret", "token", "database_url", "postgresql+", "@localhost")


def test_system_info_shape(client: TestClient) -> None:
    response = client.get("/api/v1/system/info")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "AFFILIO API"
    assert body["environment"] == "test"
    assert body["api_prefix"] == "/api/v1"
    assert body["python_version"].startswith("3.")
    assert body["modules"] == list(DOMAIN_MODULES)
    assert body["database"]["dialect"] == "postgresql"
    assert body["database"]["status"] in {"ok", "unavailable"}


def test_system_info_degrades_gracefully_without_database(client: TestClient) -> None:
    """With the default unreachable test URL the endpoint must still answer 200."""
    response = client.get("/api/v1/system/info")

    assert response.status_code == 200
    database = response.json()["database"]
    if database["status"] == "unavailable":
        assert database["latency_ms"] is None
    else:  # a real TEST_DATABASE_URL was provided
        assert database["latency_ms"] is not None


def test_system_info_never_leaks_secrets(client: TestClient) -> None:
    body = client.get("/api/v1/system/info").text.lower()

    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in body, f"system info leaked {fragment!r}"


def test_openapi_lists_versioned_routes_only(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    paths = set(schema["paths"])
    assert "/api/v1/health" in paths
    assert "/api/v1/system/info" in paths
    assert "/health" not in paths  # infra probe is intentionally hidden from the schema
    assert json.dumps(schema)  # serialisable
