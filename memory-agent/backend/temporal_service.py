"""Shared temporal interpretation and structured memory lookup helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Callable

from settings import APP_TIMEZONE, TIMEZONE_NAME

_WEEKDAY_TO_OFFSET = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_RELATIVE_DAY_OFFSETS = {
    "前天": -2,
    "昨天": -1,
    "今天": 0,
    "明天": 1,
    "后天": 2,
}
_WEEK_RANGE_OFFSETS = {
    "上上周": -14,
    "上周": -7,
    "本周": 0,
    "这周": 0,
    "下周": 7,
    "下下周": 14,
}
_WEEKDAY_PREFIXES = {
    "上上周": -14,
    "上周": -7,
    "本周": 0,
    "这周": 0,
    "下周": 7,
    "下下周": 14,
}


# This only avoids an additional model call for clearly non-temporal questions.
# Absolute resolution is deliberately delegated to the same extractor used when
# memories are written.
_TEMPORAL_CUE = re.compile(
    r"今天|明天|后天|昨天|前天|本周|这周|下周|上周|上上周|下下周|"
    r"本周末|这周末|下周末|上周末|周[一二三四五六日天]|"
    r"星期[一二三四五六日天]|\d{1,4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
)


def current_reference_datetime() -> datetime:
    """Return a timezone-aware query reference in the configured timezone."""
    return datetime.now(APP_TIMEZONE)


class TemporalQueryError(RuntimeError):
    """A temporal cue was present but could not be resolved safely."""

    def __init__(self, code: str = "date_parse_failed") -> None:
        super().__init__(code)
        self.code = code


def query_has_temporal_cue(query: str) -> bool:
    return _TEMPORAL_CUE.search(query) is not None


def _fallback_relative_mentions(query: str, reference: datetime) -> list[dict[str, Any]]:
    """Resolve the common Chinese relative-date phrases used in ask-style queries."""
    local_reference = reference.astimezone(APP_TIMEZONE)
    local_date = local_reference.date()
    mentions: list[dict[str, Any]] = []
    normalized = query.strip()
    if not re.search(
        r"(我)?(明天|今天|后天|昨天|前天|这周|本周|下周|上周|需要做哪些|有什么安排|有什么计划|有哪些|要做什么|这周末|本周末|下周末)",
        normalized,
    ):
        return []

    for expression, offset in _RELATIVE_DAY_OFFSETS.items():
        if expression in normalized:
            target_date = local_date + timedelta(days=offset)
            mentions.append({
                "original_expression": expression,
                "normalized_text": "查询范围",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
                "confidence": 0.95,
            })

    for expression, week_offset in _WEEK_RANGE_OFFSETS.items():
        if expression in normalized and not re.search(rf"{re.escape(expression)}[一二三四五六日天]", normalized):
            week_start = local_date - timedelta(days=local_date.weekday())
            start_date = week_start + timedelta(days=week_offset)
            if expression.endswith("周末"):
                start_date = start_date + timedelta(days=5)
                end_date = start_date + timedelta(days=1)
            else:
                end_date = start_date + timedelta(days=6)
            mentions.append({
                "original_expression": expression,
                "normalized_text": "查询范围",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "confidence": 0.95,
            })

    for match in re.finditer(
        r"(?:(上上周|上周|本周|这周|下周|下下周)?(?:周|星期)([一二三四五六日天]))",
        normalized,
    ):
        prefix, weekday_token = match.groups()
        expression = match.group(0)
        if expression in {mention["original_expression"] for mention in mentions}:
            continue
        week_start = local_date - timedelta(days=local_date.weekday())
        relative_offset = _WEEKDAY_PREFIXES.get(prefix or "本周", 0)
        target_date = week_start + timedelta(days=relative_offset + _WEEKDAY_TO_OFFSET[weekday_token])
        if prefix is None or prefix == "":
            if target_date < local_date:
                target_date += timedelta(days=7)
        mentions.append({
            "original_expression": expression,
            "normalized_text": "查询范围",
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "confidence": 0.95,
        })

    for weekend_exp in ("本周末", "这周末", "下周末", "上周末"):
        if weekend_exp in normalized:
            week_start = local_date - timedelta(days=local_date.weekday())
            week_offset = {"上周末": -7, "本周末": 0, "这周末": 0, "下周末": 7}[weekend_exp]
            start_date = week_start + timedelta(days=week_offset + 5)
            end_date = start_date + timedelta(days=1)
            mentions.append({
                "original_expression": weekend_exp,
                "normalized_text": "查询范围",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "confidence": 0.95,
            })

    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for mention in mentions:
        key = (
            str(mention["original_expression"]),
            str(mention["start_date"]),
            str(mention["end_date"]),
        )
        unique[key] = mention
    return list(unique.values())


def resolve_query_window(
    query: str,
    extractor: Callable[..., list[dict[str, Any]]],
    reference_datetime: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any] | None:
    """Resolve a question with the memory date extractor and combine its ranges."""
    if not query_has_temporal_cue(query):
        return None
    reference = reference_datetime or (clock or current_reference_datetime)()
    if reference.tzinfo is None:
        raise ValueError("reference_datetime must be timezone-aware")
    try:
        mentions = extractor(query, reference, TIMEZONE_NAME, purpose="query")
    except Exception:
        mentions = []
    if not mentions:
        mentions = _fallback_relative_mentions(query, reference)
    if not mentions:
        raise TemporalQueryError("date_not_resolved")
    expressions = [str(item["original_expression"]) for item in mentions]
    semantic_query = query
    for expression in sorted(set(expressions), key=len, reverse=True):
        semantic_query = semantic_query.replace(expression, " ")
    semantic_query = " ".join(semantic_query.split())
    return {
        "status": "resolved",
        "start_date": min(str(item["start_date"]) for item in mentions),
        "end_date": max(str(item["end_date"]) for item in mentions),
        "timezone": TIMEZONE_NAME,
        "reference_datetime": reference.astimezone(APP_TIMEZONE).isoformat(),
        "original_expressions": expressions,
        "semantic_query": semantic_query,
    }
