"""SQLite persistence helpers for long-term memories."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any


DATABASE_PATH = Path(__file__).resolve().parent / "memory.db"

CREATE_MEMORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _connect() -> sqlite3.Connection:
    """Open a connection that returns records as dictionaries."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    """Create the database and its tables when they do not yet exist."""
    with _connect() as connection:
        connection.execute(CREATE_MEMORIES_TABLE_SQL)


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def add_memory(content: str, tags: str | None, category: str | None) -> int:
    """Store one memory and return its newly assigned identifier."""
    initialize_database()
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO memories (content, tags, category) VALUES (?, ?, ?)",
            (content, tags, category),
        )
        return int(cursor.lastrowid)


def get_memory(memory_id: int) -> dict[str, Any] | None:
    """Return a memory by ID, or ``None`` if it does not exist."""
    initialize_database()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    return _as_dict(row)


def list_memories(
    limit: int = 20, offset: int = 0, category_filter: str | None = None
) -> list[dict[str, Any]]:
    """Return one page of memories, optionally limited to a category."""
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be non-negative")

    initialize_database()
    query = "SELECT * FROM memories"
    parameters: list[Any] = []
    if category_filter is not None:
        query += " WHERE category = ?"
        parameters.append(category_filter)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    parameters.extend([limit, offset])

    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def search_memories_by_keyword(keyword: str) -> list[dict[str, Any]]:
    """Perform a case-insensitive SQLite LIKE search over memory content."""
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM memories WHERE content LIKE ? ORDER BY id DESC",
            (f"%{keyword}%",),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_memory(memory_id: int) -> None:
    """Delete a memory. Deleting an unknown ID is a no-op."""
    initialize_database()
    with _connect() as connection:
        connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


def update_memory(
    memory_id: int, content: str, tags: str | None, category: str | None
) -> None:
    """Update a memory and refresh its modification timestamp."""
    initialize_database()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE memories
            SET content = ?, tags = ?, category = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (content, tags, category, memory_id),
        )
