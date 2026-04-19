#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mac 微信数据导出工具 - 主程序入口"""

import sys
import os

# 打包后路径修正
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    # 将工作目录切换到可执行文件所在目录，确保 data/ 等相对路径可写
    os.chdir(os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

from app.gui.main_window import main

if __name__ == '__main__':
    main()
