# -*- mode: python ; coding: utf-8 -*-
"""canon-autosync 桌面版打包配置（PyInstaller）。

用法（需先构建前端生成 frontend/dist）：
    python desktop/make_icons.py
    pyinstaller desktop/CanonAutoSync.spec --noconfirm
产物：
    macOS:  dist/CanonAutoSync.app
    Windows: dist/CanonAutoSync.exe（单文件）
"""
import platform
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
IS_WIN = platform.system() == "Windows"

# 前端构建产物 → 后端静态托管目录（main.py 的 STATIC_DIR = app/static）
datas = [(str(ROOT / "frontend" / "dist"), "app/static")]

# 视频缩略图依赖 ffmpeg：Windows 构建优先收集 CI 裁剪编译的最小化二进制
# （desktop/ffmpeg/ffmpeg.exe，由 build-desktop.yml 编译并下载）；不存在时（macOS/本地构建）
# 收集 imageio-ffmpeg 自带完整静态二进制，运行时 get_ffmpeg_exe() 在打包环境下也能找到
_trim_ffmpeg = ROOT / "desktop" / "ffmpeg" / "ffmpeg.exe"
if _trim_ffmpeg.is_file():
    datas += [(str(_trim_ffmpeg), "ffmpeg")]
else:
    try:
        import imageio_ffmpeg

        _ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        _pkg = Path(imageio_ffmpeg.__file__).resolve().parent
        if _ffmpeg.exists() and _pkg in _ffmpeg.resolve().parents:
            datas += [(str(_ffmpeg), "imageio_ffmpeg/binaries")]
    except Exception:
        pass

# entry.py 中 run_server 为函数级导入，PyInstaller 静态分析不可见，需显式声明
hiddenimports = ["app.main", "app.ccapi", "app.config", "app.state", "app.sync", "imageio_ffmpeg", "rawpy"]
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
    # Windows：单文件 exe（pythonnet 的 Python.Runtime.dll 由 hook 随包收集）
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="CanonAutoSync",
        debug=False,
        strip=False,
        upx=False,
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
    app = BUNDLE(
        coll,
        name="CanonAutoSync.app",
        icon=str(ROOT / "desktop" / "icons" / "icon.icns"),
        bundle_identifier="com.xiaokaiwang.canon-autosync",
    )
