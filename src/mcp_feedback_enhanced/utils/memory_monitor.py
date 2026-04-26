#!/usr/bin/env python3
"""
集成式内存监控系统
==================

提供与资源管理器深度集成的内存监控功能，包括：
- 系统和进程内存使用监控
- 智能清理触发机制
- 内存泄漏检测和趋势分析
- 性能优化建议
"""

import gc
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psutil

from ..debug import debug_log
from .error_handler import ErrorHandler, ErrorType


@dataclass
class MemorySnapshot:
    """内存快照数据类"""

    timestamp: datetime
    system_total: int  # 系统总内存 (bytes)
    system_available: int  # 系统可用内存 (bytes)
    system_used: int  # 系统已用内存 (bytes)
    system_percent: float  # 系统内存使用率 (%)
    process_rss: int  # 进程常驻内存 (bytes)
    process_vms: int  # 进程虚拟内存 (bytes)
    process_percent: float  # 进程内存使用率 (%)
    gc_objects: int  # Python 垃圾回收对象数量


@dataclass
class MemoryAlert:
    """内存警告数据类"""

    level: str  # warning, critical, emergency
    message: str
    timestamp: datetime
    memory_percent: float
    recommended_action: str


@dataclass
class MemoryStats:
    """内存统计数据类"""

    monitoring_duration: float  # 监控持续时间 (秒)
    snapshots_count: int  # 快照数量
    average_system_usage: float  # 平均系统内存使用率
    peak_system_usage: float  # 峰值系统内存使用率
    average_process_usage: float  # 平均进程内存使用率
    peak_process_usage: float  # 峰值进程内存使用率
    alerts_count: int  # 警告数量
    cleanup_triggers: int  # 清理触发次数
    memory_trend: str  # 内存趋势 (stable, increasing, decreasing)


