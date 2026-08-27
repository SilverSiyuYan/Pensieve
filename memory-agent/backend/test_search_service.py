"""Dynamic provider-adapter regression tests for inspiration search failures."""

from typing import Any

import httpx
import pytest

import search_service


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, json_error: Exception | None = None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> search_service.TavilySearchProvider:
    monkeypatch.setenv("INSPIRATION_SEARCH_API_KEY", "test-only-secret")
    monkeypatch.setenv("INSPIRATION_SEARCH_MAX_RETRIES", "0")
    monkeypatch.setenv("INSPIRATION_SEARCH_TIMEOUT_SECONDS", "0.2")
    return search_service.TavilySearchProvider()


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(401, "auth_failed"), (429, "rate_limited"), (500, "provider_error")],
)
def test_provider_maps_http_failures(
    provider: search_service.TavilySearchProvider,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_code: str,
) -> None:
    monkeypatch.setattr(
        search_service.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code, {}),
    )

    with pytest.raises(search_service.SearchServiceError) as captured:
        provider.search("query", 5)

    assert captured.value.code == error_code
    assert captured.value.attempts == 1


def test_provider_maps_timeout(
    provider: search_service.TavilySearchProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args, **kwargs):
        request = httpx.Request("POST", "https://search.invalid")
        raise httpx.ReadTimeout("sensitive timeout detail", request=request)

    monkeypatch.setattr(search_service.httpx, "post", timeout)

    with pytest.raises(search_service.SearchServiceError) as captured:
        provider.search("query", 5)

    assert captured.value.code == "timeout"


@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": {}}])
def test_provider_rejects_incomplete_json(
    provider: search_service.TavilySearchProvider,
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    monkeypatch.setattr(
        search_service.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(200, payload),
    )

    with pytest.raises(search_service.SearchServiceError) as captured:
        provider.search("query", 5)

    assert captured.value.code == "invalid_response"


def test_provider_parses_success_without_exposing_transport_response(
    provider: search_service.TavilySearchProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        search_service.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "Source",
                        "url": "https://example.com/article",
                        "content": "Relevant summary",
                        "score": 0.9,
                    }
                ]
            },
        ),
    )

    hits = provider.search("query", 5)

    assert len(hits) == 1
    assert hits[0].url == "https://example.com/article"
    assert hits[0].score == 0.9
