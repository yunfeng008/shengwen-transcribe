#!/bin/bash
# 「声文 ShengWen」双击安装入口（Mac Finder 双击即可）
# 内部调用 install.sh，完成后等待按键再关窗，避免闪退看不到结果

cd "$(dirname "$0")"
bash install.sh
echo ""
read -n 1 -s -r -p "按任意键关闭窗口…"
