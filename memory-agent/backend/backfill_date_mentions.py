"""Batch backfill extracted calendar dates for existing memories."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
import json
from typing import Any

from database import (
    count_memories_pending_date_extraction,
    list_memories_pending_date_extraction,
    mark_memory_date_extraction,
    replace_memory_date_mentions,
)
from llm_service import extract_date_mentions


TIMEZONE_NAME = "Asia/Shanghai"
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name=TIMEZONE_NAME)


def _reference_datetime(memory: dict[str, Any]) -> datetime:
    created_at = datetime.fromisoformat(str(memory["created_at"]))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(SHANGHAI_TIMEZONE)


def run_backfill(
    batch_size: int = 50, dry_run: bool = False, user_id: str | None = None
) -> dict[str, Any]:
    """Process at most one stable batch and return a non-sensitive summary."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    eligible_total = count_memories_pending_date_extraction(user_id)
    selected_count = min(batch_size, eligible_total)
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "eligible_total": eligible_total,
        "selected_count": selected_count,
        "processed": 0,
        "success": 0,
        "no_date": 0,
        "failed": 0,
        "failure_reasons": {},
    }
    if dry_run or selected_count == 0:
        return summary

    failures: Counter[str] = Counter()
    memories = list_memories_pending_date_extraction(batch_size, user_id)
    for memory in memories:
        scoped_user_id = str(memory["user_id"])
        memory_id = int(memory["id"])
        summary["processed"] += 1
        try:
            mentions = extract_date_mentions(
                str(memory["content"]), _reference_datetime(memory), TIMEZONE_NAME
            )
            replace_memory_date_mentions(scoped_user_id, memory_id, mentions)
            result_status = "success" if mentions else "no_date"
            mark_memory_date_extraction(scoped_user_id, memory_id, result_status)
            summary[result_status] += 1
        except Exception as exc:
            error_type = type(exc).__name__
            failures[error_type] += 1
            summary["failed"] += 1
            try:
                replace_memory_date_mentions(scoped_user_id, memory_id, [])
                mark_memory_date_extraction(scoped_user_id, memory_id, "failed", error_type)
            except Exception as state_exc:
                failures[f"state_recording:{type(state_exc).__name__}"] += 1
    summary["failure_reasons"] = dict(sorted(failures.items()))
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill calendar date mentions for one bounded batch of memories."
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user-id", help="Optionally restrict the batch to one user ID.")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    print(json.dumps(
        run_backfill(arguments.batch_size, arguments.dry_run, arguments.user_id),
        ensure_ascii=False,
        sort_keys=True,
    ))
