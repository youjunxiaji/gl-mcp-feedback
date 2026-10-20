#!/usr/bin/env python3
"""
对话房间
========

一个对话一个房间：房间持有这个对话自己的会话、排队消息和页面连接。
对话 B 的新提问不会替换对话 A 的会话，排队的消息也只在本房间里取走。
"""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from starlette.websockets import WebSocketState

from ...debug import web_debug_log as debug_log
from ...utils.error_handler import ErrorHandler, ErrorType
from ..constants import get_message_code
from .feedback_session import (
    MAX_IMAGE_SIZE,
    CleanupReason,
    SessionStatus,
    WebFeedbackSession,
)
from .message_queue import MessageQueue, QueueFullError
from .queue_files import QueueMarker, save_message_images


# 没有对话标识的调用共用的房间
DEFAULT_ROOM_ID = "default"

# 到了这些状态，会话不会再变化
TERMINAL_STATES = frozenset(
    {
        SessionStatus.COMPLETED,
        SessionStatus.ERROR,
        SessionStatus.TIMEOUT,
        SessionStatus.EXPIRED,
    }
)


class Room:
    """一个对话的房间

    Attributes:
        room_id: 房间标识，等于对话标识；没有对话标识的调用共用 DEFAULT_ROOM_ID。
        sessions: 房间里的会话，按创建顺序排列，最多 MAX_SESSIONS 个。
        current_session: 最新一轮的会话，页面的 WebSocket 和用户的回复都交给它。
        message_queue: 用户在 AI 没有等回复时排队发送的消息。
        marker: 队列里有排队中的消息时写下的标记文件，钩子靠它判断要不要来取；没有钩子的场景为 None。
        pending_session_update: 新会话创建时页面还没连上，页面连上后要补发更新通知。
        created_at: 房间创建的时间戳（秒）。
    """

    # 房间里最多保留的会话数量
    MAX_SESSIONS = 10

    # 通知页面「排队的消息已送达」最多等多久（秒），页面连接异常时不拖慢钩子的响应
    NOTIFY_TIMEOUT_SECONDS = 2

    def __init__(self, room_id: str, marker: QueueMarker | None = None):
        self.room_id = room_id
        self.sessions: dict[str, WebFeedbackSession] = {}
        self.current_session: WebFeedbackSession | None = None
        self.marker = marker
        self.message_queue = MessageQueue(on_pending_changed=self._sync_marker)
        self.pending_session_update = False
        self.created_at = time.time()

    @staticmethod
    def room_id_for(conversation_id: str | None) -> str:
        """对话标识对应的房间标识

        Args:
            conversation_id: 钩子填入的对话标识，可能为空或格式不对。

        Returns:
            合法的对话标识；没有或不合法时是 DEFAULT_ROOM_ID。
        """
        return WebFeedbackSession.normalize_conversation_id(conversation_id) or (
            DEFAULT_ROOM_ID
        )

    @property
    def conversation_id(self) -> str:
        """房间对应的对话标识，默认房间没有"""
        return "" if self.room_id == DEFAULT_ROOM_ID else self.room_id

    def page_title(self) -> str:
        """浏览器标签页标题：项目目录名，对话的房间再加对话标识的前 4 位"""
        session = self.current_session
        name = (Path(session.project_directory).name if session else "") or (
            "gl-mcp-feedback"
        )
        if self.conversation_id:
            return f"{name} · {self.conversation_id[:4]}"
        return name

    def to_list_item(self) -> dict[str, Any]:
        """房间列表里的一项"""
        session = self.current_session
        last_activity = session.last_activity if session else self.created_at
        return {
            "room_id": self.room_id,
            "title": self.page_title(),
            "conversation_id": self.conversation_id,
            "project_directory": session.project_directory if session else "",
            "awaiting_feedback": bool(session and session.is_awaiting_feedback()),
            "pending_count": self.message_queue.pending_count(),
            "session_count": len(self.sessions),
            "last_activity": int(last_activity * 1000),
            "has_websocket": bool(session and session.websocket),
        }

    def create_session(
        self, project_directory: str, summary: str
    ) -> WebFeedbackSession:
        """在房间里创建新一轮的会话，它成为房间的当前会话

        房间里还没结束的旧会话标成已完成，页面的 WebSocket 连接转给新会话。

        Args:
            project_directory: 专案目录路径。
            summary: AI 工作摘要（Markdown）。

        Returns:
            新会话。
        """
        old_session = self.current_session
        old_websocket = old_session.websocket if old_session else None

        session = WebFeedbackSession(
            str(uuid.uuid4()),
            project_directory,
            summary,
            conversation_id=self.conversation_id,
        )

        if old_session:
            if old_session.status not in TERMINAL_STATES:
                old_session.status = SessionStatus.COMPLETED
                old_session.status_message = "被新会话替代，自动完成"
            # 同步清理旧会话的资源，保留 WebSocket 连接
            old_session._cleanup_sync()

        self.current_session = session
        self.sessions[session.session_id] = session
        debug_log(f"房间 {self.room_id} 创建新的活跃会话: {session.session_id}")

        if old_websocket:
            # 直接把页面的连接转给新会话，通知由 smart_open_browser 统一发送
            session.websocket = old_websocket
        else:
            # 页面还没连上，等它连上后补发会话更新通知
            self.pending_session_update = True

        self._enforce_session_capacity()
        return session

    def _enforce_session_capacity(self) -> None:
        """超过容量上限时，清理最旧的非活跃会话"""
        excess_count = len(self.sessions) - self.MAX_SESSIONS
        if excess_count <= 0:
            return

        current_session_id = (
            self.current_session.session_id if self.current_session else None
        )

        candidates = []
        for session_id, session in self.sessions.items():
            if session_id == current_session_id:
                continue

            priority = 0.0
            if session.status in TERMINAL_STATES:
                priority += 100
            elif session.status == SessionStatus.FEEDBACK_SUBMITTED:
                priority += 50
            priority += session.get_age() / 60
            candidates.append((session_id, session, priority))

        candidates.sort(key=lambda candidate: candidate[2], reverse=True)

        cleaned = 0
        for session_id, session, _ in candidates[:excess_count]:
            try:
                session._cleanup_sync_enhanced(CleanupReason.MANUAL)
                del self.sessions[session_id]
                cleaned += 1
            except Exception as e:
                debug_log(f"容量限制清理会话 {session_id} 失败: {e}")

        if cleaned > 0:
            debug_log(
                f"房间 {self.room_id} 容量限制清理了 {cleaned} 个会话，"
                f"当前会话数: {len(self.sessions)}/{self.MAX_SESSIONS}"
            )

    def get_session(self, session_id: str) -> WebFeedbackSession | None:
        """按会话标识取房间里的会话"""
        return self.sessions.get(session_id)

    async def enqueue_user_message(
        self,
        session: WebFeedbackSession,
        feedback: str,
        images: list[dict[str, Any]],
        settings: dict[str, Any] | None = None,
    ) -> None:
        """AI 没有在等回复时，把用户的消息放进房间的队列，并通知前端结果

        每条消息都会收到一条通知：已排队（QUEUE_MESSAGE_QUEUED，带 message_id）、
        队列已满（QUEUE_FULL）或没有可排队的内容（QUEUE_MESSAGE_EMPTY，没有文字且图片都被过滤掉了）。

        Args:
            session: 收到消息的当前会话，用它的 WebSocket 回复通知。
            feedback: 消息文字。
            images: 前端上传的图片列表。
            settings: 图片设定（可选）。
        """
        size_limit = (settings or {}).get("image_size_limit", MAX_IMAGE_SIZE)
        processed = WebFeedbackSession.process_images(images, size_limit)
        thumbnails = WebFeedbackSession.detach_thumbnails(processed)
        text = feedback.strip()

        notification: dict[str, Any]
        if not text and not processed:
            debug_log("没有任何内容的排队消息，不入队")
            notification = {
                "type": "notification",
                "code": get_message_code("QUEUE_MESSAGE_EMPTY"),
                "severity": "warning",
            }
        else:
            try:
                message = self.message_queue.enqueue(text, processed, thumbnails)
                notification = {
                    "type": "notification",
                    "code": get_message_code("QUEUE_MESSAGE_QUEUED"),
                    "severity": "success",
                    "message_id": message.message_id,
                }
            except QueueFullError:
                notification = {
                    "type": "notification",
                    "code": get_message_code("QUEUE_FULL"),
                    "severity": "warning",
                }

        if session.websocket:
            try:
                await session.websocket.send_json(notification)
            except Exception as e:
                debug_log(f"发送排队结果通知失败: {e}")

    async def deliver_queued_messages(
        self, session: WebFeedbackSession
    ) -> dict[str, Any] | None:
        """AI 发起新一轮提问时，取走房间里用户排队的消息

        Args:
            session: AI 刚创建的新会话，排队的消息会标记为随它送达。

        Returns:
            interactive_feedback 的返回结构；没有排队消息时返回 None，调用方照常等用户回复。
        """
        messages = self.message_queue.deliver_with_question(session.session_id)
        if not messages:
            return None

        # 这一轮不需要用户回复，已打开的页面静默刷新，没有页面时也不用再提醒
        session.close_with_queued_messages(len(messages))
        self.pending_session_update = False
        await self.notify_existing_tab_to_refresh(silent=True)
        return MessageQueue.build_result(messages)

    async def deliver_for_tool_call(self) -> str | None:
        """AI 调用完一次工具后（由钩子来取），取走房间里用户排队的消息

        Returns:
            附在工具结果后面的说明文字（消息文字，图片以本地文件路径给出）；没有排队消息时返回 None。
        """
        messages = self.message_queue.deliver_with_tool_call()
        if not messages:
            return None

        # 消息已经标记为送达，之后任何一步都不能让钩子拿不到说明文字，所以通知页面放在最后，并限时
        image_paths = await asyncio.to_thread(save_message_images, messages)
        context = MessageQueue.build_tool_call_context(messages, image_paths)
        await self.notify_queue_delivered(len(messages))
        return context

    async def notify_queue_delivered(self, count: int) -> None:
        """通知房间的页面：排队的消息已经被 AI 取走，页面据此把「排队中」翻成「已送达」

        Args:
            count: 取走的消息条数。
        """
        session = self.current_session
        if not session or not session.websocket:
            return
        try:
            await asyncio.wait_for(
                session.websocket.send_json(
                    {"type": "queue_delivered", "count": count}
                ),
                timeout=self.NOTIFY_TIMEOUT_SECONDS,
            )
        except Exception as e:
            debug_log(f"发送排队消息送达通知失败: {e}")

    def _sync_marker(self) -> None:
        """队列里排队中的消息数量变了：让标记文件和队列一致"""
        if self.marker is not None:
            self.marker.sync(self.message_queue.pending_count)

    async def notify_existing_tab_to_refresh(self, *, silent: bool = False) -> bool:
        """通知房间已打开的页面刷新显示新会话内容

        Args:
            silent: 为 True 时页面只刷新对话记录，不响提示音、不弹桌面通知。

        Returns:
            True 表示成功发送，False 表示失败。
        """
        session = self.current_session
        if not session or not session.websocket:
            debug_log("没有活跃的WebSocket连接，无法发送刷新通知")
            return False

        try:
            await session.websocket.send_json(
                {
                    "type": "session_updated",
                    "action": "new_session_created",
                    "messageCode": "session.created",
                    "silent": silent,
                    "session_info": {
                        "session_id": session.session_id,
                        "project_directory": session.project_directory,
                        "summary": session.summary,
                        "status": session.status.value,
                    },
                }
            )
            debug_log(
                f"已向房间 {self.room_id} 的页面发送刷新通知: {session.session_id}"
            )

            # 简单等待一下让消息发送完成
            await asyncio.sleep(0.2)
            return True
        except Exception as e:
            debug_log(f"发送刷新通知失败: {e}")
            return False

    async def check_active_tab(self) -> bool:
        """检查房间的页面是否还连着：连接对象存在，并且能成功发出一条 ping"""
        session = self.current_session
        websocket = session.websocket if session else None
        if session is None or websocket is None:
            return False

        try:
            if websocket.client_state != WebSocketState.CONNECTED:
                debug_log(
                    f"WebSocket 状态不是 CONNECTED，而是 {websocket.client_state}"
                )
                session.websocket = None
                return False

            await websocket.send_json({"type": "ping", "timestamp": time.time()})
            return True
        except Exception as e:
            debug_log(f"连接测试失败，清理死连接: {e}")
            session.websocket = None
            return False

    def cleanup_expired_sessions(self) -> int:
        """清理房间里已过期的会话

        Returns:
            清理的会话数量。
        """
        expired_ids = [
            session_id
            for session_id, session in self.sessions.items()
            if session.is_expired()
        ]

        cleaned = 0
        for session_id in expired_ids:
            session = self.sessions.get(session_id)
            if session is None:
                continue
            try:
                session._cleanup_sync_enhanced(CleanupReason.EXPIRED)
                del self.sessions[session_id]
                cleaned += 1
                self._forget_if_current(session_id)
            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"session_id": session_id, "operation": "清理过期会话"},
                    error_type=ErrorType.SYSTEM,
                )
                debug_log(f"清理过期会话 {session_id} 失败 [错误ID: {error_id}]: {e}")
        return cleaned

    def cleanup_sessions_by_memory_pressure(self, force: bool = False) -> int:
        """内存吃紧时清理房间里的会话

        优先级：已完成、出错、超时的 > 已提交反馈且闲置超过 5 分钟的 > 闲置超过 10 分钟的。

        Args:
            force: 为 True 时连当前会话也清，数量不设上限。

        Returns:
            清理的会话数量。
        """
        candidates: list[tuple[str, WebFeedbackSession, int]] = []
        for session_id, session in self.sessions.items():
            if (
                not force
                and self.current_session
                and session_id == self.current_session.session_id
            ):
                continue

            if session.status in (
                SessionStatus.COMPLETED,
                SessionStatus.ERROR,
                SessionStatus.TIMEOUT,
            ):
                candidates.append((session_id, session, 1))
            elif session.status == SessionStatus.FEEDBACK_SUBMITTED:
                if session.get_idle_time() > 300:
                    candidates.append((session_id, session, 2))
            elif session.get_idle_time() > 600:
                candidates.append((session_id, session, 3))

        candidates.sort(key=lambda candidate: candidate[2])
        limit = len(candidates) if force else min(len(candidates), 5)

        cleaned = 0
        for session_id, session, _ in candidates[:limit]:
            try:
                session._cleanup_sync_enhanced(CleanupReason.MEMORY_PRESSURE)
                del self.sessions[session_id]
                cleaned += 1
                self._forget_if_current(session_id)
            except Exception as e:
                error_id = ErrorHandler.log_error_with_context(
                    e,
                    context={"session_id": session_id, "operation": "内存压力清理"},
                    error_type=ErrorType.SYSTEM,
                )
                debug_log(
                    f"内存压力清理会话 {session_id} 失败 [错误ID: {error_id}]: {e}"
                )
        return cleaned

    def cleanup_all_sessions(self) -> int:
        """服务停止时清理房间里的全部会话，并删掉标记文件

        Returns:
            清理的会话数量。
        """
        if self.marker is not None:
            self.marker.clear()
        count = len(self.sessions)
        for session in list(self.sessions.values()):
            try:
                session._cleanup_sync_enhanced(CleanupReason.SHUTDOWN)
            except Exception as e:
                debug_log(f"停止服务时清理会话失败: {e}")
        self.sessions.clear()
        self.current_session = None
        return count

    def _forget_if_current(self, session_id: str) -> None:
        """被清理的会话如果是当前会话，房间就没有当前会话了"""
        if self.current_session and self.current_session.session_id == session_id:
            self.current_session = None
            debug_log(f"房间 {self.room_id} 的当前会话已被清理")
