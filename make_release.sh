#!/bin/bash
# 打开源码发布包 + 模型权重包（用于挂 GitHub Release）
# 用法：终端 ./make_release.sh [版本号，如 v1.0.0]
#
# 产物（dist/ 目录下）：
#   shengwen-<版本>-source.tar.gz      纯源码（用户 clone 亦可，此包为免 git 版）
#   shengwen-models-<版本>.tar.gz      模型权重 1.7GB（解压到 transcribe-app/models/）

set -e
cd "$(dirname "$0")"

VER=${1:-v1.0.0}
DIST=dist
mkdir -p "$DIST"

echo "=== 打开源码包 ==="
# 用 git archive（只含入库文件，天然排除 runs/models/venv/隐私数据）
git archive --format=tar.gz --prefix="shengwen-$VER/" -o "$DIST/shengwen-$VER-source.tar.gz" HEAD
echo "✓ $DIST/shengwen-$VER-source.tar.gz"

echo ""
echo "=== 打包模型权重 ==="
if [ ! -d "models/hf_cache" ]; then
    echo "❌ models/hf_cache 不存在，请先完成安装"
    exit 1
fi
# 复制成固定顶层结构（解压后 models/hf_cache/...）
STAGE=$(mktemp -d)
mkdir -p "$STAGE/shengwen-models-$VER"
cp -R models/hf_cache "$STAGE/shengwen-models-$VER/models/hf_cache" 2>/dev/null || {
    mkdir -p "$STAGE/shengwen-models-$VER/models"
    cp -R models/hf_cache "$STAGE/shengwen-models-$VER/models/hf_cache"
}
tar -czf "$DIST/shengwen-models-$VER.tar.gz" -C "$STAGE" "shengwen-models-$VER"
rm -rf "$STAGE"
echo "✓ $DIST/shengwen-models-$VER.tar.gz"

echo ""
echo "=== 产物清单 ==="
ls -lh "$DIST/"
echo
echo "上传方式（二选一）："
echo "  A. GitHub 网页：仓库 Releases → Draft a new release → 拖入两个文件"
echo "  B. 命令行（需 gh 登录）：gh release create $VER dist/*.tar.gz --title \"$VER\" --notes \"首个版本\""
