from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from affilio.core.errors import AppError, NotFoundError


@pytest.fixture
def error_client(app: FastAPI) -> Iterator[TestClient]:
    """Attach throw-away routes that exercise each error path."""

    @app.get("/__test/validate")
    def _validate(count: int = Query(...)) -> dict[str, int]:
        return {"count": count}

    @app.get("/__test/not-found")
    def _not_found() -> None:
        raise NotFoundError("Thing not found", details={"thing_id": "abc"})

    @app.get("/__test/custom")
    def _custom() -> None:
        raise AppError("Teapot", code="teapot", status_code=418)

    @app.get("/__test/boom")
    def _boom() -> None:
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _assert_envelope(body: dict, code: str) -> dict:
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"]
    return error


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    error = _assert_envelope(response.json(), "not_found")
    assert error["request_id"] == response.headers["x-request-id"]


def test_validation_error_envelope(error_client: TestClient) -> None:
    response = error_client.get("/__test/validate", params={"count": "not-a-number"})

    assert response.status_code == 422
    error = _assert_envelope(response.json(), "validation_error")
    assert error["details"][0]["loc"] == ["query", "count"]


def test_app_error_subclass_envelope(error_client: TestClient) -> None:
    response = error_client.get("/__test/not-found")

    assert response.status_code == 404
    error = _assert_envelope(response.json(), "not_found")
    assert error["message"] == "Thing not found"
    assert error["details"] == {"thing_id": "abc"}


def test_app_error_custom_code_and_status(error_client: TestClient) -> None:
    response = error_client.get("/__test/custom")

    assert response.status_code == 418
    _assert_envelope(response.json(), "teapot")


def test_unhandled_exception_is_masked(error_client: TestClient) -> None:
    response = error_client.get("/__test/boom")

    assert response.status_code == 500
    error = _assert_envelope(response.json(), "internal_error")
    assert "kaboom" not in error["message"]
    assert response.headers["x-request-id"]
