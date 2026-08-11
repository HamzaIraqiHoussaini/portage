# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — build portable desktop binaries for macOS / Windows / Linux."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.4.0"

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "VERSION"), "."),
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
    "app.importers",
    "app.agent_loop",
    "app.agent_tools",
    "app.checkpoints",
    "app.pending_patches",
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

_win_version = None
if sys.platform == "win32":
    ver_path = ROOT / "scripts" / "file_version_info.txt"
    if ver_path.is_file():
        _win_version = str(ver_path)

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
    version=_win_version,
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
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSDocumentsFolderUsageDescription": (
                "Portage links your project folders so the agent can read and edit files there."
            ),
            "NSDownloadsFolderUsageDescription": (
                "Portage may access Downloads only when you link a workspace under that folder."
            ),
            "NSDesktopFolderUsageDescription": (
                "Portage may access Desktop only when you link a workspace under that folder."
            ),
        },
    )
