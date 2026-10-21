#!/usr/bin/env python3
"""
gl-mcp-feedback
===============

互动式用户反馈 MCP 服务器，提供 AI 辅助开发中的反馈收集功能。

作者: Fábio Ferreira
增强功能: Web UI 支援、图片上传、现代化界面设计

特色：
- Web UI 界面支援
- 智慧环境检测
- 图片上传支援
- 现代化深色主题
- 重构的模组化架构
"""

__version__ = "3.0.2"
__author__ = "鼬君夏纪"

import os

from .server import main as run_server

# 导入新的 Web UI 模组
from .web import WebUIManager, get_web_ui_manager, launch_web_feedback_ui, stop_web_ui


# 保持向后兼容性
feedback_ui = None

# 主要导出界面
__all__ = [
    "WebUIManager",
    "__author__",
    "__version__",
    "feedback_ui",
    "get_web_ui_manager",
    "launch_web_feedback_ui",
    "run_server",
    "stop_web_ui",
]


def main():
    """主要入口点，用于 uvx 执行"""
    from .__main__ import main as cli_main

    return cli_main()
