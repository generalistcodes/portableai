import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import conversation_store as store


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_create_conversation_returns_uuid(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    assert len(conv_id) == 36  # uuid4 string form


def test_new_conversation_has_no_title_and_no_messages(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] is None
    assert conv["messages"] == []
    assert conv["archived"] == 0


def test_first_user_message_sets_title(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "Should I use REST or GraphQL?")
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] == "Should I use REST or GraphQL?"


def test_title_is_truncated_and_only_set_once(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    long_message = "x" * 200
    store.add_message(conn, conv_id, "user", long_message)
    store.add_message(conn, conv_id, "assistant", "some reply")
    store.add_message(conn, conv_id, "user", "a second question entirely")
    conv = store.get_conversation(conn, conv_id)
    assert len(conv["title"]) == store.TITLE_MAX_LEN
    assert conv["title"] == "x" * store.TITLE_MAX_LEN  # not overwritten by the 2nd user msg


def test_messages_are_returned_in_order(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "What is an API?")
    store.add_message(conn, conv_id, "assistant", "Imagine a waiter...", latency_ms=812)
    store.add_message(conn, conv_id, "user", "And a webhook?")
    messages = store.get_messages(conn, conv_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["latency_ms"] == 812


def test_get_conversation_missing_returns_none(conn):
    assert store.get_conversation(conn, "does-not-exist") is None


def test_list_conversations_excludes_archived_by_default(conn):
    active_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    archived_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b")
    store.set_archived(conn, archived_id, True)

    active = store.list_conversations(conn, archived=False)
    archived = store.list_conversations(conn, archived=True)

    assert {c["id"] for c in active} == {active_id}
    assert {c["id"] for c in archived} == {archived_id}


def test_list_conversations_ordered_most_recent_first(conn):
    first = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    time.sleep(0.01)
    second = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    convs = store.list_conversations(conn)
    assert [c["id"] for c in convs] == [second, first]


def test_search_matches_title(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "Should I use REST or GraphQL?")
    results = store.list_conversations(conn, query="GraphQL")
    assert len(results) == 1
    assert results[0]["id"] == conv_id

    no_match = store.list_conversations(conn, query="kubernetes")
    assert no_match == []


def test_search_matches_message_body_not_just_title(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "What is an API?")
    store.add_message(conn, conv_id, "assistant", "Think of it like a restaurant menu.")
    results = store.list_conversations(conn, query="restaurant menu")
    assert len(results) == 1
    assert results[0]["id"] == conv_id


def test_search_does_not_duplicate_rows_on_multiple_message_matches(conn):
    conv_id = store.create_conversation(conn, "eli5-explainer", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "tell me about apples")
    store.add_message(conn, conv_id, "assistant", "apples are a fruit, apples grow on trees")
    results = store.list_conversations(conn, query="apples")
    assert len(results) == 1


def test_set_archived_toggle(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    store.set_archived(conn, conv_id, True)
    assert store.list_conversations(conn, archived=True)[0]["id"] == conv_id
    store.set_archived(conn, conv_id, False)
    assert store.list_conversations(conn, archived=False)[0]["id"] == conv_id


def test_rename_conversation(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
    store.add_message(conn, conv_id, "user", "original title source")
    store.rename_conversation(conn, conv_id, "My custom title")
    conv = store.get_conversation(conn, conv_id)
    assert conv["title"] == "My custom title"


def test_delete_conversation_removes_messages_too(conn):
    conv_id = store.create_conversation(conn, "no-nonsense-mentor", "llama3.2:3b")
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
    conv_id = store.create_conversation(conn1, "no-nonsense-mentor", "llama3.2:3b")
    conn1.close()

    conn2 = store.connect(path)  # re-running schema creation shouldn't wipe data
    conv = store.get_conversation(conn2, conv_id)
    assert conv is not None
    conn2.close()
