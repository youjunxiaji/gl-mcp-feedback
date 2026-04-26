#!/usr/bin/env python3
"""
Web UI 数据模型模组
==================

定义 Web UI 相关的数据结构和型别。
"""

from .feedback_result import FeedbackResult
from .feedback_session import CleanupReason, SessionStatus, WebFeedbackSession


__all__ = ["CleanupReason", "FeedbackResult", "SessionStatus", "WebFeedbackSession"]
