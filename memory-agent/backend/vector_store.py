"""ChromaDB-backed semantic index for long-term memories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from database import list_memories


CHROMA_PATH = str(Path(__file__).resolve().parent / "chroma_data")
COLLECTION_NAME = "memories"


def _get_collection() -> chromadb.Collection:
    """Get the persistent collection, creating it on first use.

    No embedding function is supplied: ChromaDB therefore uses its bundled
    all-MiniLM-L6-v2 default embedding function for documents and queries.
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(name=COLLECTION_NAME)


def _vector_id(user_id: str, memory_id: int) -> str:
    return f"{user_id}:{memory_id}"


def _normalise_metadata(user_id: str, memory_id: int, metadata: dict[str, Any]) -> dict[str, Any]:
    """Ensure metadata conforms to ChromaDB's scalar-value constraints."""
    return {
        "memory_id": memory_id,
        "user_id": user_id,
        "tags": metadata.get("tags") or "",
        "category": metadata.get("category") or "",
        "created_at": metadata.get("created_at") or "",
    }


def add_to_vector(user_id: str, memory_id: int, content: str, metadata: dict[str, Any]) -> None:
    """Embed and persist a memory's original text in the ``memories`` collection."""
    _get_collection().upsert(
        ids=[_vector_id(user_id, memory_id)],
        documents=[content],
        metadatas=[_normalise_metadata(user_id, memory_id, metadata)],
    )


def search_similar(user_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Return the closest semantic memories, ordered by ChromaDB distance."""
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")

    collection = _get_collection()
    if collection.count() == 0:
        return []

    result = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        where={"user_id": user_id},
        include=["documents", "metadatas", "distances"],
    )
    documents = result["documents"][0]
    metadatas = result["metadatas"][0]
    distances = result["distances"][0]

    return [
        {
            "memory_id": metadata["memory_id"],
            "content": document,
            "distance_score": distance,
            "metadata": metadata,
        }
        for document, metadata, distance in zip(documents, metadatas, distances)
    ]


def search_similar_within(
    user_id: str, query: str, memory_ids: list[int], top_k: int | None = None
) -> list[dict[str, Any]]:
    """Rank only an already structurally filtered candidate set."""
    unique_ids = list(dict.fromkeys(int(memory_id) for memory_id in memory_ids))
    if not query.strip() or not unique_ids:
        return []
    limit = min(top_k or len(unique_ids), len(unique_ids))
    collection = _get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_texts=[query],
        n_results=limit,
        where={"$and": [{"user_id": user_id}, {"memory_id": {"$in": unique_ids}}]},
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "memory_id": metadata["memory_id"],
            "content": document,
            "distance_score": distance,
            "metadata": metadata,
        }
        for document, metadata, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        )
    ]


def delete_from_vector(user_id: str, memory_id: int) -> None:
    """Remove the vector associated with a SQLite memory ID."""
    _get_collection().delete(ids=[_vector_id(user_id, memory_id)], where={"user_id": user_id})


def rebuild_vector_store(user_id: str) -> int:
    """Recreate one user's vectors from their SQLite memories.

    Returns the number of SQLite memories indexed.
    """
    collection = _get_collection()
    collection.delete(where={"user_id": user_id})

    memories = list_memories(user_id, limit=100_000, offset=0)
    if memories:
        collection.upsert(
            ids=[_vector_id(user_id, memory["id"]) for memory in memories],
            documents=[memory["content"] for memory in memories],
            metadatas=[_normalise_metadata(user_id, memory["id"], memory) for memory in memories],
        )
    return len(memories)
