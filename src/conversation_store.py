"""
SQLite-backed conversation history: create/list/search/archive/delete
chats and their messages, scoped per owner (a paired device, or "local"
for the desktop browser on the server machine itself).

Deliberately just the standard library's sqlite3 -- no ORM, no separate
DB server. This is meant to run on a laptop next to Ollama with zero
extra services, same as everything else in this repo.

Every function takes an open sqlite3.Connection as its first argument
rather than owning connection lifecycle itself, so callers (the Flask
routes, or tests) control when connections open/close and can point at
a temp file in tests without any monkeypatching of this module.

Ownership model: "local" is the fixed owner_id for anything created from
the desktop browser (trusted via remote_addr, not a real device token).
Every paired phone's owner_id is its device token. This module does not
enforce access control -- it just stores and filters by owner_id; the
Flask layer decides who is allowed to ask for which owner_id (see
server.py's _caller_identity()).
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path

LOCAL_OWNER_ID = "local"
log = logging.getLogger(__name__)


class ChatDatabaseError(RuntimeError):
    """chats.db exists but is not a usable SQLite database."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    persona TEXT NOT NULL,
    model_used TEXT NOT NULL,
    title TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    latency_ms INTEGER,
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
"""
# Kept separate from SCHEMA_SQL: on a pre-ownership database, the
# conversations table exists without owner_id yet, so this index can only
# be created *after* _migrate_add_owner_id() has added the column below.
_OWNER_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_id);"

TITLE_MAX_LEN = 60


def _migrate_add_owner_id(conn: sqlite3.Connection) -> None:
    """Databases created before per-device ownership existed won't have
    this column yet -- CREATE TABLE IF NOT EXISTS doesn't retrofit
    existing tables, so add it explicitly. Existing rows default to
    'local' (the admin view), which is a safe assumption since nothing
    was previously scoped to any specific device anyway."""
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "owner_id" not in columns:
        conn.execute(f"ALTER TABLE conversations ADD COLUMN owner_id TEXT NOT NULL DEFAULT '{LOCAL_OWNER_ID}'")
        conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection and ensure the schema exists. Safe to call
    repeatedly -- CREATE TABLE IF NOT EXISTS is idempotent.

    Distinguishes a brand-new file from a wiped or malformed one:
    - missing file → create schema (normal first run)
    - exists, size 0 → warn (looks truncated), then initialize
    - exists, malformed / integrity_check fail → ChatDatabaseError
    """
    path = Path(db_path)
    existed = path.exists()
    size = path.stat().st_size if existed else 0

    if existed and size == 0:
        log.warning(
            "chat history database %s exists but is 0 bytes — looks like a "
            "wiped/truncated database; initializing a new empty schema. "
            "Prior chat history is gone.",
            path,
        )

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if existed and size > 0:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise ChatDatabaseError("chat history database appears corrupted")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        _migrate_add_owner_id(conn)
        conn.execute(_OWNER_INDEX_SQL)
        conn.commit()
    except ChatDatabaseError:
        conn.close()
        raise
    except sqlite3.DatabaseError as e:
        conn.close()
        raise ChatDatabaseError("chat history database appears corrupted") from e
    return conn


def create_conversation(conn: sqlite3.Connection, persona: str, model_used: str, owner_id: str) -> str:
    conv_id = str(uuid.uuid4())
    now = time.time()
    conn.execute(
        "INSERT INTO conversations (id, owner_id, persona, model_used, title, created_at, updated_at, archived) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?, 0)",
        (conv_id, owner_id, persona, model_used, now, now),
    )
    conn.commit()
    return conv_id


def add_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    latency_ms: int | None = None,
) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, latency_ms, created_at) VALUES (?, ?, ?, ?, ?)",
        (conversation_id, role, content, latency_ms, now),
    )
    row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is not None and row["title"] is None and role == "user":
        title = content.strip().splitlines()[0][:TITLE_MAX_LEN]
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conversation_id),
        )
    else:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    conn.commit()


def get_messages(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content, latency_ms, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    """Returns the conversation regardless of owner -- callers that need
    to enforce 'is this actually yours' must check the returned
    owner_id themselves (see server.py). Kept unauthenticated here so
    the admin view and ownership checks can both use the same function."""
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        return None
    conv = dict(row)
    conv["messages"] = get_messages(conn, conversation_id)
    return conv


def list_conversations(
    conn: sqlite3.Connection,
    owner_id: str | None = None,
    archived: bool = False,
    query: str | None = None,
) -> list[dict]:
    """owner_id=None means no ownership filter -- the admin view across
    every device. Pass a specific owner_id to scope to just that device
    (or 'local')."""
    params: list = [1 if archived else 0]
    sql = "SELECT DISTINCT c.* FROM conversations c"
    if query:
        sql += " LEFT JOIN messages m ON m.conversation_id = c.id"
    sql += " WHERE c.archived = ?"
    if owner_id is not None:
        sql += " AND c.owner_id = ?"
        params.append(owner_id)
    if query:
        like = f"%{query}%"
        sql += " AND (c.title LIKE ? OR m.content LIKE ?)"
        params.extend([like, like])
    sql += " ORDER BY c.updated_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_archived(conn: sqlite3.Connection, conversation_id: str, archived: bool) -> None:
    conn.execute(
        "UPDATE conversations SET archived = ?, updated_at = ? WHERE id = ?",
        (1 if archived else 0, time.time(), conversation_id),
    )
    conn.commit()


def rename_conversation(conn: sqlite3.Connection, conversation_id: str, title: str) -> None:
    conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
    conn.commit()


def delete_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
