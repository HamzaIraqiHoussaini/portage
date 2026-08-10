# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — build portable desktop binaries for macOS / Windows / Linux."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / ".env.example"), "."),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "app.main",
    "app.desktop",
    "app.providers",
    "app.workspaces",
    "app.skills",
    "app.chats",
    "app.writeback",
    "boto3",
    "webview",
]

for pkg in ("uvicorn", "webview"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports += collect_submodules(pkg)

a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Portage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=sys.platform == "darwin",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Portage",
)

if sys.platform == "darwin":
    icon = ROOT / "Portage.app" / "Contents" / "Resources" / "AppIcon.icns"
    app = BUNDLE(
        coll,
        name="Portage.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="app.portage.desktop",
        info_plist={
            "CFBundleDisplayName": "Portage",
            "CFBundleName": "Portage",
            "CFBundleShortVersionString": "0.3.0",
            "NSHighResolutionCapable": True,
        },
    )
