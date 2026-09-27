"""Path helpers for source vs PyInstaller-frozen runs. No Flask, no Ollama."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os

from app_paths import (
    _device_is_removable,
    data_dir,
    install_root,
    is_frozen,
    resource_root,
)


def test_unfrozen_is_not_frozen():
    assert is_frozen() is False


def test_unfrozen_resource_root_is_the_repo():
    repo = Path(__file__).resolve().parent.parent
    assert resource_root() == repo
    assert (resource_root() / "personas").is_dir()
    assert (resource_root() / "ui" / "static" / "index.html").is_file()
    assert (resource_root() / "ui" / "model_catalog.json").is_file()


def test_unfrozen_data_dir_follows_argv0(monkeypatch, tmp_path):
    launched = tmp_path / "run.py"
    launched.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(launched)])
    assert install_root() == tmp_path
    assert data_dir() == tmp_path / "data"


def _linux_packaged(monkeypatch, home: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


def test_frozen_linux_data_dir_is_xdg_when_not_removable(monkeypatch, tmp_path):
    extract = tmp_path / "_MEI12345"
    extract.mkdir()
    bindir = tmp_path / "opt"
    bindir.mkdir()
    binary = bindir / "portableai"
    binary.write_bytes(b"\0")
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.delenv("APPIMAGE", raising=False)
    _linux_packaged(monkeypatch, home)
    monkeypatch.setattr("app_paths._is_removable_mount", lambda path: False)

    assert is_frozen() is True
    assert resource_root() == extract
    assert install_root() == bindir
    assert data_dir() == home / ".local" / "share" / "PortableAI"
    assert extract not in data_dir().parents
    assert data_dir() != bindir / "data"


def test_frozen_linux_data_dir_stays_next_to_the_binary_on_removable_media(monkeypatch, tmp_path):
    extract = tmp_path / "_MEI12345"
    extract.mkdir()
    bindir = tmp_path / "usb"
    bindir.mkdir()
    binary = bindir / "portableai"
    binary.write_bytes(b"\0")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.delenv("APPIMAGE", raising=False)
    _linux_packaged(monkeypatch, tmp_path / "home")
    monkeypatch.setattr("app_paths._is_removable_mount", lambda path: True)

    assert data_dir() == bindir / "data"


def test_appimage_on_internal_disk_uses_xdg_data_home(monkeypatch, tmp_path):
    extract = tmp_path / "_MEI"
    extract.mkdir()
    squash = tmp_path / "squash" / "usr" / "bin"
    squash.mkdir(parents=True)
    binary = squash / "portableai"
    binary.write_bytes(b"\0")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    appimage = downloads / "PortableAI.AppImage"
    appimage.write_bytes(b"\0")
    xdg = tmp_path / "xdg"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setenv("APPIMAGE", str(appimage))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr("app_paths._is_removable_mount", lambda path: False)

    assert install_root() == downloads
    assert data_dir() == xdg / "PortableAI"


def test_appimage_on_removable_media_keeps_data_next_to_the_file(monkeypatch, tmp_path):
    downloads = tmp_path / "usb"
    downloads.mkdir()
    appimage = downloads / "PortableAI.AppImage"
    appimage.write_bytes(b"\0")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setenv("APPIMAGE", str(appimage))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("app_paths._is_removable_mount", lambda path: True)

    assert data_dir() == downloads / "data"


def test_legacy_data_next_to_the_app_is_moved_into_xdg(monkeypatch, tmp_path):
    bindir = tmp_path / "opt"
    bindir.mkdir()
    binary = bindir / "portableai"
    binary.write_bytes(b"\0")
    legacy = bindir / "data"
    legacy.mkdir()
    (legacy / "chats.db").write_bytes(b"sqlite-bytes")
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.delenv("APPIMAGE", raising=False)
    _linux_packaged(monkeypatch, home)
    monkeypatch.setattr("app_paths._is_removable_mount", lambda path: False)

    target = home / ".local" / "share" / "PortableAI"
    assert data_dir() == target
    assert (target / "chats.db").read_bytes() == b"sqlite-bytes"
    assert not legacy.exists()


def test_device_is_removable_reads_the_parent_disk(tmp_path):
    sysfs = tmp_path / "sys"
    disk = sysfs / "devices" / "sdb"
    disk.mkdir(parents=True)
    (disk / "removable").write_text("1\n", encoding="utf-8")
    part = disk / "sdb1"
    part.mkdir()
    link = sysfs / "dev" / "block" / "8:17"
    link.parent.mkdir(parents=True)
    link.symlink_to(part)

    assert _device_is_removable(os.makedev(8, 17), sysfs) is True
    (disk / "removable").write_text("0\n", encoding="utf-8")
    assert _device_is_removable(os.makedev(8, 17), sysfs) is False


def test_macos_app_data_dir_is_next_to_the_bundle(monkeypatch, tmp_path):
    extract = tmp_path / "_MEI"
    extract.mkdir()
    folder = tmp_path / "Applications"
    macos_dir = folder / "PortableAI.app" / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    binary = macos_dir / "portableai"
    binary.write_bytes(b"\0")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("APPIMAGE", raising=False)

    assert install_root() == folder
    assert data_dir() == folder / "data"