class MemoryMonitor:
    """集成式内存监控器"""

    def __init__(
        self,
        warning_threshold: float = 0.8,
        critical_threshold: float = 0.9,
        emergency_threshold: float = 0.95,
        monitoring_interval: int = 30,
        max_snapshots: int = 1000,
    ):
        """
        初始化内存监控器

        Args:
            warning_threshold: 警告阈值 (0.0-1.0)
            critical_threshold: 危险阈值 (0.0-1.0)
            emergency_threshold: 紧急阈值 (0.0-1.0)
            monitoring_interval: 监控间隔 (秒)
            max_snapshots: 最大快照数量
        """
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.emergency_threshold = emergency_threshold
        self.monitoring_interval = monitoring_interval
        self.max_snapshots = max_snapshots

        # 监控状态
        self.is_monitoring = False
        self.monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 数据存储
        self.snapshots: deque = deque(maxlen=max_snapshots)
        self.alerts: list[MemoryAlert] = []
        self.max_alerts = 100

        # 回调函数
        self.cleanup_callbacks: list[Callable] = []
        self.alert_callbacks: list[Callable[[MemoryAlert], None]] = []

        # 统计数据
        self.start_time: datetime | None = None
        self.cleanup_triggers_count = 0

        # 进程信息
        self.process = psutil.Process()

        debug_log("MemoryMonitor 初始化完成")

    def start_monitoring(self) -> bool:
        """
        开始内存监控

        Returns:
            bool: 是否成功启动
        """
        if self.is_monitoring:
            debug_log("内存监控已在运行")
            return True

        try:
            self.is_monitoring = True
            self.start_time = datetime.now()
            self._stop_event.clear()

            self.monitor_thread = threading.Thread(
                target=self._monitoring_loop, name="MemoryMonitor", daemon=True
            )
            self.monitor_thread.start()

            debug_log(f"内存监控已启动，间隔 {self.monitoring_interval} 秒")
            return True

        except Exception as e:
            self.is_monitoring = False
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "启动内存监控"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"启动内存监控失败 [错误ID: {error_id}]: {e}")
            return False

    def stop_monitoring(self) -> bool:
        """
        停止内存监控

        Returns:
            bool: 是否成功停止
        """
        if not self.is_monitoring:
            debug_log("内存监控未在运行")
            return True

        try:
            self.is_monitoring = False
            self._stop_event.set()

            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=5)

            debug_log("内存监控已停止")
            return True

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "停止内存监控"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"停止内存监控失败 [错误ID: {error_id}]: {e}")
            return False

    def _monitoring_loop(self):
        """内存监控主循环"""
        debug_log("内存监控循环开始")

        while not self._stop_event.is_set():
            try:
                # 收集内存快照
                snapshot = self._collect_memory_snapshot()
                self.snapshots.append(snapshot)

                # 检查内存使用情况
                self._check_memory_usage(snapshot)

                # 等待下次监控
                if self._stop_event.wait(self.monitoring_interval):
                    break

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "内存监控循环"},
                    error_type=ErrorType.SYSTEM,
                )
                debug_log(f"内存监控循环错误 [错误ID: {error_id}]: {e}")

                # 发生错误时等待较短时间后重试
                if self._stop_event.wait(5):
                    break

        debug_log("内存监控循环结束")

    def _collect_memory_snapshot(self) -> MemorySnapshot:
        """收集内存快照"""
        try:
            # 系统内存信息
            system_memory = psutil.virtual_memory()

            # 进程内存信息
            process_memory = self.process.memory_info()
            process_percent = self.process.memory_percent()

            # Python 垃圾回收信息
            gc_objects = len(gc.get_objects())

            return MemorySnapshot(
                timestamp=datetime.now(),
                system_total=system_memory.total,
                system_available=system_memory.available,
                system_used=system_memory.used,
                system_percent=system_memory.percent,
                process_rss=process_memory.rss,
                process_vms=process_memory.vms,
                process_percent=process_percent,
                gc_objects=gc_objects,
            )

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "收集内存快照"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"收集内存快照失败 [错误ID: {error_id}]: {e}")
            raise

    def _check_memory_usage(self, snapshot: MemorySnapshot):
        """检查内存使用情况并触发相应动作"""
        usage_percent = snapshot.system_percent / 100.0

        # 检查紧急阈值
        if usage_percent >= self.emergency_threshold:
            alert = MemoryAlert(
                level="emergency",
                message=f"内存使用率达到紧急水平: {snapshot.system_percent:.1f}%",
                timestamp=snapshot.timestamp,
                memory_percent=snapshot.system_percent,
                recommended_action="立即执行强制清理和垃圾回收",
            )
            self._handle_alert(alert)
            self._trigger_emergency_cleanup()

        # 检查危险阈值
        elif usage_percent >= self.critical_threshold:
            alert = MemoryAlert(
                level="critical",
                message=f"内存使用率达到危险水平: {snapshot.system_percent:.1f}%",
                timestamp=snapshot.timestamp,
                memory_percent=snapshot.system_percent,
                recommended_action="执行资源清理和垃圾回收",
            )
            self._handle_alert(alert)
            self._trigger_cleanup()

        # 检查警告阈值
        elif usage_percent >= self.warning_threshold:
            alert = MemoryAlert(
                level="warning",
                message=f"内存使用率较高: {snapshot.system_percent:.1f}%",
                timestamp=snapshot.timestamp,
                memory_percent=snapshot.system_percent,
                recommended_action="考虑执行轻量级清理",
            )
            self._handle_alert(alert)

    def _handle_alert(self, alert: MemoryAlert):
        """处理内存警告"""
        # 添加到警告列表
        self.alerts.append(alert)

        # 限制警告数量
        if len(self.alerts) > self.max_alerts:
            self.alerts = self.alerts[-self.max_alerts :]

        # 调用警告回调
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                debug_log(f"警告回调执行失败: {e}")

        debug_log(f"内存警告 [{alert.level}]: {alert.message}")

    def _trigger_cleanup(self):
        """触发清理操作"""
        self.cleanup_triggers_count += 1
        debug_log("触发内存清理操作")

        # 执行 Python 垃圾回收
        collected = gc.collect()
        debug_log(f"垃圾回收清理了 {collected} 个对象")

        # 调用清理回调
        for callback in self.cleanup_callbacks:
            try:
                callback()
            except Exception as e:
                debug_log(f"清理回调执行失败: {e}")

    def _trigger_emergency_cleanup(self):
        """触发紧急清理操作"""
        debug_log("触发紧急内存清理操作")

        # 执行强制垃圾回收
        for _ in range(3):
            collected = gc.collect()
            debug_log(f"强制垃圾回收清理了 {collected} 个对象")

        # 调用清理回调（强制模式）
        for callback in self.cleanup_callbacks:
            try:
                # 修复 unreachable 错误 - 简化逻辑，移除不可达的 else 分支
                # 尝试传递 force 参数
                import inspect

                sig = inspect.signature(callback)
                if "force" in sig.parameters:
                    callback(force=True)
                else:
                    callback()
            except Exception as e:
                debug_log(f"紧急清理回调执行失败: {e}")

    def add_cleanup_callback(self, callback: Callable):
        """添加清理回调函数"""
        if callback not in self.cleanup_callbacks:
            self.cleanup_callbacks.append(callback)
            debug_log("添加清理回调函数")

    def add_alert_callback(self, callback: Callable[[MemoryAlert], None]):
        """添加警告回调函数"""
        if callback not in self.alert_callbacks:
            self.alert_callbacks.append(callback)
            debug_log("添加警告回调函数")

    def remove_cleanup_callback(self, callback: Callable):
        """移除清理回调函数"""
        if callback in self.cleanup_callbacks:
            self.cleanup_callbacks.remove(callback)
            debug_log("移除清理回调函数")

    def remove_alert_callback(self, callback: Callable[[MemoryAlert], None]):
        """移除警告回调函数"""
        if callback in self.alert_callbacks:
            self.alert_callbacks.remove(callback)
            debug_log("移除警告回调函数")

    def get_current_memory_info(self) -> dict[str, Any]:
        """获取当前内存信息"""
        try:
            snapshot = self._collect_memory_snapshot()
            return {
                "timestamp": snapshot.timestamp.isoformat(),
                "system": {
                    "total_gb": round(snapshot.system_total / (1024**3), 2),
                    "available_gb": round(snapshot.system_available / (1024**3), 2),
                    "used_gb": round(snapshot.system_used / (1024**3), 2),
                    "usage_percent": round(snapshot.system_percent, 1),
                },
                "process": {
                    "rss_mb": round(snapshot.process_rss / (1024**2), 2),
                    "vms_mb": round(snapshot.process_vms / (1024**2), 2),
                    "usage_percent": round(snapshot.process_percent, 1),
                },
                "gc_objects": snapshot.gc_objects,
                "status": self._get_memory_status(snapshot.system_percent / 100.0),
            }
        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "获取当前内存信息"},
                error_type=ErrorType.SYSTEM,
            )
            debug_log(f"获取内存信息失败 [错误ID: {error_id}]: {e}")
            return {}

    def get_memory_stats(self) -> MemoryStats:
        """获取内存统计数据"""
        if not self.snapshots:
            return MemoryStats(
                monitoring_duration=0.0,
                snapshots_count=0,
                average_system_usage=0.0,
                peak_system_usage=0.0,
                average_process_usage=0.0,
                peak_process_usage=0.0,
                alerts_count=0,
                cleanup_triggers=0,
                memory_trend="unknown",
            )

        # 计算统计数据
        system_usages = [s.system_percent for s in self.snapshots]
        process_usages = [s.process_percent for s in self.snapshots]

        duration = 0.0
        if self.start_time:
            duration = (datetime.now() - self.start_time).total_seconds()

        return MemoryStats(
            monitoring_duration=duration,
            snapshots_count=len(self.snapshots),
            average_system_usage=sum(system_usages) / len(system_usages),
            peak_system_usage=max(system_usages),
            average_process_usage=sum(process_usages) / len(process_usages),
            peak_process_usage=max(process_usages),
            alerts_count=len(self.alerts),
            cleanup_triggers=self.cleanup_triggers_count,
            memory_trend=self._analyze_memory_trend(),
        )

    def get_recent_alerts(self, limit: int = 10) -> list[MemoryAlert]:
        """获取最近的警告"""
        return self.alerts[-limit:] if self.alerts else []

    def _get_memory_status(self, usage_percent: float) -> str:
        """获取内存状态描述"""
        if usage_percent >= self.emergency_threshold:
            return "emergency"
        if usage_percent >= self.critical_threshold:
            return "critical"
        if usage_percent >= self.warning_threshold:
            return "warning"
        return "normal"

    def _analyze_memory_trend(self) -> str:
        """分析内存使用趋势"""
        if len(self.snapshots) < 10:
            return "insufficient_data"

        # 取最近的快照进行趋势分析
        recent_snapshots = list(self.snapshots)[-10:]
        usages = [s.system_percent for s in recent_snapshots]

        # 简单的线性趋势分析
        first_half = usages[:5]
        second_half = usages[5:]

        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        diff = avg_second - avg_first

        if abs(diff) < 2.0:  # 变化小于 2%
            return "stable"
        if diff > 0:
            return "increasing"
        return "decreasing"

    def force_cleanup(self):
        """手动触发清理操作"""
        debug_log("手动触发内存清理")
        self._trigger_cleanup()

    def force_emergency_cleanup(self):
        """手动触发紧急清理操作"""
        debug_log("手动触发紧急内存清理")
        self._trigger_emergency_cleanup()

    def reset_stats(self):
        """重置统计数据"""
        self.snapshots.clear()
        self.alerts.clear()
        self.cleanup_triggers_count = 0
        self.start_time = datetime.now() if self.is_monitoring else None
        debug_log("内存监控统计数据已重置")

    def export_memory_data(self) -> dict[str, Any]:
        """导出内存数据"""
        return {
            "config": {
                "warning_threshold": self.warning_threshold,
                "critical_threshold": self.critical_threshold,
                "emergency_threshold": self.emergency_threshold,
                "monitoring_interval": self.monitoring_interval,
            },
            "current_info": self.get_current_memory_info(),
            "stats": self.get_memory_stats().__dict__,
            "recent_alerts": [
                {
                    "level": alert.level,
                    "message": alert.message,
                    "timestamp": alert.timestamp.isoformat(),
                    "memory_percent": alert.memory_percent,
                    "recommended_action": alert.recommended_action,
                }
                for alert in self.get_recent_alerts()
            ],
            "is_monitoring": self.is_monitoring,
        }


# 全域内存监控器实例
_memory_monitor: MemoryMonitor | None = None
_monitor_lock = threading.Lock()


def get_memory_monitor() -> MemoryMonitor:
    """获取全域内存监控器实例"""
    global _memory_monitor
    if _memory_monitor is None:
        with _monitor_lock:
            if _memory_monitor is None:
                _memory_monitor = MemoryMonitor()
    return _memory_monitor
