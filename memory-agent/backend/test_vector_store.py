"""Integration tests for semantic memory retrieval with ChromaDB."""

from pathlib import Path

import pytest

import vector_store


@pytest.fixture(autouse=True)
def isolated_vector_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a fresh persisted ChromaDB directory for each test."""
    monkeypatch.setattr(vector_store, "CHROMA_PATH", str(tmp_path / "chroma_data"))


def test_semantic_search_returns_stored_memories() -> None:
    vector_store.add_to_vector(
        "user-a",
        1,
        "周四洗头",
        {"tags": "周四,个人护理", "category": "日程", "created_at": "2026-08-21"},
    )
    vector_store.add_to_vector(
        "user-a",
        2,
        "周五游泳",
        {"tags": "周五,运动", "category": "日程", "created_at": "2026-08-21"},
    )

    weekly_results = vector_store.search_similar("user-a", "这周做了什么")
    cleaning_results = vector_store.search_similar("user-a", "这周个人清洁安排")
    exercise_results = vector_store.search_similar("user-a", "本周运动计划")

    assert {item["content"] for item in weekly_results} == {"周四洗头", "周五游泳"}
    assert any("洗头" in item["content"] for item in cleaning_results)
    assert any("游泳" in item["content"] for item in exercise_results)


def test_delete_from_vector_removes_memory() -> None:
    vector_store.add_to_vector(
        "user-a",
        3,
        "周六去游泳馆",
        {"tags": "周六,运动", "category": "日程", "created_at": "2026-08-21"},
    )

    vector_store.delete_from_vector("user-a", 3)

    assert vector_store.search_similar("user-a", "游泳") == []


def test_vector_search_is_isolated_by_user() -> None:
    vector_store.add_to_vector(
        "user-a", 1, "我周五游泳",
        {"tags": "周五,运动", "category": "日程", "created_at": "2026-08-21"},
    )

    assert vector_store.search_similar("user-b", "我周五做什么") == []
    assert vector_store.search_similar("user-a", "我周五做什么")[0]["content"] == "我周五游泳"
