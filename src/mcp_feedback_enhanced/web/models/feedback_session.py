#!/usr/bin/env python3
"""
Web 反馈会话模型
===============

管理 Web 反馈会话的数据和逻辑。
"""

import asyncio
import base64
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from ...debug import web_debug_log as debug_log
from ...utils.error_handler import ErrorHandler, ErrorType
from ...utils.resource_manager import get_resource_manager
from ..constants import get_message_code


class SessionStatus(Enum):
    """会话状态枚举 - 单向流转设计"""

    WAITING = "waiting"  # 等待中
    ACTIVE = "active"  # 活跃状态
    FEEDBACK_SUBMITTED = "feedback_submitted"  # 已提交反馈
    COMPLETED = "completed"  # 已完成
    ERROR = "error"  # 错误（终态）
    TIMEOUT = "timeout"  # 超时（终态）
    EXPIRED = "expired"  # 已过期（终态）


class CleanupReason(Enum):
    """清理原因枚举"""

    TIMEOUT = "timeout"  # 超时清理
    EXPIRED = "expired"  # 过期清理
    MEMORY_PRESSURE = "memory_pressure"  # 内存压力清理
    MANUAL = "manual"  # 手动清理
    ERROR = "error"  # 错误清理
    SHUTDOWN = "shutdown"  # 系统关闭清理


# 常数定义
MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB 图片大小限制
MAX_THUMBNAIL_LENGTH = 300 * 1024  # 对话记录里缩略图 data URL 的最大字符数
SUPPORTED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/bmp",
    "image/webp",
}
TEMP_DIR = Path.home() / ".cache" / "interactive-feedback-mcp-web"
# Cursor 钩子填入的对话标识只认 UUID 一类的字符，其他内容一律当作没有
CONVERSATION_ID_PATTERN = re.compile(r"[0-9A-Za-z_-]{1,128}")

# 消息代码现在从统一的常量文件导入
# 使用 get_message_code 函数来获取消息代码


