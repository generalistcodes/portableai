import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pairing_store as store


def test_generate_pin_is_six_digits(tmp_path):
    path = tmp_path / "pairing.json"
    pin = store.generate_pin(path)
    assert len(pin) == 6
    assert pin.isdigit()


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
