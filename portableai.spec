# -*- mode: python ; coding: utf-8 -*-
"""Linux-only one-file build of PortableAI. Mac/Windows are a later pass."""
import sys
from pathlib import Path

if not sys.platform.startswith("linux"):
    raise SystemExit("portableai.spec is Linux-only for now")

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
        "persona_loader",
        "conversation_store",
        "pairing_store",
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
    excludes=["benchmarks", "tests", "tkinter"],
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
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
