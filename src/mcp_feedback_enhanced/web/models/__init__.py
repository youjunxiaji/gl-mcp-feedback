#!/usr/bin/env python3
"""
Web UI 数据模型模组
==================

定义 Web UI 相关的数据结构和型别。
"""

from .feedback_result import FeedbackResult
from .feedback_session import CleanupReason, SessionStatus, WebFeedbackSession
from .message_queue import (
    DeliveryChannel,
    MessageNotPendingError,
    MessageQueue,
    QueuedMessage,
    QueueFullError,
)
from .queue_files import QueueMarker, RoomRegistration
from .room import DEFAULT_ROOM_ID, Room, TurnStatus


__all__ = [
    "DEFAULT_ROOM_ID",
    "CleanupReason",
    "DeliveryChannel",
    "FeedbackResult",
    "MessageNotPendingError",
    "MessageQueue",
    "QueueFullError",
    "QueueMarker",
    "QueuedMessage",
    "Room",
    "RoomRegistration",
    "SessionStatus",
    "TurnStatus",
    "WebFeedbackSession",
]
