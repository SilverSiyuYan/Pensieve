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
    memory_id = database.add_memory(user_id, "周四洗头", "周四,个人护理", "todo")

    memory = database.get_memory(user_id, memory_id)

    assert memory is not None
    assert memory["id"] == memory_id
    assert memory["content"] == "周四洗头"
    assert memory["tags"] == "周四,个人护理"
    assert memory["category"] == "todo"
    assert memory["created_at"] is not None
    assert memory["updated_at"] is not None


def test_list_memories_paginates_and_filters_category() -> None:
    user_id = create_test_user()
    first_id = database.add_memory(user_id, "周四洗头", "周四", "todo")
    database.add_memory(user_id, "喜欢美式", "偏好", "note")
    third_id = database.add_memory(user_id, "周五游泳", "周五", "todo")

    assert [item["id"] for item in database.list_memories(user_id, limit=2, offset=0)] == [
        third_id,
        first_id + 1,
    ]
    assert [item["content"] for item in database.list_memories(user_id, category_filter="todo")] == [
        "周五游泳",
        "周四洗头",
    ]


def test_list_memories_filters_all_categories() -> None:
    user_id = create_test_user()
    for category in database.MEMORY_CATEGORIES:
        database.add_memory(user_id, category, None, category)
    for category in database.MEMORY_CATEGORIES:
        assert [item["content"] for item in database.list_memories(user_id, category_filter=category)] == [category]
    assert {item["content"] for item in database.list_memories(user_id)} == set(database.MEMORY_CATEGORIES)


def test_list_memories_sorts_stably_and_combines_pagination() -> None:
    user_id = create_test_user()
    first = database.add_memory(user_id, "最早", None, "todo")
    second = database.add_memory(user_id, "同一时间较早 ID", None, "todo")
    third = database.add_memory(user_id, "同一时间较晚 ID", None, "todo")
    fourth = database.add_memory(user_id, "其他分类", None, "note")
    with database._connect() as connection:
        connection.execute("UPDATE memories SET created_at = '2026-01-01 08:00:00' WHERE id = ?", (first,))
        connection.execute(
            "UPDATE memories SET created_at = '2026-01-02 08:00:00' WHERE id IN (?, ?, ?)",
            (second, third, fourth),
        )

    assert [item["id"] for item in database.list_memories(user_id)] == [fourth, third, second, first]
    assert [item["id"] for item in database.list_memories(user_id, sort_order="asc")] == [first, second, third, fourth]
    assert [item["id"] for item in database.list_memories(
        user_id, category_filter="todo", sort_order="desc", limit=1, offset=1
    )] == [second]


def test_list_memories_rejects_invalid_filters() -> None:
    user_id = create_test_user()
    with pytest.raises(ValueError, match="category"):
        database.list_memories(user_id, category_filter="private")
    with pytest.raises(ValueError, match="sort_order"):
        database.list_memories(user_id, sort_order="sideways")


def test_search_memories_by_keyword() -> None:
    user_id = create_test_user()
    database.add_memory(user_id, "周四洗头", "周四,个人护理", "todo")
    database.add_memory(user_id, "周五游泳", "周五,运动", "todo")

    results = database.search_memories_by_keyword(user_id, "洗")

    assert [item["content"] for item in results] == ["周四洗头"]


def test_update_memory() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "周四洗头", "周四", "todo")

    database.update_memory(user_id, memory_id, "周四晚上洗头", "周四,晚上,个人护理", "note")

    memory = database.get_memory(user_id, memory_id)
    assert memory is not None
    assert memory["content"] == "周四晚上洗头"
    assert memory["tags"] == "周四,晚上,个人护理"
    assert memory["category"] == "note"


def test_delete_memory() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "待删除记忆", None, None)

    database.delete_memory(user_id, memory_id)

    assert database.get_memory(user_id, memory_id) is None


def test_memories_are_isolated_by_user() -> None:
    user_a = str(database.create_user("a@example.com", "hash")["id"])
    user_b = str(database.create_user("b@example.com", "hash")["id"])
    memory_id = database.add_memory(user_a, "我周五游泳", "周五,运动", "todo")

    assert database.get_memory(user_b, memory_id) is None
    assert database.list_memories(user_b) == []
    assert database.search_memories_by_keyword(user_b, "游泳") == []
    assert database.delete_memory(user_b, memory_id) is False
    assert database.get_memory(user_a, memory_id) is not None


def test_category_is_required_and_constrained() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "普通便签", None, None)
    assert database.get_memory(user_id, memory_id)["category"] == "note"
    with pytest.raises(Exception):
        database.add_memory(user_id, "非法分类", None, "other")


def test_existing_memories_are_migrated_to_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_path = tmp_path / "legacy-category.db"
    monkeypatch.setattr(database, "DATABASE_PATH", legacy_path)
    connection = database._connect()
    connection.execute(database.CREATE_USERS_SQL)
    connection.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id), content TEXT NOT NULL,
            tags TEXT, category TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    connection.execute("INSERT INTO users (id, email, password_hash) VALUES ('u1', 'old@example.com', 'x')")
    connection.execute("INSERT INTO memories (user_id, content, category) VALUES ('u1', '旧记录', '日程')")
    connection.execute(
        """CREATE TABLE memory_embeddings (
            memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            vector_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    connection.execute(
        "INSERT INTO memory_embeddings (memory_id, user_id, vector_id) VALUES (1, 'u1', 'u1:1')"
    )
    connection.commit()
    connection.close()

    database.initialize_database()
    database.initialize_database()

    memory = database.get_memory("u1", 1)
    assert memory is not None
    assert memory["content"] == "旧记录"
    assert memory["category"] == "note"
    with database._connect() as migrated:
        column = next(row for row in migrated.execute("PRAGMA table_info(memories)") if row["name"] == "category")
        assert column["notnull"] == 1
        assert str(column["dflt_value"]).strip("'") == "note"
        assert migrated.execute("SELECT vector_id FROM memory_embeddings").fetchone()["vector_id"] == "u1:1"
        foreign_key_tables = {
            row["table"] for row in migrated.execute("PRAGMA foreign_key_list(memory_embeddings)")
        }
        assert "memories" in foreign_key_tables
        assert "memories_pre_category_enum" not in foreign_key_tables
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"] == 1
