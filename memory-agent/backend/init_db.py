"""Initialize SQLite storage and ensure the three example memories exist."""

from database import add_memory, initialize_database, search_memories_by_keyword


TEST_MEMORIES = [
    ("周四洗头", "周四,个人护理", "日程"),
    ("周五游泳", "周五,运动", "日程"),
    ("喜欢喝美式咖啡", "偏好,饮品", "偏好"),
]


def seed_test_memories() -> int:
    """Insert missing sample memories and return the number inserted."""
    inserted = 0
    for content, tags, category in TEST_MEMORIES:
        if not any(item["content"] == content for item in search_memories_by_keyword(content)):
            add_memory(content, tags, category)
            inserted += 1
    return inserted


if __name__ == "__main__":
    initialize_database()
    inserted = seed_test_memories()
    print(f"数据库已初始化；新增 {inserted} 条测试数据。")
