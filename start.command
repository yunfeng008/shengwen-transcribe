#!/bin/bash
# 「声文」启动脚本（Mac Finder 双击运行）
# 用法：双击 start.command，或终端 ./start.command

cd "$(dirname "$0")"

# 找 venv 的 python
VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  osascript -e 'display dialog "找不到 .venv/bin/python。请先双击 install.sh 初始化环境。" buttons {"好的"} default button 1 with icon caution'
  exit 1
fi

# 端口
PORT=${PORT:-8765}

# 启动 Web 服务，自动打开浏览器
exec "$VENV_PY" -u server/app.py --host 127.0.0.1 --port "$PORT" --open