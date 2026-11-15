# PyInstaller spec for Markdown Companion V2.  Build:  pyinstaller --noconfirm markdown_companion.spec
# (run on Windows to produce a Windows .exe; PyInstaller cannot cross-compile)
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "icons" / "app.ico"

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=["watchdog.observers.read_directory_changes"],   # Windows watcher backend
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MarkdownCompanionV2",
    console=False,                       # tray app: no console window
    icon=str(ICON) if sys.platform == "win32" and ICON.exists() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MarkdownCompanionV2", upx=False)
