"""API-level regressions for write -> structured date -> retrieval -> LLM candidates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
from typing import Any

from fastapi.testclient import TestClient
import pytest

import backfill_date_mentions
import database
import main
from settings import APP_TIMEZONE
import temporal_service


def _contract_extractor(
    content: str, reference: datetime, timezone_name: str, purpose: str = "memory"
) -> list[dict[str, Any]]:
    """Deterministic model boundary stub; production validation is tested separately."""
    del purpose
    assert timezone_name == "Asia/Shanghai"
    local_date = reference.astimezone(APP_TIMEZONE).date()
    mentions: list[dict[str, Any]] = []
    relative_days = {"前天": -2, "昨天": -1, "今天": 0, "明天": 1, "后天": 2}
    for expression, offset in relative_days.items():
        if expression in content:
            target = local_date + timedelta(days=offset)
            mentions.append(_mention(expression, target.isoformat(), target.isoformat(), content))

    week_start = local_date - timedelta(days=local_date.weekday())
    for expression, offset in (("上周", -7), ("本周", 0), ("这周", 0), ("下周", 7)):
        if expression in content and not re.search(rf"{expression}[一二三四五六日天]", content):
            start = week_start + timedelta(days=offset)
            mentions.append(_mention(expression, start.isoformat(), (start + timedelta(days=6)).isoformat(), content))
    weekday_numbers = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    for match in re.finditer(r"(这周|下周)([一二三四五六日天])", content):
        prefix, weekday = match.groups()
        target = week_start + timedelta(days=(7 if prefix == "下周" else 0) + weekday_numbers[weekday])
        mentions.append(_mention(match.group(0), target.isoformat(), target.isoformat(), content))

    for match in re.finditer(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日", content):
        year = int(match.group(1) or local_date.year)
        target = datetime(year, int(match.group(2)), int(match.group(3))).date()
        mentions.append(_mention(match.group(0), target.isoformat(), target.isoformat(), content))
    return mentions


def _mention(expression: str, start: str, end: str, content: str) -> dict[str, Any]:
    return {
        "original_expression": expression,
        "normalized_text": content.replace(expression, "").strip(" ，,？?" ) or "事项",
        "start_date": start,
        "end_date": end,
        "confidence": 0.99,
    }


@pytest.fixture
def temporal_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "todo")
    monkeypatch.setattr(main, "extract_date_mentions", _contract_extractor)
    monkeypatch.setattr(backfill_date_mentions, "extract_date_mentions", _contract_extractor)
    monkeypatch.setattr(main, "add_to_vector", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "mark_embedding_result", lambda *args, **kwargs: None)

    write_utc = {"value": "2026-08-20 02:00:00"}
    original_add_memory = database.add_memory

    def add_at_frozen_time(user_id: str, content: str, tags: str | None, category: str | None) -> int:
        memory_id = original_add_memory(user_id, content, tags, category)
        with database._connect() as connection:
            connection.execute(
                "UPDATE memories SET created_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                (write_utc["value"], write_utc["value"], user_id, memory_id),
            )
        return memory_id

    monkeypatch.setattr(main, "add_memory", add_at_frozen_time)
    query_now = {"value": datetime(2026, 8, 20, 12, 0, tzinfo=APP_TIMEZONE)}
    monkeypatch.setattr(temporal_service, "current_reference_datetime", lambda: query_now["value"])

    llm_calls: list[dict[str, Any]] = []

    def answer(query: str, memories: list[dict[str, Any]], temporal_filter=None) -> str:
        llm_calls.append({"query": query, "memories": memories, "temporal_filter": temporal_filter})
        return "；".join(memory["content"] for memory in memories) or "没有记录"

    monkeypatch.setattr(main, "generate_integrated_answer", answer)

    def rank_within(user_id: str, query: str, memory_ids: list[int], top_k: int):
        del user_id, top_k
        with database._connect() as connection:
            rows = connection.execute(
                f"SELECT id, content FROM memories WHERE id IN ({','.join('?' for _ in memory_ids)})",
                memory_ids,
            ).fetchall()
        relevant = [row for row in rows if "生物" not in query or "生物" in row["content"]]
        return [{"memory_id": row["id"]} for row in relevant]

    monkeypatch.setattr(main, "search_similar_within", rank_within)
    ordinary_semantic_ids: list[int] = []
    monkeypatch.setattr(
        main,
        "search_similar",
        lambda user_id, query, top_k: [{"memory_id": memory_id} for memory_id in ordinary_semantic_ids],
    )
    monkeypatch.setattr(main, "search_memories_by_keyword", lambda user_id, query: [])

    with TestClient(main.application) as client:
        registration = client.post(
            "/api/auth/register",
            json={"email": "temporal-e2e@example.com", "password": "safe-password-123"},
        ).json()
        yield {
            "client": client,
            "token": registration["access_token"],
            "user_id": registration["user"]["id"],
            "write_utc": write_utc,
            "query_now": query_now,
            "llm_calls": llm_calls,
            "ordinary_semantic_ids": ordinary_semantic_ids,
        }


def _headers(state) -> dict[str, str]:
    return {"Authorization": f"Bearer {state['token']}"}


def _store(state, content: str) -> int:
    response = state["client"].post(
        "/api/memory/store", headers=_headers(state), json={"content": content}
    )
    assert response.status_code == 200
    return response.json()["memory_id"]


def _query(state, text: str) -> dict[str, Any]:
    response = state["client"].post(
        "/api/memory/query", headers=_headers(state), json={"query": text}
    )
    assert response.status_code == 200
    return response.json()


def test_tomorrow_is_frozen_at_write_time_and_llm_candidates_follow_query_date(temporal_api) -> None:
    state = temporal_api
    memory_id = _store(state, "明天交作业")
    mention = database.list_memory_date_mentions(state["user_id"], memory_id)[0]
    assert mention["start_date"] == "2026-08-21"

    recalled = _query(state, "明天要做什么")
    assert [item["id"] for item in recalled["source_memories"]] == [memory_id]
    assert [item["id"] for item in state["llm_calls"][-1]["memories"]] == [memory_id]

    state["query_now"]["value"] = datetime(2026, 8, 25, 12, 0, tzinfo=APP_TIMEZONE)
    not_recalled = _query(state, "明天要做什么")
    assert not_recalled["source_memories"] == []
    assert state["llm_calls"][-1]["memories"] == []

    explicit = _query(state, "8 月 21 日做了什么")
    assert [item["id"] for item in explicit["source_memories"]] == [memory_id]
    assert [item["id"] for item in state["llm_calls"][-1]["memories"]] == [memory_id]


def test_identical_relative_text_on_different_write_dates_never_cross_matches(temporal_api) -> None:
    state = temporal_api
    first = _store(state, "明天交作业")
    state["write_utc"]["value"] = "2026-08-24 02:00:00"
    second = _store(state, "明天交作业")

    state["query_now"]["value"] = datetime(2026, 8, 20, 12, 0, tzinfo=APP_TIMEZONE)
    assert [item["id"] for item in _query(state, "明天要做什么")["source_memories"]] == [first]
    state["query_now"]["value"] = datetime(2026, 8, 24, 12, 0, tzinfo=APP_TIMEZONE)
    assert [item["id"] for item in _query(state, "明天要做什么")["source_memories"]] == [second]


@pytest.mark.parametrize(
    ("content", "write_utc", "expected"),
    [
        ("前天整理资料", "2026-08-20 02:00:00", "2026-08-18"),
        ("昨天整理资料", "2026-08-20 02:00:00", "2026-08-19"),
        ("今天整理资料", "2026-08-20 02:00:00", "2026-08-20"),
        ("后天整理资料", "2026-08-20 02:00:00", "2026-08-22"),
        ("明天跨月", "2026-08-31 02:00:00", "2026-09-01"),
        ("明天跨年", "2026-12-31 02:00:00", "2027-01-01"),
        ("明天闰日", "2024-02-28 02:00:00", "2024-02-29"),
        ("明天午夜边界", "2026-08-20 16:30:00", "2026-08-22"),
    ],
)
def test_relative_boundaries_use_frozen_created_at(temporal_api, content, write_utc, expected) -> None:
    state = temporal_api
    state["write_utc"]["value"] = write_utc
    memory_id = _store(state, content)
    assert database.list_memory_date_mentions(state["user_id"], memory_id)[0]["start_date"] == expected


def test_week_ranges_and_weekdays_cross_boundaries(temporal_api) -> None:
    state = temporal_api
    state["query_now"]["value"] = datetime(2026, 12, 31, 12, 0, tzinfo=APP_TIMEZONE)
    assert _query(state, "本周有什么")["temporal_filter"]["start_date"] == "2026-12-28"
    assert _query(state, "本周有什么")["temporal_filter"]["end_date"] == "2027-01-03"
    assert _query(state, "下周五有什么")["temporal_filter"]["start_date"] == "2027-01-08"


def test_date_plus_topic_filters_first_then_ranks_only_allowed_ids(temporal_api) -> None:
    state = temporal_api
    biology = _store(state, "明天生物课观察细胞")
    other = _store(state, "明天提交数学作业")
    state["write_utc"]["value"] = "2026-08-19 02:00:00"
    historical_biology = _store(state, "明天生物课观察植物")

    result = _query(state, "明天有哪些生物课相关的事")

    assert [item["id"] for item in result["source_memories"]] == [biology, other]
    assert historical_biology not in [item["id"] for item in state["llm_calls"][-1]["memories"]]
    assert state["llm_calls"][-1]["memories"][0]["id"] == biology


def test_non_temporal_query_keeps_original_hybrid_path(temporal_api) -> None:
    state = temporal_api
    memory_id = _store(state, "生物课观察细胞")
    state["ordinary_semantic_ids"].append(memory_id)
    result = _query(state, "生物课相关的事")
    assert [item["id"] for item in result["source_memories"]] == [memory_id]
    assert result["temporal_filter"] is None
    assert [item["id"] for item in state["llm_calls"][-1]["memories"]] == [memory_id]


def test_parse_failure_never_reaches_keyword_vector_or_llm(temporal_api, monkeypatch) -> None:
    state = temporal_api
    _store(state, "明天交作业")
    monkeypatch.setattr(main, "extract_date_mentions", lambda *args, **kwargs: [])
    before_calls = len(state["llm_calls"])
    result = _query(state, "明天要做什么")
    assert result["source_memories"] == []
    assert result["temporal_filter"]["status"] == "failed"
    assert len(state["llm_calls"]) == before_calls


def test_one_memory_with_multiple_dates_is_recalled_on_each_absolute_day(temporal_api) -> None:
    state = temporal_api
    memory_id = _store(state, "今天整理资料，明天交作业")
    assert [item["start_date"] for item in database.list_memory_date_mentions(state["user_id"], memory_id)] == [
        "2026-08-20", "2026-08-21"
    ]
    assert [item["id"] for item in _query(state, "8 月 20 日做了什么")["source_memories"]] == [memory_id]
    assert [item["id"] for item in _query(state, "8 月 21 日做了什么")["source_memories"]] == [memory_id]


def test_similar_memories_remain_isolated_between_users(temporal_api) -> None:
    state = temporal_api
    own = _store(state, "明天交作业")
    other_registration = state["client"].post(
        "/api/auth/register",
        json={"email": "temporal-other@example.com", "password": "safe-password-123"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other_registration['access_token']}"}
    other = state["client"].post(
        "/api/memory/store", headers=other_headers, json={"content": "明天交作业"}
    ).json()["memory_id"]
    result = _query(state, "明天要做什么")
    assert [item["id"] for item in result["source_memories"]] == [own]
    assert other not in [item["id"] for item in state["llm_calls"][-1]["memories"]]


def test_historical_backfill_becomes_queryable_without_changing_original(temporal_api) -> None:
    state = temporal_api
    memory_id = database.add_memory(state["user_id"], "明天补交历史作业", "历史", "todo")
    with database._connect() as connection:
        connection.execute(
            "UPDATE memories SET created_at = '2026-08-20 02:00:00' WHERE id = ?", (memory_id,)
        )
    before = database.get_memory(state["user_id"], memory_id)
    summary = backfill_date_mentions.run_backfill(batch_size=10, user_id=state["user_id"])
    assert summary["extracted_from_created_at"] == 1
    assert database.get_memory(state["user_id"], memory_id) == before
    result = _query(state, "8 月 21 日做了什么")
    assert [item["id"] for item in result["source_memories"]] == [memory_id]
    assert [item["id"] for item in state["llm_calls"][-1]["memories"]] == [memory_id]
