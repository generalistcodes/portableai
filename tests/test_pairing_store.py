import json
import sys
import time

import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pairing_store as store


def test_generate_pin_is_six_digits(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    assert len(pin) == 6
    assert pin.isdigit()


def test_generate_pin_sets_expires_at_in_the_future(tmp_path):
    path = tmp_path / "pairing.json"
    store.generate_pin(path, ttl_seconds=300)
    current = store.get_current_pin(path)
    assert current is not None
    assert current["expires_at"] > time.time()
    assert current["expires_at"] <= time.time() + 301


def test_get_current_pin_none_when_never_generated(tmp_path):
    path = tmp_path / "pairing.json"
    assert store.get_current_pin(path) is None


def test_get_current_pin_returns_active_pin(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    current = store.get_current_pin(path)
    assert current["pin"] == pin


def test_get_current_pin_none_when_expired(tmp_path):
    path = tmp_path / "pairing.json"
    store.generate_pin(path, ttl_seconds=-1)  # already expired
    assert store.get_current_pin(path) is None


def test_claim_pin_correct_returns_token(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "Kim's iPhone")
    assert token is not None
    assert len(token) == 32  # secrets.token_hex(16)


def test_claim_pin_is_single_use(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    first = store.claim_pin(path, pin, "Device A")
    assert first is not None
    second = store.claim_pin(path, pin, "Device B")  # same PIN again
    assert second is None


def test_claim_pin_wrong_pin_returns_none(tmp_path):
    path = tmp_path / "pairing.json"
    store.generate_pin(path)
    assert store.claim_pin(path, "000000", "Some device") is None


def test_claim_pin_expired_returns_none(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path, ttl_seconds=-1)
    assert store.claim_pin(path, pin, "Some device") is None


def test_claim_pin_locks_out_after_max_attempts(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    for _ in range(5):
        assert store.claim_pin(path, "999999", "attacker") is None
    # PIN should now be invalidated even though it was never guessed correctly
    assert store.claim_pin(path, pin, "legitimate device") is None
    data = json.loads(path.read_text())
    assert data["current_pin"] is None
    assert data["failed_attempts"] == 0


def test_save_is_valid_json_after_claim(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    store.claim_pin(path, pin, "Device")
    json.loads(path.read_text())  # must not raise JSONDecodeError


def test_concurrent_wrong_pins_lock_out_exactly_once(tmp_path):
    """20 concurrent wrong PINs: lockout after 5 failures, file never torn."""
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(store.claim_pin, path, "000000", "attacker") for _ in range(20)]
        results = [f.result() for f in as_completed(futures)]
    assert all(r is None for r in results)
    assert store.get_current_pin(path) is None
    assert store.claim_pin(path, pin, "late legitimate") is None
    data = json.loads(path.read_text())
    assert data["current_pin"] is None
    assert isinstance(data["devices"], dict)


def test_concurrent_correct_and_wrong_persists_issued_token(tmp_path):
    """2 concurrent correct + 8 wrong: issued token is persisted, JSON intact."""
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)

    def attempt(i):
        submitted = pin if i < 2 else "111111"
        return store.claim_pin(path, submitted, f"device-{i}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(attempt, i) for i in range(10)]
        results = [f.result() for f in as_completed(futures)]

    issued = [t for t in results if t is not None]
    # PIN is single-use: at most one winner, and that token must be on disk.
    assert len(issued) == 1
    data = json.loads(path.read_text())
    assert issued[0] in data["devices"]
    assert store.is_valid_token(path, issued[0]) is True
    assert data["current_pin"] is None


def test_claim_pin_defaults_device_name(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "")
    devices = store.list_devices(path)
    assert devices[0]["name"] == "Unnamed device"
    assert devices[0]["token"] == token


def test_is_valid_token_true_for_paired_device(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "Kim's iPhone")
    assert store.is_valid_token(path, token) is True


def test_is_valid_token_false_for_unknown_token(tmp_path):
    path = tmp_path / "pairing.json"
    assert store.is_valid_token(path, "not-a-real-token") is False


def test_is_valid_token_false_for_empty(tmp_path):
    path = tmp_path / "pairing.json"
    assert store.is_valid_token(path, "") is False


def test_list_devices_includes_token_and_metadata(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "Kim's iPhone")
    devices = store.list_devices(path)
    assert len(devices) == 1
    assert devices[0]["token"] == token
    assert devices[0]["name"] == "Kim's iPhone"
    assert "paired_at" in devices[0]


def test_revoke_device_removes_it(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "Kim's iPhone")
    assert store.revoke_device(path, token) is True
    assert store.is_valid_token(path, token) is False
    assert store.list_devices(path) == []


def test_revoke_device_unknown_token_returns_false(tmp_path):
    path = tmp_path / "pairing.json"
    assert store.revoke_device(path, "does-not-exist") is False


def test_multiple_devices_can_be_paired(tmp_path):
    path = tmp_path / "pairing.json"
    pin1 = store.generate_pin(path)
    token1 = store.claim_pin(path, pin1, "iPhone")
    pin2 = store.generate_pin(path)
    token2 = store.claim_pin(path, pin2, "iPad")
    assert store.is_valid_token(path, token1) is True
    assert store.is_valid_token(path, token2) is True
    assert len(store.list_devices(path)) == 2


def test_family_password_unset_by_default(tmp_path):
    path = tmp_path / "pairing.json"
    assert store.family_password_is_set(path) is False
    result = store.claim_family_password(path, "secret", "Phone", "192.168.1.50")
    assert result["status"] == "invalid"
    assert result["token"] is None


def test_family_password_claim_issues_token_without_consuming_pin(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    store.set_family_password(path, "family-secret")
    first = store.claim_family_password(path, "family-secret", "iPhone", "192.168.1.50")
    second = store.claim_family_password(path, "family-secret", "iPad", "192.168.1.51")
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert first["token"] != second["token"]
    assert store.is_valid_token(path, first["token"]) is True
    assert store.is_valid_token(path, second["token"]) is True
    assert store.get_current_pin(path)["pin"] == pin
    assert store.claim_pin(path, pin, "PIN device") is not None


def test_family_password_wrong_does_not_touch_pin_lockout(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    store.set_family_password(path, "family-secret")
    for _ in range(3):
        assert store.claim_family_password(path, "nope", "attacker", "192.168.1.50")["status"] == "invalid"
    data = json.loads(path.read_text())
    assert data["failed_attempts"] == 0
    assert data["current_pin"] == pin
    assert store.claim_pin(path, pin, "legit") is not None


def test_pin_failures_do_not_lock_family_password(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    store.set_family_password(path, "family-secret")
    for _ in range(5):
        assert store.claim_pin(path, "000000", "attacker") is None
    result = store.claim_family_password(path, "family-secret", "Phone", "192.168.1.50")
    assert result["status"] == "ok"
    assert store.get_current_pin(path) is None


def test_family_password_rate_limit_is_per_source(tmp_path):
    path = tmp_path / "pairing.json"
    store.set_family_password(path, "family-secret")
    locked_source = "192.168.1.50"
    other_source = "192.168.1.51"
    statuses = [
        store.claim_family_password(path, "wrong", "attacker", locked_source)["status"]
        for _ in range(5)
    ]
    assert statuses[:4] == ["invalid"] * 4
    assert statuses[4] == "locked"
    still_locked = store.claim_family_password(path, "family-secret", "attacker", locked_source)
    assert still_locked["status"] == "locked"
    other = store.claim_family_password(path, "family-secret", "Other phone", other_source)
    assert other["status"] == "ok"
    data = json.loads(path.read_text())
    assert data["failed_attempts"] == 0


def test_family_password_lockout_expires(tmp_path):
    path = tmp_path / "pairing.json"
    store.set_family_password(path, "family-secret")
    now = 1_000.0
    for _ in range(5):
        store.claim_family_password(
            path, "wrong", "attacker", "192.168.1.50", lockout_seconds=60, now=now
        )
    locked = store.claim_family_password(
        path, "family-secret", "Phone", "192.168.1.50", lockout_seconds=60, now=now + 10
    )
    assert locked["status"] == "locked"
    after = store.claim_family_password(
        path, "family-secret", "Phone", "192.168.1.50", lockout_seconds=60, now=now + 61
    )
    assert after["status"] == "ok"


def test_clearing_family_password_stops_claims(tmp_path):
    path = tmp_path / "pairing.json"
    store.set_family_password(path, "family-secret")
    store.set_family_password(path, "")
    assert store.family_password_is_set(path) is False
    result = store.claim_family_password(path, "family-secret", "Phone", "192.168.1.50")
    assert result["status"] == "invalid"


def test_claim_without_pending_parent_is_independent(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    token = store.claim_pin(path, pin, "Solo")
    device = store.list_devices(path)[0]
    assert device["token"] == token
    assert device["parent_device_id"] is None
    assert device["id"] and device["id"] != token


def test_pending_parent_is_applied_once_at_claim(tmp_path):
    path = tmp_path / "pairing.json"
    parent = store.claim_pin(path, store.generate_pin(path), "Parent")
    store.set_pending_parent(path, parent)
    assert store.get_pending_parent(path) == parent
    child = store.claim_pin(path, store.generate_pin(path), "Child")
    assert store.get_pending_parent(path) is None
    rows = {d["token"]: d for d in store.list_devices(path)}
    assert rows[child]["parent_device_id"] == parent
    assert rows[parent]["parent_device_id"] is None
    # A later claim does not inherit the spent link.
    later = store.claim_pin(path, store.generate_pin(path), "Later")
    rows = {d["token"]: d for d in store.list_devices(path)}
    assert rows[later]["parent_device_id"] is None
    assert rows[parent]["parent_device_id"] is None


def test_set_pending_parent_does_not_rewrite_existing_devices(tmp_path):
    path = tmp_path / "pairing.json"
    parent = store.claim_pin(path, store.generate_pin(path), "Parent")
    already = store.claim_pin(path, store.generate_pin(path), "Already paired")
    store.set_pending_parent(path, parent)
    rows = {d["token"]: d for d in store.list_devices(path)}
    assert rows[already]["parent_device_id"] is None
    assert rows[parent]["parent_device_id"] is None


def test_set_pending_parent_rejects_unknown_token(tmp_path):
    path = tmp_path / "pairing.json"
    store.generate_pin(path)
    with pytest.raises(ValueError):
        store.set_pending_parent(path, "not-a-device")
    assert store.get_pending_parent(path) is None


def test_list_children_and_mirror_lookup_follow_the_stored_parent(tmp_path):
    path = tmp_path / "pairing.json"
    parent = store.claim_pin(path, store.generate_pin(path), "Parent")
    stranger = store.claim_pin(path, store.generate_pin(path), "Stranger")
    store.set_pending_parent(path, parent)
    child = store.claim_pin(path, store.generate_pin(path), "Child")
    rows = {d["token"]: d for d in store.list_devices(path)}
    children = store.list_children(path, parent)
    assert children == [{"id": rows[child]["id"], "name": "Child"}]
    assert "token" not in children[0]
    assert store.list_children(path, child) == []
    assert store.list_children(path, stranger) == []
    found = store.child_for_parent(path, rows[child]["id"], parent)
    assert found["token"] == child
    assert store.child_for_parent(path, rows[child]["id"], stranger) is None
    assert store.child_for_parent(path, rows[child]["id"], child) is None
    assert store.child_for_parent(path, rows[stranger]["id"], parent) is None
    assert store.child_for_parent(path, "not-an-id", parent) is None


def test_family_password_claim_consumes_pending_parent(tmp_path):
    path = tmp_path / "pairing.json"
    parent = store.claim_pin(path, store.generate_pin(path), "Parent")
    store.set_family_password(path, "family-secret")
    store.set_pending_parent(path, parent)
    result = store.claim_family_password(path, "family-secret", "Child", "192.168.1.50")
    assert result["status"] == "ok"
    rows = {d["token"]: d for d in store.list_devices(path)}
    assert rows[result["token"]]["parent_device_id"] == parent
    assert store.get_pending_parent(path) is None


def test_last_seen_sorts_recent_activity_first(tmp_path):
    path = tmp_path / "pairing.json"
    older = store.claim_pin(path, store.generate_pin(path), "Older")
    newer = store.claim_pin(path, store.generate_pin(path), "Newer")
    store.touch_last_seen(path, older, now=1_000)
    store.touch_last_seen(path, newer, now=2_000)
    store.touch_last_seen(path, newer, now=2_030)
    rows = store.list_devices(path)
    assert [row["name"] for row in rows] == ["Newer", "Older"]
    assert rows[0]["last_seen"] == 2_000
    store.touch_last_seen(path, "not-a-device", now=9_000)
    assert store.list_devices(path)[0]["last_seen"] == 2_000


def test_linked_child_requires_a_parent(tmp_path):
    path = tmp_path / "pairing.json"
    parent = store.claim_pin(path, store.generate_pin(path), "Parent")
    store.set_pending_parent(path, parent)
    child = store.claim_pin(path, store.generate_pin(path), "Child")
    rows = {d["token"]: d for d in store.list_devices(path)}
    assert store.linked_child(path, rows[child]["id"])["token"] == child
    assert store.linked_child(path, rows[parent]["id"]) is None
    assert store.linked_child(path, "missing") is None


def test_family_password_is_hashed_on_disk(tmp_path):
    path = tmp_path / "pairing.json"
    store.set_family_password(path, "family-secret")
    raw = path.read_text()
    assert "family-secret" not in raw
    data = json.loads(raw)
    assert data["family_password_hash"]
