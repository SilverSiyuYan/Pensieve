"""Regression contracts for browser CORS behavior at the public ASGI boundary."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import database
import main


ALLOWED_ORIGINS = ("http://127.0.0.1:8080", "http://localhost:8080")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    with TestClient(main.application, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_authenticated_json_preflight_is_public_and_exact(client: TestClient, origin: str) -> None:
    response = client.options(
        "/api/memory/auto",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.text == "OK"
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers
    # Pensieve uses an explicit Bearer header, not cross-origin cookies.
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_actual_get_and_post_responses_have_exact_origin(client: TestClient, origin: str) -> None:
    get_response = client.get("/api/health", headers={"Origin": origin})
    post_response = client.post(
        "/api/auth/register",
        headers={"Origin": origin},
        json={"email": f"cors-{origin.split('//')[1].replace(':', '-')}@example.com", "password": "safe-password-123"},
    )

    assert get_response.status_code == 200
    assert post_response.status_code == 201
    assert get_response.headers["access-control-allow-origin"] == origin
    assert post_response.headers["access-control-allow-origin"] == origin


def test_unknown_origin_is_not_allowed(client: TestClient) -> None:
    origin = "https://attacker.invalid"
    preflight = client.options(
        "/api/memory/auto",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    actual = client.get("/api/health", headers={"Origin": origin})

    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
    assert actual.status_code == 200
    assert "access-control-allow-origin" not in actual.headers


@pytest.mark.parametrize(
    ("path", "expected_status"),
    (("/api/auth/me", 401), ("/api/does-not-exist", 404)),
)
def test_http_error_responses_keep_cors_headers(
    client: TestClient, path: str, expected_status: int
) -> None:
    origin = ALLOWED_ORIGINS[0]
    response = client.get(path, headers={"Origin": origin})

    assert response.status_code == expected_status
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["content-type"].startswith("application/json")


def test_controlled_unhandled_error_keeps_cors_headers(client: TestClient) -> None:
    route_count = len(main.app.router.routes)

    def controlled_failure() -> None:
        raise RuntimeError("controlled CORS regression probe")

    main.app.add_api_route("/_test/controlled-error", controlled_failure, methods=["GET"])
    try:
        origin = ALLOWED_ORIGINS[0]
        response = client.get("/_test/controlled-error", headers={"Origin": origin})
        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == origin
    finally:
        del main.app.router.routes[route_count:]
