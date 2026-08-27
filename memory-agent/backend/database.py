"""SQLite persistence for users, sessions, conversations, and memories."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from memory_categories import DEFAULT_MEMORY_CATEGORY, MEMORY_CATEGORIES
from settings import TIMEZONE_NAME

DATABASE_PATH = Path(__file__).resolve().parent / "memory.db"
LEGACY_USER_ID = "00000000-0000-0000-0000-000000000000"
_CATEGORY_SQL_VALUES = ", ".join(f"'{category}'" for category in MEMORY_CATEGORIES)

CREATE_USERS_SQL = """CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""

CREATE_MEMORIES_SQL = f"""CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL, tags TEXT,
    category TEXT NOT NULL DEFAULT '{DEFAULT_MEMORY_CATEGORY.value}'
        CHECK(category IN ({_CATEGORY_SQL_VALUES})),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""

CREATE_MEMORY_DATE_MENTIONS_SQL = """CREATE TABLE IF NOT EXISTS memory_date_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    start_date TEXT NOT NULL
        CHECK(length(start_date) = 10 AND start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    end_date TEXT NOT NULL
        CHECK(length(end_date) = 10 AND end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    original_expression TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    timezone_name TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    temporal_type TEXT NOT NULL DEFAULT 'date'
        CHECK(temporal_type IN ('date', 'date_range')),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(start_date <= end_date),
    FOREIGN KEY(user_id, memory_id) REFERENCES memories(user_id, id) ON DELETE CASCADE)"""

CREATE_MEMORY_DATE_EXTRACTIONS_SQL = """CREATE TABLE IF NOT EXISTS memory_date_extractions (
    memory_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success', 'no_date', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id, memory_id) REFERENCES memories(user_id, id) ON DELETE CASCADE)"""

CREATE_MEMORY_INSPIRATION_RENDERINGS_SQL = """CREATE TABLE IF NOT EXISTS memory_inspiration_renderings (
    memory_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    rendering_status TEXT NOT NULL
        CHECK(rendering_status IN ('skipped_not_inspiration', 'pending', 'succeeded', 'partial', 'failed')),
    search_query TEXT,
    provider TEXT,
    requested_count INTEGER NOT NULL DEFAULT 5 CHECK(requested_count > 0),
    result_count INTEGER NOT NULL DEFAULT 0 CHECK(result_count >= 0),
    error_code TEXT,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    generated_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id, memory_id) REFERENCES memories(user_id, id) ON DELETE CASCADE)"""

CREATE_INSPIRATION_RENDERING_RESULTS_SQL = """CREATE TABLE IF NOT EXISTS inspiration_rendering_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK(rank > 0),
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id, memory_id)
        REFERENCES memory_inspiration_renderings(user_id, memory_id) ON DELETE CASCADE,
    UNIQUE(user_id, memory_id, rank),
    UNIQUE(user_id, memory_id, url))"""

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
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_id_id ON memories(user_id, id)",
    CREATE_MEMORY_DATE_MENTIONS_SQL,
    CREATE_MEMORY_DATE_EXTRACTIONS_SQL,
    CREATE_MEMORY_INSPIRATION_RENDERINGS_SQL,
    CREATE_INSPIRATION_RENDERING_RESULTS_SQL,
    "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user_created ON memories(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user_category ON memories(user_id, category)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_messages_user_conversation ON messages(user_id, conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_memory_tasks_user_status ON memory_tasks(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_mentions_memory ON memory_date_mentions(memory_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_mentions_user_dates ON memory_date_mentions(user_id, start_date, end_date)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_mentions_start_date ON memory_date_mentions(start_date)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_mentions_end_date ON memory_date_mentions(end_date)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_extractions_status ON memory_date_extractions(status, memory_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_date_extractions_user_status ON memory_date_extractions(user_id, status, memory_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_renderings_user_memory ON memory_inspiration_renderings(user_id, memory_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_renderings_user_status ON memory_inspiration_renderings(user_id, rendering_status, memory_id)",
    "CREATE INDEX IF NOT EXISTS idx_rendering_results_user_memory_rank ON inspiration_rendering_results(user_id, memory_id, rank)",
]


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _verify_database_writable(connection: sqlite3.Connection) -> None:
    """Acquire and release a main-database write transaction without changing data."""
    connection.execute("BEGIN IMMEDIATE")
    connection.rollback()


def database_is_accessible() -> bool:
    """Check that SQLite can both read and accept writes without changing user data."""
    try:
        with _connect() as connection:
            if connection.execute("SELECT 1").fetchone()[0] != 1:
                return False
            _verify_database_writable(connection)
            return True
    except (OSError, sqlite3.Error):
        return False


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
           SELECT id, ?, content, tags, 'note', created_at, updated_at FROM memories_legacy""",
        (LEGACY_USER_ID,),
    )
    connection.execute("DROP TABLE memories_legacy")


def _migrate_memory_categories(connection: sqlite3.Connection) -> None:
    """Constrain categories and map every pre-enum memory to the safe default."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if table_sql_row is None:
        connection.execute(CREATE_MEMORIES_SQL)
        return
    table_sql = str(table_sql_row["sql"] or "").lower().replace('"', "").replace("`", "")
    columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(memories)")}
    category = columns.get("category")
    is_current = (
        category is not None
        and int(category["notnull"]) == 1
        and str(category["dflt_value"] or "").strip("'") == DEFAULT_MEMORY_CATEGORY.value
        and f"check(category in ({_CATEGORY_SQL_VALUES}))" in table_sql
    )
    if is_current:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    try:
        connection.execute("BEGIN")
        connection.execute("ALTER TABLE memories RENAME TO memories_pre_category_enum")
        connection.execute(CREATE_MEMORIES_SQL)
        connection.execute(
            """INSERT INTO memories (id, user_id, content, tags, category, created_at, updated_at)
               SELECT id, user_id, content, tags, 'note', created_at, updated_at
               FROM memories_pre_category_enum"""
        )
        connection.execute("DROP TABLE memories_pre_category_enum")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError("Foreign key violation while migrating memory categories")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA legacy_alter_table = OFF")
        connection.execute("PRAGMA foreign_keys = ON")


def initialize_database() -> None:
    connection = _connect()
    try:
        connection.execute(CREATE_USERS_SQL)
        _migrate_legacy_memories(connection)
        _migrate_memory_categories(connection)
        for statement in SCHEMA_SQL:
            connection.execute(statement)
        mention_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(memory_date_mentions)")
        }
        if "timezone_name" not in mention_columns:
            connection.execute(
                "ALTER TABLE memory_date_mentions ADD COLUMN timezone_name TEXT NOT NULL DEFAULT 'Asia/Shanghai'"
            )
        if "temporal_type" not in mention_columns:
            connection.execute(
                "ALTER TABLE memory_date_mentions ADD COLUMN temporal_type TEXT NOT NULL DEFAULT 'date'"
            )
            connection.execute(
                "UPDATE memory_date_mentions SET temporal_type = 'date_range' WHERE start_date <> end_date"
            )
        connection.commit()
        _verify_database_writable(connection)
    finally:
        connection.close()


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
    category = category or DEFAULT_MEMORY_CATEGORY.value
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


