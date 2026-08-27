"""Authenticated HTTP API for user-scoped long-term memories."""

from __future__ import annotations

import os
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
import logging
from pathlib import Path
import sqlite3
from typing import Annotated, Any, Callable, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel, Field, field_validator

from app_meta import APP_NAME, APP_VERSION
from auth import authenticate_token, hash_password, issue_session, logout_token, verify_password
from database import (
    DATABASE_PATH,
    add_memory,
    add_message,
    create_user,
    database_is_accessible,
    delete_memory,
    get_memory,
    get_calendar_month_counts,
    get_or_create_conversation,
    get_user_by_email,
    initialize_database,
    list_conversations,
    list_memories,
    list_memories_created_between,
    list_memories_mentioning_date,
    list_memories_mentioning_range,
    list_messages,
    mark_memory_date_extraction,
    mark_embedding_result,
    replace_memory_date_mentions,
    search_memories_by_keyword,
    update_memory,
)
from inspiration_rendering import public_inspiration_rendering, render_inspiration
from llm_service import (
    classify_intent,
    classify_memory_category,
    extract_date_mentions,
    generate_integrated_answer,
)
from memory_categories import MemoryCategory
from settings import APP_TIMEZONE, TIMEZONE_NAME
from temporal_service import TemporalQueryError, resolve_query_window
from vector_store import (
    add_to_vector,
    delete_from_vector,
    rebuild_vector_store,
    search_similar,
    search_similar_within,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
    ).split(",")
    if origin.strip()
]
application = CORSMiddleware(
    app=app,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

bearer = HTTPBearer(auto_error=False)
MemorySortOrder = Literal["desc", "asc"]
DEFAULT_TIMEZONE = TIMEZONE_NAME
SHANGHAI_TIMEZONE = APP_TIMEZONE
logger = logging.getLogger(__name__)


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        normalised = value.strip().lower()
        if "@" not in normalised or normalised.startswith("@") or normalised.endswith("@"):
            raise ValueError("Invalid email address")
        return normalised


class MemoryStoreRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    category: MemoryCategory | None = None
    inspiration_rendering: bool = False


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    tags: list[str] | None = Field(default=None, max_length=20)


class MemoryQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    conversation_id: str | None = None


class AutoMemoryRequest(BaseModel):
    input: str = Field(min_length=1, max_length=10_000)
    conversation_id: str | None = None
    inspiration_rendering: bool = False


def require_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user = authenticate_token(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(require_current_user)]


def _public_user(user: dict[str, Any]) -> dict[str, str]:
    return {"id": str(user["id"]), "email": str(user["email"])}


def _session_response(user: dict[str, Any]) -> dict[str, Any]:
    token, expires_at = issue_session(str(user["id"]))
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": _public_user(user),
    }


def _memory_reference_datetime(memory: dict[str, Any]) -> datetime:
    """Interpret SQLite CURRENT_TIMESTAMP as UTC, then use the UI timezone."""
    created_at = datetime.fromisoformat(str(memory["created_at"]))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(SHANGHAI_TIMEZONE)


def _utc_boundary(value: date) -> str:
    local_boundary = datetime.combine(value, time.min, tzinfo=SHANGHAI_TIMEZONE)
    return local_boundary.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _refresh_memory_date_mentions(user_id: str, memory: dict[str, Any]) -> None:
    """Best-effort extraction that never makes memory persistence fail."""
    memory_id = int(memory["id"])
    try:
        mentions = extract_date_mentions(
            str(memory["content"]), _memory_reference_datetime(memory), DEFAULT_TIMEZONE
        )
        replace_memory_date_mentions(user_id, memory_id, mentions)
        mark_memory_date_extraction(
            user_id, memory_id, "success" if mentions else "no_date"
        )
    except Exception as exc:
        error_type = type(exc).__name__
        logger.warning(
            "Date mention extraction failed for user_id=%s memory_id=%s error_type=%s",
            user_id,
            memory_id,
            error_type,
        )
        try:
            # Edited content must not retain calendar dates extracted from its old text.
            replace_memory_date_mentions(user_id, memory_id, [])
            mark_memory_date_extraction(user_id, memory_id, "failed", error_type)
        except Exception:
            logger.warning(
                "Failed to clear stale date mentions for user_id=%s memory_id=%s",
                user_id,
                memory_id,
            )


