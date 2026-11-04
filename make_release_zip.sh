#!/bin/bash
# 打 zip 发行包：源码 + 引擎代码（vendor）+ 双击安装入口
# 产出 dist/shengwen-transcribe-v<版本>.zip
# 用户流程：下载 zip → Finder 双击解压 → 双击 install.command → 双击 start.command

set -e
cd "$(dirname "$0")"

VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "v1.0.0")
OUT="dist/shengwen-transcribe-${VERSION}.zip"
STAGE=$(mktemp -d /tmp/shengwen-zip.XXXXXX)
SRC_NAME="shengwen-transcribe"

echo "==> 打包版本 ${VERSION}"

# 1. git archive 导出仓库干净源码（--prefix 带顶层目录）
git archive --prefix="${SRC_NAME}/" HEAD | tar -x -C "$STAGE"

# 2. 补充 vendor 引擎代码（gitignore 不在 archive 内；上游 Apache-2.0 允许随包分发）
rsync -a --exclude '.git' --exclude '__pycache__' \
    vendor/MOSS-Transcribe-Diarize "$STAGE/$SRC_NAME/vendor/"

# 3. 打 zip（Finder 双击可解压）
mkdir -p dist
rm -f "$OUT"
(cd "$STAGE" && zip -qr "$OLDPWD/$OUT" "$SRC_NAME" \
    -x "*/__pycache__/*" -x "*.DS_Store")

# 4. 清理与汇报
rm -rf "$STAGE"
SIZE=$(du -h "$OUT" | cut -f1 | tr -d ' ')
echo "==> 完成: $OUT ($SIZE)"
echo "    内含: 源码 + vendor 引擎代码 + install.command 双击安装"