def _iso_date(value: str) -> str:
    """Validate and canonicalise a calendar date as ISO ``YYYY-MM-DD``."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("date must use the YYYY-MM-DD format") from None
    if parsed.isoformat() != value:
        raise ValueError("date must use the YYYY-MM-DD format")
    return value


def add_memory_date_mention(
    user_id: str,
    memory_id: int,
    start_date: str,
    end_date: str,
    original_expression: str,
    normalized_text: str,
    confidence: float,
    timezone_name: str = TIMEZONE_NAME,
    temporal_type: str | None = None,
) -> int:
    """Attach one user-scoped event date or date range to a memory."""
    initialize_database()
    start_date = _iso_date(start_date)
    end_date = _iso_date(end_date)
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")
    temporal_type = temporal_type or ("date" if start_date == end_date else "date_range")
    if temporal_type not in {"date", "date_range"}:
        raise ValueError("temporal_type must be date or date_range")
    with _connect() as connection:
        cursor = connection.execute(
            """INSERT INTO memory_date_mentions (
                   memory_id, user_id, start_date, end_date,
                   original_expression, normalized_text, timezone_name,
                   temporal_type, confidence
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                user_id,
                start_date,
                end_date,
                original_expression,
                normalized_text,
                timezone_name,
                temporal_type,
                confidence,
            ),
        )
    return int(cursor.lastrowid)


