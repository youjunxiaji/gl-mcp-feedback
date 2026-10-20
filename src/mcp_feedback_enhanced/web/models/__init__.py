#!/usr/bin/env python3
"""
Web UI 数据模型模组
==================

定义 Web UI 相关的数据结构和型别。
"""

from .feedback_result import FeedbackResult
from .feedback_session import CleanupReason, SessionStatus, WebFeedbackSession
from .message_queue import (
    MessageNotPendingError,
    MessageQueue,
    QueuedMessage,
    QueueFullError,
)
from .room import DEFAULT_ROOM_ID, Room


__all__ = [
    "DEFAULT_ROOM_ID",
    "CleanupReason",
    "FeedbackResult",
    "MessageNotPendingError",
    "MessageQueue",
    "QueueFullError",
    "QueuedMessage",
    "Room",
    "SessionStatus",
    "WebFeedbackSession",
]
