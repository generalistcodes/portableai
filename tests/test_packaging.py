"""Static checks for the release packaging layout. No PyInstaller, no network."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_release_workflow_is_tag_triggered_and_ships_all_three_oses():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "v*.*.*" in text
    assert "build-linux" in text
    assert "build-windows" in text
    assert "build-macos" in text
    assert "appimagetool" in text
    assert "dmgbuild" in text
    assert "unsigned" in text.lower()
    assert "unnotarized" in text.lower()
    assert "INCOMPLETE.txt" in text
    assert "softprops/action-gh-release" in text
    assert "$99" in text


def test_release_workflow_uploads_stable_filenames():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "PortableAI-linux-${ARCH}.AppImage" in text
    assert "PortableAI-macos-${ARCH}.dmg" in text
    assert "PortableAI-windows-x86_64.exe" in text
    assert "releases/latest/download/PortableAI-linux-x86_64.AppImage" in text
    assert "Duplicate stable-named assets" in text
    linux_job = text.split("name: Linux AppImage")[1].split("name: Windows exe")[0]
    assert 'cp "$image" "dist/PortableAI-linux-${ARCH}.AppImage"' in linux_job
    macos_job = text.split("name: macOS DMG")[1].split("name: GitHub Release")[0]
    assert 'cp "$src" "dist/PortableAI-macos-${ARCH}.dmg"' in macos_job


def test_install_sh_is_the_single_curl_pipe_path():
    path = ROOT / "install.sh"
    text = path.read_text(encoding="utf-8")
    assert path.is_file()
    assert "PortableAI-linux-x86_64.AppImage" in text
    assert "PortableAI-macos-arm64.dmg" in text
    assert "releases/latest/download" in text
    assert "chmod +x" in text
    assert "does not start PortableAI" in text.lower() or "Does not start PortableAI" in text
    assert "MINGW" in text and "arm64" in text
    # Must not exec/launch the downloaded binary (chmod is the only +x).
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        assert "AppImage --" not in stripped
        assert not stripped.startswith("./PortableAI.AppImage")
        assert not stripped.startswith("exec ")


def test_spec_builds_on_every_os():
    text = (ROOT / "portableai.spec").read_text(encoding="utf-8")
    assert "SystemExit" not in text
    assert "persona_cards" in text
    assert 'name="portableai"' in text
    assert '"PIL"' in text


def test_appimage_desktop_entry_has_name_and_icon():
    desktop = (ROOT / "packaging/linux/portableai.desktop").read_text(encoding="utf-8")
    assert "Name=PortableAI" in desktop
    assert "Icon=portableai" in desktop
    assert "Terminal=true" in desktop
    apprun = (ROOT / "packaging/linux/AppRun").read_text(encoding="utf-8")
    assert "--appimage-extract-and-run" in apprun
    assert "portableai.desktop" in apprun
    assert "metadata::custom-icon" in apprun
    assert "portableai.png" in apprun
    script = (ROOT / "packaging/linux/make_appimage.sh").read_text(encoding="utf-8")
    assert "branding/generate_icons.py" in script
    assert "--appdir" in script
    assert "sync_icons.py" not in script
    logo = ROOT / "ui" / "static" / "logo.png"
    assert logo.is_file()
    svg = ROOT / "ui" / "static" / "logo.svg"
    assert svg.is_file()
    source = ROOT / "branding" / "logo-source.svg"
    assert source.is_file()
    assert svg.read_bytes() == source.read_bytes()


def test_appimage_generate_icons_writes_real_diricon(tmp_path):
    import shutil
    import struct
    import subprocess
    import sys

    root = tmp_path / "repo"
    (root / "branding").mkdir(parents=True)
    shutil.copyfile(ROOT / "branding" / "logo-source.svg", root / "branding" / "logo-source.svg")
    appdir = tmp_path / "AppDir"
    script = ROOT / "branding" / "generate_icons.py"
    subprocess.check_call(
        [sys.executable, str(script), "--root", str(root), "--appdir", str(appdir)]
    )
    icon = appdir / "portableai.png"
    diricon = appdir / ".DirIcon"
    assert icon.is_file()
    assert diricon.is_file()
    assert not diricon.is_symlink()
    data = icon.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (256, 256)
    hicolor = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "portableai.png"
    assert hicolor.is_file()
    assert (appdir / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "portableai.svg").is_file()
    assert (appdir / "portableai.svg").read_bytes() == (ROOT / "branding" / "logo-source.svg").read_bytes()


def test_windows_incomplete_file_names_the_real_gaps():
    text = (ROOT / "packaging/windows/INCOMPLETE.txt").read_text(encoding="utf-8")
    assert "_enumerate_lan_ips" in text
    assert "ollama_runtime" in text
    assert "Linux" in text and "macOS" in text


def test_makefile_has_release_and_help_default():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert ".DEFAULT_GOAL := help" in text
    assert "scripts/release.py" in text
    assert "make release VERSION=" in text


def test_changelog_check_workflow_is_advisory_and_skips_changelog_file():
    text = (ROOT / ".github/workflows/changelog-check.yml").read_text(encoding="utf-8")
    assert "continue-on-error: true" in text
    assert "pull_request" in text
    assert "APP_VERSION" in text
    assert "CHANGELOG.md is generated by make release" in text
    assert "do not hand-edit it per PR" in text.lower() or "do not hand-edit it per PR" in text
    # Must not require CHANGELOG.md to appear in the PR diff.
    assert "CHANGELOG.md was NOT modified" not in text


def test_dev_requirements_include_git_cliff():
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "git-cliff" in text