def _store_memory(user_id: str, content: str, tags: list[str], category: str | None) -> dict[str, Any]:
    tags_text = ",".join(tags)
    memory_id = add_memory(user_id, content, tags_text, category)
    memory = get_memory(user_id, memory_id)
    if memory is None:
        raise RuntimeError("Failed to read newly stored memory")
    _refresh_memory_date_mentions(user_id, memory)
    try:
        add_to_vector(user_id, memory_id, content, {
            "tags": tags_text, "category": category, "created_at": memory["created_at"]
        })
        mark_embedding_result(user_id, memory_id)
    except Exception as exc:
        mark_embedding_result(user_id, memory_id, str(exc)[:500])
        raise
    return {"success": True, "memory_id": memory_id, "message": "记忆已保存"}


def _render_saved_memory(
    user_id: str, result: dict[str, Any], content: str, category: str
) -> dict[str, Any]:
    """Append rendering state without allowing it to change memory success."""
    result["memory_saved"] = True
    try:
        result["inspiration_rendering"] = render_inspiration(
            user_id, int(result["memory_id"]), content, category
        )
    except Exception as exc:
        logger.exception(
            "Failed to record inspiration rendering stage=rendering_or_persistence user_id=%s memory_id=%s error_type=%s",
            user_id, result["memory_id"], type(exc).__name__,
        )
        result["inspiration_rendering"] = {
            "status": "failed",
            "search_query": None,
            "provider": None,
            "requested_count": 5,
            "result_count": 0,
            "error_code": "provider_error",
            "message": "记忆已保存，但灵感渲染暂时不可用。",
            "generated_at": None,
            "results": [],
        }
    return result


def _query_memory(
    user_id: str, query: str, clock: Callable[[], datetime] | None = None
) -> dict[str, Any]:
    try:
        temporal_filter = resolve_query_window(query, extract_date_mentions, clock=clock)
    except TemporalQueryError as exc:
        return {
            "answer": "检测到时间条件，但未能可靠解析日期；未执行记忆检索。",
            "source_memories": [],
            "temporal_filter": {
                "status": "failed",
                "error": exc.code,
                "timezone": DEFAULT_TIMEZONE,
            },
        }
    if temporal_filter is not None:
        source_memories = list_memories_mentioning_range(
            user_id, temporal_filter["start_date"], temporal_filter["end_date"]
        )
        semantic_query = str(temporal_filter.get("semantic_query") or "").strip()
        if semantic_query and source_memories:
            by_id = {int(memory["id"]): memory for memory in source_memories}
            ranked = search_similar_within(
                user_id, semantic_query, list(by_id), top_k=len(by_id)
            )
            ranked_ids = [
                int(match["memory_id"])
                for match in ranked
                if int(match["memory_id"]) in by_id
            ]
            ranked_set = set(ranked_ids)
            source_memories = [by_id[memory_id] for memory_id in ranked_ids] + [
                memory for memory_id, memory in by_id.items() if memory_id not in ranked_set
            ]
        answer = generate_integrated_answer(query, source_memories, temporal_filter)
        return {
            "answer": answer,
            "source_memories": source_memories,
            "temporal_filter": temporal_filter,
        }
    semantic_matches = search_similar(user_id, query, top_k=5)
    keyword_matches = search_memories_by_keyword(user_id, query)
    source_memories: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for match in semantic_matches:
        memory_id = int(match["memory_id"])
        memory = get_memory(user_id, memory_id)
        if memory is not None and memory_id not in seen_ids:
            source_memories.append(memory)
            seen_ids.add(memory_id)
    for memory in keyword_matches:
        memory_id = int(memory["id"])
        if memory_id not in seen_ids:
            source_memories.append(memory)
            seen_ids.add(memory_id)
    answer = generate_integrated_answer(query, source_memories)
    return {"answer": answer, "source_memories": source_memories, "temporal_filter": None}


@app.on_event("startup")
def startup() -> None:
    initialize_database()
    logging.getLogger("uvicorn.error").info(
        "Application startup: version=%s listen=%s allowed_origins=%s database=%s",
        APP_VERSION,
        os.getenv("MEMORY_AGENT_LISTEN_ADDRESS", "configured by Uvicorn; see server startup log"),
        ",".join(allowed_origins),
        DATABASE_PATH.resolve(),
    )


