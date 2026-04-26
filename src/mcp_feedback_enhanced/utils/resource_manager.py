"""
统一资源管理器
==============

提供统一的资源管理功能，包括：
- 临时文件和目录管理
- 进程生命周期追踪
- 自动资源清理
- 资源使用监控
"""

import atexit
import os
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from typing import Any

from ..debug import debug_log
from .error_handler import ErrorHandler, ErrorType


class ResourceType:
    """资源类型常量"""

    TEMP_FILE = "temp_file"
    TEMP_DIR = "temp_dir"
    PROCESS = "process"
    FILE_HANDLE = "file_handle"


class ResourceManager:
    """统一资源管理器 - 提供完整的资源生命周期管理"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化资源管理器"""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True

        # 资源追踪集合
        self.temp_files: set[str] = set()
        self.temp_dirs: set[str] = set()
        self.processes: dict[int, dict[str, Any]] = {}
        self.file_handles: set[Any] = set()

        # 资源统计
        self.stats: dict[str, int | float] = {
            "temp_files_created": 0,
            "temp_dirs_created": 0,
            "processes_registered": 0,
            "cleanup_runs": 0,
            "last_cleanup": 0.0,  # 使用 0.0 而非 None，避免类型混淆
        }

        # 配置
        self.auto_cleanup_enabled = True
        self.cleanup_interval = 300  # 5分钟
        self.temp_file_max_age = 3600  # 1小时

        # 清理线程
        self._cleanup_thread: threading.Thread | None = None
        self._stop_cleanup = threading.Event()

        # 注册退出清理
        atexit.register(self.cleanup_all)

        # 启动自动清理
        self._start_auto_cleanup()

        # 集成内存监控
        self._setup_memory_monitoring()

        debug_log("ResourceManager 初始化完成")

    def _setup_memory_monitoring(self):
        """设置内存监控集成"""
        try:
            # 延迟导入避免循环依赖
            from .memory_monitor import get_memory_monitor

            self.memory_monitor = get_memory_monitor()

            # 注册清理回调
            self.memory_monitor.add_cleanup_callback(self._memory_triggered_cleanup)

            # 启动内存监控
            if self.memory_monitor.start_monitoring():
                debug_log("内存监控已集成到资源管理器")
            else:
                debug_log("内存监控启动失败")

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "设置内存监控"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"设置内存监控失败 [错误ID: {error_id}]: {e}")

    def _memory_triggered_cleanup(self, force: bool = False):
        """内存监控触发的清理操作"""
        debug_log(f"内存监控触发清理操作 (force={force})")

        try:
            # 清理临时文件
            cleaned_files = self.cleanup_temp_files()

            # 清理临时目录
            cleaned_dirs = self.cleanup_temp_dirs()

            # 清理文件句柄
            cleaned_handles = self.cleanup_file_handles()

            # 如果是强制清理，也清理进程
            cleaned_processes = 0
            if force:
                cleaned_processes = self.cleanup_processes(force=True)

            debug_log(
                f"内存触发清理完成: 文件={cleaned_files}, 目录={cleaned_dirs}, "
                f"句柄={cleaned_handles}, 进程={cleaned_processes}"
            )

            # 更新统计
            self.stats["cleanup_runs"] += 1
            self.stats["last_cleanup"] = time.time()

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "内存触发清理", "force": force},
                error_type=ErrorType.SYSTEM,
            )
            debug_log(f"内存触发清理失败 [错误ID: {error_id}]: {e}")

    def create_temp_file(
        self,
        suffix: str = "",
        prefix: str = "mcp_",
        dir: str | None = None,
        text: bool = True,
    ) -> str:
        """
        创建临时文件并追踪

        Args:
            suffix: 文件后缀
            prefix: 文件前缀
            dir: 临时目录，None 使用系统默认
            text: 是否为文本模式

        Returns:
            str: 临时文件路径
        """
        try:
            # 创建临时文件
            fd, temp_path = tempfile.mkstemp(
                suffix=suffix, prefix=prefix, dir=dir, text=text
            )
            os.close(fd)  # 关闭文件描述符

            # 追踪文件
            self.temp_files.add(temp_path)
            self.stats["temp_files_created"] += 1

            debug_log(f"创建临时文件: {temp_path}")
            return temp_path

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={
                    "operation": "创建临时文件",
                    "suffix": suffix,
                    "prefix": prefix,
                },
                error_type=ErrorType.FILE_IO,
            )
            debug_log(f"创建临时文件失败 [错误ID: {error_id}]: {e}")
            raise

    def create_temp_dir(
        self, suffix: str = "", prefix: str = "mcp_", dir: str | None = None
    ) -> str:
        """
        创建临时目录并追踪

        Args:
            suffix: 目录后缀
            prefix: 目录前缀
            dir: 父目录，None 使用系统默认

        Returns:
            str: 临时目录路径
        """
        try:
            # 创建临时目录
            temp_dir = tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)

            # 追踪目录
            self.temp_dirs.add(temp_dir)
            self.stats["temp_dirs_created"] += 1

            debug_log(f"创建临时目录: {temp_dir}")
            return temp_dir

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={
                    "operation": "创建临时目录",
                    "suffix": suffix,
                    "prefix": prefix,
                },
                error_type=ErrorType.FILE_IO,
            )
            debug_log(f"创建临时目录失败 [错误ID: {error_id}]: {e}")
            raise

    def register_process(
        self,
        process: subprocess.Popen | int,
        description: str = "",
        auto_cleanup: bool = True,
    ) -> int:
        """
        注册进程追踪

        Args:
            process: 进程对象或 PID
            description: 进程描述
            auto_cleanup: 是否自动清理

        Returns:
            int: 进程 PID
        """
        try:
            if isinstance(process, subprocess.Popen):
                pid = process.pid
                process_obj = process
            else:
                pid = process
                process_obj = None

            # 注册进程
            self.processes[pid] = {
                "process": process_obj,
                "description": description,
                "auto_cleanup": auto_cleanup,
                "registered_at": time.time(),
                "last_check": time.time(),
            }

            self.stats["processes_registered"] += 1

            debug_log(f"注册进程追踪: PID {pid} - {description}")
            return pid

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "注册进程", "description": description},
                error_type=ErrorType.PROCESS,
            )
            debug_log(f"注册进程失败 [错误ID: {error_id}]: {e}")
            raise

    def register_file_handle(self, file_handle: Any) -> None:
        """
        注册文件句柄追踪

        Args:
            file_handle: 文件句柄对象
        """
        try:
            # 使用弱引用避免循环引用
            self.file_handles.add(weakref.ref(file_handle))
            debug_log(f"注册文件句柄: {type(file_handle).__name__}")

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "注册文件句柄"}, error_type=ErrorType.FILE_IO
            )
            debug_log(f"注册文件句柄失败 [错误ID: {error_id}]: {e}")

    def unregister_temp_file(self, file_path: str) -> bool:
        """
        取消临时文件追踪

        Args:
            file_path: 文件路径

        Returns:
            bool: 是否成功取消追踪
        """
        try:
            if file_path in self.temp_files:
                self.temp_files.remove(file_path)
                debug_log(f"取消临时文件追踪: {file_path}")
                return True
            return False

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "取消文件追踪", "file_path": file_path},
                error_type=ErrorType.FILE_IO,
            )
            debug_log(f"取消文件追踪失败 [错误ID: {error_id}]: {e}")
            return False

    def unregister_process(self, pid: int) -> bool:
        """
        取消进程追踪

        Args:
            pid: 进程 PID

        Returns:
            bool: 是否成功取消追踪
        """
        try:
            if pid in self.processes:
                del self.processes[pid]
                debug_log(f"取消进程追踪: PID {pid}")
                return True
            return False

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "取消进程追踪", "pid": pid},
                error_type=ErrorType.PROCESS,
            )
            debug_log(f"取消进程追踪失败 [错误ID: {error_id}]: {e}")
            return False

    def cleanup_temp_files(self, max_age: int | None = None) -> int:
        """
        清理临时文件

        Args:
            max_age: 最大文件年龄（秒），None 使用默认值

        Returns:
            int: 清理的文件数量
        """
        if max_age is None:
            max_age = self.temp_file_max_age

        cleaned_count = 0
        current_time = time.time()
        files_to_remove = set()

        for file_path in self.temp_files.copy():
            try:
                if not os.path.exists(file_path):
                    files_to_remove.add(file_path)
                    continue

                # 检查文件年龄
                file_age = current_time - os.path.getmtime(file_path)
                if file_age > max_age:
                    os.remove(file_path)
                    files_to_remove.add(file_path)
                    cleaned_count += 1
                    debug_log(f"清理过期临时文件: {file_path}")

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "清理临时文件", "file_path": file_path},
                    error_type=ErrorType.FILE_IO,
                )
                debug_log(f"清理临时文件失败 [错误ID: {error_id}]: {e}")
                files_to_remove.add(file_path)  # 移除无效追踪

        # 移除已清理的文件追踪
        self.temp_files -= files_to_remove

        return cleaned_count

    def cleanup_temp_dirs(self) -> int:
        """
        清理临时目录

        Returns:
            int: 清理的目录数量
        """
        cleaned_count = 0
        dirs_to_remove = set()

        for dir_path in self.temp_dirs.copy():
            try:
                if not os.path.exists(dir_path):
                    dirs_to_remove.add(dir_path)
                    continue

                # 尝试删除目录
                shutil.rmtree(dir_path)
                dirs_to_remove.add(dir_path)
                cleaned_count += 1
                debug_log(f"清理临时目录: {dir_path}")

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "清理临时目录", "dir_path": dir_path},
                    error_type=ErrorType.FILE_IO,
                )
                debug_log(f"清理临时目录失败 [错误ID: {error_id}]: {e}")
                dirs_to_remove.add(dir_path)  # 移除无效追踪

        # 移除已清理的目录追踪
        self.temp_dirs -= dirs_to_remove

        return cleaned_count

    def cleanup_processes(self, force: bool = False) -> int:
        """
        清理进程

        Args:
            force: 是否强制终止进程

        Returns:
            int: 清理的进程数量
        """
        cleaned_count = 0
        processes_to_remove = []

        for pid, process_info in self.processes.copy().items():
            try:
                process_obj = process_info.get("process")
                auto_cleanup = process_info.get("auto_cleanup", True)

                if not auto_cleanup:
                    continue

                # 检查进程是否还在运行
                if process_obj and hasattr(process_obj, "poll"):
                    if process_obj.poll() is None:  # 进程还在运行
                        if force:
                            debug_log(f"强制终止进程: PID {pid}")
                            process_obj.kill()
                        else:
                            debug_log(f"优雅终止进程: PID {pid}")
                            process_obj.terminate()

                        # 等待进程结束
                        try:
                            process_obj.wait(timeout=5)
                            cleaned_count += 1
                        except subprocess.TimeoutExpired:
                            if not force:
                                debug_log(f"进程 {pid} 优雅终止超时，强制终止")
                                process_obj.kill()
                                process_obj.wait(timeout=3)
                                cleaned_count += 1

                    processes_to_remove.append(pid)
                else:
                    # 使用 psutil 检查进程
                    try:
                        import psutil

                        if psutil.pid_exists(pid):
                            proc = psutil.Process(pid)
                            if force:
                                proc.kill()
                            else:
                                proc.terminate()
                            proc.wait(timeout=5)
                            cleaned_count += 1
                        processes_to_remove.append(pid)
                    except ImportError:
                        debug_log("psutil 不可用，跳过进程检查")
                        processes_to_remove.append(pid)
                    except Exception as e:
                        debug_log(f"清理进程 {pid} 失败: {e}")
                        processes_to_remove.append(pid)

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "清理进程", "pid": pid},
                    error_type=ErrorType.PROCESS,
                )
                debug_log(f"清理进程失败 [错误ID: {error_id}]: {e}")
                processes_to_remove.append(pid)

        # 移除已清理的进程追踪
        for pid in processes_to_remove:
            self.processes.pop(pid, None)

        return cleaned_count

    def cleanup_file_handles(self) -> int:
        """
        清理文件句柄

        Returns:
            int: 清理的句柄数量
        """
        cleaned_count = 0
        handles_to_remove = set()

        for handle_ref in self.file_handles.copy():
            try:
                handle = handle_ref()
                if handle is None:
                    # 弱引用已失效
                    handles_to_remove.add(handle_ref)
                    continue

                # 尝试关闭文件句柄
                if hasattr(handle, "close") and not handle.closed:
                    handle.close()
                    cleaned_count += 1
                    debug_log(f"关闭文件句柄: {type(handle).__name__}")

                handles_to_remove.add(handle_ref)

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "清理文件句柄"},
                    error_type=ErrorType.FILE_IO,
                )
                debug_log(f"清理文件句柄失败 [错误ID: {error_id}]: {e}")
                handles_to_remove.add(handle_ref)

        # 移除已清理的句柄追踪
        self.file_handles -= handles_to_remove

        return cleaned_count

    def cleanup_all(self, force: bool = False) -> dict[str, int]:
        """
        清理所有资源

        Args:
            force: 是否强制清理

        Returns:
            Dict[str, int]: 清理统计
        """
        debug_log("开始全面资源清理...")

        results = {"temp_files": 0, "temp_dirs": 0, "processes": 0, "file_handles": 0}

        try:
            # 清理文件句柄
            results["file_handles"] = self.cleanup_file_handles()

            # 清理进程
            results["processes"] = self.cleanup_processes(force=force)

            # 清理临时文件
            results["temp_files"] = self.cleanup_temp_files(max_age=0)  # 清理所有文件

            # 清理临时目录
            results["temp_dirs"] = self.cleanup_temp_dirs()

            # 更新统计
            self.stats["cleanup_runs"] += 1
            self.stats["last_cleanup"] = time.time()

            total_cleaned = sum(results.values())
            debug_log(f"资源清理完成，共清理 {total_cleaned} 个资源: {results}")

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "全面资源清理"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"全面资源清理失败 [错误ID: {error_id}]: {e}")

        return results

    def _start_auto_cleanup(self) -> None:
        """启动自动清理线程"""
        if not self.auto_cleanup_enabled or self._cleanup_thread:
            return

        def cleanup_worker():
            """清理工作线程"""
            while not self._stop_cleanup.wait(self.cleanup_interval):
                try:
                    # 执行定期清理
                    self.cleanup_temp_files()
                    self._check_process_health()

                except Exception as e:
                    error_id = ErrorHandler.log_error_with_context(
                        e,
                        context={"operation": "自动清理"},
                        error_type=ErrorType.SYSTEM,
                    )
                    debug_log(f"自动清理失败 [错误ID: {error_id}]: {e}")

        self._cleanup_thread = threading.Thread(
            target=cleanup_worker, name="ResourceManager-AutoCleanup", daemon=True
        )
        self._cleanup_thread.start()
        debug_log("自动清理线程已启动")

    def _check_process_health(self) -> None:
        """检查进程健康状态"""
        current_time = time.time()

        for pid, process_info in self.processes.items():
            try:
                process_obj = process_info.get("process")
                last_check = process_info.get("last_check", current_time)

                # 每分钟检查一次
                if current_time - last_check < 60:
                    continue

                # 更新检查时间
                process_info["last_check"] = current_time

                # 检查进程是否还在运行
                if process_obj and hasattr(process_obj, "poll"):
                    if process_obj.poll() is not None:
                        # 进程已结束，移除追踪
                        debug_log(f"检测到进程 {pid} 已结束，移除追踪")
                        self.unregister_process(pid)

            except Exception as e:
                debug_log(f"检查进程 {pid} 健康状态失败: {e}")

    def stop_auto_cleanup(self) -> None:
        """停止自动清理"""
        if self._cleanup_thread:
            self._stop_cleanup.set()
            self._cleanup_thread.join(timeout=5)
            self._cleanup_thread = None
            debug_log("自动清理线程已停止")

    def get_resource_stats(self) -> dict[str, Any]:
        """
        获取资源统计信息

        Returns:
            Dict[str, Any]: 资源统计
        """
        current_stats = self.stats.copy()
        current_stats.update(
            {
                "current_temp_files": len(self.temp_files),
                "current_temp_dirs": len(self.temp_dirs),
                "current_processes": len(self.processes),
                "current_file_handles": len(self.file_handles),
                "auto_cleanup_enabled": self.auto_cleanup_enabled,
                "cleanup_interval": self.cleanup_interval,
                "temp_file_max_age": self.temp_file_max_age,
            }
        )

        # 添加内存监控统计
        try:
            if hasattr(self, "memory_monitor") and self.memory_monitor:
                memory_info = self.memory_monitor.get_current_memory_info()
                memory_stats = self.memory_monitor.get_memory_stats()

                current_stats.update(
                    {
                        "memory_monitoring_enabled": self.memory_monitor.is_monitoring,
                        "current_memory_usage": memory_info.get("system", {}).get(
                            "usage_percent", 0
                        ),
                        "memory_status": memory_info.get("status", "unknown"),
                        "memory_cleanup_triggers": memory_stats.cleanup_triggers,
                        "memory_alerts_count": memory_stats.alerts_count,
                    }
                )
        except Exception as e:
            debug_log(f"获取内存统计失败: {e}")

        return current_stats

    def get_detailed_info(self) -> dict[str, Any]:
        """
        获取详细资源信息

        Returns:
            Dict[str, Any]: 详细资源信息
        """
        return {
            "temp_files": list(self.temp_files),
            "temp_dirs": list(self.temp_dirs),
            "processes": {
                pid: {
                    "description": info.get("description", ""),
                    "auto_cleanup": info.get("auto_cleanup", True),
                    "registered_at": info.get("registered_at", 0),
                    "last_check": info.get("last_check", 0),
                }
                for pid, info in self.processes.items()
            },
            "file_handles_count": len(self.file_handles),
            "stats": self.get_resource_stats(),
        }

    def configure(
        self,
        auto_cleanup_enabled: bool | None = None,
        cleanup_interval: int | None = None,
        temp_file_max_age: int | None = None,
    ) -> None:
        """
        配置资源管理器

        Args:
            auto_cleanup_enabled: 是否启用自动清理
            cleanup_interval: 清理间隔（秒）
            temp_file_max_age: 临时文件最大年龄（秒）
        """
        if auto_cleanup_enabled is not None:
            old_enabled = self.auto_cleanup_enabled
            self.auto_cleanup_enabled = auto_cleanup_enabled

            if old_enabled and not auto_cleanup_enabled:
                self.stop_auto_cleanup()
            elif not old_enabled and auto_cleanup_enabled:
                self._start_auto_cleanup()
            elif auto_cleanup_enabled and self._cleanup_thread is None:
                # 如果启用了自动清理但线程不存在，重新启动
                self._start_auto_cleanup()

        if cleanup_interval is not None:
            self.cleanup_interval = max(60, cleanup_interval)  # 最小1分钟

        if temp_file_max_age is not None:
            self.temp_file_max_age = max(300, temp_file_max_age)  # 最小5分钟

        debug_log(
            f"ResourceManager 配置已更新: auto_cleanup={self.auto_cleanup_enabled}, "
            f"interval={self.cleanup_interval}, max_age={self.temp_file_max_age}"
        )


# 全局资源管理器实例
_resource_manager = None


def get_resource_manager() -> ResourceManager:
    """
    获取全局资源管理器实例

    Returns:
        ResourceManager: 资源管理器实例
    """
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager


# 便捷函数
def create_temp_file(suffix: str = "", prefix: str = "mcp_", **kwargs) -> str:
    """创建临时文件的便捷函数"""
    return get_resource_manager().create_temp_file(
        suffix=suffix, prefix=prefix, **kwargs
    )


def create_temp_dir(suffix: str = "", prefix: str = "mcp_", **kwargs) -> str:
    """创建临时目录的便捷函数"""
    return get_resource_manager().create_temp_dir(
        suffix=suffix, prefix=prefix, **kwargs
    )


def register_process(
    process: subprocess.Popen | int, description: str = "", **kwargs
) -> int:
    """注册进程的便捷函数"""
    return get_resource_manager().register_process(
        process, description=description, **kwargs
    )


def cleanup_all_resources(force: bool = False) -> dict[str, int]:
    """清理所有资源的便捷函数"""
    return get_resource_manager().cleanup_all(force=force)
