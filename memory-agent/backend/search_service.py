"""Provider-neutral web search used by inspiration rendering."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    score: float | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchHit]: ...


class SearchServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        attempts: int = 1,
        *,
        http_status: int | None = None,
        timed_out: bool = False,
        response_shape_valid: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts
        self.http_status = http_status
        self.timed_out = timed_out
        self.response_shape_valid = response_shape_valid


class TavilySearchProvider:
    """Small Tavily adapter; no provider response escapes into business code."""

    name = "tavily"

    def __init__(self) -> None:
        self._api_key = os.environ.get("INSPIRATION_SEARCH_API_KEY", "").strip()
        if not self._api_key:
            raise SearchServiceError("not_configured", "Search provider is not configured", 0)
        self._base_url = os.getenv(
            "INSPIRATION_SEARCH_BASE_URL", "https://api.tavily.com/search"
        ).strip()
        self._timeout = max(
            0.1, float(os.getenv("INSPIRATION_SEARCH_TIMEOUT_SECONDS", "8"))
        )
        self._max_retries = max(
            0, min(2, int(os.getenv("INSPIRATION_SEARCH_MAX_RETRIES", "1")))
        )

    def search(self, query: str, limit: int) -> list[SearchHit]:
        attempts = 0
        while True:
            attempts += 1
            try:
                response = httpx.post(
                    self._base_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "query": query,
                        "max_results": limit,
                        "search_depth": "advanced",
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                    timeout=self._timeout,
                )
                if response.status_code == 429:
                    raise SearchServiceError(
                        "rate_limited", "Search provider rate limited", attempts,
                        http_status=response.status_code,
                    )
                if response.status_code in (401, 403):
                    raise SearchServiceError(
                        "auth_failed", "Search provider rejected credentials", attempts,
                        http_status=response.status_code,
                    )
                if response.status_code >= 500:
                    if attempts <= self._max_retries:
                        time.sleep(min(0.25 * attempts, 0.5))
                        continue
                    raise SearchServiceError(
                        "provider_error", "Search provider failed", attempts,
                        http_status=response.status_code,
                    )
                if response.status_code >= 400:
                    raise SearchServiceError(
                        "provider_error", "Search provider rejected request", attempts,
                        http_status=response.status_code,
                    )
                return self._parse_response(response.json(), attempts)
            except httpx.TimeoutException:
                if attempts <= self._max_retries:
                    continue
                raise SearchServiceError(
                    "timeout", "Search provider timed out", attempts, timed_out=True
                ) from None
            except httpx.RequestError:
                if attempts <= self._max_retries:
                    continue
                raise SearchServiceError("provider_error", "Search provider is unavailable", attempts) from None
            except ValueError:
                raise SearchServiceError(
                    "invalid_response", "Search provider returned invalid JSON", attempts,
                    response_shape_valid=False,
                ) from None

    @staticmethod
    def _parse_response(payload: Any, attempts: int) -> list[SearchHit]:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SearchServiceError(
                "invalid_response", "Search response has no results list", attempts,
                response_shape_valid=False,
            )
        hits: list[SearchHit] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            title, url, snippet = item.get("title"), item.get("url"), item.get("content")
            if not all(isinstance(value, str) for value in (title, url, snippet)):
                continue
            score = item.get("score")
            hits.append(SearchHit(
                title=title.strip(), url=url.strip(), snippet=snippet.strip(),
                score=float(score) if isinstance(score, (int, float)) else None,
            ))
        return hits


def configured_provider_name() -> str:
    return os.getenv("INSPIRATION_RENDERING_PROVIDER", "tavily").strip().lower() or "tavily"


def create_search_provider() -> SearchProvider:
    provider = configured_provider_name()
    if provider == "tavily":
        return TavilySearchProvider()
    raise SearchServiceError("not_configured", "Unsupported search provider", 0)