@app.get("/health")
def health() -> dict[str, Any]:
    """Compatibility alias for load balancers and existing deployments."""
    return api_health()


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    database_ok = database_is_accessible()
    return {
        "status": "ok" if database_ok else "degraded",
        "application": APP_NAME,
        "version": APP_VERSION,
        "current_time": datetime.now(UTC).isoformat(),
        "timezone": DEFAULT_TIMEZONE,
        "database_accessible": database_ok,
    }


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: AuthRequest) -> dict[str, Any]:
    try:
        user = create_user(payload.email, hash_password(payload.password))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists") from None
    return _session_response(user)


@app.post("/api/auth/login")
def login(payload: AuthRequest) -> dict[str, Any]:
    user = get_user_by_email(payload.email)
    if user is None or user["status"] != "active" or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _session_response(user)


@app.post("/api/auth/logout")
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
    current_user: CurrentUser,
) -> dict[str, bool]:
    del current_user
    logout_token(credentials.credentials)
    return {"success": True}


@app.get("/api/auth/me")
def me(current_user: CurrentUser) -> dict[str, str]:
    return _public_user(current_user)


@app.post("/api/memory/store")
def store_memory(payload: MemoryStoreRequest, current_user: CurrentUser) -> dict[str, Any]:
    category = classify_memory_category(payload.content)
    user_id = str(current_user["id"])
    result = _store_memory(user_id, payload.content, payload.tags, category)
    if payload.inspiration_rendering:
        _render_saved_memory(user_id, result, payload.content, str(category))
    return result


@app.post("/api/memory/query")
def query_memory(payload: MemoryQueryRequest, current_user: CurrentUser) -> dict[str, Any]:
    return _query_memory(str(current_user["id"]), payload.query)


@app.post("/api/memory/auto")
def auto_memory(payload: AutoMemoryRequest, current_user: CurrentUser) -> dict[str, Any]:
    user_id = str(current_user["id"])
    try:
        return _run_auto_memory(user_id, payload)
    except APITimeoutError:
        raise HTTPException(status_code=504, detail="Model service timed out") from None
    except APIConnectionError:
        raise HTTPException(status_code=502, detail="Unable to connect to the model service") from None
    except APIStatusError as exc:
        logger.warning(
            "Model service rejected auto-memory request status=%s error_type=%s",
            exc.status_code,
            type(exc).__name__,
        )
        if exc.status_code in (401, 403):
            detail = "Model service rejected the configured credentials or model"
        else:
            detail = f"Model service returned HTTP {exc.status_code}"
        raise HTTPException(status_code=502, detail=detail) from None


