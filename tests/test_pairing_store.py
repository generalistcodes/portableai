import json
import sys
import time
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
