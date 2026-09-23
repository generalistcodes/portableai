"""Path helpers for source vs PyInstaller-frozen runs. No Flask, no Ollama."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app_paths import data_dir, install_root, is_frozen, resource_root


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


def test_frozen_data_dir_is_next_to_the_binary_not_meipass(monkeypatch, tmp_path):
    extract = tmp_path / "_MEI12345"
    extract.mkdir()
    bindir = tmp_path / "opt"
    bindir.mkdir()
    binary = bindir / "portableai"
    binary.write_bytes(b"\0")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.delenv("APPIMAGE", raising=False)

    assert is_frozen() is True
    assert resource_root() == extract
    assert install_root() == bindir
    assert data_dir() == bindir / "data"
    assert extract not in data_dir().parents
    assert data_dir() != extract / "data"


def test_appimage_data_dir_is_next_to_the_appimage_file(monkeypatch, tmp_path):
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

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extract), raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setenv("APPIMAGE", str(appimage))

    assert install_root() == downloads
    assert data_dir() == downloads / "data"


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
    monkeypatch.delenv("APPIMAGE", raising=False)

    assert install_root() == folder
    assert data_dir() == folder / "data"
