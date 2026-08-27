"""Best-effort inspiration rendering performed only after memory persistence."""

from __future__ import annotations

import logging
import os
import traceback
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from database import (
    complete_inspiration_rendering,
    create_inspiration_rendering,
    get_inspiration_rendering,
)
from llm_service import summarize_inspiration_search_results
from search_service import (
    SearchHit,
    SearchProvider,
    SearchServiceError,
    configured_provider_name,
    create_search_provider,
)


logger = logging.getLogger(__name__)
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src",
}
FAILED_RENDERING_MESSAGE = "记忆已保存，但灵感渲染暂时不可用。"
EMPTY_RENDERING_MESSAGE = "记忆已保存，但暂未找到可展示的相关资料。"


def _clean_url(value: str) -> tuple[str, str, str] | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    parameters = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    cleaned = urlunsplit((scheme, netloc, parsed.path or "/", urlencode(parameters), ""))
    # Deliberately ignore query parameters for duplicate detection: search providers
    # often return the same page with tracking or presentation variants.
    duplicate_key = urlunsplit((scheme, netloc, (parsed.path or "/").rstrip("/") or "/", "", ""))
    return cleaned, hostname, duplicate_key


def normalize_search_hits(hits: list[SearchHit], limit: int) -> list[dict[str, Any]]:
    """Validate, rank and deduplicate provider-owned titles, URLs and snippets."""
    candidates: list[tuple[int, SearchHit]] = list(enumerate(hits))
    candidates.sort(key=lambda item: (
        item[1].score is None,
        -(item[1].score or 0.0),
        item[0],
    ))
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, hit in candidates:
        cleaned = _clean_url(hit.url)
        if cleaned is None or not hit.title.strip() or not hit.snippet.strip():
            continue
        url, domain, duplicate_key = cleaned
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)
        results.append({
            "title": hit.title.strip(),
            "summary": hit.snippet.strip(),
            "url": url,
            "source_domain": domain,
            "rank": len(results) + 1,
        })
        if len(results) >= limit:
            break
    return results


def _public_rendering(rendering: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": rendering["rendering_status"],
        "search_query": rendering.get("search_query"),
        "provider": rendering.get("provider"),
        "requested_count": rendering.get("requested_count", 5),
        "result_count": rendering.get("result_count", 0),
        "error_code": rendering.get("error_code"),
        "message": rendering.get("error_message"),
        "generated_at": rendering.get("generated_at"),
        "results": rendering.get("results", []),
    }


def render_inspiration(
    user_id: str,
    memory_id: int,
    content: str,
    category: str,
    provider_factory: Callable[[], SearchProvider] | None = None,
) -> dict[str, Any]:
    """Render a saved memory; every failure remains isolated from memory creation."""
    target_count = max(1, min(5, int(os.getenv("INSPIRATION_RENDERING_RESULT_LIMIT", "5"))))
    if category != "inspiration":
        create_inspiration_rendering(
            user_id, memory_id, "skipped_not_inspiration", requested_count=target_count
        )
        rendering = get_inspiration_rendering(user_id, memory_id)
        assert rendering is not None
        return _public_rendering(rendering)

    candidate_count = max(
        target_count,
        min(20, int(os.getenv("INSPIRATION_SEARCH_REQUEST_LIMIT", "10"))),
    )
    provider_name = configured_provider_name()
    create_inspiration_rendering(
        user_id, memory_id, "pending", content, provider_name, target_count
    )
    attempts = 0
    stage = "provider_initialization"
    try:
        provider = (provider_factory or create_search_provider)()
        provider_name = provider.name
        stage = "search"
        hits = provider.search(content, candidate_count)
        attempts = 1
        stage = "normalization"
        results = normalize_search_hits(hits, target_count)
        if not results:
            raise SearchServiceError(
                "insufficient_results", "未找到可信的相关资料。", attempts
            )
        summary_failed = False
        try:
            stage = "summarization"
            summaries = summarize_inspiration_search_results(content, results)
            for result in results:
                result["summary"] = summaries[result["rank"]]
        except Exception as exc:
            # Provider snippets remain real, non-empty summaries. A model formatting
            # failure must not discard or fabricate otherwise trustworthy results.
            summary_failed = True
            logger.exception(
                "Inspiration rendering failed stage=summarization user_id=%s memory_id=%s error_type=%s",
                user_id,
                memory_id,
                type(exc).__name__,
            )
        status = (
            "succeeded"
            if len(results) == target_count and not summary_failed
            else "partial"
        )
        error_code = (
            None
            if status == "succeeded"
            else "invalid_response" if summary_failed else "insufficient_results"
        )
        message = None
        if summary_failed:
            message = "资料已找到，但关联概括未完全生成。"
        elif status == "partial":
            message = f"仅找到 {len(results)} 条可信结果。"
        stage = "persistence"
        complete_inspiration_rendering(
            user_id, memory_id, status, results, attempts, error_code, message
        )
    except SearchServiceError as exc:
        attempts = exc.attempts
        message = (
            EMPTY_RENDERING_MESSAGE
            if exc.code == "insufficient_results"
            else FAILED_RENDERING_MESSAGE
        )
        try:
            complete_inspiration_rendering(
                user_id, memory_id, "failed", [], attempts, exc.code, message
            )
        except Exception as persistence_exc:
            logger.exception(
                "Inspiration rendering failed stage=persistence user_id=%s memory_id=%s error_type=%s",
                user_id,
                memory_id,
                type(persistence_exc).__name__,
            )
            raise
        logger.warning(
            "Inspiration rendering unavailable stage=%s user_id=%s memory_id=%s "
            "error_type=%s error_code=%s third_party_error_category=%s "
            "http_status=%s timed_out=%s response_shape_valid=%s attempts=%s stack=%s",
            stage,
            user_id,
            memory_id,
            type(exc).__name__,
            exc.code,
            exc.code,
            exc.http_status,
            exc.timed_out,
            exc.response_shape_valid,
            attempts,
            " | ".join(frame.strip() for frame in traceback.format_tb(exc.__traceback__)),
        )
    except Exception as exc:
        try:
            complete_inspiration_rendering(
                user_id,
                memory_id,
                "failed",
                [],
                attempts,
                "invalid_response",
                FAILED_RENDERING_MESSAGE,
            )
        except Exception as persistence_exc:
            logger.exception(
                "Inspiration rendering failed stage=persistence user_id=%s memory_id=%s error_type=%s",
                user_id,
                memory_id,
                type(persistence_exc).__name__,
            )
            raise
        logger.exception(
            "Inspiration rendering failed stage=%s user_id=%s memory_id=%s error_type=%s",
            stage,
            user_id,
            memory_id,
            type(exc).__name__,
        )
    rendering = get_inspiration_rendering(user_id, memory_id)
    assert rendering is not None
    return _public_rendering(rendering)


def public_inspiration_rendering(user_id: str, memory_id: int) -> dict[str, Any] | None:
    rendering = get_inspiration_rendering(user_id, memory_id)
    return _public_rendering(rendering) if rendering is not None else None
