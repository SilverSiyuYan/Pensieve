"""Tests for the SQLite memory persistence layer."""

from pathlib import Path
import sqlite3

import pytest

import database


class _ReadOnlyConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement: str):
        if statement == "SELECT 1":
            return self
        raise sqlite3.OperationalError("attempt to write a readonly database")

    def fetchone(self):
        return (1,)

    def rollback(self):
        return None


def test_database_health_rejects_read_only_connection(monkeypatch) -> None:
    monkeypatch.setattr(database, "_connect", lambda: _ReadOnlyConnection())

    assert database.database_is_accessible() is False


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


def test_memory_date_mentions_support_multiple_dates_and_stable_iso_format() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "明天开会，8 月 30 日提交报告", None, "todo")

    first_id = database.add_memory_date_mention(
        user_id, memory_id, "2026-08-25", "2026-08-25", "明天", "开会", 0.98
    )
    second_id = database.add_memory_date_mention(
        user_id, memory_id, "2026-08-30", "2026-08-30", "8 月 30 日", "提交报告", 0.95
    )

    mentions = database.list_memory_date_mentions(user_id, memory_id)
    assert [mention["id"] for mention in mentions] == [first_id, second_id]
    assert mentions[0]["start_date"] == "2026-08-25"
    assert mentions[0]["end_date"] == "2026-08-25"
    assert mentions[1]["original_expression"] == "8 月 30 日"
    assert mentions[1]["normalized_text"] == "提交报告"
    assert mentions[0]["timezone_name"] == "Asia/Shanghai"
    assert mentions[0]["temporal_type"] == "date"


def test_memory_range_query_reuses_calendar_date_mentions() -> None:
    user_id = create_test_user()
    old = database.add_memory(user_id, "明天去银行", None, "todo")
    current = database.add_memory(user_id, "明天交作业", None, "todo")
    database.add_memory_date_mention(
        user_id, old, "2026-08-11", "2026-08-11", "明天", "去银行", 0.98
    )
    database.add_memory_date_mention(
        user_id, current, "2026-08-21", "2026-08-21", "明天", "交作业", 0.99
    )

    results = database.list_memories_mentioning_range(
        user_id, "2026-08-21", "2026-08-21"
    )

    assert [item["id"] for item in results] == [current]
    assert results[0]["date_mentions"][0]["original_expression"] == "明天"
    assert results[0]["date_mentions"][0]["timezone_name"] == "Asia/Shanghai"


def test_memory_date_mentions_validate_dates_ranges_and_confidence() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "日期校验", None, "note")

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        database.add_memory_date_mention(user_id, memory_id, "2026-02-30", "2026-03-01", "日期", "事件", 1.0)
    with pytest.raises(ValueError, match="start_date"):
        database.add_memory_date_mention(user_id, memory_id, "2026-09-02", "2026-09-01", "日期", "事件", 1.0)
    with pytest.raises(ValueError, match="confidence"):
        database.add_memory_date_mention(user_id, memory_id, "2026-09-01", "2026-09-01", "日期", "事件", 1.1)


def test_memory_date_mentions_are_isolated_by_composite_foreign_key() -> None:
    user_a = str(database.create_user("date-a@example.com", "hash")["id"])
    user_b = str(database.create_user("date-b@example.com", "hash")["id"])
    memory_id = database.add_memory(user_a, "A 的明日安排", None, "todo")
    database.add_memory_date_mention(
        user_a, memory_id, "2026-08-25", "2026-08-25", "明日", "A 的安排", 0.9
    )

    assert database.list_memory_date_mentions(user_b) == []
    with pytest.raises(Exception):
        database.add_memory_date_mention(
            user_b, memory_id, "2026-08-25", "2026-08-25", "明日", "越权关联", 0.9
        )


def test_deleting_memory_cascades_to_date_mentions() -> None:
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "下周一到周三出差", None, "todo")
    database.add_memory_date_mention(
        user_id, memory_id, "2026-08-31", "2026-09-02", "下周一到周三", "出差", 0.9
    )
    database.mark_memory_date_extraction(user_id, memory_id, "success")

    assert database.delete_memory(user_id, memory_id) is True
    assert database.list_memory_date_mentions(user_id, memory_id) == []
    assert database.get_memory_date_extraction(user_id, memory_id) is None


def test_date_mention_migration_preserves_existing_memories_and_creates_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_path = tmp_path / "date-mention-migration.db"
    monkeypatch.setattr(database, "DATABASE_PATH", migration_path)
    database.initialize_database()
    user_id = create_test_user()
    memory_id = database.add_memory(user_id, "迁移前记忆", None, "note")
    with database._connect() as connection:
        connection.execute("DROP TABLE memory_date_mentions")

    database.initialize_database()
    database.initialize_database()

    assert database.get_memory(user_id, memory_id)["content"] == "迁移前记忆"
    with database._connect() as migrated:
        indexes = {
            row["name"] for row in migrated.execute("PRAGMA index_list(memory_date_mentions)")
        }
        assert {
            "idx_memory_date_mentions_memory",
            "idx_memory_date_mentions_user_dates",
            "idx_memory_date_mentions_start_date",
            "idx_memory_date_mentions_end_date",
        } <= indexes
        foreign_keys = migrated.execute("PRAGMA foreign_key_list(memory_date_mentions)").fetchall()
        assert {(row["from"], row["to"], row["on_delete"]) for row in foreign_keys} == {
            ("user_id", "user_id", "CASCADE"),
            ("memory_id", "id", "CASCADE"),
        }
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


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
