"""Runtime identity contracts that detect stale or incorrectly imported backends."""

from pathlib import Path

import app_meta
import database
import main


EXPECTED_API_PATHS = {
    "/health",
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/memory/store",
    "/api/memory/query",
    "/api/memory/auto",
    "/api/memories",
    "/api/calendar/month",
    "/api/calendar/day",
    "/api/conversations",
    "/api/conversations/{conversation_id}/messages",
    "/api/memory/{memory_id}",
    "/api/memory/rebuild",
}


def test_version_has_one_runtime_source() -> None:
    schema = main.app.openapi()
    assert main.app.version == app_meta.APP_VERSION
    assert schema["info"]["version"] == app_meta.APP_VERSION


def test_openapi_exposes_exact_current_source_routes() -> None:
    schema_paths = set(main.app.openapi()["paths"])
    source_routes = {route.path for route in main.app.routes if route.path.startswith("/api") or route.path == "/health"}
    assert schema_paths == source_routes == EXPECTED_API_PATHS


def test_database_path_is_absolute_and_anchored_to_backend() -> None:
    expected = Path(database.__file__).resolve().parent / "memory.db"
    assert database.DATABASE_PATH.is_absolute()
    assert database.DATABASE_PATH.resolve() == expected
