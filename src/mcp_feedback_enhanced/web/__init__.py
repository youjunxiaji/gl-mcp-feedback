#!/usr/bin/env python3
"""
MCP Feedback Enhanced Web UI 模组

基于 FastAPI 和 WebSocket 的 Web 用户界面，提供丰富的互动反馈功能。
支援文字输入、图片上传、命令执行等功能，设计采用现代化的 Web UI 架构。

主要功能：
- FastAPI Web 应用程序
- WebSocket 实时通讯
- 多语言国际化支援
- 图片上传与预览
- 命令执行与结果展示
- 响应式设计
- 本地和远端环境适配
"""

from .main import WebUIManager, get_web_ui_manager, launch_web_feedback_ui, stop_web_ui


__all__ = [
    "WebUIManager",
    "get_web_ui_manager",
    "launch_web_feedback_ui",
    "stop_web_ui",
]
