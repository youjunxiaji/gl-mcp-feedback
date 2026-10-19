#!/usr/bin/env python3
"""
会话清理管理器
==============

统一管理 Web 会话的清理策略、统计和性能监控。
与内存监控系统深度集成，提供智能清理决策。
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ...debug import web_debug_log as debug_log
from ...utils.error_handler import ErrorHandler, ErrorType
from ..models.feedback_session import CleanupReason, SessionStatus


@dataclass
class CleanupPolicy:
    """清理策略配置"""

    max_idle_time: int = 1800  # 最大空闲时间（秒）
    max_session_age: int = 7200  # 最大会话年龄（秒）
    max_sessions: int = 10  # 最大会话数量
    cleanup_interval: int = 300  # 清理间隔（秒）
    memory_pressure_threshold: float = 0.8  # 内存压力阈值
    enable_auto_cleanup: bool = True  # 启用自动清理
    preserve_active_session: bool = True  # 保护活跃会话


@dataclass
class CleanupStats:
    """清理统计数据"""

    total_cleanups: int = 0
    expired_cleanups: int = 0
    memory_pressure_cleanups: int = 0
    manual_cleanups: int = 0
    auto_cleanups: int = 0
    total_sessions_cleaned: int = 0
    total_cleanup_time: float = 0.0
    average_cleanup_time: float = 0.0
    last_cleanup_time: datetime | None = None
    cleanup_efficiency: float = 0.0  # 清理效率（清理的会话数/总会话数）


class CleanupTrigger(Enum):
    """清理触发器类型"""

    AUTO = "auto"  # 自动清理
    MEMORY_PRESSURE = "memory_pressure"  # 内存压力
    MANUAL = "manual"  # 手动清理
    EXPIRED = "expired"  # 过期清理
    CAPACITY = "capacity"  # 容量限制


class SessionCleanupManager:
    """会话清理管理器"""

    def __init__(self, web_ui_manager, policy: CleanupPolicy | None = None):
        """
        初始化会话清理管理器

        Args:
            web_ui_manager: WebUIManager 实例
            policy: 清理策略配置
        """
        self.web_ui_manager = web_ui_manager
        self.policy = policy or CleanupPolicy()
        self.stats = CleanupStats()

        # 清理状态
        self.is_running = False
        self.cleanup_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 回调函数
        self.cleanup_callbacks: list[Callable] = []
        self.stats_callbacks: list[Callable] = []

        # 清理历史记录
        self.cleanup_history: list[dict[str, Any]] = []
        self.max_history = 100

        debug_log("SessionCleanupManager 初始化完成")

    def start_auto_cleanup(self) -> bool:
        """启动自动清理"""
        if not self.policy.enable_auto_cleanup:
            debug_log("自动清理已禁用")
            return False

        if self.is_running:
            debug_log("自动清理已在运行")
            return True

        try:
            self.is_running = True
            self._stop_event.clear()

            self.cleanup_thread = threading.Thread(
                target=self._auto_cleanup_loop,
                name="SessionCleanupManager",
                daemon=True,
            )
            self.cleanup_thread.start()

            debug_log(f"自动清理已启动，间隔 {self.policy.cleanup_interval} 秒")
            return True

        except Exception as e:
            self.is_running = False
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "启动自动清理"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"启动自动清理失败 [错误ID: {error_id}]: {e}")
            return False

    def stop_auto_cleanup(self) -> bool:
        """停止自动清理"""
        if not self.is_running:
            debug_log("自动清理未在运行")
            return True

        try:
            self.is_running = False
            self._stop_event.set()

            if self.cleanup_thread and self.cleanup_thread.is_alive():
                self.cleanup_thread.join(timeout=5)

            debug_log("自动清理已停止")
            return True

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "停止自动清理"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"停止自动清理失败 [错误ID: {error_id}]: {e}")
            return False

    def _auto_cleanup_loop(self):
        """自动清理主循环"""
        debug_log("自动清理循环开始")

        while not self._stop_event.is_set():
            try:
                # 执行清理检查
                self._perform_auto_cleanup()

                # 等待下次清理
                if self._stop_event.wait(self.policy.cleanup_interval):
                    break

            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"operation": "自动清理循环"},
                    error_type=ErrorType.SYSTEM,
                )
                debug_log(f"自动清理循环错误 [错误ID: {error_id}]: {e}")

                # 发生错误时等待较短时间后重试
                if self._stop_event.wait(30):
                    break

        debug_log("自动清理循环结束")

    def _perform_auto_cleanup(self):
        """执行自动清理"""
        cleanup_start_time = time.time()
        cleaned_sessions = 0

        try:
            # 1. 检查会话数量限制
            if len(self.web_ui_manager.sessions) > self.policy.max_sessions:
                cleaned = self._cleanup_by_capacity()
                cleaned_sessions += cleaned
                debug_log(f"容量限制清理了 {cleaned} 个会话")

            # 2. 清理过期会话
            cleaned = self._cleanup_expired_sessions()
            cleaned_sessions += cleaned

            # 3. 清理空闲会话
            cleaned = self._cleanup_idle_sessions()
            cleaned_sessions += cleaned

            # 4. 更新统计
            cleanup_duration = time.time() - cleanup_start_time
            self._update_cleanup_stats(
                CleanupTrigger.AUTO, cleaned_sessions, cleanup_duration
            )

            if cleaned_sessions > 0:
                debug_log(
                    f"自动清理完成，清理了 {cleaned_sessions} 个会话，耗时: {cleanup_duration:.2f}秒"
                )

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e, context={"operation": "执行自动清理"}, error_type=ErrorType.SYSTEM
            )
            debug_log(f"执行自动清理失败 [错误ID: {error_id}]: {e}")

    def trigger_cleanup(self, trigger: CleanupTrigger, force: bool = False) -> int:
        """触发清理操作"""
        cleanup_start_time = time.time()
        cleaned_sessions = 0

        try:
            debug_log(f"触发清理操作，触发器: {trigger.value}，强制: {force}")

            if trigger == CleanupTrigger.MEMORY_PRESSURE:
                cleaned_sessions = (
                    self.web_ui_manager.cleanup_sessions_by_memory_pressure(force)
                )
            elif trigger == CleanupTrigger.EXPIRED:
                cleaned_sessions = self.web_ui_manager.cleanup_expired_sessions()
            elif trigger == CleanupTrigger.CAPACITY:
                cleaned_sessions = self._cleanup_by_capacity()
            elif trigger == CleanupTrigger.MANUAL:
                # 手动清理：组合多种策略
                cleaned_sessions += self.web_ui_manager.cleanup_expired_sessions()
                if force:
                    cleaned_sessions += (
                        self.web_ui_manager.cleanup_sessions_by_memory_pressure(force)
                    )
            else:
                # 自动清理
                self._perform_auto_cleanup()
                return 0  # 统计已在 _perform_auto_cleanup 中更新

            # 更新统计
            cleanup_duration = time.time() - cleanup_start_time
            self._update_cleanup_stats(trigger, cleaned_sessions, cleanup_duration)

            debug_log(
                f"清理操作完成，清理了 {cleaned_sessions} 个会话，耗时: {cleanup_duration:.2f}秒"
            )
            return cleaned_sessions

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={
                    "operation": "触发清理",
                    "trigger": trigger.value,
                    "force": force,
                },
                error_type=ErrorType.SYSTEM,
            )
            debug_log(f"触发清理操作失败 [错误ID: {error_id}]: {e}")
            return 0

    def _cleanup_by_capacity(self) -> int:
        """根据容量限制清理会话"""
        sessions = self.web_ui_manager.sessions
        if len(sessions) <= self.policy.max_sessions:
            return 0

        # 计算需要清理的会话数量
        excess_count = len(sessions) - self.policy.max_sessions

        # 按优先级排序会话（优先清理旧的、非活跃的会话）
        session_priorities = []
        for session_id, session in sessions.items():
            # 跳过当前活跃会话（如果启用保护）
            if (
                self.policy.preserve_active_session
                and self.web_ui_manager.current_session
                and session.session_id == self.web_ui_manager.current_session.session_id
            ):
                continue

            # 计算优先级分数（分数越高越优先清理）
            priority_score = 0

            # 状态优先级
            if session.status in [
                SessionStatus.COMPLETED,
                SessionStatus.ERROR,
                SessionStatus.TIMEOUT,
            ]:
                priority_score += 100
            elif session.status == SessionStatus.FEEDBACK_SUBMITTED:
                priority_score += 50

            # 年龄优先级
            age = session.get_age()
            priority_score += age / 60  # 每分钟加1分

            # 空闲时间优先级
            idle_time = session.get_idle_time()
            priority_score += idle_time / 30  # 每30秒加1分

            session_priorities.append((session_id, session, priority_score))

        # 按优先级排序并清理
        session_priorities.sort(key=lambda x: x[2], reverse=True)
        cleaned_count = 0

        for i in range(min(excess_count, len(session_priorities))):
            session_id, session, _ = session_priorities[i]
            try:
                session._cleanup_sync_enhanced(CleanupReason.MANUAL)
                del self.web_ui_manager.sessions[session_id]
                cleaned_count += 1
            except Exception as e:
                debug_log(f"容量清理会话 {session_id} 失败: {e}")

        return cleaned_count

    def _cleanup_expired_sessions(self) -> int:
        """清理过期会话"""
        expired_sessions = []

        for session_id, session in self.web_ui_manager.sessions.items():
            # 检查是否过期
            if session.is_expired() or session.get_age() > self.policy.max_session_age:
                expired_sessions.append(session_id)

        # 清理过期会话
        cleaned_count = 0
        for session_id in expired_sessions:
            try:
                session = self.web_ui_manager.sessions.get(session_id)
                if session:
                    session._cleanup_sync_enhanced(CleanupReason.EXPIRED)
                    del self.web_ui_manager.sessions[session_id]
                    cleaned_count += 1

                    # 如果清理的是当前活跃会话，清空当前会话
                    if (
                        self.web_ui_manager.current_session
                        and self.web_ui_manager.current_session.session_id == session_id
                    ):
                        self.web_ui_manager.current_session = None

            except Exception as e:
                debug_log(f"清理过期会话 {session_id} 失败: {e}")

        return cleaned_count

    def _cleanup_idle_sessions(self) -> int:
        """清理空闲会话"""
        idle_sessions = []

        for session_id, session in self.web_ui_manager.sessions.items():
            # 跳过当前活跃会话（如果启用保护）
            if (
                self.policy.preserve_active_session
                and self.web_ui_manager.current_session
                and session.session_id == self.web_ui_manager.current_session.session_id
            ):
                continue

            # 检查是否空闲时间过长
            if session.get_idle_time() > self.policy.max_idle_time:
                idle_sessions.append(session_id)

        # 清理空闲会话
        cleaned_count = 0
        for session_id in idle_sessions:
            try:
                session = self.web_ui_manager.sessions.get(session_id)
                if session:
                    session._cleanup_sync_enhanced(CleanupReason.EXPIRED)
                    del self.web_ui_manager.sessions[session_id]
                    cleaned_count += 1

            except Exception as e:
                debug_log(f"清理空闲会话 {session_id} 失败: {e}")

        return cleaned_count

    def _update_cleanup_stats(
        self, trigger: CleanupTrigger, cleaned_count: int, duration: float
    ):
        """更新清理统计"""
        self.stats.total_cleanups += 1
        self.stats.total_sessions_cleaned += cleaned_count
        self.stats.total_cleanup_time += duration
        self.stats.last_cleanup_time = datetime.now()

        # 更新平均清理时间
        if self.stats.total_cleanups > 0:
            self.stats.average_cleanup_time = (
                self.stats.total_cleanup_time / self.stats.total_cleanups
            )

        # 更新清理效率
        total_sessions = len(self.web_ui_manager.sessions) + cleaned_count
        if total_sessions > 0:
            self.stats.cleanup_efficiency = cleaned_count / total_sessions

        # 根据触发器类型更新统计
        if trigger == CleanupTrigger.AUTO:
            self.stats.auto_cleanups += 1
        elif trigger == CleanupTrigger.MEMORY_PRESSURE:
            self.stats.memory_pressure_cleanups += 1
        elif trigger == CleanupTrigger.EXPIRED:
            self.stats.expired_cleanups += 1
        elif trigger == CleanupTrigger.MANUAL:
            self.stats.manual_cleanups += 1

        # 记录清理历史
        cleanup_record = {
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger.value,
            "cleaned_count": cleaned_count,
            "duration": duration,
            "total_sessions_before": total_sessions,
            "total_sessions_after": len(self.web_ui_manager.sessions),
        }

        self.cleanup_history.append(cleanup_record)

        # 限制历史记录数量
        if len(self.cleanup_history) > self.max_history:
            self.cleanup_history = self.cleanup_history[-self.max_history :]

        # 调用统计回调
        for callback in self.stats_callbacks:
            try:
                callback(self.stats, cleanup_record)
            except Exception as e:
                debug_log(f"统计回调执行失败: {e}")

    def get_cleanup_statistics(self) -> dict[str, Any]:
        """获取清理统计数据"""
        stats_dict = {
            "total_cleanups": self.stats.total_cleanups,
            "expired_cleanups": self.stats.expired_cleanups,
            "memory_pressure_cleanups": self.stats.memory_pressure_cleanups,
            "manual_cleanups": self.stats.manual_cleanups,
            "auto_cleanups": self.stats.auto_cleanups,
            "total_sessions_cleaned": self.stats.total_sessions_cleaned,
            "total_cleanup_time": round(self.stats.total_cleanup_time, 2),
            "average_cleanup_time": round(self.stats.average_cleanup_time, 2),
            "cleanup_efficiency": round(self.stats.cleanup_efficiency, 3),
            "last_cleanup_time": self.stats.last_cleanup_time.isoformat()
            if self.stats.last_cleanup_time
            else None,
            "is_auto_cleanup_running": self.is_running,
            "current_sessions": len(self.web_ui_manager.sessions),
            "policy": {
                "max_idle_time": self.policy.max_idle_time,
                "max_session_age": self.policy.max_session_age,
                "max_sessions": self.policy.max_sessions,
                "cleanup_interval": self.policy.cleanup_interval,
                "enable_auto_cleanup": self.policy.enable_auto_cleanup,
                "preserve_active_session": self.policy.preserve_active_session,
            },
        }

        return stats_dict

    def get_cleanup_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取清理历史记录"""
        return self.cleanup_history[-limit:] if self.cleanup_history else []

    def add_cleanup_callback(self, callback: Callable):
        """添加清理回调函数"""
        if callback not in self.cleanup_callbacks:
            self.cleanup_callbacks.append(callback)
            debug_log("添加清理回调函数")

    def add_stats_callback(self, callback: Callable):
        """添加统计回调函数"""
        if callback not in self.stats_callbacks:
            self.stats_callbacks.append(callback)
            debug_log("添加统计回调函数")

    def update_policy(self, **kwargs):
        """更新清理策略"""
        for key, value in kwargs.items():
            if hasattr(self.policy, key):
                setattr(self.policy, key, value)
                debug_log(f"更新清理策略 {key} = {value}")
            else:
                debug_log(f"未知的策略参数: {key}")

    def reset_stats(self):
        """重置统计数据"""
        self.stats = CleanupStats()
        self.cleanup_history.clear()
        debug_log("清理统计数据已重置")

    def force_cleanup_all(self, exclude_current: bool = True) -> int:
        """强制清理所有会话"""
        sessions_to_clean = []

        for session_id, session in self.web_ui_manager.sessions.items():
            # 是否排除当前活跃会话
            if (
                exclude_current
                and self.web_ui_manager.current_session
                and session.session_id == self.web_ui_manager.current_session.session_id
            ):
                continue
            sessions_to_clean.append(session_id)

        # 清理会话
        cleaned_count = 0
        for session_id in sessions_to_clean:
            try:
                session = self.web_ui_manager.sessions.get(session_id)
                if session:
                    session._cleanup_sync_enhanced(CleanupReason.MANUAL)
                    del self.web_ui_manager.sessions[session_id]
                    cleaned_count += 1
            except Exception as e:
                debug_log(f"强制清理会话 {session_id} 失败: {e}")

        # 更新统计
        self._update_cleanup_stats(CleanupTrigger.MANUAL, cleaned_count, 0.0)

        debug_log(f"强制清理完成，清理了 {cleaned_count} 个会话")
        return cleaned_count
