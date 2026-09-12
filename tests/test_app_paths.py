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

    assert is_frozen() is True
    assert resource_root() == extract
    assert install_root() == bindir
    assert data_dir() == bindir / "data"
    assert extract not in data_dir().parents
    assert data_dir() != extract / "data"
