"""Shared temporal interpretation and structured memory lookup helpers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable

from settings import APP_TIMEZONE, TIMEZONE_NAME


# This only avoids an additional model call for clearly non-temporal questions.
# Absolute resolution is deliberately delegated to the same extractor used when
# memories are written.
_TEMPORAL_CUE = re.compile(
    r"今天|明天|后天|昨天|前天|本周|这周|下周|上周|周[一二三四五六日天]|"
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
    except Exception as exc:
        raise TemporalQueryError() from exc
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
