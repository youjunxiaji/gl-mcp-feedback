"""
端口管理工具模组

提供增强的端口管理功能，包括：
- 智能端口查找
- 进程检测和清理
- 端口冲突解决
"""

import socket
import time
from typing import Any

import psutil

from ...debug import debug_log


class PortManager:
    """端口管理器 - 提供增强的端口管理功能"""

    @staticmethod
    def find_process_using_port(port: int) -> dict[str, Any] | None:
        """
        查找占用指定端口的进程

        Args:
            port: 要检查的端口号

        Returns:
            Dict[str, Any]: 进程信息字典，包含 pid, name, cmdline 等
            None: 如果没有进程占用该端口
        """
        try:
            for conn in psutil.net_connections(kind="inet"):
                if (
                    conn.laddr
                    and conn.laddr.port == port
                    and conn.status == psutil.CONN_LISTEN
                ):
                    try:
                        process = psutil.Process(conn.pid)
                        return {
                            "pid": conn.pid,
                            "name": process.name(),
                            "cmdline": " ".join(process.cmdline()),
                            "create_time": process.create_time(),
                            "status": process.status(),
                        }
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # 进程可能已经结束或无权限访问
                        continue
        except Exception as e:
            debug_log(f"查找端口 {port} 占用进程时发生错误: {e}")

        return None

    @staticmethod
    def kill_process_on_port(port: int, force: bool = False) -> bool:
        """
        终止占用指定端口的进程

        Args:
            port: 要清理的端口号
            force: 是否强制终止进程

        Returns:
            bool: 是否成功终止进程
        """
        process_info = PortManager.find_process_using_port(port)
        if not process_info:
            debug_log(f"端口 {port} 没有被任何进程占用")
            return True

        try:
            pid = process_info["pid"]
            process = psutil.Process(pid)
            process_name = process_info["name"]

            debug_log(f"发现进程 {process_name} (PID: {pid}) 占用端口 {port}")

            # 检查是否是自己的进程（避免误杀）
            if "mcp-feedback-enhanced" in process_info["cmdline"].lower():
                debug_log("检测到 MCP Feedback Enhanced 相关进程，尝试优雅终止")

            if force:
                debug_log(f"强制终止进程 {process_name} (PID: {pid})")
                process.kill()
            else:
                debug_log(f"优雅终止进程 {process_name} (PID: {pid})")
                process.terminate()

            # 等待进程结束
            try:
                process.wait(timeout=5)
                debug_log(f"成功终止进程 {process_name} (PID: {pid})")
                return True
            except psutil.TimeoutExpired:
                if not force:
                    debug_log(f"优雅终止超时，强制终止进程 {process_name} (PID: {pid})")
                    process.kill()
                    process.wait(timeout=3)
                    return True
                debug_log(f"强制终止进程 {process_name} (PID: {pid}) 失败")
                return False

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            debug_log(f"无法终止进程 (PID: {process_info['pid']}): {e}")
            return False
        except Exception as e:
            debug_log(f"终止端口 {port} 占用进程时发生错误: {e}")
            return False

    @staticmethod
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
            # 首先尝试不使用 SO_REUSEADDR 来检测端口
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
                return True
        except OSError:
            # 如果绑定失败，再检查是否真的有进程在监听
            # 使用 psutil 检查是否有进程在监听该端口
            try:
                import psutil

                for conn in psutil.net_connections(kind="inet"):
                    if (
                        conn.laddr
                        and conn.laddr.port == port
                        and conn.laddr.ip in [host, "0.0.0.0", "::"]
                        and conn.status == psutil.CONN_LISTEN
                    ):
                        return False
                # 没有找到监听的进程，可能是临时占用，认为可用
                return True
            except Exception:
                # 如果 psutil 检查失败，保守地认为端口不可用
                return False

    @staticmethod
    def find_free_port_enhanced(
        preferred_port: int = 8765,
        auto_cleanup: bool = True,
        host: str = "127.0.0.1",
        max_attempts: int = 100,
    ) -> int:
        """
        增强的端口查找功能

        Args:
            preferred_port: 偏好端口号
            auto_cleanup: 是否自动清理占用端口的进程
            host: 主机地址
            max_attempts: 最大尝试次数

        Returns:
            int: 可用的端口号

        Raises:
            RuntimeError: 如果找不到可用端口
        """
        # 首先尝试偏好端口
        if PortManager.is_port_available(host, preferred_port):
            debug_log(f"偏好端口 {preferred_port} 可用")
            return preferred_port

        # 如果偏好端口被占用且启用自动清理
        if auto_cleanup:
            debug_log(f"偏好端口 {preferred_port} 被占用，尝试清理占用进程")
            process_info = PortManager.find_process_using_port(preferred_port)

            if process_info:
                debug_log(
                    f"端口 {preferred_port} 被进程 {process_info['name']} (PID: {process_info['pid']}) 占用"
                )

                # 询问用户是否清理（在实际使用中可能需要配置选项）
                if PortManager._should_cleanup_process(process_info):
                    if PortManager.kill_process_on_port(preferred_port):
                        # 等待一下让端口释放
                        time.sleep(1)
                        if PortManager.is_port_available(host, preferred_port):
                            debug_log(f"成功清理端口 {preferred_port}，现在可用")
                            return preferred_port

        # 如果偏好端口仍不可用，寻找其他端口
        debug_log(f"偏好端口 {preferred_port} 不可用，寻找其他可用端口")

        for i in range(max_attempts):
            port = preferred_port + i + 1
            if PortManager.is_port_available(host, port):
                debug_log(f"找到可用端口: {port}")
                return port

        # 如果向上查找失败，尝试向下查找
        for i in range(1, min(preferred_port - 1024, max_attempts)):
            port = preferred_port - i
            if port < 1024:  # 避免使用系统保留端口
                break
            if PortManager.is_port_available(host, port):
                debug_log(f"找到可用端口: {port}")
                return port

        raise RuntimeError(
            f"无法在 {preferred_port}±{max_attempts} 范围内找到可用端口。"
            f"请检查是否有过多进程占用端口，或手动指定其他端口。"
        )

    @staticmethod
    def _should_cleanup_process(process_info: dict[str, Any]) -> bool:
        """
        判断是否应该清理指定进程

        Args:
            process_info: 进程信息字典

        Returns:
            bool: 是否应该清理该进程
        """
        # 检查是否是 MCP Feedback Enhanced 相关进程
        cmdline = process_info.get("cmdline", "").lower()
        process_name = process_info.get("name", "").lower()

        # 如果是自己的进程，允许清理
        if any(
            keyword in cmdline
            for keyword in ["mcp-feedback-enhanced", "mcp_feedback_enhanced"]
        ):
            return True

        # 如果是 Python 进程且命令行包含相关关键字
        if "python" in process_name and any(
            keyword in cmdline for keyword in ["uvicorn", "fastapi"]
        ):
            return True

        # 其他情况下，为了安全起见，不自动清理
        debug_log(
            f"进程 {process_info['name']} (PID: {process_info['pid']}) 不是 MCP 相关进程，跳过自动清理"
        )
        return False

    @staticmethod
    def get_port_status(port: int, host: str = "127.0.0.1") -> dict[str, Any]:
        """
        获取端口状态信息

        Args:
            port: 端口号
            host: 主机地址

        Returns:
            Dict[str, Any]: 端口状态信息
        """
        status = {
            "port": port,
            "host": host,
            "available": False,
            "process": None,
            "error": None,
        }

        try:
            # 检查端口是否可用
            status["available"] = PortManager.is_port_available(host, port)

            # 如果不可用，查找占用进程
            if not status["available"]:
                status["process"] = PortManager.find_process_using_port(port)

        except Exception as e:
            status["error"] = str(e)
            debug_log(f"获取端口 {port} 状态时发生错误: {e}")

        return status

    @staticmethod
    def list_listening_ports(
        start_port: int = 8000, end_port: int = 9000
    ) -> list[dict[str, Any]]:
        """
        列出指定范围内正在监听的端口

        Args:
            start_port: 起始端口
            end_port: 结束端口

        Returns:
            List[Dict[str, Any]]: 监听端口列表
        """
        listening_ports = []

        try:
            for conn in psutil.net_connections(kind="inet"):
                if (
                    conn.laddr
                    and conn.status == psutil.CONN_LISTEN
                    and start_port <= conn.laddr.port <= end_port
                ):
                    try:
                        process = psutil.Process(conn.pid)
                        port_info = {
                            "port": conn.laddr.port,
                            "host": conn.laddr.ip,
                            "pid": conn.pid,
                            "process_name": process.name(),
                            "cmdline": " ".join(process.cmdline()),
                        }
                        listening_ports.append(port_info)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

        except Exception as e:
            debug_log(f"列出监听端口时发生错误: {e}")

        return listening_ports
