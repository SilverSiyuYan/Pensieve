"""Regression tests for calendar/query use of the same parsed dates."""

import main


def test_temporal_query_filters_before_vector_and_keyword_search(monkeypatch) -> None:
    expected = [{
        "id": 3,
        "content": "明天交作业",
        "created_at": "2026-08-20 02:00:00",
        "tags": "",
        "date_mentions": [{
            "original_expression": "明天",
            "normalized_text": "交作业",
            "start_date": "2026-08-21",
            "end_date": "2026-08-21",
            "timezone_name": "Asia/Shanghai",
            "temporal_type": "date",
            "confidence": 0.99,
        }],
    }]
    temporal_filter = {
        "status": "resolved",
        "start_date": "2026-08-21",
        "end_date": "2026-08-21",
        "timezone": "Asia/Shanghai",
        "semantic_query": "要做什么",
    }
    monkeypatch.setattr(
        main, "resolve_query_window", lambda query, extractor, clock=None: temporal_filter
    )
    monkeypatch.setattr(
        main,
        "list_memories_mentioning_range",
        lambda user_id, start, end: expected,
    )
    monkeypatch.setattr(
        main, "search_similar", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(
        main,
        "search_memories_by_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        main,
        "search_similar_within",
        lambda user_id, query, memory_ids, top_k: [{"memory_id": 3}],
    )
    monkeypatch.setattr(
        main,
        "generate_integrated_answer",
        lambda query, memories, temporal_filter: "8 月 21 日交作业",
    )

    result = main._query_memory("user-a", "明天要做什么")

    assert result["source_memories"] == expected
    assert result["temporal_filter"] == temporal_filter


def test_non_temporal_query_keeps_hybrid_retrieval(monkeypatch) -> None:
    monkeypatch.setattr(main, "resolve_query_window", lambda query, extractor, clock=None: None)
    monkeypatch.setattr(main, "search_similar", lambda user_id, query, top_k: [])
    monkeypatch.setattr(
        main,
        "search_memories_by_keyword",
        lambda user_id, query: [{"id": 1, "content": "游泳计划"}],
    )
    monkeypatch.setattr(main, "generate_integrated_answer", lambda query, memories: "游泳计划")

    result = main._query_memory("user-a", "运动计划")

    assert [item["id"] for item in result["source_memories"]] == [1]
    assert result["temporal_filter"] is None


def test_temporal_parse_failure_does_not_fall_back_to_any_retrieval(monkeypatch) -> None:
    def fail(query, extractor, clock=None):
        raise main.TemporalQueryError("date_parse_failed")

    monkeypatch.setattr(main, "resolve_query_window", fail)
    monkeypatch.setattr(
        main, "search_similar", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(
        main,
        "search_memories_by_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    result = main._query_memory("user-a", "明天要做什么")

    assert result["source_memories"] == []
    assert result["temporal_filter"] == {
        "status": "failed",
        "error": "date_parse_failed",
        "timezone": "Asia/Shanghai",
    }
