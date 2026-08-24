"""Tests for bounded, resumable historical date extraction backfill."""

from pathlib import Path

import pytest

import backfill_date_mentions
import database


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    database.initialize_database()


def _user(email: str) -> str:
    return str(database.create_user(email, "hash")["id"])


def test_dry_run_only_counts_eligible_bounded_batch() -> None:
    user_id = _user("dry-run@example.com")
    completed = database.add_memory(user_id, "已处理", None, "note")
    database.mark_memory_date_extraction(user_id, completed, "no_date")
    pending_a = database.add_memory(user_id, "明天开会", None, "todo")
    database.add_memory(user_id, "后天游泳", None, "todo")

    summary = backfill_date_mentions.run_backfill(batch_size=1, dry_run=True)

    assert summary == {
        "dry_run": True,
        "eligible_total": 2,
        "selected_count": 1,
        "processed": 0,
        "success": 0,
        "no_date": 0,
        "failed": 0,
        "failure_reasons": {},
    }
    assert database.get_memory_date_extraction(user_id, pending_a) is None


def test_backfill_records_each_result_continues_after_failure_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = _user("batch@example.com")
    skipped = database.add_memory(user_id, "已经处理", None, "note")
    dated = database.add_memory(user_id, "明天开会", None, "todo")
    no_date = database.add_memory(user_id, "以后学习英语", None, "todo")
    failed = database.add_memory(user_id, "模型暂时失败", None, "note")
    database.mark_memory_date_extraction(user_id, skipped, "success")

    def extract(content: str, reference, timezone: str) -> list[dict]:
        if content == "模型暂时失败":
            raise TimeoutError("sensitive provider detail must not enter summary")
        if content == "以后学习英语":
            return []
        return [{
            "original_expression": "明天",
            "normalized_text": "开会",
            "start_date": "2026-08-25",
            "end_date": "2026-08-25",
            "confidence": 0.95,
        }]

    monkeypatch.setattr(backfill_date_mentions, "extract_date_mentions", extract)
    first = backfill_date_mentions.run_backfill(batch_size=10)

    assert first["processed"] == 3
    assert first["success"] == 1
    assert first["no_date"] == 1
    assert first["failed"] == 1
    assert first["failure_reasons"] == {"TimeoutError": 1}
    assert "sensitive" not in str(first)
    assert database.get_memory_date_extraction(user_id, dated)["status"] == "success"
    assert database.get_memory_date_extraction(user_id, no_date)["status"] == "no_date"
    assert database.get_memory_date_extraction(user_id, failed)["status"] == "failed"
    assert len(database.list_memory_date_mentions(user_id, dated)) == 1

    monkeypatch.setattr(backfill_date_mentions, "extract_date_mentions", lambda *args: [])
    second = backfill_date_mentions.run_backfill(batch_size=10)

    assert second["processed"] == 1
    assert second["no_date"] == 1
    assert second["failed"] == 0
    assert database.count_memories_pending_date_extraction() == 0
    assert len(database.list_memory_date_mentions(user_id, dated)) == 1


def test_backfill_user_filter_preserves_user_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    user_a = _user("backfill-a@example.com")
    user_b = _user("backfill-b@example.com")
    memory_a = database.add_memory(user_a, "明天开会", None, "todo")
    memory_b = database.add_memory(user_b, "明天游泳", None, "todo")
    monkeypatch.setattr(backfill_date_mentions, "extract_date_mentions", lambda *args: [])

    summary = backfill_date_mentions.run_backfill(batch_size=10, user_id=user_a)

    assert summary["processed"] == 1
    assert database.get_memory_date_extraction(user_a, memory_a)["status"] == "no_date"
    assert database.get_memory_date_extraction(user_b, memory_b) is None
    assert database.count_memories_pending_date_extraction(user_a) == 0
    assert database.count_memories_pending_date_extraction(user_b) == 1
