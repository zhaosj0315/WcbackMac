#!/bin/bash
# Mac 微信数据导出工具 - 快速启动脚本

echo "=========================================="
echo "Mac 微信数据导出工具 v3.0"
echo "功能完成度: 98%"
echo "=========================================="
echo ""

# 检查依赖
echo "检查依赖..."
python3 -c "import PyQt6" 2>/dev/null || {
    echo "安装 PyQt6..."
    pip3 install -q PyQt6
}

python3 -c "import dbutils" 2>/dev/null || {
    echo "安装 dbutils..."
    pip3 install -q dbutils
}

echo ""
echo "启动 GUI..."
echo ""

cd "$(dirname "$0")"
python3 app/gui/main_window.py
