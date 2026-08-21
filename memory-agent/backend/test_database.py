"""Tests for the SQLite memory persistence layer."""

from pathlib import Path

import pytest

import database


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at an isolated temporary SQLite database."""
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    database.initialize_database()


def test_add_and_get_memory() -> None:
    memory_id = database.add_memory("周四洗头", "周四,个人护理", "日程")

    memory = database.get_memory(memory_id)

    assert memory is not None
    assert memory["id"] == memory_id
    assert memory["content"] == "周四洗头"
    assert memory["tags"] == "周四,个人护理"
    assert memory["category"] == "日程"
    assert memory["created_at"] is not None
    assert memory["updated_at"] is not None


def test_list_memories_paginates_and_filters_category() -> None:
    first_id = database.add_memory("周四洗头", "周四", "日程")
    database.add_memory("喜欢美式", "偏好", "偏好")
    third_id = database.add_memory("周五游泳", "周五", "日程")

    assert [item["id"] for item in database.list_memories(limit=2, offset=0)] == [
        third_id,
        first_id + 1,
    ]
    assert [item["content"] for item in database.list_memories(category_filter="日程")] == [
        "周五游泳",
        "周四洗头",
    ]


def test_search_memories_by_keyword() -> None:
    database.add_memory("周四洗头", "周四,个人护理", "日程")
    database.add_memory("周五游泳", "周五,运动", "日程")

    results = database.search_memories_by_keyword("洗")

    assert [item["content"] for item in results] == ["周四洗头"]


def test_update_memory() -> None:
    memory_id = database.add_memory("周四洗头", "周四", "日程")

    database.update_memory(memory_id, "周四晚上洗头", "周四,晚上,个人护理", "个人事务")

    memory = database.get_memory(memory_id)
    assert memory is not None
    assert memory["content"] == "周四晚上洗头"
    assert memory["tags"] == "周四,晚上,个人护理"
    assert memory["category"] == "个人事务"


def test_delete_memory() -> None:
    memory_id = database.add_memory("待删除记忆", None, None)

    database.delete_memory(memory_id)

    assert database.get_memory(memory_id) is None
