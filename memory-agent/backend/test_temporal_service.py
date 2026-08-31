"""Tests for shared query-time temporal resolution."""

from datetime import datetime
import temporal_service


def test_non_temporal_query_does_not_call_extractor() -> None:
    def fail(*args, **kwargs):
        raise AssertionError("extractor should not be called")

    assert temporal_service.resolve_query_window("我记过什么运动计划", fail) is None


def test_query_uses_shared_extractor_and_configured_timezone() -> None:
    calls = []

    def extract(content, reference, timezone_name, purpose="memory"):
        calls.append((content, reference, timezone_name, purpose))
        return [{
            "original_expression": "明天",
            "normalized_text": "要做什么",
            "start_date": "2026-08-21",
            "end_date": "2026-08-21",
            "confidence": 0.99,
        }]

    reference = datetime(2026, 8, 20, 23, 30, tzinfo=temporal_service.APP_TIMEZONE)
    result = temporal_service.resolve_query_window("明天要做什么", extract, reference)

    assert result == {
        "status": "resolved",
        "start_date": "2026-08-21",
        "end_date": "2026-08-21",
        "timezone": "Asia/Shanghai",
        "reference_datetime": "2026-08-20T23:30:00+08:00",
        "original_expressions": ["明天"],
        "semantic_query": "要做什么",
    }
    assert calls == [("明天要做什么", reference, "Asia/Shanghai", "query")]


def test_temporal_parse_failure_is_explicit_and_never_becomes_text_search() -> None:
    def extract(*args, **kwargs):
        return []

    try:
        temporal_service.resolve_query_window(
            "明天要做什么",
            extract,
            datetime(2026, 8, 20, 23, 30, tzinfo=temporal_service.APP_TIMEZONE),
        )
    except temporal_service.TemporalQueryError as exc:
        assert exc.code == "date_not_resolved"
    else:
        raise AssertionError("temporal parsing failure must be explicit")


def test_common_chinese_relative_queries_have_local_fallback_resolution() -> None:
    reference = datetime(2026, 8, 20, 23, 30, tzinfo=temporal_service.APP_TIMEZONE)

    tomorrow = temporal_service.resolve_query_window(
        "我明天需要做哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert tomorrow == {
        "status": "resolved",
        "start_date": "2026-08-21",
        "end_date": "2026-08-21",
        "timezone": "Asia/Shanghai",
        "reference_datetime": "2026-08-20T23:30:00+08:00",
        "original_expressions": ["明天"],
        "semantic_query": "我 需要做哪些事情",
    }

    this_week = temporal_service.resolve_query_window(
        "我这周需要做哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert this_week == {
        "status": "resolved",
        "start_date": "2026-08-17",
        "end_date": "2026-08-23",
        "timezone": "Asia/Shanghai",
        "reference_datetime": "2026-08-20T23:30:00+08:00",
        "original_expressions": ["这周"],
        "semantic_query": "我 需要做哪些事情",
    }

    this_weekend = temporal_service.resolve_query_window(
        "我这周末需要做哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert this_weekend == {
        "status": "resolved",
        "start_date": "2026-08-22",
        "end_date": "2026-08-23",
        "timezone": "Asia/Shanghai",
        "reference_datetime": "2026-08-20T23:30:00+08:00",
        "original_expressions": ["这周末"],
        "semantic_query": "我 需要做哪些事情",
    }

    tomorrow_variant = temporal_service.resolve_query_window(
        "我明天要干哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert tomorrow_variant["status"] == "resolved"
    assert tomorrow_variant["start_date"] == "2026-08-21"
    assert tomorrow_variant["end_date"] == "2026-08-21"
    assert "明天" in tomorrow_variant["original_expressions"]

    next_weekend = temporal_service.resolve_query_window(
        "我下周末要干哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert next_weekend["status"] == "resolved"
    assert next_weekend["start_date"] == "2026-08-29"
    assert next_weekend["end_date"] == "2026-08-30"
    assert "下周末" in next_weekend["original_expressions"]

    next_next_week = temporal_service.resolve_query_window(
        "我下下周要干哪些事情",
        lambda *args, **kwargs: [],
        reference,
    )
    assert next_next_week["status"] == "resolved"
    assert next_next_week["start_date"] == "2026-08-31"
    assert next_next_week["end_date"] == "2026-09-06"
    assert "下下周" in next_next_week["original_expressions"]
