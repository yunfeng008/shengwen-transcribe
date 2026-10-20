#!/bin/bash
# 「声文」首次安装
# 用法：终端 ./install.sh
# 作用：
#   1. 检查 uv（没有就装）
#   2. 建 Python 3.12 venv（如不存在）
#   3. 装依赖
#   4. 下载模型（约 1.7GB，需要 VPN/代理访问 huggingface.co）
#   5. 提示用户打开 start.command

set -e
cd "$(dirname "$0")"

echo "==========================================="
echo "  声文 · 一键安装"
echo "==========================================="
echo ""

# 0. 检查 ffmpeg（转码和取时长都依赖它）
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "❌ 未找到 ffmpeg/ffprobe，请先安装："
    echo "   brew install ffmpeg"
    echo "然后重新运行本脚本。"
    exit 1
fi
echo "✓ ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

# 1. 检查 uv
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

# 2. venv
if [ ! -x ".venv/bin/python" ]; then
    echo "[2/4] 创建 Python 3.12 venv..."
    $UV venv --python 3.12 .venv
fi
echo "✓ venv: $(.venv/bin/python --version)"

# 3. 依赖
echo "[3/4] 安装依赖（1-2 分钟）..."
$UV pip install -p .venv/bin/python -q \
    "vendor/MOSS-Transcribe-Diarize/[torch-runtime]" \
    python-docx fastapi uvicorn python-multipart 2>&1 | tail -3
echo "✓ 依赖已装"

# 4. 模型
if [ ! -d "models/hf_cache/hub" ]; then
    echo "[4/4] 下载模型（约 1.7GB，需要访问 huggingface.co）..."
    HF_HOME=$(pwd)/models/hf_cache .venv/bin/python -c "
from huggingface_hub import snapshot_download
p = snapshot_download('OpenMOSS-Team/MOSS-Transcribe-Diarize')
print(f'模型已下载到: {p}')
"
else
    echo "✓ 模型已存在，跳过"
fi

echo ""
echo "==========================================="
echo "  安装完成！"
echo "==========================================="
echo ""
echo "  下一步：双击 start.command 启动"
echo ""
echo "  卸载：删除整个 transcribe-app 目录即可"
echo "==========================================="