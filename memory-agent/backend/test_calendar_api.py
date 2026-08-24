"""API tests for authenticated month and day calendar queries."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import database
import main


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    with TestClient(main.app) as test_client:
        yield test_client


def _register(client: TestClient, email: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "safe-password-123"}
    )
    payload = response.json()
    return payload["access_token"], payload["user"]["id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _memory(user_id: str, content: str, created_at: str) -> int:
    memory_id = database.add_memory(user_id, content, None, "note")
    with database._connect() as connection:
        connection.execute(
            "UPDATE memories SET created_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
            (created_at, created_at, user_id, memory_id),
        )
    return memory_id


def _mention(user_id: str, memory_id: int, start: str, end: str) -> None:
    database.add_memory_date_mention(
        user_id, memory_id, start, end, f"{start} 至 {end}", "测试事项", 0.95
    )


def test_month_overview_counts_created_and_mentioned_at_month_boundaries(
    client: TestClient,
) -> None:
    token, user_id = _register(client, "month@example.com")
    first = _memory(user_id, "月初写入", "2026-07-31 16:00:00")
    last = _memory(user_id, "月末写入", "2026-08-31 15:59:59")
    _mention(user_id, first, "2026-08-01", "2026-08-01")
    _mention(user_id, last, "2026-08-31", "2026-09-02")

    response = client.get("/api/calendar/month?year=2026&month=8", headers=_auth(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["timezone"] == "Asia/Shanghai"
    assert len(payload["days"]) == 31
    days = {item["date"]: item for item in payload["days"]}
    assert days["2026-08-01"] == {
        "date": "2026-08-01", "weekday": 6,
        "created_count": 1, "mentioned_count": 1, "has_content": True,
    }
    assert days["2026-08-31"]["created_count"] == 1
    assert days["2026-08-31"]["mentioned_count"] == 1
    assert days["2026-08-15"]["has_content"] is False


def test_month_overview_handles_leap_year_february(client: TestClient) -> None:
    token, user_id = _register(client, "leap@example.com")
    leap_memory = _memory(user_id, "闰日写入", "2024-02-28 16:00:00")
    _mention(user_id, leap_memory, "2024-02-29", "2024-02-29")

    leap = client.get("/api/calendar/month?year=2024&month=2", headers=_auth(token)).json()
    ordinary = client.get("/api/calendar/month?year=2025&month=2", headers=_auth(token)).json()

    assert len(leap["days"]) == 29
    assert leap["days"][-1]["date"] == "2024-02-29"
    assert leap["days"][-1]["created_count"] == 1
    assert leap["days"][-1]["mentioned_count"] == 1
    assert len(ordinary["days"]) == 28


def test_day_detail_keeps_created_and_cross_month_mentioned_semantics_separate(
    client: TestClient,
) -> None:
    token, user_id = _register(client, "detail@example.com")
    both = _memory(user_id, "当天写入且提及当天", "2026-08-31 03:00:00")
    range_memory = _memory(user_id, "跨月培训", "2026-08-20 03:00:00")
    _mention(user_id, both, "2026-08-31", "2026-08-31")
    _mention(user_id, range_memory, "2026-08-31", "2026-09-02")

    august = client.get("/api/calendar/day?date=2026-08-31", headers=_auth(token)).json()
    september = client.get("/api/calendar/day?date=2026-09-01", headers=_auth(token)).json()

    assert [item["id"] for item in august["created_memories"]] == [both]
    assert {item["id"] for item in august["mentioned_memories"]} == {both, range_memory}
    assert all(item["date_mentions"] for item in august["mentioned_memories"])
    assert august["mentioned_memories"][0]["date_mentions"][0]["original_expression"]
    assert september["created_memories"] == []
    assert [item["id"] for item in september["mentioned_memories"]] == [range_memory]


def test_empty_day_has_stable_empty_groups(client: TestClient) -> None:
    token, _ = _register(client, "empty-day@example.com")
    response = client.get("/api/calendar/day?date=2026-08-15", headers=_auth(token))
    assert response.json() == {
        "date": "2026-08-15",
        "timezone": "Asia/Shanghai",
        "sort_order": "desc",
        "created_memories": [],
        "mentioned_memories": [],
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/calendar/month?year=2026&month=13",
        "/api/calendar/month?year=9999&month=1",
        "/api/calendar/day?date=2026-02-30",
        "/api/calendar/day?date=9999-12-31",
        "/api/calendar/day?date=2026-08-01&sort_order=newest",
    ],
)
def test_calendar_rejects_invalid_parameters(client: TestClient, path: str) -> None:
    token, _ = _register(client, f"invalid-{abs(hash(path))}@example.com")
    assert client.get(path, headers=_auth(token)).status_code == 422


def test_calendar_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/calendar/month?year=2026&month=8").status_code == 401
    assert client.get("/api/calendar/day?date=2026-08-01").status_code == 401


def test_calendar_isolates_two_users(client: TestClient) -> None:
    token_a, user_a = _register(client, "calendar-a@example.com")
    token_b, user_b = _register(client, "calendar-b@example.com")
    memory_a = _memory(user_a, "A 的记忆", "2026-08-01 00:00:00")
    memory_b = _memory(user_b, "B 的记忆", "2026-08-01 00:00:00")
    _mention(user_a, memory_a, "2026-08-01", "2026-08-01")
    _mention(user_b, memory_b, "2026-08-01", "2026-08-01")

    day_a = client.get("/api/calendar/day?date=2026-08-01", headers=_auth(token_a)).json()
    day_b = client.get("/api/calendar/day?date=2026-08-01", headers=_auth(token_b)).json()

    assert {item["content"] for item in day_a["created_memories"]} == {"A 的记忆"}
    assert {item["content"] for item in day_a["mentioned_memories"]} == {"A 的记忆"}
    assert {item["content"] for item in day_b["created_memories"]} == {"B 的记忆"}
    assert {item["content"] for item in day_b["mentioned_memories"]} == {"B 的记忆"}


def test_day_detail_supports_ascending_and_descending_sort(client: TestClient) -> None:
    token, user_id = _register(client, "calendar-sort@example.com")
    older = _memory(user_id, "较早", "2026-08-01 01:00:00")
    newer = _memory(user_id, "较晚", "2026-08-01 02:00:00")
    _mention(user_id, older, "2026-08-01", "2026-08-01")
    _mention(user_id, newer, "2026-08-01", "2026-08-01")

    descending = client.get(
        "/api/calendar/day?date=2026-08-01&sort_order=desc", headers=_auth(token)
    ).json()
    ascending = client.get(
        "/api/calendar/day?date=2026-08-01&sort_order=asc", headers=_auth(token)
    ).json()

    assert [item["id"] for item in descending["created_memories"]] == [newer, older]
    assert [item["id"] for item in ascending["created_memories"]] == [older, newer]
    assert [item["id"] for item in descending["mentioned_memories"]] == [newer, older]
    assert [item["id"] for item in ascending["mentioned_memories"]] == [older, newer]
