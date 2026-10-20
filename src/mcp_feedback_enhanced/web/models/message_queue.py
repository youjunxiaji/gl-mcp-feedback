#!/usr/bin/env python3
"""
用户消息队列
============

AI 没有在等回复时（正在干活，或者上一次调用已经结束），用户发来的消息先在这里排队，
有两条路送到 AI 手里，谁先到谁取走所有排队中的消息：

- AI 调用完任意一次工具，Cursor 钩子来取，附在那次工具结果后面（``DeliveryChannel.TOOL_CALL``）
- AI 下次调用 interactive_feedback，随调用的返回值带回（``DeliveryChannel.QUESTION``）
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from ...debug import web_debug_log as debug_log


# 同时处于排队状态的消息上限，已送达的记录不占名额
MAX_PENDING_MESSAGES = 3

# 随工具调用送达的记录，每个队列最多保留最近这么多条
MAX_TOOL_CALL_RECORDS = 20


class DeliveryChannel(StrEnum):
    """排队消息送到 AI 手里的途径"""

    # 随 AI 的下一次提问（interactive_feedback 的返回值）
    QUESTION = "question"
    # 随 AI 的某次工具调用，由钩子取走，附在工具结果后面
    TOOL_CALL = "tool_call"


class QueueFullError(Exception):
    """排队中的消息已达上限"""


class MessageNotPendingError(Exception):
    """消息不存在，或者已经被 AI 取走，不能再修改或撤回"""


@dataclass
class QueuedMessage:
    """一条排队的用户消息

    Attributes:
        message_id: 消息的唯一标识。
        text: 消息文字。
        images: 图片原始数据，送达后释放。
        thumbnails: 对话记录里展示的缩略图，每项含 name 和 thumbnail，送达后仍保留。
        image_count: 附带的图片数量。
        submitted_at: 用户发送的时间戳（秒）。
        delivered_at: AI 取走的时间戳（秒），排队中为 None。
        delivered_via: 随什么途径送达，排队中为 None。
        delivered_session_id: 随提问送达时，是哪个会话；其余情况为 None。
    """

    message_id: str
    text: str
    images: list[dict[str, Any]]
    thumbnails: list[dict[str, str]]
    image_count: int
    submitted_at: float
    delivered_at: float | None = None
    delivered_via: DeliveryChannel | None = None
    delivered_session_id: str | None = None

    @property
    def is_pending(self) -> bool:
        """是否还在排队，没有被 AI 取走"""
        return self.delivered_at is None


class MessageQueue:
    """线程安全的用户消息队列

    Web 线程（WebSocket 与接口）负责写入、修改和撤回，MCP 线程在 AI 发起新一轮提问时取走，
    钩子的请求由 Web 线程处理，也会取走。
    数据只放在内存里，服务重启后清空。
    """

    def __init__(
        self,
        max_pending: int = MAX_PENDING_MESSAGES,
        on_pending_changed: Callable[[], None] | None = None,
    ):
        """
        Args:
            max_pending: 同时排队的消息上限。
            on_pending_changed: 排队中的消息数量可能变了（加入、撤回、取走）之后调用，
                在队列的锁之外执行，出错只记日志，不影响队列操作。
        """
        self.max_pending = max_pending
        self._on_pending_changed = on_pending_changed
        self._messages: list[QueuedMessage] = []
        self._lock = threading.Lock()

    def pending_count(self) -> int:
        """排队中的消息数量"""
        with self._lock:
            return sum(1 for message in self._messages if message.is_pending)

    def enqueue(
        self,
        text: str,
        images: list[dict[str, Any]],
        thumbnails: list[dict[str, str]],
    ) -> QueuedMessage:
        """加入一条排队消息

        Args:
            text: 消息文字。
            images: 已处理好的图片列表，每项含 name、data、size。
            thumbnails: 图片对应的缩略图，每项含 name 和 thumbnail。

        Returns:
            新加入的消息。

        Raises:
            QueueFullError: 排队中的消息已达上限。
        """
        with self._lock:
            pending = sum(1 for message in self._messages if message.is_pending)
            if pending >= self.max_pending:
                raise QueueFullError(f"排队的消息已达上限 {self.max_pending} 条")
            message = QueuedMessage(
                message_id=uuid.uuid4().hex,
                text=text,
                images=images,
                thumbnails=thumbnails,
                image_count=len(images),
                submitted_at=time.time(),
            )
            self._messages.append(message)
        debug_log(f"用户消息已排队: {message.message_id}，排队中 {pending + 1} 条")
        self._notify_pending_changed()
        return message

    def update_text(self, message_id: str, text: str) -> None:
        """修改一条排队中的消息的文字

        Args:
            message_id: 消息标识。
            text: 新的文字。

        Raises:
            MessageNotPendingError: 消息不存在，或已经被 AI 取走。
            ValueError: 新文字为空，并且消息没有附带图片。
        """
        text = text.strip()
        with self._lock:
            message = self._find_pending(message_id)
            if not text and message.image_count == 0:
                raise ValueError("消息内容不能为空")
            message.text = text
        debug_log(f"排队消息已修改: {message_id}")

    def cancel(self, message_id: str) -> None:
        """撤回一条排队中的消息

        Args:
            message_id: 消息标识。

        Raises:
            MessageNotPendingError: 消息不存在，或已经被 AI 取走。
        """
        with self._lock:
            message = self._find_pending(message_id)
            self._messages.remove(message)
        debug_log(f"排队消息已撤回: {message_id}")
        self._notify_pending_changed()

    def deliver_with_question(self, session_id: str) -> list[QueuedMessage]:
        """AI 发起新一轮提问时，取走所有排队中的消息，标记为随这个会话送达

        队列里的记录只保留缩略图，图片原始数据交给调用方。

        Args:
            session_id: 本次送达所在的会话。

        Returns:
            取走的消息，按发送顺序排列，图片原始数据完整；没有排队消息时为空列表。
        """
        return self._deliver(DeliveryChannel.QUESTION, session_id)

    def deliver_with_tool_call(self) -> list[QueuedMessage]:
        """AI 调用完一次工具后，取走所有排队中的消息，标记为随工具调用送达

        队列里的记录只保留缩略图，图片原始数据交给调用方；
        随工具调用送达的记录只保留最近 MAX_TOOL_CALL_RECORDS 条。

        Returns:
            取走的消息，按发送顺序排列，图片原始数据完整；没有排队消息时为空列表。
        """
        return self._deliver(DeliveryChannel.TOOL_CALL, None)

    def _deliver(
        self, channel: DeliveryChannel, session_id: str | None
    ) -> list[QueuedMessage]:
        """取走所有排队中的消息，标记送达的时间和途径

        Args:
            channel: 送达的途径。
            session_id: 随提问送达时是哪个会话，随工具调用送达时为 None。

        Returns:
            取走的消息副本，按发送顺序排列，已带上送达标记，图片原始数据完整。
        """
        with self._lock:
            now = time.time()
            delivered: list[QueuedMessage] = []
            for message in self._messages:
                if not message.is_pending:
                    continue
                message.delivered_at = now
                message.delivered_via = channel
                message.delivered_session_id = session_id
                delivered.append(replace(message))
                message.images = []
            self._trim_tool_call_records()
        if delivered:
            debug_log(f"取走 {len(delivered)} 条排队消息，途径: {channel.value}")
            self._notify_pending_changed()
        return delivered

    def visible_messages(self, retained_session_ids: set[str]) -> list[QueuedMessage]:
        """返回对话记录里要展示的消息

        排队中的消息一定展示；随工具调用送达的记录展示最近 MAX_TOOL_CALL_RECORDS 条；
        随提问送达的消息只在它随之送达的会话还保留在记录里时展示，其余的同时丢弃。

        Args:
            retained_session_ids: 仍保留在对话记录里的会话标识。

        Returns:
            按发送顺序排列的消息副本，不含图片原始数据。
        """
        with self._lock:
            self._messages = [
                message
                for message in self._messages
                if message.delivered_via is not DeliveryChannel.QUESTION
                or message.delivered_session_id in retained_session_ids
            ]
            return [replace(message, images=[]) for message in self._messages]

    @staticmethod
    def build_result(messages: list[QueuedMessage]) -> dict[str, Any]:
        """把取走的消息整理成 interactive_feedback 的返回结构

        Args:
            messages: deliver_with_question 取走的消息。

        Returns:
            含 interactive_feedback（文字）、images（按消息顺序排列的图片）和 settings 的字典。
        """
        lines = [
            f"（你工作期间，用户追加了 {len(messages)} 条消息，按发送顺序列出。"
            "它们不是对你本次 summary 的回答，请按顺序当作新指令处理。）"
        ]
        images: list[dict[str, Any]] = []
        for index, message in enumerate(messages, 1):
            header = f"[{index}] {MessageQueue._format_sent_time(message)}"
            if message.images:
                first = len(images) + 1
                last = len(images) + len(message.images)
                span = f"第 {first} 张" if first == last else f"第 {first}-{last} 张"
                header += f"（附 {len(message.images)} 张图片，是返回内容里的{span}）"
            lines += ["", header, message.text or "（无文字）"]
            images.extend(message.images)
        return {
            "interactive_feedback": "\n".join(lines),
            "images": images,
            "settings": {},
        }

    @staticmethod
    def build_tool_call_context(
        messages: list[QueuedMessage], image_paths: dict[str, list[Path]]
    ) -> str:
        """把取走的消息整理成附在工具结果后面的说明

        Args:
            messages: deliver_with_tool_call 取走的消息。
            image_paths: 消息标识到图片文件路径的对应，没有图片的消息不在其中。

        Returns:
            交给 AI 的说明文字：先是一段提示，再按发送顺序列出每条 ``[序号] HH:MM`` 加文字，
            图片文件的路径列在对应消息的文字下面。
        """
        lines = [
            f"（你工作期间，用户追加了 {len(messages)} 条消息，按发送顺序列出。"
            "请按顺序当作新指令处理，处理完再调用 interactive_feedback 汇报。）"
        ]
        for index, message in enumerate(messages, 1):
            header = f"[{index}] {MessageQueue._format_sent_time(message)}"
            paths = image_paths.get(message.message_id, [])
            if paths:
                header += f"（附 {len(paths)} 张图片，已存成本地文件，用 Read 查看）"
            lines += ["", header, message.text or "（无文字）"]
            lines += [f"图片：{path}" for path in paths]
        return "\n".join(lines)

    @staticmethod
    def _format_sent_time(message: QueuedMessage) -> str:
        """消息发送的时间，HH:MM"""
        return time.strftime("%H:%M", time.localtime(message.submitted_at))

    def _notify_pending_changed(self) -> None:
        """通知排队中的消息数量可能变了；必须在锁之外调用"""
        if self._on_pending_changed is None:
            return
        try:
            self._on_pending_changed()
        except Exception as e:
            debug_log(f"排队数量变化的回调出错: {e}")

    def _trim_tool_call_records(self) -> None:
        """在已持有锁的情况下，丢弃超出保留数量的、最早的随工具调用送达的记录"""
        records = [
            message
            for message in self._messages
            if message.delivered_via is DeliveryChannel.TOOL_CALL
        ]
        excess = len(records) - MAX_TOOL_CALL_RECORDS
        if excess <= 0:
            return
        dropped = {id(message) for message in records[:excess]}
        self._messages = [
            message for message in self._messages if id(message) not in dropped
        ]

    def _find_pending(self, message_id: str) -> QueuedMessage:
        """在已持有锁的情况下，查找排队中的消息

        Args:
            message_id: 消息标识。

        Returns:
            排队中的消息。

        Raises:
            MessageNotPendingError: 消息不存在，或已经被 AI 取走。
        """
        for message in self._messages:
            if message.message_id == message_id and message.is_pending:
                return message
        raise MessageNotPendingError(f"消息 {message_id} 不在排队中")
