"""Tests for the SQLite memory persistence layer."""

from pathlib import Path

import pytest

import database


def create_test_user() -> str:
    return str(database.create_user("test@example.com", "test-hash")["id"])


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at an isolated temporary SQLite database."""
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    database.initialize_database()


def test_add_and_get_memory() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "周四洗头", "周四,个人护理", "日程")

    memory = database.get_memory(user_id, memory_id)

    assert memory is not None
    assert memory["id"] == memory_id
    assert memory["content"] == "周四洗头"
    assert memory["tags"] == "周四,个人护理"
    assert memory["category"] == "日程"
    assert memory["created_at"] is not None
    assert memory["updated_at"] is not None


def test_list_memories_paginates_and_filters_category() -> None:
    user_id = create_test_user()
    first_id = database.add_memory(user_id, "周四洗头", "周四", "日程")
    database.add_memory(user_id, "喜欢美式", "偏好", "偏好")
    third_id = database.add_memory(user_id, "周五游泳", "周五", "日程")

    assert [item["id"] for item in database.list_memories(user_id, limit=2, offset=0)] == [
        third_id,
        first_id + 1,
    ]
    assert [item["content"] for item in database.list_memories(user_id, category_filter="日程")] == [
        "周五游泳",
        "周四洗头",
    ]


def test_search_memories_by_keyword() -> None:
    user_id = create_test_user()
    database.add_memory(user_id, "周四洗头", "周四,个人护理", "日程")
    database.add_memory(user_id, "周五游泳", "周五,运动", "日程")

    results = database.search_memories_by_keyword(user_id, "洗")

    assert [item["content"] for item in results] == ["周四洗头"]


def test_update_memory() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "周四洗头", "周四", "日程")

    database.update_memory(user_id, memory_id, "周四晚上洗头", "周四,晚上,个人护理", "个人事务")

    memory = database.get_memory(user_id, memory_id)
    assert memory is not None
    assert memory["content"] == "周四晚上洗头"
    assert memory["tags"] == "周四,晚上,个人护理"
    assert memory["category"] == "个人事务"


def test_delete_memory() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "待删除记忆", None, None)

    database.delete_memory(user_id, memory_id)

    assert database.get_memory(user_id, memory_id) is None


def test_memories_are_isolated_by_user() -> None:
    user_a = str(database.create_user("a@example.com", "hash")["id"])
    user_b = str(database.create_user("b@example.com", "hash")["id"])
    memory_id = database.add_memory(user_a, "我周五游泳", "周五,运动", "日程")

    assert database.get_memory(user_b, memory_id) is None
    assert database.list_memories(user_b) == []
    assert database.search_memories_by_keyword(user_b, "游泳") == []
    assert database.delete_memory(user_b, memory_id) is False
    assert database.get_memory(user_a, memory_id) is not None