def replace_memory_date_mentions(
    user_id: str, memory_id: int, mentions: list[dict[str, Any]]
) -> list[int]:
    """Atomically replace all extracted dates for one user-owned memory."""
    validated: list[tuple[str, str, str, str, str, str, float]] = []
    for mention in mentions:
        start_date = _iso_date(mention["start_date"])
        end_date = _iso_date(mention["end_date"])
        confidence = float(mention["confidence"])
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        validated.append(
            (
                start_date,
                end_date,
                str(mention["original_expression"]),
                str(mention["normalized_text"]),
                str(mention.get("timezone_name") or TIMEZONE_NAME),
                str(mention.get("temporal_type") or (
                    "date" if start_date == end_date else "date_range"
                )),
                confidence,
            )
        )

    initialize_database()
    inserted_ids: list[int] = []
    with _connect() as connection:
        owner = connection.execute(
            "SELECT 1 FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)
        ).fetchone()
        if owner is None:
            raise ValueError("Memory not found")
        connection.execute(
            "DELETE FROM memory_date_mentions WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        )
        for (
            start_date, end_date, original_expression, normalized_text,
            timezone_name, temporal_type, confidence,
        ) in validated:
            if temporal_type not in {"date", "date_range"}:
                raise ValueError("temporal_type must be date or date_range")
            cursor = connection.execute(
                """INSERT INTO memory_date_mentions (
                       memory_id, user_id, start_date, end_date,
                       original_expression, normalized_text, timezone_name,
                       temporal_type, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id, user_id, start_date, end_date,
                    original_expression, normalized_text, timezone_name,
                    temporal_type, confidence,
                ),
            )
            inserted_ids.append(int(cursor.lastrowid))
    return inserted_ids


def list_memory_date_mentions(user_id: str, memory_id: int | None = None) -> list[dict[str, Any]]:
    """List date mentions visible to one user, optionally for one memory."""
    initialize_database()
    query = "SELECT * FROM memory_date_mentions WHERE user_id = ?"
    parameters: list[Any] = [user_id]
    if memory_id is not None:
        query += " AND memory_id = ?"
        parameters.append(memory_id)
    query += " ORDER BY start_date, end_date, id"
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def mark_memory_date_extraction(
    user_id: str, memory_id: int, result_status: str, last_error: str | None = None
) -> None:
    """Record one isolated extraction attempt for a user-owned memory."""
    if result_status not in {"success", "no_date", "failed"}:
        raise ValueError("Invalid date extraction status")
    error = last_error[:500] if last_error else None
    initialize_database()
    with _connect() as connection:
        owner = connection.execute(
            "SELECT 1 FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)
        ).fetchone()
        if owner is None:
            raise ValueError("Memory not found")
        connection.execute(
            """INSERT INTO memory_date_extractions (
                   memory_id, user_id, status, attempts, last_error
               ) VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(memory_id) DO UPDATE SET
                   user_id = excluded.user_id,
                   status = excluded.status,
                   attempts = memory_date_extractions.attempts + 1,
                   last_error = excluded.last_error,
                   processed_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP""",
            (memory_id, user_id, result_status, error),
        )


def list_memories_pending_date_extraction(
    limit: int, user_id: str | None = None
) -> list[dict[str, Any]]:
    """Return unprocessed or failed memories in stable batches."""
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    initialize_database()
    query = """SELECT memories.*,
                      (SELECT COUNT(*) FROM memory_date_mentions
                       WHERE memory_date_mentions.user_id = memories.user_id
                         AND memory_date_mentions.memory_id = memories.id) AS date_mention_count
               FROM memories
               LEFT JOIN memory_date_extractions
                 ON memory_date_extractions.user_id = memories.user_id
                AND memory_date_extractions.memory_id = memories.id
               WHERE (memory_date_extractions.memory_id IS NULL
                      OR memory_date_extractions.status = 'failed')"""
    parameters: list[Any] = []
    if user_id is not None:
        query += " AND memories.user_id = ?"
        parameters.append(user_id)
    # Process never-attempted rows before retries so a persistent early failure
    # cannot starve later historical memories in every bounded batch.
    query += """ ORDER BY
        CASE WHEN memory_date_extractions.memory_id IS NULL THEN 0 ELSE 1 END,
        memories.id LIMIT ?"""
    parameters.append(limit)
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def count_memories_pending_date_extraction(user_id: str | None = None) -> int:
    """Count memories eligible for a first extraction or failed retry."""
    initialize_database()
    query = """SELECT COUNT(*) AS count FROM memories
               LEFT JOIN memory_date_extractions
                 ON memory_date_extractions.user_id = memories.user_id
                AND memory_date_extractions.memory_id = memories.id
               WHERE (memory_date_extractions.memory_id IS NULL
                      OR memory_date_extractions.status = 'failed')"""
    parameters: list[Any] = []
    if user_id is not None:
        query += " AND memories.user_id = ?"
        parameters.append(user_id)
    with _connect() as connection:
        return int(connection.execute(query, parameters).fetchone()["count"])


def count_memories_with_date_mentions(user_id: str | None = None) -> int:
    """Count distinct memories that already have authoritative absolute dates."""
    initialize_database()
    query = "SELECT COUNT(DISTINCT memory_id) AS count FROM memory_date_mentions"
    parameters: list[Any] = []
    if user_id is not None:
        query += " WHERE user_id = ?"
        parameters.append(user_id)
    with _connect() as connection:
        return int(connection.execute(query, parameters).fetchone()["count"])


def get_memory_date_extraction(user_id: str, memory_id: int) -> dict[str, Any] | None:
    """Read extraction state without exposing another user's record."""
    initialize_database()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM memory_date_extractions WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        ).fetchone()
    return _as_dict(row)


def get_calendar_month_counts(
    user_id: str,
    first_day: str,
    last_day: str,
    utc_start: str,
    utc_end: str,
    local_time_modifier: str,
) -> dict[str, dict[str, int]]:
    """Return month-level created and mentioned counts with two aggregate queries."""
    initialize_database()
    counts: dict[str, dict[str, int]] = {}
    with _connect() as connection:
        created_rows = connection.execute(
            """SELECT date(datetime(created_at, ?)) AS calendar_date,
                      COUNT(*) AS count
               FROM memories
               WHERE user_id = ? AND created_at >= ? AND created_at < ?
               GROUP BY calendar_date""",
            (local_time_modifier, user_id, utc_start, utc_end),
        ).fetchall()
        mentioned_rows = connection.execute(
            """WITH RECURSIVE calendar(calendar_date) AS (
                   SELECT ?
                   UNION ALL
                   SELECT date(calendar_date, '+1 day') FROM calendar
                   WHERE calendar_date < ?
               )
               SELECT calendar.calendar_date AS calendar_date,
                      COUNT(DISTINCT memory_date_mentions.memory_id) AS count
               FROM calendar
               LEFT JOIN memory_date_mentions
                 ON memory_date_mentions.user_id = ?
                AND memory_date_mentions.start_date <= calendar.calendar_date
                AND memory_date_mentions.end_date >= calendar.calendar_date
               GROUP BY calendar.calendar_date""",
            (first_day, last_day, user_id),
        ).fetchall()
    for row in created_rows:
        counts.setdefault(str(row["calendar_date"]), {"created": 0, "mentioned": 0})[
            "created"
        ] = int(row["count"])
    for row in mentioned_rows:
        counts.setdefault(str(row["calendar_date"]), {"created": 0, "mentioned": 0})[
            "mentioned"
        ] = int(row["count"])
    return counts


def list_memories_created_between(
    user_id: str, utc_start: str, utc_end: str, sort_order: str = "desc"
) -> list[dict[str, Any]]:
    """List one user's memories written inside a UTC half-open interval."""
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    direction = "ASC" if sort_order == "asc" else "DESC"
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT * FROM memories
                WHERE user_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at {direction}, id {direction}""",
            (user_id, utc_start, utc_end),
        ).fetchall()
    return [dict(row) for row in rows]


def list_memories_mentioning_date(
    user_id: str, calendar_date: str, sort_order: str = "desc"
) -> list[dict[str, Any]]:
    """List one user's distinct memories whose extracted range covers a date."""
    _iso_date(calendar_date)
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    direction = "ASC" if sort_order == "asc" else "DESC"
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT memories.*,
                       memory_date_mentions.id AS mention_id,
                       memory_date_mentions.original_expression AS mention_original_expression,
                       memory_date_mentions.normalized_text AS mention_normalized_text,
                       memory_date_mentions.start_date AS mention_start_date,
                       memory_date_mentions.end_date AS mention_end_date,
                       memory_date_mentions.timezone_name AS mention_timezone_name,
                       memory_date_mentions.temporal_type AS mention_temporal_type,
                       memory_date_mentions.confidence AS mention_confidence
                FROM memories
                JOIN memory_date_mentions
                  ON memory_date_mentions.user_id = memories.user_id
                 AND memory_date_mentions.memory_id = memories.id
                WHERE memories.user_id = ?
                  AND memory_date_mentions.start_date <= ?
                  AND memory_date_mentions.end_date >= ?
                ORDER BY memories.created_at {direction}, memories.id {direction},
                         memory_date_mentions.id ASC""",
            (user_id, calendar_date, calendar_date),
        ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        memory_id = int(row["id"])
        if memory_id not in grouped:
            grouped[memory_id] = {
                key: row[key]
                for key in ("id", "user_id", "content", "tags", "category", "created_at", "updated_at")
            }
            grouped[memory_id]["date_mentions"] = []
        grouped[memory_id]["date_mentions"].append({
            "id": row["mention_id"],
            "original_expression": row["mention_original_expression"],
            "normalized_text": row["mention_normalized_text"],
            "start_date": row["mention_start_date"],
            "end_date": row["mention_end_date"],
            "timezone_name": row["mention_timezone_name"],
            "temporal_type": row["mention_temporal_type"],
            "confidence": row["mention_confidence"],
        })
    return list(grouped.values())


def list_memories_mentioning_range(
    user_id: str, start_date: str, end_date: str, sort_order: str = "desc"
) -> list[dict[str, Any]]:
    """Return memories whose authoritative parsed dates overlap a query range."""
    start_date = _iso_date(start_date)
    end_date = _iso_date(end_date)
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    direction = "ASC" if sort_order == "asc" else "DESC"
    initialize_database()
    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT memories.*,
                       memory_date_mentions.original_expression AS mention_original_expression,
                       memory_date_mentions.normalized_text AS mention_normalized_text,
                       memory_date_mentions.start_date AS mention_start_date,
                       memory_date_mentions.end_date AS mention_end_date,
                       memory_date_mentions.timezone_name AS mention_timezone_name,
                       memory_date_mentions.temporal_type AS mention_temporal_type,
                       memory_date_mentions.confidence AS mention_confidence
                FROM memories
                JOIN memory_date_mentions
                  ON memory_date_mentions.user_id = memories.user_id
                 AND memory_date_mentions.memory_id = memories.id
                WHERE memories.user_id = ?
                  AND memory_date_mentions.start_date <= ?
                  AND memory_date_mentions.end_date >= ?
                ORDER BY memories.created_at {direction}, memories.id {direction},
                         memory_date_mentions.id ASC""",
            (user_id, end_date, start_date),
        ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        memory_id = int(row["id"])
        if memory_id not in grouped:
            grouped[memory_id] = {
                key: row[key]
                for key in ("id", "user_id", "content", "tags", "category", "created_at", "updated_at")
            }
            grouped[memory_id]["date_mentions"] = []
        grouped[memory_id]["date_mentions"].append({
            "original_expression": row["mention_original_expression"],
            "normalized_text": row["mention_normalized_text"],
            "start_date": row["mention_start_date"],
            "end_date": row["mention_end_date"],
            "timezone_name": row["mention_timezone_name"],
            "temporal_type": row["mention_temporal_type"],
            "confidence": row["mention_confidence"],
        })
    return list(grouped.values())


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


def create_inspiration_rendering(
    user_id: str,
    memory_id: int,
    rendering_status: str,
    search_query: str | None = None,
    provider: str | None = None,
    requested_count: int = 5,
) -> None:
    """Create or reset one rendering owned by the same user as its memory."""
    if rendering_status not in {"skipped_not_inspiration", "pending"}:
        raise ValueError("Invalid initial rendering status")
    initialize_database()
    with _connect() as connection:
        owner = connection.execute(
            "SELECT 1 FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)
        ).fetchone()
        if owner is None:
            raise ValueError("Memory not found")
        connection.execute(
            """INSERT INTO memory_inspiration_renderings (
                   memory_id, user_id, rendering_status, search_query, provider,
                   requested_count, result_count, error_code, error_message, attempts
               ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL, 0)
               ON CONFLICT(memory_id) DO UPDATE SET
                   user_id = excluded.user_id,
                   rendering_status = excluded.rendering_status,
                   search_query = excluded.search_query,
                   provider = excluded.provider,
                   requested_count = excluded.requested_count,
                   result_count = 0,
                   error_code = NULL,
                   error_message = NULL,
                   attempts = 0,
                   generated_at = NULL,
                   updated_at = CURRENT_TIMESTAMP""",
            (memory_id, user_id, rendering_status, search_query, provider, requested_count),
        )
        connection.execute(
            "DELETE FROM inspiration_rendering_results WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        )


def complete_inspiration_rendering(
    user_id: str,
    memory_id: int,
    rendering_status: str,
    results: list[dict[str, Any]],
    attempts: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Atomically replace results and finish one user-scoped rendering attempt."""
    if rendering_status not in {"succeeded", "partial", "failed"}:
        raise ValueError("Invalid final rendering status")
    safe_error = error_message[:500] if error_message else None
    initialize_database()
    with _connect() as connection:
        rendering = connection.execute(
            """SELECT 1 FROM memory_inspiration_renderings
               WHERE user_id = ? AND memory_id = ?""",
            (user_id, memory_id),
        ).fetchone()
        if rendering is None:
            raise ValueError("Rendering not found")
        connection.execute(
            "DELETE FROM inspiration_rendering_results WHERE user_id = ? AND memory_id = ?",
            (user_id, memory_id),
        )
        for result in results:
            connection.execute(
                """INSERT INTO inspiration_rendering_results (
                       memory_id, user_id, title, summary, url, source_domain, rank
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id, user_id, result["title"], result["summary"], result["url"],
                    result["source_domain"], int(result["rank"]),
                ),
            )
        connection.execute(
            """UPDATE memory_inspiration_renderings SET
                   rendering_status = ?, result_count = ?, error_code = ?,
                   error_message = ?, attempts = ?, generated_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE user_id = ? AND memory_id = ?""",
            (
                rendering_status, len(results), error_code, safe_error, attempts,
                user_id, memory_id,
            ),
        )


def get_inspiration_rendering(user_id: str, memory_id: int) -> dict[str, Any] | None:
    """Return one rendering and its ranked results without crossing user boundaries."""
    initialize_database()
    with _connect() as connection:
        row = connection.execute(
            """SELECT * FROM memory_inspiration_renderings
               WHERE user_id = ? AND memory_id = ?""",
            (user_id, memory_id),
        ).fetchone()
        if row is None:
            return None
        results = connection.execute(
            """SELECT title, summary, url, source_domain, rank, generated_at
               FROM inspiration_rendering_results
               WHERE user_id = ? AND memory_id = ? ORDER BY rank""",
            (user_id, memory_id),
        ).fetchall()
    rendering = dict(row)
    rendering["results"] = [dict(result) for result in results]
    return rendering


def get_memory(user_id: str, memory_id: int) -> dict[str, Any] | None:
    initialize_database()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
    return _as_dict(row)


def list_memories(
    user_id: str,
    limit: int = 20,
    offset: int = 0,
    category_filter: str | None = None,
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be non-negative")
    if category_filter is not None and category_filter not in MEMORY_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(MEMORY_CATEGORIES)}")
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order must be asc or desc")
    initialize_database()
    query, parameters = "SELECT * FROM memories WHERE user_id = ?", [user_id]
    if category_filter is not None:
        query += " AND category = ?"
        parameters.append(category_filter)
    direction = "ASC" if sort_order == "asc" else "DESC"
    query += f" ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?"
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
