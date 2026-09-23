"""Branding generator: one SVG in, every platform copy out."""
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATE = ROOT / "branding" / "generate_icons.py"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_web_logo_svg_is_byte_identical_to_source():
    source = (ROOT / "branding" / "logo-source.svg").read_bytes()
    web = (ROOT / "ui" / "static" / "logo.svg").read_bytes()
    assert source == web
    assert b"portableai-badge" in source
    assert b"#b224aa" in source


def test_macos_dmg_script_uses_branding_icns():
    text = (ROOT / "packaging" / "macos" / "make_dmg.sh").read_text(encoding="utf-8")
    assert "branding/generated/macos/portableai.icns" in text
    assert "CFBundleIconFile" in text
    assert "ui/static/logo.png" not in text


def test_release_macos_job_generates_branding_icons():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    macos = text.split("name: macOS DMG")[1].split("name: GitHub Release")[0]
    assert "python branding/generate_icons.py" in macos
    assert "make_dmg.sh" in macos


def test_generate_icons_writes_all_four_targets(tmp_path):
    root = tmp_path / "repo"
    branding = root / "branding"
    branding.mkdir(parents=True)
    shutil.copyfile(ROOT / "branding" / "logo-source.svg", branding / "logo-source.svg")
    subprocess.check_call([sys.executable, str(GENERATE), "--root", str(root)])

    source = (branding / "logo-source.svg").read_bytes()
    web_svg = root / "ui" / "static" / "logo.svg"
    assert web_svg.read_bytes() == source
    assert _png_size(root / "ui" / "static" / "logo.png") == (512, 512)

    linux_png = root / "packaging" / "linux" / "icons" / "portableai.png"
    assert _png_size(linux_png) == (256, 256)
    linux_svg = root / "packaging" / "linux" / "icons" / "portableai.svg"
    assert linux_svg.read_bytes() == source
    assert (
        root
        / "packaging"
        / "linux"
        / "icons"
        / "hicolor"
        / "256x256"
        / "apps"
        / "portableai.png"
    ).is_file()

    icns = root / "branding" / "generated" / "macos" / "portableai.icns"
    assert icns.read_bytes()[:4] == b"icns"
    assert (root / "branding" / "generated" / "macos" / "portableai.iconset" / "icon_512x512@2x.png").is_file()

    appicon = root / "branding" / "generated" / "ios" / "AppIcon.appiconset"
    store = appicon / "AppIcon-1024x1024.png"
    assert _png_size(store) == (1024, 1024)
    catalog = json.loads((appicon / "Contents.json").read_text(encoding="utf-8"))
    names = {img["filename"] for img in catalog["images"]}
    assert "AppIcon-1024x1024.png" in names
    assert (appicon / "AppIcon-60x60@3x.png").is_file()
    assert _png_size(appicon / "AppIcon-60x60@3x.png") == (180, 180)
