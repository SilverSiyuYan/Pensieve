"""Authenticated HTTP API for user-scoped long-term memories."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from auth import authenticate_token, hash_password, issue_session, logout_token, verify_password
from database import (
    add_memory,
    add_message,
    create_user,
    delete_memory,
    get_memory,
    get_or_create_conversation,
    get_user_by_email,
    initialize_database,
    list_conversations,
    list_memories,
    list_messages,
    mark_embedding_result,
    search_memories_by_keyword,
    update_memory,
)
from llm_service import classify_intent, classify_memory_category, generate_integrated_answer
from memory_categories import MemoryCategory
from vector_store import add_to_vector, delete_from_vector, rebuild_vector_store, search_similar

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

app = FastAPI(title="memory-agent", version="0.2.0")
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

bearer = HTTPBearer(auto_error=False)
MemorySortOrder = Literal["desc", "asc"]


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


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    tags: list[str] | None = Field(default=None, max_length=20)


class MemoryQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    conversation_id: str | None = None


class AutoMemoryRequest(BaseModel):
    input: str = Field(min_length=1, max_length=10_000)
    conversation_id: str | None = None


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


def _store_memory(user_id: str, content: str, tags: list[str], category: str | None) -> dict[str, Any]:
    tags_text = ",".join(tags)
    memory_id = add_memory(user_id, content, tags_text, category)
    memory = get_memory(user_id, memory_id)
    if memory is None:
        raise RuntimeError("Failed to read newly stored memory")
    try:
        add_to_vector(user_id, memory_id, content, {
            "tags": tags_text, "category": category, "created_at": memory["created_at"]
        })
        mark_embedding_result(user_id, memory_id)
    except Exception as exc:
        mark_embedding_result(user_id, memory_id, str(exc)[:500])
        raise
    return {"success": True, "memory_id": memory_id, "message": "记忆已保存"}


def _query_memory(user_id: str, query: str) -> dict[str, Any]:
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
    return {"answer": answer, "source_memories": source_memories}


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    return _store_memory(str(current_user["id"]), payload.content, payload.tags, category)


@app.post("/api/memory/query")
def query_memory(payload: MemoryQueryRequest, current_user: CurrentUser) -> dict[str, Any]:
    return _query_memory(str(current_user["id"]), payload.query)


@app.post("/api/memory/auto")
def auto_memory(payload: AutoMemoryRequest, current_user: CurrentUser) -> dict[str, Any]:
    user_id = str(current_user["id"])
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
    delete_from_vector(user_id, memory_id)
    delete_memory(user_id, memory_id)
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
