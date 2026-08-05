#!/bin/bash
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PKG="$ROOT/fpk/canon-autosync"

echo "==> 构建前端"
(cd "$ROOT/frontend" && npm install --silent && npm run build)

echo "==> 组装应用文件"
rm -rf "$PKG/app/backend"
mkdir -p "$PKG/app/backend"
cp -R "$ROOT/backend/app" "$PKG/app/backend/app"
cp "$ROOT/backend/requirements.txt" "$PKG/app/backend/"
rm -rf "$PKG/app/backend/app/static" "$PKG/app/backend/app/__pycache__" "$PKG/app/backend/data"
cp -R "$ROOT/frontend/dist" "$PKG/app/backend/app/static"
find "$PKG" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> 版本号 +1"
MANIFEST="$PKG/manifest"
OLD_VER=$(grep '^version=' "$MANIFEST" | sed 's/version="\(.*\)"/\1/')
if [ -n "$FPK_VERSION" ]; then
  # 环境变量指定版本（如 tag 构建 v1.0.1 -> FPK_VERSION=1.0.1）
  echo "$FPK_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
    || { echo "错误: FPK_VERSION 格式须为 x.y.z: $FPK_VERSION"; exit 1; }
  NEW_VER="$FPK_VERSION"
else
  MAJOR=$(echo "$OLD_VER" | cut -d. -f1)
  MINOR=$(echo "$OLD_VER" | cut -d. -f2)
  PATCH=$(echo "$OLD_VER" | cut -d. -f3)
  NEW_VER="$MAJOR.$MINOR.$((PATCH + 1))"
fi
sed -i.bak "s/^version=\"$OLD_VER\"/version=\"$NEW_VER\"/" "$MANIFEST" && rm -f "$MANIFEST.bak"
echo "    $OLD_VER -> $NEW_VER"

echo "==> 写入后端版本号"
CFG="$PKG/app/backend/app/config.py"
sed -i.bak "s/APP_VERSION = os.environ.get(\"APP_VERSION\", \".*\")/APP_VERSION = os.environ.get(\"APP_VERSION\", \"$NEW_VER\")/" "$CFG" && rm -f "$CFG.bak"
grep APP_VERSION "$CFG" | head -1

echo "==> 打包 fpk"
if command -v fnpack >/dev/null 2>&1; then
  (cd "$PKG" && fnpack build)
else
  echo "未找到 fnpack，请先安装：https://developer.fnnas.com/docs/cli/fnpack/"
  echo "安装后执行: cd $PKG && fnpack build"
fi
