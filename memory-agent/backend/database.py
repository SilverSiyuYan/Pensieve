"""SQLite persistence for users, sessions, conversations, and memories."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

DATABASE_PATH = Path(__file__).resolve().parent / "memory.db"
LEGACY_USER_ID = "00000000-0000-0000-0000-000000000000"

CREATE_USERS_SQL = """CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""

CREATE_MEMORIES_SQL = """CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL, tags TEXT, category TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""

SCHEMA_SQL = [
    CREATE_USERS_SQL,
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE, expires_at TIMESTAMP NOT NULL,
        revoked_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')), content TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS memory_embeddings (
        memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        vector_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
        last_error TEXT, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS memory_tasks (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        operation TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user_created ON memories(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user_category ON memories(user_id, category)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_messages_user_conversation ON messages(user_id, conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tasks_user_status ON memory_tasks(user_id, status)",
]


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _migrate_legacy_memories(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if table is None:
        connection.execute(CREATE_MEMORIES_SQL)
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)")}
    if "user_id" in columns:
        return
    connection.execute(
        "INSERT OR IGNORE INTO users (id, email, password_hash, status) VALUES (?, ?, '!', 'disabled')",
        (LEGACY_USER_ID, "legacy-local-user@invalid"),
    )
    connection.execute("ALTER TABLE memories RENAME TO memories_legacy")
    connection.execute(CREATE_MEMORIES_SQL)
    connection.execute(
        """INSERT INTO memories (id, user_id, content, tags, category, created_at, updated_at)
           SELECT id, ?, content, tags, category, created_at, updated_at FROM memories_legacy""",
        (LEGACY_USER_ID,),
    )
    connection.execute("DROP TABLE memories_legacy")


def initialize_database() -> None:
    with _connect() as connection:
        connection.execute(CREATE_USERS_SQL)
        _migrate_legacy_memories(connection)
        for statement in SCHEMA_SQL:
            connection.execute(statement)


def create_user(email: str, password_hash: str) -> dict[str, Any]:
    initialize_database()
    user_id = str(uuid4())
    with _connect() as connection:
        connection.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)", (user_id, email, password_hash))
    user = get_user_by_id(user_id)
    assert user is not None
    return user


def get_user_by_email(email: str) -> dict[str, Any] | None:
    initialize_database()
    with _connect() as connection:
        return _as_dict(connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    initialize_database()
    with _connect() as connection:
        return _as_dict(connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def create_session(user_id: str, token_hash: str, expires_at: datetime) -> str:
    session_id = str(uuid4())
    with _connect() as connection:
        connection.execute(
            "INSERT INTO sessions (id, user_id, token_hash, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, token_hash, expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")),
        )
    return session_id


def get_user_by_session_token_hash(token_hash: str) -> dict[str, Any] | None:
    initialize_database()
    with _connect() as connection:
        row = connection.execute(
            """SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL
                 AND sessions.expires_at > CURRENT_TIMESTAMP AND users.status = 'active'""",
            (token_hash,),
        ).fetchone()
    return _as_dict(row)


def revoke_session(token_hash: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?", (token_hash,))


def get_or_create_conversation(user_id: str, conversation_id: str | None = None) -> str:
    initialize_database()
    with _connect() as connection:
        if conversation_id:
            row = connection.execute(
                "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
            ).fetchone()
            if row is None:
                raise ValueError("Conversation not found")
            return str(row["id"])
        new_id = str(uuid4())
        connection.execute("INSERT INTO conversations (id, user_id) VALUES (?, ?)", (new_id, user_id))
        return new_id


def add_message(user_id: str, conversation_id: str, role: str, content: str) -> str:
    message_id = str(uuid4())
    with _connect() as connection:
        owner = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
        if owner is None:
            raise ValueError("Conversation not found")
        connection.execute(
            "INSERT INTO messages (id, user_id, conversation_id, role, content) VALUES (?, ?, ?, ?, ?)",
            (message_id, user_id, conversation_id, role, content),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
    return message_id


def list_conversations(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_messages(user_id: str, conversation_id: str, limit: int = 200) -> list[dict[str, Any]] | None:
    initialize_database()
    with _connect() as connection:
        owner = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
        if owner is None:
            return None
        rows = connection.execute(
            """SELECT id, conversation_id, role, content, created_at FROM messages
               WHERE user_id = ? AND conversation_id = ? ORDER BY created_at, rowid LIMIT ?""",
            (user_id, conversation_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def add_memory(user_id: str, content: str, tags: str | None, category: str | None) -> int:
    initialize_database()
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO memories (user_id, content, tags, category) VALUES (?, ?, ?, ?)",
            (user_id, content, tags, category),
        )
        memory_id = int(cursor.lastrowid)
        vector_id = f"{user_id}:{memory_id}"
        connection.execute(
            "INSERT INTO memory_embeddings (memory_id, user_id, vector_id) VALUES (?, ?, ?)",
            (memory_id, user_id, vector_id),
        )
        connection.execute(
            "INSERT INTO memory_tasks (id, user_id, memory_id, operation) VALUES (?, ?, ?, 'upsert')",
            (str(uuid4()), user_id, memory_id),
        )
    return memory_id


def mark_embedding_result(user_id: str, memory_id: int, error: str | None = None) -> None:
    status, task_status = ("failed", "failed") if error else ("ready", "completed")
    with _connect() as connection:
        connection.execute(
            """UPDATE memory_embeddings SET status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ? AND memory_id = ?""", (status, error, user_id, memory_id),
        )
        connection.execute(
            """UPDATE memory_tasks SET status = ?, attempts = attempts + 1, last_error = ?,
               updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND memory_id = ? AND status = 'pending'""",
            (task_status, error, user_id, memory_id),
        )


def get_memory(user_id: str, memory_id: int) -> dict[str, Any] | None:
    initialize_database()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
    return _as_dict(row)


def list_memories(user_id: str, limit: int = 20, offset: int = 0, category_filter: str | None = None) -> list[dict[str, Any]]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be non-negative")
    initialize_database()
    query, parameters = "SELECT * FROM memories WHERE user_id = ?", [user_id]
    if category_filter is not None:
        query += " AND category = ?"
        parameters.append(category_filter)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    parameters.extend([limit, offset])
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def search_memories_by_keyword(user_id: str, keyword: str, limit: int = 50) -> list[dict[str, Any]]:
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM memories WHERE user_id = ? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (user_id, f"%{keyword}%", limit),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_memory(user_id: str, memory_id: int) -> bool:
    initialize_database()
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id))
    return cursor.rowcount > 0


def update_memory(user_id: str, memory_id: int, content: str, tags: str | None, category: str | None) -> bool:
    initialize_database()
    with _connect() as connection:
        cursor = connection.execute(
            """UPDATE memories SET content = ?, tags = ?, category = ?, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ? AND id = ?""", (content, tags, category, user_id, memory_id),
        )
    return cursor.rowcount > 0
