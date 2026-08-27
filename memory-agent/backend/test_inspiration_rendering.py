"""Backend contracts for optional, user-scoped inspiration rendering."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import database
import inspiration_rendering
import llm_service
import main
from search_service import SearchHit, SearchServiceError
import vector_store


class FakeProvider:
    name = "fake-search"

    def __init__(self, hits: list[SearchHit] | None = None, error: Exception | None = None):
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> list[SearchHit]:
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.hits


def hits(count: int) -> list[SearchHit]:
    return [
        SearchHit(
            title=f"真实资料 {index}",
            url=f"https://source{index}.example/articles/{index}",
            snippet=f"资料 {index} 提供可验证的方法和案例。",
            score=1 - index / 100,
        )
        for index in range(1, count + 1)
    ]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(vector_store, "CHROMA_PATH", str(tmp_path / "chroma_data"))
    monkeypatch.setattr(main, "extract_date_mentions", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        inspiration_rendering,
        "summarize_inspiration_search_results",
        lambda content, results: {
            item["rank"]: f"该资料可从第 {item['rank']} 个角度扩展这条灵感。"
            for item in results
        },
    )
    with TestClient(main.application) as test_client:
        yield test_client


def register(client: TestClient, email: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "safe-password-123"}
    )
    body = response.json()
    return body["access_token"], body["user"]["id"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_old_request_and_disabled_switch_do_not_initialize_search_or_write_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(
        inspiration_rendering,
        "create_search_provider",
        lambda: pytest.fail("disabled rendering initialized the search provider"),
    )
    token, user_id = register(client, "disabled@example.com")

    old_response = client.post(
        "/api/memory/store", headers=auth(token), json={"content": "一个旧格式请求"}
    )
    disabled_response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "显式关闭", "inspiration_rendering": False},
    )

    assert old_response.json() == {
        "success": True,
        "memory_id": old_response.json()["memory_id"],
        "message": "记忆已保存",
    }
    assert "inspiration_rendering" not in disabled_response.json()
    assert database.get_inspiration_rendering(user_id, old_response.json()["memory_id"]) is None
    assert database.get_inspiration_rendering(user_id, disabled_response.json()["memory_id"]) is None


@pytest.mark.parametrize("category", ["inspiration", "todo", "knowledge", "note"])
def test_disabled_rendering_preserves_original_save_flow_for_every_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, category: str
) -> None:
    monkeypatch.setattr(main, "classify_memory_category", lambda content: category)
    monkeypatch.setattr(
        inspiration_rendering,
        "create_search_provider",
        lambda: pytest.fail("disabled rendering initialized the search provider"),
    )
    token, _ = register(client, f"disabled-{category}@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": f"关闭渲染的 {category}", "inspiration_rendering": False},
    )

    assert response.status_code == 200
    assert "inspiration_rendering" not in response.json()
    assert response.json()["success"] is True
    assert client.get("/api/memories", headers=auth(token)).json()[0]["category"] == category


def test_enabled_inspiration_returns_five_provider_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(7))
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "inspiration@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "研究一种新的记忆整理方式", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "succeeded"
    assert rendering["result_count"] == 5
    assert [item["rank"] for item in rendering["results"]] == [1, 2, 3, 4, 5]
    assert all(item["url"].startswith("https://source") for item in rendering["results"])
    assert len(provider.calls) == 1


def test_enabled_non_inspiration_is_saved_and_skips_search(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "todo")
    monkeypatch.setattr(
        inspiration_rendering,
        "create_search_provider",
        lambda: pytest.fail("non-inspiration initialized the search provider"),
    )
    token, _ = register(client, "skipped@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "明天提交报告", "inspiration_rendering": True},
    )

    assert response.json()["memory_saved"] is True
    assert response.json()["inspiration_rendering"]["status"] == "skipped_not_inspiration"
    assert client.get("/api/memories", headers=auth(token)).json()[0]["category"] == "todo"


def test_search_timeout_does_not_rollback_or_duplicate_memory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(error=SearchServiceError("timeout", "Search timed out", 2))
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "timeout-rendering@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "一个会保存的灵感", "inspiration_rendering": True},
    )

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    rendering = response.json()["inspiration_rendering"]
    assert rendering["status"] == "failed"
    assert rendering["error_code"] == "timeout"
    assert rendering["message"] == "记忆已保存，但灵感渲染暂时不可用。"
    assert rendering["results"] == []
    memories = client.get("/api/memories", headers=auth(token)).json()
    assert [memory["content"] for memory in memories] == ["一个会保存的灵感"]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("error_code", ["auth_failed", "rate_limited", "provider_error"])
def test_search_provider_errors_are_structured_and_keep_memory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error_code: str
) -> None:
    provider = FakeProvider(
        error=SearchServiceError(error_code, "sensitive upstream detail", 1)
    )
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, f"{error_code}@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": f"{error_code} 后仍保存的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "failed"
    assert rendering["error_code"] == error_code
    assert rendering["message"] == "记忆已保存，但灵感渲染暂时不可用。"
    assert "sensitive upstream detail" not in response.text
    assert rendering["results"] == []


def test_search_failure_log_is_diagnostic_and_excludes_sensitive_values(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = FakeProvider(
        error=SearchServiceError(
            "provider_error",
            "API_KEY=secret Authorization=Bearer-token password=hunter2",
            1,
        )
    )
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "safe-render-log@example.com")

    with caplog.at_level("WARNING"):
        response = client.post(
            "/api/memory/store",
            headers=auth(token),
            json={"content": "日志不得包含这条原始内容", "inspiration_rendering": True},
        )

    assert response.status_code == 200
    assert "stage=search" in caplog.text
    assert "error_type=SearchServiceError" in caplog.text
    assert "error_code=provider_error" in caplog.text
    for secret in (
        "API_KEY=secret",
        "Authorization",
        "Bearer-token",
        "password=hunter2",
        "日志不得包含这条原始内容",
        token,
    ):
        assert secret not in caplog.text


def test_empty_search_results_are_explicit_and_never_fabricated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([])
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "empty-rendering@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "暂时找不到资料的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "failed"
    assert rendering["error_code"] == "insufficient_results"
    assert rendering["message"] == "记忆已保存，但暂未找到可展示的相关资料。"
    assert rendering["result_count"] == 0
    assert rendering["results"] == []


def test_missing_search_configuration_is_structured_and_keeps_memory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INSPIRATION_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("INSPIRATION_RENDERING_PROVIDER", "tavily")
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    token, _ = register(client, "missing-search-config@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "搜索未配置时仍保存的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "failed"
    assert rendering["error_code"] == "not_configured"
    assert rendering["message"] == "记忆已保存，但灵感渲染暂时不可用。"
    assert rendering["results"] == []


def test_summary_model_failure_returns_real_search_results_as_partial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(5))
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    monkeypatch.setattr(
        inspiration_rendering,
        "summarize_inspiration_search_results",
        lambda content, results: (_ for _ in ()).throw(TimeoutError("model timeout")),
    )
    token, _ = register(client, "summary-timeout@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "摘要模型失败时仍保存的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "partial"
    assert rendering["error_code"] == "invalid_response"
    assert rendering["result_count"] == 5
    assert all(item["url"].startswith("https://source") for item in rendering["results"])


def test_missing_summary_model_key_keeps_memory_and_real_search_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(5))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    monkeypatch.setattr(
        inspiration_rendering,
        "summarize_inspiration_search_results",
        llm_service.summarize_inspiration_search_results,
    )
    token, _ = register(client, "missing-summary-key@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "模型密钥缺失时仍保存的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert response.status_code == 200
    assert response.json()["memory_saved"] is True
    assert rendering["status"] == "partial"
    assert rendering["error_code"] == "invalid_response"
    assert rendering["result_count"] == 5
    assert len(client.get("/api/memories", headers=auth(token)).json()) == 1


def test_rendering_result_limit_cannot_exceed_five(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(8))
    monkeypatch.setenv("INSPIRATION_RENDERING_RESULT_LIMIT", "10")
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "five-result-limit@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "最多返回五条资料", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert rendering["status"] == "succeeded"
    assert rendering["requested_count"] == 5
    assert rendering["result_count"] == 5
    assert len(rendering["results"]) == 5


def test_fewer_than_five_results_are_partial_and_never_filled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(3))
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "partial@example.com")

    response = client.post(
        "/api/memory/store",
        headers=auth(token),
        json={"content": "只有少量资料的灵感", "inspiration_rendering": True},
    )
    rendering = response.json()["inspiration_rendering"]

    assert rendering["status"] == "partial"
    assert rendering["error_code"] == "insufficient_results"
    assert rendering["result_count"] == len(rendering["results"]) == 3


def test_url_normalization_rejects_protocols_and_deduplicates_page_variants() -> None:
    normalized = inspiration_rendering.normalize_search_hits(
        [
            SearchHit("首条", "https://Example.com/page?utm_source=x&id=1#part", "有效摘要", 0.9),
            SearchHit("同页", "https://example.com/page?id=2", "另一个摘要", 0.8),
            SearchHit("脚本", "javascript:alert(1)", "不能接受", 1.0),
            SearchHit("空摘要", "https://empty.example/page", "", 0.7),
            SearchHit("另一页", "http://safe.example/article", "有效内容", 0.6),
        ],
        5,
    )

    assert [item["title"] for item in normalized] == ["首条", "另一页"]
    assert normalized[0]["url"] == "https://example.com/page?id=1"
    assert normalized[0]["source_domain"] == "example.com"


def test_rendering_read_is_user_scoped_and_deletion_cascades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(2))
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token_a, user_a = register(client, "render-a@example.com")
    token_b, _ = register(client, "render-b@example.com")
    stored = client.post(
        "/api/memory/store",
        headers=auth(token_a),
        json={"content": "A 的灵感", "inspiration_rendering": True},
    ).json()
    path = f"/api/memories/{stored['memory_id']}/inspiration-rendering"

    assert client.get(path, headers=auth(token_a)).status_code == 200
    assert client.get(path, headers=auth(token_b)).status_code == 404
    assert client.delete(f"/api/memory/{stored['memory_id']}", headers=auth(token_a)).status_code == 200
    assert database.get_inspiration_rendering(user_a, stored["memory_id"]) is None


def test_auto_store_accepts_optional_switch_without_changing_conversation_contract(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(hits(1))
    monkeypatch.setattr(
        main, "classify_intent",
        lambda value: {"intent": "store", "extracted_content": value, "extracted_tags": []},
    )
    monkeypatch.setattr(main, "classify_memory_category", lambda content: "inspiration")
    monkeypatch.setattr(inspiration_rendering, "create_search_provider", lambda: provider)
    token, _ = register(client, "auto-render@example.com")

    response = client.post(
        "/api/memory/auto",
        headers=auth(token),
        json={"input": "自动录入灵感", "inspiration_rendering": True},
    )

    assert response.json()["success"] is True
    assert response.json()["conversation_id"]
    assert response.json()["inspiration_rendering"]["status"] == "partial"