class WebFeedbackSession:
    """Web 反馈会话管理"""

    def __init__(
        self,
        session_id: str,
        project_directory: str,
        summary: str,
        auto_cleanup_delay: int = 3600,
        max_idle_time: int = 1800,
        *,
        conversation_id: str = "",
    ):
        self.session_id = session_id
        self.project_directory = project_directory
        self.summary = summary
        # 这次调用来自哪个 Cursor 对话，由钩子填入；没有钩子或格式不对时为空串
        self.conversation_id = self.normalize_conversation_id(conversation_id)
        self.websocket: WebSocket | None = None
        self.feedback_result: str | None = None
        self.feedback_submitted_at: float | None = None  # 用户提交反馈的时间
        # 对话记录里展示的图片缩略图，每项含 name 和 thumbnail（data URL）
        self.feedback_images: list[dict[str, str]] = []
        self.images: list[dict] = []
        self.settings: dict[str, Any] = {}  # 图片设定
        self.feedback_completed = threading.Event()
        self._cleanup_done = False  # 防止重复清理

        # 新增：会话状态管理
        self.status = SessionStatus.WAITING
        self.status_message = "等待用户反馈"
        # 统一使用 time.time() 以避免时间基准不一致
        self.created_at = time.time()
        self.last_activity = self.created_at

        # 新增：自动清理配置
        self.auto_cleanup_delay = auto_cleanup_delay  # 自动清理延迟时间（秒）
        self.max_idle_time = max_idle_time  # 最大空闲时间（秒）
        self.cleanup_timer: threading.Timer | None = None
        self.cleanup_callbacks: list[Callable[..., None]] = []  # 清理回调函数列表

        # 新增：清理统计
        self.cleanup_stats: dict[str, Any] = {
            "cleanup_count": 0,
            "last_cleanup_time": None,
            "cleanup_reason": None,
            "cleanup_duration": 0.0,
            "memory_freed": 0,
            "resources_cleaned": 0,
        }

        # 新增：活跃标签页管理
        self.active_tabs: dict[str, Any] = {}

        # 确保临时目录存在
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # 获取资源管理器实例
        self.resource_manager = get_resource_manager()

        # 启动自动清理定时器
        self._schedule_auto_cleanup()

        debug_log(
            f"会话 {self.session_id} 初始化完成，自动清理延迟: {auto_cleanup_delay}秒，最大空闲: {max_idle_time}秒"
        )

    def get_message_code(self, key: str) -> str:
        """
        获取消息代码

        Args:
            key: 消息 key

        Returns:
            消息代码（前端据此识别通知类型）
        """
        return get_message_code(key)

    def next_step(self, message: str | None = None) -> bool:
        """进入下一个状态 - 单向流转，不可倒退"""
        old_status = self.status

        # 定义状态流转路径
        next_status_map = {
            SessionStatus.WAITING: SessionStatus.ACTIVE,
            SessionStatus.ACTIVE: SessionStatus.FEEDBACK_SUBMITTED,
            SessionStatus.FEEDBACK_SUBMITTED: SessionStatus.COMPLETED,
            SessionStatus.COMPLETED: None,  # 终态
            SessionStatus.ERROR: None,  # 终态
            SessionStatus.TIMEOUT: None,  # 终态
            SessionStatus.EXPIRED: None,  # 终态
        }

        next_status = next_status_map.get(self.status)

        if next_status is None:
            debug_log(
                f"⚠️ 会话 {self.session_id} 已处于终态 {self.status.value}，无法进入下一步"
            )
            return False

        # 执行状态转换
        self.status = next_status
        if message:
            self.status_message = message
        else:
            # 默认消息
            default_messages = {
                SessionStatus.ACTIVE: "会话已启动",
                SessionStatus.FEEDBACK_SUBMITTED: "用户已提交反馈",
                SessionStatus.COMPLETED: "会话已完成",
            }
            self.status_message = default_messages.get(next_status, "状态已更新")

        self.last_activity = time.time()

        # 如果会话变为已提交状态，重置清理定时器
        if next_status == SessionStatus.FEEDBACK_SUBMITTED:
            self._schedule_auto_cleanup()

        debug_log(
            f"✅ 会话 {self.session_id} 状态流转: {old_status.value} → {next_status.value} - {self.status_message}"
        )
        return True

    def set_error(self, message: str = "会话发生错误") -> bool:
        """设置错误状态（特殊方法，可从任何状态进入）"""
        old_status = self.status
        self.status = SessionStatus.ERROR
        self.status_message = message
        self.last_activity = time.time()

        debug_log(
            f"❌ 会话 {self.session_id} 设置为错误状态: {old_status.value} → {self.status.value} - {message}"
        )
        return True

    def set_expired(self, message: str = "会话已过期") -> bool:
        """设置过期状态（特殊方法，可从任何状态进入）"""
        old_status = self.status
        self.status = SessionStatus.EXPIRED
        self.status_message = message
        self.last_activity = time.time()

        debug_log(
            f"⏰ 会话 {self.session_id} 设置为过期状态: {old_status.value} → {self.status.value} - {message}"
        )
        return True

    def can_proceed(self) -> bool:
        """检查是否可以进入下一步"""
        return self.status in [SessionStatus.WAITING, SessionStatus.FEEDBACK_SUBMITTED]

    def is_terminal(self) -> bool:
        """检查是否处于终态"""
        return self.status in [
            SessionStatus.COMPLETED,
            SessionStatus.ERROR,
            SessionStatus.TIMEOUT,
            SessionStatus.EXPIRED,
        ]

    @staticmethod
    def normalize_conversation_id(raw: str | None) -> str:
        """整理 Cursor 钩子填入的对话标识

        Args:
            raw: 调用方传入的原始值，没有钩子时为空，AI 自己乱填时可能带空白或奇怪的字符。

        Returns:
            去掉首尾空白后，由字母、数字、下划线和连字符组成（最长 128 位）的标识；不符合时为空串。
        """
        value = (raw or "").strip()
        return value if CONVERSATION_ID_PATTERN.fullmatch(value) else ""

    def get_status_info(self) -> dict[str, Any]:
        """获取会话状态信息"""
        return {
            "status": self.status.value,
            "message": self.status_message,
            "feedback_completed": self.feedback_completed.is_set(),
            "has_websocket": self.websocket is not None,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "project_directory": self.project_directory,
            "summary": self.summary,
            "session_id": self.session_id,
        }

    def is_awaiting_feedback(self) -> bool:
        """AI 的调用是否还在等这个会话的回复

        用户此刻发来的消息：在等就是对当前提问的回复，没在等就进入消息队列。
        """
        return (
            self.status == SessionStatus.WAITING
            and not self.feedback_completed.is_set()
        )

    def close_with_queued_messages(self, count: int) -> None:
        """排队的用户消息已随这次调用送达，之后没有人再等这个会话的回复

        Args:
            count: 送达的消息数量
        """
        self.next_step(f"已送达 {count} 条排队消息，等待下次 MCP 调用")
        self.feedback_completed.set()

    def is_active(self) -> bool:
        """检查会话是否活跃"""
        return self.status in [
            SessionStatus.WAITING,
            SessionStatus.ACTIVE,
            SessionStatus.FEEDBACK_SUBMITTED,
        ]

    def is_expired(self) -> bool:
        """检查会话是否已过期"""
        # 统一使用 time.time()
        current_time = time.time()

        # 检查是否超过最大空闲时间
        idle_time = current_time - self.last_activity
        if idle_time > self.max_idle_time:
            debug_log(
                f"会话 {self.session_id} 空闲时间过长: {idle_time:.1f}秒 > {self.max_idle_time}秒"
            )
            return True

        # 检查是否处于已过期状态
        if self.status == SessionStatus.EXPIRED:
            return True

        # 检查是否处于错误或超时状态且超过一定时间
        if self.status in [SessionStatus.ERROR, SessionStatus.TIMEOUT]:
            error_time = current_time - self.last_activity
            if error_time > 300:  # 错误状态超过5分钟视为过期
                debug_log(
                    f"会话 {self.session_id} 错误状态时间过长: {error_time:.1f}秒"
                )
                return True

        return False

    def get_age(self) -> float:
        """获取会话年龄（秒）"""
        current_time = time.time()
        return current_time - self.created_at

    def get_idle_time(self) -> float:
        """获取会话空闲时间（秒）"""
        current_time = time.time()
        return current_time - self.last_activity

    def _schedule_auto_cleanup(self):
        """安排自动清理定时器"""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        def auto_cleanup():
            """自动清理回调"""
            try:
                if not self._cleanup_done and self.is_expired():
                    debug_log(f"会话 {self.session_id} 触发自动清理（过期）")
                    # 使用异步方式执行清理
                    import asyncio

                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(
                            self._cleanup_resources_enhanced(CleanupReason.EXPIRED)
                        )
                    except RuntimeError:
                        # 如果没有事件循环，使用同步清理
                        self._cleanup_sync_enhanced(CleanupReason.EXPIRED)
                else:
                    # 如果还没过期，重新安排定时器
                    self._schedule_auto_cleanup()
            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"session_id": self.session_id, "operation": "自动清理"},
                    error_type=ErrorType.SYSTEM,
                )
                debug_log(f"自动清理失败 [错误ID: {error_id}]: {e}")

        self.cleanup_timer = threading.Timer(self.auto_cleanup_delay, auto_cleanup)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        debug_log(
            f"会话 {self.session_id} 自动清理定时器已设置，{self.auto_cleanup_delay}秒后触发"
        )

    def extend_cleanup_timer(self, additional_time: int | None = None):
        """延长清理定时器"""
        if additional_time is None:
            additional_time = self.auto_cleanup_delay

        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        self.cleanup_timer = threading.Timer(additional_time, lambda: None)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()

        debug_log(f"会话 {self.session_id} 清理定时器已延长 {additional_time} 秒")

    def add_cleanup_callback(self, callback: Callable[..., None]):
        """添加清理回调函数"""
        if callback not in self.cleanup_callbacks:
            self.cleanup_callbacks.append(callback)
            debug_log(f"会话 {self.session_id} 添加清理回调函数")

    def remove_cleanup_callback(self, callback: Callable[..., None]):
        """移除清理回调函数"""
        if callback in self.cleanup_callbacks:
            self.cleanup_callbacks.remove(callback)
            debug_log(f"会话 {self.session_id} 移除清理回调函数")

    def get_cleanup_stats(self) -> dict[str, Any]:
        """获取清理统计信息"""
        stats = self.cleanup_stats.copy()
        stats.update(
            {
                "session_id": self.session_id,
                "age": self.get_age(),
                "idle_time": self.get_idle_time(),
                "is_expired": self.is_expired(),
                "is_active": self.is_active(),
                "status": self.status.value,
                "has_websocket": self.websocket is not None,
                "images_count": len(self.images),
            }
        )
        return stats

    async def wait_for_feedback(self, timeout: int = 600) -> dict[str, Any]:
        """
        等待用户反馈，包含图片，支援超时自动清理

        Args:
            timeout: 超时时间（秒）

        Returns:
            dict: 反馈结果
        """
        try:
            # 使用比 MCP 超时稍短的时间（提前处理，避免边界竞争）
            # 对于短超时（<30秒），提前1秒；对于长超时，提前5秒
            if timeout <= 30:
                actual_timeout = max(timeout - 1, 5)  # 短超时提前1秒，最少5秒
            else:
                actual_timeout = timeout - 5  # 长超时提前5秒
            debug_log(
                f"会话 {self.session_id} 开始等待反馈，超时时间: {actual_timeout} 秒（原始: {timeout} 秒）"
            )

            loop = asyncio.get_event_loop()

            def wait_in_thread():
                return self.feedback_completed.wait(actual_timeout)

            completed = await loop.run_in_executor(None, wait_in_thread)

            if completed:
                debug_log(f"会话 {self.session_id} 收到用户反馈")
                return {
                    "interactive_feedback": self.feedback_result or "",
                    "images": self.images,
                    "settings": self.settings,
                }
            # 超时了，立即清理资源
            debug_log(
                f"会话 {self.session_id} 在 {actual_timeout} 秒后超时，开始清理资源..."
            )
            await self._cleanup_resources_on_timeout()
            raise TimeoutError(
                f"等待用户反馈超时（{actual_timeout}秒），界面已自动关闭"
            )

        except asyncio.CancelledError:
            # AI 取消了这次调用，没有人再等回复：标记为已结束，之后用户发的消息改走队列
            debug_log(f"会话 {self.session_id} 的等待被取消")
            self.status = SessionStatus.COMPLETED
            self.status_message = "AI 已取消这次调用"
            self.feedback_completed.set()
            raise

        except Exception as e:
            # 任何异常都要确保清理资源
            debug_log(f"会话 {self.session_id} 发生异常: {e}")
            await self._cleanup_resources_on_timeout()
            raise

    async def submit_feedback(
        self,
        feedback: str,
        images: list[dict[str, Any]],
        settings: dict[str, Any] | None = None,
    ):
        """
        提交反馈和图片

        Args:
            feedback: 文字反馈
            images: 图片列表
            settings: 图片设定（可选）
        """
        self.feedback_result = feedback
        self.feedback_submitted_at = time.time()
        # 先设置设定，再处理图片（因为处理图片时需要用到设定）
        self.settings = settings or {}
        self.images = self.process_images(
            images, self.settings.get("image_size_limit", MAX_IMAGE_SIZE)
        )
        self.feedback_images = self.detach_thumbnails(self.images)

        # 进入下一步：等待中 → 已提交反馈
        self.next_step("已送出反馈，等待下次 MCP 调用")

        self.feedback_completed.set()

        # 发送反馈已收到的消息给前端
        if self.websocket:
            try:
                await self.websocket.send_json(
                    {
                        "type": "notification",
                        "code": self.get_message_code("FEEDBACK_SUBMITTED"),
                        "severity": "success",
                        "status": self.status.value,
                    }
                )

            except Exception as e:
                debug_log(f"发送反馈确认失败: {e}")

        # 重构：不再自动关闭 WebSocket，保持连接以支援页面持久性

    @staticmethod
    def process_images(
        images: list[dict], size_limit: int = MAX_IMAGE_SIZE
    ) -> list[dict]:
        """
        处理图片数据，转换为统一格式

        Args:
            images: 原始图片数据列表
            size_limit: 单张图片的大小上限（字节），0 或负数表示不限制

        Returns:
            List[dict]: 处理后的图片数据
        """
        processed_images = []

        for img in images:
            try:
                if not all(key in img for key in ["name", "data", "size"]):
                    continue

                # 检查文件大小（只有当限制大于0时才检查）
                if size_limit > 0 and img["size"] > size_limit:
                    debug_log(
                        f"图片 {img['name']} 超过大小限制 ({size_limit} bytes)，跳过"
                    )
                    continue

                # 解码 base64 数据
                if isinstance(img["data"], str):
                    try:
                        image_bytes = base64.b64decode(img["data"])
                    except Exception as e:
                        debug_log(f"图片 {img['name']} base64 解码失败: {e}")
                        continue
                else:
                    image_bytes = img["data"]

                if len(image_bytes) == 0:
                    debug_log(f"图片 {img['name']} 数据为空，跳过")
                    continue

                processed_image: dict[str, Any] = {
                    "name": img["name"],
                    "data": image_bytes,  # 保存原始 bytes 数据
                    "size": len(image_bytes),
                }

                # 前端随图片一起上传的缩略图，仅保留格式合法且大小合理的
                thumbnail = img.get("thumbnail")
                if (
                    isinstance(thumbnail, str)
                    and thumbnail.startswith("data:image/")
                    and len(thumbnail) <= MAX_THUMBNAIL_LENGTH
                ):
                    processed_image["thumbnail"] = thumbnail

                processed_images.append(processed_image)

                debug_log(
                    f"图片 {img['name']} 处理成功，大小: {len(image_bytes)} bytes"
                )

            except Exception as e:
                debug_log(f"图片处理错误: {e}")
                continue

        return processed_images

    @staticmethod
    def detach_thumbnails(images: list[dict]) -> list[dict[str, str]]:
        """从已处理的图片里取出缩略图，供对话记录展示

        缩略图单独保存：清理图片原始数据时不会被带走，也不会出现在返回给 AI 的结果里。

        Args:
            images: process_images 处理后的图片列表，其中的 thumbnail 字段会被移除

        Returns:
            缩略图列表，每项含 name 和 thumbnail（data URL）
        """
        thumbnails: list[dict[str, str]] = []
        for img in images:
            thumbnail = img.pop("thumbnail", None)
            if thumbnail:
                thumbnails.append({"name": img["name"], "thumbnail": thumbnail})
        return thumbnails

    async def _cleanup_resources_on_timeout(self):
        """超时时清理所有资源（保持向后兼容）"""
        await self._cleanup_resources_enhanced(CleanupReason.TIMEOUT)

    async def _cleanup_resources_enhanced(self, reason: CleanupReason):
        """增强的资源清理方法"""
        if self._cleanup_done:
            return  # 避免重复清理

        cleanup_start_time = time.time()
        self._cleanup_done = True

        debug_log(f"开始清理会话 {self.session_id} 的资源，原因: {reason.value}")

        # 更新清理统计
        self.cleanup_stats["cleanup_count"] += 1
        self.cleanup_stats["cleanup_reason"] = reason.value
        self.cleanup_stats["last_cleanup_time"] = datetime.now().isoformat()

        resources_cleaned = 0
        memory_before = 0

        try:
            # 记录清理前的内存使用（如果可能）
            try:
                import psutil

                process = psutil.Process()
                memory_before = process.memory_info().rss
            except:
                pass

            # 1. 取消自动清理定时器
            if self.cleanup_timer:
                self.cleanup_timer.cancel()
                self.cleanup_timer = None
                resources_cleaned += 1

            # 2. 关闭 WebSocket 连接
            if self.websocket:
                try:
                    # 根据清理原因获取消息代码
                    code_key_map = {
                        CleanupReason.TIMEOUT: "TIMEOUT_CLEANUP",
                        CleanupReason.EXPIRED: "EXPIRED_CLEANUP",
                        CleanupReason.MEMORY_PRESSURE: "MEMORY_PRESSURE_CLEANUP",
                        CleanupReason.MANUAL: "MANUAL_CLEANUP",
                        CleanupReason.ERROR: "ERROR_CLEANUP",
                        CleanupReason.SHUTDOWN: "SHUTDOWN_CLEANUP",
                    }

                    code_key = code_key_map.get(reason, "SESSION_CLEANUP")

                    await self.websocket.send_json(
                        {
                            "type": "notification",
                            "code": self.get_message_code(code_key),
                            "severity": "warning",
                            "reason": reason.value,
                        }
                    )
                    await asyncio.sleep(0.1)  # 给前端一点时间处理消息

                    # 安全关闭 WebSocket
                    await self._safe_close_websocket()
                    debug_log(f"会话 {self.session_id} WebSocket 已关闭")
                    resources_cleaned += 1
                except Exception as e:
                    debug_log(f"关闭 WebSocket 时发生错误: {e}")
                finally:
                    self.websocket = None

            # 3. 设置完成事件（防止其他地方还在等待）
            self.feedback_completed.set()

            # 4. 清理临时数据
            images_count = len(self.images)

            self.images.clear()
            self.settings.clear()

            if images_count > 0:
                resources_cleaned += images_count
                debug_log(f"清理了 {images_count} 张图片")

            # 5. 更新会话状态
            if reason == CleanupReason.EXPIRED:
                self.status = SessionStatus.EXPIRED
            elif reason == CleanupReason.TIMEOUT:
                self.status = SessionStatus.TIMEOUT
            elif reason == CleanupReason.ERROR:
                self.status = SessionStatus.ERROR
            else:
                self.status = SessionStatus.COMPLETED

            # 6. 调用清理回调函数
            for callback in self.cleanup_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(self, reason)
                    else:
                        callback(self, reason)
                except Exception as e:
                    debug_log(f"清理回调执行失败: {e}")

            # 7. 计算清理效果
            cleanup_duration = time.time() - cleanup_start_time
            memory_after = 0
            try:
                import psutil

                process = psutil.Process()
                memory_after = process.memory_info().rss
            except:
                pass

            memory_freed = max(0, memory_before - memory_after)

            # 更新清理统计
            self.cleanup_stats.update(
                {
                    "cleanup_duration": cleanup_duration,
                    "memory_freed": memory_freed,
                    "resources_cleaned": resources_cleaned,
                }
            )

            debug_log(
                f"会话 {self.session_id} 资源清理完成，耗时: {cleanup_duration:.2f}秒，"
                f"清理资源: {resources_cleaned}个，释放内存: {memory_freed}字节"
            )

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={
                    "session_id": self.session_id,
                    "cleanup_reason": reason.value,
                    "operation": "增强资源清理",
                },
                error_type=ErrorType.SYSTEM,
            )
            debug_log(
                f"清理会话 {self.session_id} 资源时发生错误 [错误ID: {error_id}]: {e}"
            )

            # 即使发生错误也要更新统计
            self.cleanup_stats["cleanup_duration"] = time.time() - cleanup_start_time

    def _cleanup_sync(self):
        """同步清理会话资源（但保留 WebSocket 连接）- 保持向后兼容"""
        self._cleanup_sync_enhanced(CleanupReason.MANUAL, preserve_websocket=True)

    def _cleanup_sync_enhanced(
        self, reason: CleanupReason, preserve_websocket: bool = False
    ):
        """增强的同步清理会话资源"""
        if self._cleanup_done and not preserve_websocket:
            return

        cleanup_start_time = time.time()
        debug_log(
            f"同步清理会话 {self.session_id} 资源，原因: {reason.value}，保留WebSocket: {preserve_websocket}"
        )

        # 更新清理统计
        self.cleanup_stats["cleanup_count"] += 1
        self.cleanup_stats["cleanup_reason"] = reason.value
        self.cleanup_stats["last_cleanup_time"] = datetime.now().isoformat()

        resources_cleaned = 0
        memory_before = 0

        try:
            # 记录清理前的内存使用
            try:
                import psutil

                process = psutil.Process()
                memory_before = process.memory_info().rss
            except:
                pass

            # 1. 取消自动清理定时器
            if self.cleanup_timer:
                self.cleanup_timer.cancel()
                self.cleanup_timer = None
                resources_cleaned += 1

            # 2. 清理临时数据
            images_count = len(self.images)

            if not preserve_websocket:
                self.images.clear()
                self.settings.clear()
                resources_cleaned += images_count

            # 3. 设置完成事件
            if not preserve_websocket:
                self.feedback_completed.set()

            # 4. 更新状态
            if not preserve_websocket:
                if reason == CleanupReason.EXPIRED:
                    self.status = SessionStatus.EXPIRED
                elif reason == CleanupReason.TIMEOUT:
                    self.status = SessionStatus.TIMEOUT
                elif reason == CleanupReason.ERROR:
                    self.status = SessionStatus.ERROR
                else:
                    self.status = SessionStatus.COMPLETED

                self._cleanup_done = True

            # 5. 调用清理回调函数（同步版本）
            for callback in self.cleanup_callbacks:
                try:
                    if not asyncio.iscoroutinefunction(callback):
                        callback(self, reason)
                except Exception as e:
                    debug_log(f"同步清理回调执行失败: {e}")

            # 6. 计算清理效果
            cleanup_duration = time.time() - cleanup_start_time
            memory_after = 0
            try:
                import psutil

                process = psutil.Process()
                memory_after = process.memory_info().rss
            except:
                pass

            memory_freed = max(0, memory_before - memory_after)

            # 更新清理统计
            self.cleanup_stats.update(
                {
                    "cleanup_duration": cleanup_duration,
                    "memory_freed": memory_freed,
                    "resources_cleaned": resources_cleaned,
                }
            )

            debug_log(
                f"会话 {self.session_id} 同步清理完成，耗时: {cleanup_duration:.2f}秒，"
                f"清理资源: {resources_cleaned}个，释放内存: {memory_freed}字节"
            )

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={
                    "session_id": self.session_id,
                    "cleanup_reason": reason.value,
                    "preserve_websocket": preserve_websocket,
                    "operation": "同步资源清理",
                },
                error_type=ErrorType.SYSTEM,
            )
            debug_log(
                f"同步清理会话 {self.session_id} 资源时发生错误 [错误ID: {error_id}]: {e}"
            )

            # 即使发生错误也要更新统计
            self.cleanup_stats["cleanup_duration"] = time.time() - cleanup_start_time

    def cleanup(self):
        """同步清理会话资源（保持向后兼容）"""
        self._cleanup_sync_enhanced(CleanupReason.MANUAL)

    async def _safe_close_websocket(self):
        """安全关闭 WebSocket 连接，避免事件循环冲突"""
        if not self.websocket:
            return

        try:
            # 检查连接状态
            if (
                hasattr(self.websocket, "client_state")
                and self.websocket.client_state.DISCONNECTED
            ):
                debug_log("WebSocket 已断开，跳过关闭操作")
                return

            # 尝试正常关闭
            await asyncio.wait_for(
                self.websocket.close(code=1000, reason="会话清理"), timeout=2.0
            )
            debug_log(f"会话 {self.session_id} WebSocket 已正常关闭")

        except TimeoutError:
            debug_log(f"会话 {self.session_id} WebSocket 关闭超时")
        except RuntimeError as e:
            if "attached to a different loop" in str(e):
                debug_log(
                    f"会话 {self.session_id} WebSocket 事件循环冲突，忽略关闭错误: {e}"
                )
            else:
                debug_log(f"会话 {self.session_id} WebSocket 关闭时发生运行时错误: {e}")
        except Exception as e:
            debug_log(f"会话 {self.session_id} 关闭 WebSocket 时发生未知错误: {e}")
