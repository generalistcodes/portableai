"""Unit tests for scripts/release.py (no git push)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location(
    "portableai_release", ROOT / "scripts" / "release.py"
)
release = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release)


def test_parse_version_accepts_semver_tag():
    assert release.parse_version("v0.6.1") == "v0.6.1"
    assert release.version_number("v0.6.1") == "0.6.1"


def test_parse_version_rejects_unprefixed_and_prerelease():
    with pytest.raises(release.ReleaseError):
        release.parse_version("0.6.1")
    with pytest.raises(release.ReleaseError):
        release.parse_version("v0.6.1-test")
    with pytest.raises(release.ReleaseError):
        release.parse_version("")


def test_set_app_version_rewrites_assignment(tmp_path):
    path = tmp_path / "server.py"
    path.write_text('APP_NAME = "PortableAI"\nAPP_VERSION = "0.4.0"\n', encoding="utf-8")
    release.set_app_version(path, "0.6.1")
    assert 'APP_VERSION = "0.6.1"' in path.read_text(encoding="utf-8")
    assert 'APP_VERSION = "0.4.0"' not in path.read_text(encoding="utf-8")
