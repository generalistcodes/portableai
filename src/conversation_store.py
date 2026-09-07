"""
SQLite-backed conversation history: create/list/search/archive/delete
chats and their messages.

Deliberately just the standard library's sqlite3 -- no ORM, no separate
DB server. This is meant to run on a laptop next to Ollama with zero
extra services, same as everything else in this repo.

Every function takes an open sqlite3.Connection as its first argument
rather than owning connection lifecycle itself, so callers (the Flask
routes, or tests) control when connections open/close and can point at
a temp file in tests without any monkeypatching of this module.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
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

TITLE_MAX_LEN = 60


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection and ensure the schema exists. Safe to call
    repeatedly -- CREATE TABLE IF NOT EXISTS is idempotent."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def create_conversation(conn: sqlite3.Connection, persona: str, model_used: str) -> str:
    conv_id = str(uuid.uuid4())
    now = time.time()
    conn.execute(
        "INSERT INTO conversations (id, persona, model_used, title, created_at, updated_at, archived) "
        "VALUES (?, ?, ?, NULL, ?, ?, 0)",
        (conv_id, persona, model_used, now, now),
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
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        return None
    conv = dict(row)
    conv["messages"] = get_messages(conn, conversation_id)
    return conv


def list_conversations(
    conn: sqlite3.Connection, archived: bool = False, query: str | None = None
) -> list[dict]:
    params: list = [1 if archived else 0]
    sql = "SELECT DISTINCT c.* FROM conversations c"
    if query:
        sql += " LEFT JOIN messages m ON m.conversation_id = c.id"
    sql += " WHERE c.archived = ?"
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
