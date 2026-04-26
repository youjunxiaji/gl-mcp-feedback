#!/usr/bin/env python3
"""
反馈结果数据模型

定义反馈收集的数据结构，用于 Web UI 与后端的数据传输。
"""

from typing import TypedDict


class FeedbackResult(TypedDict):
    """反馈结果的型别定义"""

    interactive_feedback: str
    images: list[dict]
