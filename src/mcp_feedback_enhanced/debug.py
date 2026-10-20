#!/usr/bin/env python3
"""
统一调试日志模组
================

提供统一的调试日志功能，确保调试输出不会干扰 MCP 通信。
所有调试输出都会发送到 stderr，并且只在调试模式启用时才输出。

使用方法：
```python
from .debug import debug_log

debug_log("这是一条调试信息")
```

环境变数控制：
- MCP_DEBUG=true/1/yes/on: 启用调试模式
- MCP_DEBUG=false/0/no/off: 关闭调试模式（默认）

作者: Minidoracat
"""

import os
import sys
from typing import Any


def debug_log(message: Any, prefix: str = "DEBUG") -> None:
    """
    输出调试消息到标准错误，避免污染标准输出

    Args:
        message: 要输出的调试信息
        prefix: 调试信息的前缀标识，默认为 "DEBUG"
    """
    # 只在启用调试模式时才输出，避免干扰 MCP 通信
    if os.getenv("MCP_DEBUG", "").lower() not in ("true", "1", "yes", "on"):
        return

    try:
        # 确保消息是字符串类型
        if not isinstance(message, str):
            message = str(message)

        # 安全地输出到 stderr，处理编码问题
        try:
            print(f"[{prefix}] {message}", file=sys.stderr, flush=True)
        except UnicodeEncodeError:
            # 如果遇到编码问题，使用 ASCII 安全模式
            safe_message = message.encode("ascii", errors="replace").decode("ascii")
            print(f"[{prefix}] {safe_message}", file=sys.stderr, flush=True)
    except Exception:
        # 最后的备用方案：静默失败，不影响主程序
        pass


def server_debug_log(message: Any) -> None:
    """服务器模组专用的调试日志"""
    debug_log(message, "SERVER")


def web_debug_log(message: Any) -> None:
    """Web UI 模组专用的调试日志"""
    debug_log(message, "WEB")


def is_debug_enabled() -> bool:
    """检查是否启用了调试模式"""
    return os.getenv("MCP_DEBUG", "").lower() in ("true", "1", "yes", "on")


def set_debug_mode(enabled: bool) -> None:
    """设置调试模式（用于测试）"""
    os.environ["MCP_DEBUG"] = "true" if enabled else "false"
