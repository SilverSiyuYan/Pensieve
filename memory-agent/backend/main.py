"""HTTP API for storing, retrieving, and querying long-term memories."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import (
    add_memory,
    delete_memory,
    get_memory,
    initialize_database,
    list_memories,
    search_memories_by_keyword,
)
from llm_service import classify_intent, generate_integrated_answer
from vector_store import (
    add_to_vector,
    delete_from_vector,
    rebuild_vector_store,
    search_similar,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

app = FastAPI(title="memory-agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MemoryStoreRequest(BaseModel):
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    category: str | None = None


class MemoryQueryRequest(BaseModel):
    query: str = Field(min_length=1)


class AutoMemoryRequest(BaseModel):
    input: str = Field(min_length=1)


def _store_memory(content: str, tags: list[str], category: str | None) -> dict[str, Any]:
    """Persist a memory in both SQLite and its ChromaDB semantic index."""
    tags_text = ",".join(tags)
    memory_id = add_memory(content, tags_text, category)
    memory = get_memory(memory_id)
    if memory is None:
        raise RuntimeError("Failed to read newly stored memory")
    add_to_vector(
        memory_id,
        content,
        {
            "tags": tags_text,
            "category": category,
            "created_at": memory["created_at"],
        },
    )
    return {"success": True, "memory_id": memory_id, "message": "记忆已保存"}


def _query_memory(query: str) -> dict[str, Any]:
    """Combine vector recall and keyword recall, then ask the LLM to answer."""
    semantic_matches = search_similar(query, top_k=5)
    keyword_matches = search_memories_by_keyword(query)

    source_memories: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for match in semantic_matches:
        memory_id = int(match["memory_id"])
        memory = get_memory(memory_id)
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
    return {"status": "ok", "model": os.getenv("MODEL_NAME", "not configured")}


@app.post("/api/memory/store")
def store_memory(payload: MemoryStoreRequest) -> dict[str, Any]:
    return _store_memory(payload.content, payload.tags, payload.category)


@app.post("/api/memory/query")
def query_memory(payload: MemoryQueryRequest) -> dict[str, Any]:
    return _query_memory(payload.query)


@app.post("/api/memory/auto")
def auto_memory(payload: AutoMemoryRequest) -> dict[str, Any]:
    intent_result = classify_intent(payload.input)
    if intent_result["intent"] == "store":
        return _store_memory(
            intent_result["extracted_content"],
            intent_result["extracted_tags"],
            "未分类",
        )
    return _query_memory(payload.input)


@app.get("/api/memories")
def get_memories(
    limit: int = Query(default=20, ge=0),
    offset: int = Query(default=0, ge=0),
    category: str | None = None,
) -> list[dict[str, Any]]:
    return list_memories(limit=limit, offset=offset, category_filter=category)


@app.delete("/api/memory/{memory_id}")
def remove_memory(memory_id: int) -> dict[str, Any]:
    if get_memory(memory_id) is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    delete_memory(memory_id)
    delete_from_vector(memory_id)
    return {"success": True, "memory_id": memory_id, "message": "记忆已删除"}


@app.post("/api/memory/rebuild")
def rebuild_memories() -> dict[str, Any]:
    indexed_count = rebuild_vector_store()
    return {"success": True, "indexed_count": indexed_count, "message": "向量库已重建"}
