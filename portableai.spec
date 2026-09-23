# -*- mode: python ; coding: utf-8 -*-
"""One-file PyInstaller build of PortableAI for Linux, macOS, and Windows.

Linux is the complete packaged path (vendored Ollama + LAN IP detection).
macOS has those runtime pieces; the .app/.dmg this spec feeds is unsigned.
Windows still has no vendored-Ollama download and no LAN IP enumerator —
the .exe is the Python UI plus a console, not a full installer.
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    ["run.py"],
    pathex=[str(ROOT / "src"), str(ROOT / "ui")],
    binaries=[],
    datas=[
        (str(ROOT / "ui" / "static"), "ui/static"),
        (str(ROOT / "personas"), "personas"),
        (str(ROOT / "ui" / "model_catalog.json"), "ui"),
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
