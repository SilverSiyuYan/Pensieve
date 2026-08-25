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
