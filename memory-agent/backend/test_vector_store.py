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
        1,
        "周四洗头",
        {"tags": "周四,个人护理", "category": "日程", "created_at": "2026-08-21"},
    )
    vector_store.add_to_vector(
        2,
        "周五游泳",
        {"tags": "周五,运动", "category": "日程", "created_at": "2026-08-21"},
    )

    weekly_results = vector_store.search_similar("这周做了什么")
    cleaning_results = vector_store.search_similar("这周个人清洁安排")
    exercise_results = vector_store.search_similar("本周运动计划")

    assert {item["content"] for item in weekly_results} == {"周四洗头", "周五游泳"}
    assert any("洗头" in item["content"] for item in cleaning_results)
    assert any("游泳" in item["content"] for item in exercise_results)


def test_delete_from_vector_removes_memory() -> None:
    vector_store.add_to_vector(
        3,
        "周六去游泳馆",
        {"tags": "周六,运动", "category": "日程", "created_at": "2026-08-21"},
    )

    vector_store.delete_from_vector(3)

    assert vector_store.search_similar("游泳") == []
