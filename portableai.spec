# -*- mode: python ; coding: utf-8 -*-
"""One-file PyInstaller build of PortableAI for Linux, macOS, and Windows.

Linux is the complete packaged path (vendored Ollama + LAN IP detection).
macOS has those runtime pieces; the .app/.dmg this spec feeds is unsigned.
Windows vendors Ollama (zip) and enumerates LAN IPs via ipconfig; the .exe
is unsigned and has no installer (see packaging/windows/INCOMPLETE.txt).

Frozen HTTPS on macOS does not inherit the system CA store. certifi's
cacert.pem is shipped as package data so urllib can verify GitHub TLS.
hook-certifi from _pyinstaller_hooks_contrib also collects this file;
the datas entry below is explicit so a missing hook cannot drop it.
"""
import sys
from pathlib import Path

import certifi

ROOT = Path(SPECPATH)
# certifi.where() is .../certifi/cacert.pem at analysis time. Dest dir
# "certifi" means the frozen path is _MEIPASS/certifi/cacert.pem, which
# matches certifi.where() inside the bundle.
CERTIFI_CACERT = Path(certifi.where())

a = Analysis(
    ["run.py"],
    pathex=[str(ROOT / "src"), str(ROOT / "ui")],
    binaries=[],
    datas=[
        (str(ROOT / "ui" / "static"), "ui/static"),
        (str(ROOT / "personas"), "personas"),
        (str(ROOT / "ui" / "model_catalog.json"), "ui"),
        (str(CERTIFI_CACERT), "certifi"),
    ],
    hiddenimports=[
        "server",
        "app_paths",
        "ollama_client",
        "ollama_runtime",
        "setup_progress",
        "first_run",
        "persona_loader",
        "persona_cards",
        "conversation_store",
        "pairing_store",
        "mdns_broadcast",
        "zeroconf",
        "ifaddr",
        "flask",
        "jinja2",
        "werkzeug",
        "requests",
        "certifi",
        "qrcode",
        "qrcode.image.svg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["benchmarks", "tests", "tkinter", "PIL", "Pillow"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="portableai",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=sys.platform == "darwin",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