def _run_auto_memory(user_id: str, payload: AutoMemoryRequest) -> dict[str, Any]:
    try:
        conversation_id = get_or_create_conversation(user_id, payload.conversation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    add_message(user_id, conversation_id, "user", payload.input)
    intent_result = classify_intent(payload.input)
    if intent_result["intent"] == "store":
        content = intent_result["extracted_content"]
        category = classify_memory_category(content)
        result = _store_memory(user_id, content, intent_result["extracted_tags"], category)
        if payload.inspiration_rendering:
            _render_saved_memory(user_id, result, content, str(category))
        assistant_text = result["message"]
    else:
        result = _query_memory(user_id, payload.input)
        assistant_text = result["answer"]
    add_message(user_id, conversation_id, "assistant", assistant_text)
    result["conversation_id"] = conversation_id
    return result


@app.get("/api/memories")
def get_memories(
    current_user: CurrentUser,
    limit: int = Query(default=20, ge=0, le=100),
    offset: int = Query(default=0, ge=0),
    category: MemoryCategory | None = None,
    sort_order: MemorySortOrder = "desc",
) -> list[dict[str, Any]]:
    return list_memories(
        str(current_user["id"]),
        limit=limit,
        offset=offset,
        category_filter=category,
        sort_order=sort_order,
    )


@app.get("/api/memories/{memory_id}/inspiration-rendering")
def get_memory_inspiration_rendering(
    memory_id: int, current_user: CurrentUser
) -> dict[str, Any]:
    user_id = str(current_user["id"])
    if get_memory(user_id, memory_id) is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    rendering = public_inspiration_rendering(user_id, memory_id)
    if rendering is not None:
        return rendering
    return {
        "status": "not_requested",
        "search_query": None,
        "provider": None,
        "requested_count": 5,
        "result_count": 0,
        "error_code": None,
        "message": None,
        "generated_at": None,
        "results": [],
    }


@app.get("/api/calendar/month")
def get_calendar_month(
    current_user: CurrentUser,
    year: int = Query(ge=1, le=9998),
    month: int = Query(ge=1, le=12),
) -> dict[str, Any]:
    first_day = date(year, month, 1)
    day_count = monthrange(year, month)[1]
    last_day = date(year, month, day_count)
    next_month = last_day + timedelta(days=1)
    counts = get_calendar_month_counts(
        str(current_user["id"]),
        first_day.isoformat(),
        last_day.isoformat(),
        _utc_boundary(first_day),
        _utc_boundary(next_month),
        "+8 hours",
    )
    days = []
    for day_number in range(1, day_count + 1):
        calendar_day = date(year, month, day_number)
        day_counts = counts.get(calendar_day.isoformat(), {"created": 0, "mentioned": 0})
        created_count = day_counts["created"]
        mentioned_count = day_counts["mentioned"]
        days.append({
            "date": calendar_day.isoformat(),
            "weekday": calendar_day.isoweekday(),
            "created_count": created_count,
            "mentioned_count": mentioned_count,
            "has_content": created_count > 0 or mentioned_count > 0,
        })
    return {
        "year": year,
        "month": month,
        "timezone": DEFAULT_TIMEZONE,
        "days": days,
    }


@app.get("/api/calendar/day")
def get_calendar_day(
    current_user: CurrentUser,
    calendar_date: date = Query(alias="date"),
    sort_order: MemorySortOrder = "desc",
) -> dict[str, Any]:
    if calendar_date == date.max:
        raise HTTPException(status_code=422, detail="date is outside the supported range")
    next_day = calendar_date + timedelta(days=1)
    user_id = str(current_user["id"])
    return {
        "date": calendar_date.isoformat(),
        "timezone": DEFAULT_TIMEZONE,
        "sort_order": sort_order,
        "created_memories": list_memories_created_between(
            user_id, _utc_boundary(calendar_date), _utc_boundary(next_day), sort_order
        ),
        "mentioned_memories": list_memories_mentioning_date(
            user_id, calendar_date.isoformat(), sort_order
        ),
    }


@app.get("/api/conversations")
def get_conversations(
    current_user: CurrentUser, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    return list_conversations(str(current_user["id"]), limit)


@app.get("/api/conversations/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    current_user: CurrentUser,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    messages = list_messages(str(current_user["id"]), conversation_id, limit)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages


@app.delete("/api/memory/{memory_id}")
def remove_memory(memory_id: int, current_user: CurrentUser) -> dict[str, Any]:
    user_id = str(current_user["id"])
    if get_memory(user_id, memory_id) is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    delete_memory(user_id, memory_id)
    try:
        delete_from_vector(user_id, memory_id)
    except Exception as exc:
        # SQLite is authoritative. Retrieval re-checks user_id + memory_id in
        # SQLite, and a later vector rebuild removes any stale derived entry.
        logger.warning(
            "Vector cleanup failed after memory deletion user_id=%s memory_id=%s error_type=%s",
            user_id,
            memory_id,
            type(exc).__name__,
        )
    return {"success": True, "memory_id": memory_id, "message": "记忆已删除"}


@app.patch("/api/memory/{memory_id}")
def edit_memory(memory_id: int, payload: MemoryUpdateRequest, current_user: CurrentUser) -> dict[str, Any]:
    user_id = str(current_user["id"])
    memory = get_memory(user_id, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one field is required")

    content = payload.content if "content" in payload.model_fields_set else str(memory["content"])
    tags = ",".join(payload.tags or []) if "tags" in payload.model_fields_set else memory["tags"]
    content_changed = "content" in payload.model_fields_set and content != memory["content"]
    category = classify_memory_category(content) if content_changed else str(memory["category"])
    update_memory(user_id, memory_id, content, tags, category)
    updated = get_memory(user_id, memory_id)
    assert updated is not None
    if content_changed:
        _refresh_memory_date_mentions(user_id, updated)
        try:
            add_to_vector(user_id, memory_id, content, {
                "tags": tags, "category": category, "created_at": updated["created_at"]
            })
            mark_embedding_result(user_id, memory_id)
        except Exception as exc:
            mark_embedding_result(user_id, memory_id, str(exc)[:500])
    return {"success": True, "memory": updated, "message": "记忆已更新"}


@app.post("/api/memory/rebuild")
def rebuild_memories(current_user: CurrentUser) -> dict[str, Any]:
    indexed_count = rebuild_vector_store(str(current_user["id"]))
    return {"success": True, "indexed_count": indexed_count, "message": "当前用户向量索引已重建"}
