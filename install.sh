#!/bin/bash
# 「声文 ShengWen」首次安装
# 用法：终端 ./install.sh
# 流程：ffmpeg → uv → venv → 依赖 → 模型（官方源失败自动切国内镜像）

set -e
cd "$(dirname "$0")"

REPO_VENDOR="https://github.com/OpenMOSS/MOSS-Transcribe-Diarize.git"
MODEL_REPO="OpenMOSS-Team/MOSS-Transcribe-Diarize"

echo "==========================================="
echo "  声文 ShengWen · 一键安装"
echo "==========================================="
echo ""

# 0. 前置检查：ffmpeg + git
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "❌ 未找到 ffmpeg/ffprobe，请先安装后重跑本脚本："
    echo "   brew install ffmpeg"
    exit 1
fi
echo "✓ ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

if ! command -v git >/dev/null 2>&1; then
    echo "❌ 未找到 git（xcode-select --install 可安装），安装后重跑本脚本。"
    exit 1
fi

# 1. uv（Python 环境管理器）
if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "[1/4] 安装 uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
echo "✓ uv: $($UV --version)"

# 2. 上游推理代码（vendor）
if [ ! -d "vendor/MOSS-Transcribe-Diarize" ]; then
    echo "[2/4] 获取转写引擎代码..."
    mkdir -p vendor
    if ! git clone --depth 1 "$REPO_VENDOR" vendor/MOSS-Transcribe-Diarize 2>/dev/null; then
        echo "   官方源失败，尝试镜像..."
        git clone --depth 1 "https://gitclone.com/github.com/OpenMOSS/MOSS-Transcribe-Diarize.git" vendor/MOSS-Transcribe-Diarize
    fi
    rm -rf vendor/MOSS-Transcribe-Diarize/.git
fi
echo "✓ 引擎代码就绪"

# 3. venv + 依赖
if [ ! -x ".venv/bin/python" ]; then
    echo "[3/4] 创建 Python 3.12 venv..."
    $UV venv --python 3.12 .venv
fi
$UV pip install -p .venv/bin/python -q \
    "vendor/MOSS-Transcribe-Diarize/[torch-runtime]" \
    python-docx fastapi uvicorn python-multipart huggingface_hub 2>&1 | tail -2
echo "✓ 依赖已装（$($UV pip list -p .venv/bin/python 2>/dev/null | grep -vcE 'Package|----') 个包）"

# 4. 模型（官方源失败自动切国内镜像 hf-mirror.com）
if [ ! -d "models/hf_cache/hub" ]; then
    echo "[4/4] 下载模型（约 1.7GB）..."
    download_models() {
        HF_HOME=$(pwd)/models/hf_cache "$1" - << PYEOF
import os
from huggingface_hub import snapshot_download
p = snapshot_download('$MODEL_REPO')
print(f'模型就绪: {p}')
PYEOF
    }
    # 先走官方源，失败自动切 hf-mirror（国内免代理）
    if ! download_models .venv/bin/python; then
        echo "   官方源不可达，切换国内镜像 hf-mirror.com ..."
        HF_ENDPOINT=https://hf-mirror.com HF_HOME=$(pwd)/models/hf_cache \
            .venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('$MODEL_REPO')
print('模型就绪')"
    fi
else
    echo "✓ 模型已存在，跳过"
fi
echo "✓ 模型就绪"

echo ""
echo "==========================================="
echo "  安装完成！"
echo "==========================================="
echo ""
echo "  下一步：双击 start.command 启动"
echo "  卸载：删除整个 transcribe-app 目录即可"
echo "==========================================="
