import sys
import sqlite3
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import conversation_store as store

LOCAL = store.LOCAL_OWNER_ID
DEVICE_A = "device-token-aaa"
DEVICE_B = "device-token-bbb"


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_create_conversation_returns_uuid(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    assert len(conv_id) == 36  # uuid4 string form


def test_new_conversation_has_no_title_and_no_messages(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] is None
    assert conv["messages"] == []
    assert conv["archived"] == 0
    assert conv["owner_id"] == LOCAL


def test_create_conversation_stores_owner_id(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_A)
    conv = store.get_conversation(conn, conv_id)
    assert conv["owner_id"] == DEVICE_A


def test_first_user_message_sets_title(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "Should I use REST or GraphQL?")
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] == "Should I use REST or GraphQL?"


def test_title_is_truncated_and_only_set_once(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    long_message = "x" * 200
    store.add_message(conn, conv_id, "user", long_message)
    store.add_message(conn, conv_id, "assistant", "some reply")
    store.add_message(conn, conv_id, "user", "a second question entirely")
    conv = store.get_conversation(conn, conv_id)
    assert len(conv["title"]) == store.TITLE_MAX_LEN
    assert conv["title"] == "x" * store.TITLE_MAX_LEN  # not overwritten by the 2nd user msg


def test_messages_are_returned_in_order(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "What is an API?")
    store.add_message(conn, conv_id, "assistant", "Imagine a waiter...", latency_ms=812)
    store.add_message(conn, conv_id, "user", "And a webhook?")
    messages = store.get_messages(conn, conv_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["latency_ms"] == 812


def test_get_conversation_missing_returns_none(conn):
    assert store.get_conversation(conn, "does-not-exist") is None


def test_list_conversations_excludes_archived_by_default(conn):
    active_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    archived_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", LOCAL)
    store.set_archived(conn, archived_id, True)

    active = store.list_conversations(conn, archived=False)
    archived = store.list_conversations(conn, archived=True)

    assert {c["id"] for c in active} == {active_id}
    assert {c["id"] for c in archived} == {archived_id}


def test_list_conversations_ordered_most_recent_first(conn):
    first = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    time.sleep(0.01)
    second = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    convs = store.list_conversations(conn)
    assert [c["id"] for c in convs] == [second, first]


def test_search_matches_title(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "Should I use REST or GraphQL?")
    results = store.list_conversations(conn, query="GraphQL")
    assert len(results) == 1
    assert results[0]["id"] == conv_id

    no_match = store.list_conversations(conn, query="kubernetes")
    assert no_match == []


def test_search_matches_message_body_not_just_title(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "What is an API?")
    store.add_message(conn, conv_id, "assistant", "Think of it like a restaurant menu.")
    results = store.list_conversations(conn, query="restaurant menu")
    assert len(results) == 1
    assert results[0]["id"] == conv_id


def test_search_does_not_duplicate_rows_on_multiple_message_matches(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "tell me about apples")
    store.add_message(conn, conv_id, "assistant", "apples are a fruit, apples grow on trees")
    results = store.list_conversations(conn, query="apples")
    assert len(results) == 1


def test_set_archived_toggle(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    store.set_archived(conn, conv_id, True)
    assert store.list_conversations(conn, archived=True)[0]["id"] == conv_id
    store.set_archived(conn, conv_id, False)
    assert store.list_conversations(conn, archived=False)[0]["id"] == conv_id


def test_rename_conversation(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "original title source")
    store.rename_conversation(conn, conv_id, "My custom title")
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] == "My custom title"


def test_delete_conversation_removes_messages_too(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    store.add_message(conn, conv_id, "user", "hello")
    store.delete_conversation(conn, conv_id)
    assert store.get_conversation(conn, conv_id) is None
    remaining = conn.execute(
        "SELECT COUNT(*) as n FROM messages WHERE conversation_id = ?", (conv_id,)
    ).fetchone()
    assert remaining["n"] == 0


def test_connect_is_idempotent_on_existing_db(tmp_path):
    path = tmp_path / "test.db"
    conn1 = store.connect(path)
    conv_id = store.create_conversation(conn1, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    conn1.close()

    conn2 = store.connect(path)  # re-running schema creation shouldn't wipe data
    conv = store.get_conversation(conn2, conv_id)
    assert conv is not None
    conn2.close()


# ---------- Per-device ownership ----------


def test_list_conversations_with_owner_id_filters_to_that_owner(conn):
    mine = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_A)
    theirs = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_B)

    only_a = store.list_conversations(conn, owner_id=DEVICE_A)
    only_b = store.list_conversations(conn, owner_id=DEVICE_B)

    assert {c["id"] for c in only_a} == {mine}
    assert {c["id"] for c in only_b} == {theirs}


def test_list_conversations_with_no_owner_id_returns_everyone(conn):
    a = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_A)
    b = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", DEVICE_B)
    local = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b", LOCAL)

    everyone = store.list_conversations(conn, owner_id=None)
    assert {c["id"] for c in everyone} == {a, b, local}


def test_owner_filter_combines_with_search(conn):
    mine = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_A)
    store.add_message(conn, mine, "user", "ask about GraphQL")
    theirs = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", DEVICE_B)
    store.add_message(conn, theirs, "user", "ask about GraphQL too")

    results = store.list_conversations(conn, owner_id=DEVICE_A, query="GraphQL")
    assert {c["id"] for c in results} == {mine}


def test_migration_adds_owner_id_to_pre_existing_db(tmp_path):
    """Simulates a chats.db created before ownership existed: a
    conversations table with no owner_id column at all."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, persona TEXT NOT NULL, "
        "model_used TEXT NOT NULL, title TEXT, created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO conversations (id, persona, model_used, title, created_at, updated_at, archived) "
        "VALUES ('legacy-1', 'no-nonsense-mentor', 'llama3.2:3b', 'Old chat', 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    migrated = store.connect(path)
    conv = store.get_conversation(migrated, "legacy-1")
    assert conv is not None
    assert conv["owner_id"] == store.LOCAL_OWNER_ID  # safe default for pre-existing rows
    migrated.close()


def test_connect_zero_byte_file_warns_then_initializes(tmp_path, caplog):
    path = tmp_path / "chats.db"
    path.write_bytes(b"")
    with caplog.at_level("WARNING", logger="conversation_store"):
        conn = store.connect(path)
    assert any("0 bytes" in r.message for r in caplog.records)
    store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    conn.close()


def test_connect_garbage_header_raises_chat_database_error(tmp_path):
    path = tmp_path / "chats.db"
    path.write_bytes(b"this is not a sqlite database at all!!!!")
    with pytest.raises(store.ChatDatabaseError, match="corrupted"):
        store.connect(path)


def test_connect_truncated_db_raises_chat_database_error(tmp_path):
    path = tmp_path / "chats.db"
    conn = store.connect(path)
    store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b", LOCAL)
    conn.close()
    raw = path.read_bytes()
    path.write_bytes(raw[:40])  # keep a sqlite header-ish prefix, drop the rest
    with pytest.raises(store.ChatDatabaseError, match="corrupted"):
        store.connect(path)
