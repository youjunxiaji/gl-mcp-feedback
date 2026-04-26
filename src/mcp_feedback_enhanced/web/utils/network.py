#!/usr/bin/env python3
"""
网络工具函数
============

提供网络相关的工具函数，如端口检测等。
"""

import socket


def find_free_port(
    start_port: int = 8765, max_attempts: int = 100, preferred_port: int = 8765
) -> int:
    """
    寻找可用的端口，优先使用偏好端口

    Args:
        start_port: 起始端口号
        max_attempts: 最大尝试次数
        preferred_port: 偏好端口号（用于保持设定持久性）

    Returns:
        int: 可用的端口号

    Raises:
        RuntimeError: 如果找不到可用端口
    """
    # 首先尝试偏好端口（通常是 8765）
    if is_port_available("127.0.0.1", preferred_port):
        return preferred_port

    # 如果偏好端口不可用，尝试其他端口
    for i in range(max_attempts):
        port = start_port + i
        if port == preferred_port:  # 跳过已经尝试过的偏好端口
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue

    raise RuntimeError(
        f"无法在 {start_port}-{start_port + max_attempts - 1} 范围内找到可用端口"
    )


def is_port_available(host: str, port: int) -> bool:
    """
    检查端口是否可用

    Args:
        host: 主机地址
        port: 端口号

    Returns:
        bool: 端口是否可用
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False
