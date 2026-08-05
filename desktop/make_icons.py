#!/usr/bin/env python3
"""从应用图标 PNG 生成打包所需的 icon.ico（Windows）和 icon.icns（macOS）。

用法：python desktop/make_icons.py   （产物输出到 desktop/icons/）
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "fpk" / "canon-autosync" / "ICON_256.PNG"
OUT = Path(__file__).resolve().parent / "icons"


def make_ico(src: Path, dst: Path) -> None:
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(dst, format="ICO", sizes=sizes)
    print(f"ico -> {dst}")


def make_icns(src: Path, dst: Path) -> None:
    if not shutil.which("iconutil"):
        print("iconutil not available, skip icns (macOS only)")
        return
    from PIL import Image

    iconset = OUT / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGBA")
    # iconset 规格：icon_16x16.png ... icon_512x512.png + 对应 @2x（双倍像素）
    for size in (16, 32, 128, 256, 512):
        img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
    for size in (16, 32, 128, 256, 512):
        px = size * 2
        img.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dst)], check=True)
    shutil.rmtree(iconset)
    print(f"icns -> {dst}")


def main() -> None:
    # Windows 控制台默认代码页（cp1252）无法输出中文，统一为 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not SRC.exists():
        print(f"source icon not found: {SRC}", file=sys.stderr)
        sys.exit(1)
    OUT.mkdir(parents=True, exist_ok=True)
    make_ico(SRC, OUT / "icon.ico")
    make_icns(SRC, OUT / "icon.icns")


if __name__ == "__main__":
    main()
