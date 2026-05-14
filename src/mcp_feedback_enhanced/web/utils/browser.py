#!/usr/bin/env python3
"""
浏览器工具函数
==============

提供浏览器相关的工具函数，包含 WSL 环境的特殊处理。
"""

import os
import subprocess
import webbrowser
from collections.abc import Callable

# 导入调试功能
from ...debug import server_debug_log as debug_log


def is_wsl_environment() -> bool:
    """
    检测是否在 WSL 环境中运行

    Returns:
        bool: True 表示 WSL 环境，False 表示其他环境
    """
    try:
        # 检查 /proc/version 文件是否包含 WSL 标识
        if os.path.exists("/proc/version"):
            with open("/proc/version") as f:
                version_info = f.read().lower()
                if "microsoft" in version_info or "wsl" in version_info:
                    return True

        # 检查 WSL 相关环境变数
        wsl_env_vars = ["WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV"]
        for env_var in wsl_env_vars:
            if os.getenv(env_var):
                return True

        # 检查是否存在 WSL 特有的路径
        wsl_paths = ["/mnt/c", "/mnt/d", "/proc/sys/fs/binfmt_misc/WSLInterop"]
        for path in wsl_paths:
            if os.path.exists(path):
                return True

    except Exception:
        pass

    return False


def open_browser_in_wsl(url: str) -> None:
    """
    在 WSL 环境中开启 Windows 浏览器

    Args:
        url: 要开启的 URL
    """
    try:
        # 尝试使用 cmd.exe 启动浏览器
        cmd = ["cmd.exe", "/c", "start", "", url]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

        if result.returncode == 0:
            debug_log(f"成功使用 cmd.exe 启动浏览器: {url}")
            return
        debug_log(
            f"cmd.exe 启动失败，返回码: {result.returncode}, 错误: {result.stderr}"
        )

    except Exception as e:
        debug_log(f"使用 cmd.exe 启动浏览器失败: {e}")

    try:
        # 尝试使用 powershell.exe 启动浏览器
        cmd = ["powershell.exe", "-NoProfile", "-Command", "Start-Process", url]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

        if result.returncode == 0:
            debug_log(f"成功使用 powershell.exe 启动浏览器: {url}")
            return
        debug_log(
            f"powershell.exe 启动失败，返回码: {result.returncode}, 错误: {result.stderr}"
        )

    except Exception as e:
        debug_log(f"使用 powershell.exe 启动浏览器失败: {e}")

    try:
        # 最后尝试使用 wslview（如果安装了 wslu 包）
        cmd = ["wslview", url]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

        if result.returncode == 0:
            debug_log(f"成功使用 wslview 启动浏览器: {url}")
            return
        debug_log(
            f"wslview 启动失败，返回码: {result.returncode}, 错误: {result.stderr}"
        )

    except Exception as e:
        debug_log(f"使用 wslview 启动浏览器失败: {e}")

    # 如果所有方法都失败，抛出异常
    raise Exception("无法在 WSL 环境中启动 Windows 浏览器")


def smart_browser_open(url: str) -> None:
    """
    智能浏览器开启函数，根据环境选择最佳方式

    Args:
        url: 要开启的 URL
    """
    if is_wsl_environment():
        debug_log("检测到 WSL 环境，使用 WSL 专用浏览器启动方式")
        open_browser_in_wsl(url)
    else:
        debug_log("使用标准浏览器启动方式")
        webbrowser.open(url)


def get_browser_opener() -> Callable[[str], None]:
    """
    获取浏览器开启函数

    Returns:
        Callable: 浏览器开启函数
    """
    return smart_browser_open
