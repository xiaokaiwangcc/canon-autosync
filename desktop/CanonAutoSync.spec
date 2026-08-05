# -*- mode: python ; coding: utf-8 -*-
"""canon-autosync 桌面版打包配置（PyInstaller）。

用法（需先构建前端生成 frontend/dist）：
    python desktop/make_icons.py
    pyinstaller desktop/CanonAutoSync.spec --noconfirm
产物：
    macOS:  dist/CanonAutoSync.app
    Windows: dist/CanonAutoSync/  （目录内 CanonAutoSync.exe）
"""
import platform
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
IS_WIN = platform.system() == "Windows"

# 前端构建产物 → 后端静态托管目录（main.py 的 STATIC_DIR = app/static）
datas = [(str(ROOT / "frontend" / "dist"), "app/static")]

# entry.py 中 run_server 为函数级导入，PyInstaller 静态分析不可见，需显式声明
hiddenimports = ["app.main", "app.ccapi", "app.config", "app.state", "app.sync"]
if IS_WIN:
    hiddenimports += ["clr"]  # pywebview Windows 后端基于 pythonnet

a = Analysis(
    [str(ROOT / "desktop" / "entry.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

if IS_WIN:
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name="CanonAutoSync",
        debug=False,
        console=False,
        icon=str(ROOT / "desktop" / "icons" / "icon.ico"),
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name="CanonAutoSync",
        console=False,
    )

coll = COLLECT(exe, a.binaries, a.datas, name="CanonAutoSync")

if not IS_WIN:
    app = BUNDLE(
        coll,
        name="CanonAutoSync.app",
        icon=str(ROOT / "desktop" / "icons" / "icon.icns"),
        bundle_identifier="com.xiaokaiwang.canon-autosync",
    )
