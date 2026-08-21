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


def _vector_id(memory_id: int) -> str:
    return str(memory_id)


def _normalise_metadata(memory_id: int, metadata: dict[str, Any]) -> dict[str, Any]:
    """Ensure metadata conforms to ChromaDB's scalar-value constraints."""
    return {
        "memory_id": memory_id,
        "tags": metadata.get("tags") or "",
        "category": metadata.get("category") or "",
        "created_at": metadata.get("created_at") or "",
    }


def add_to_vector(memory_id: int, content: str, metadata: dict[str, Any]) -> None:
    """Embed and persist a memory's original text in the ``memories`` collection."""
    _get_collection().upsert(
        ids=[_vector_id(memory_id)],
        documents=[content],
        metadatas=[_normalise_metadata(memory_id, metadata)],
    )


def search_similar(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Return the closest semantic memories, ordered by ChromaDB distance."""
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")

    collection = _get_collection()
    if collection.count() == 0:
        return []

    result = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
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


def delete_from_vector(memory_id: int) -> None:
    """Remove the vector associated with a SQLite memory ID."""
    _get_collection().delete(ids=[_vector_id(memory_id)])


def rebuild_vector_store() -> int:
    """Recreate the vector collection from every memory stored in SQLite.

    Returns the number of SQLite memories indexed.
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection(COLLECTION_NAME)
    except ValueError:
        # The first rebuild has no existing collection to delete.
        pass
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    memories = list_memories(limit=100_000, offset=0)
    if memories:
        collection.upsert(
            ids=[_vector_id(memory["id"]) for memory in memories],
            documents=[memory["content"] for memory in memories],
            metadatas=[_normalise_metadata(memory["id"], memory) for memory in memories],
        )
    return len(memories)
