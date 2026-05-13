#!/usr/bin/env python3
"""多会话 Web UI 行为测试。"""

import pytest
from fastapi.testclient import TestClient

from mcp_feedback_enhanced.web.models import SessionStatus


def test_status_routes_share_current_session_payload(web_ui_manager):
    """等待页旧接口和新接口应返回同一会话状态。"""
    client = TestClient(web_ui_manager.app)

    no_session = client.get("/api/status")
    assert no_session.status_code == 200
    assert no_session.json()["has_session"] is False

    session_id = web_ui_manager.create_session("/tmp/project", "summary", title="测试")

    legacy_status = client.get("/api/status")
    current_status = client.get("/api/session-status")

    assert legacy_status.status_code == 200
    assert current_status.status_code == 200
    assert legacy_status.json() == current_status.json()
    assert legacy_status.json()["session_info"]["session_id"] == session_id


def test_create_session_keeps_existing_session_waiting(web_ui_manager):
    """创建新会话不应完成或唤醒仍在等待的旧会话。"""
    first_id = web_ui_manager.create_session("/tmp/first", "first")
    first_session = web_ui_manager.get_session(first_id)

    second_id = web_ui_manager.create_session("/tmp/second", "second")
    second_session = web_ui_manager.get_session(second_id)

    assert first_session is not None
    assert second_session is not None
    assert web_ui_manager.get_current_session() == second_session
    assert first_session.status == SessionStatus.WAITING
    assert second_session.status == SessionStatus.WAITING
    assert not first_session.feedback_completed.is_set()
    assert not second_session.feedback_completed.is_set()


@pytest.mark.asyncio
async def test_submit_feedback_is_scoped_to_session(web_ui_manager):
    """提交某个会话的反馈不应影响其他等待会话。"""
    first_id = web_ui_manager.create_session("/tmp/first", "first")
    second_id = web_ui_manager.create_session("/tmp/second", "second")

    first_session = web_ui_manager.get_session(first_id)
    second_session = web_ui_manager.get_session(second_id)
    assert first_session is not None
    assert second_session is not None

    await first_session.submit_feedback("first feedback", [], {})

    assert first_session.feedback_completed.is_set()
    assert first_session.feedback_result == "first feedback"
    assert first_session.status == SessionStatus.FEEDBACK_SUBMITTED
    assert not second_session.feedback_completed.is_set()
    assert second_session.feedback_result is None


def test_session_detail_endpoint_returns_requested_session(web_ui_manager):
    """指定会话详情接口应按 session_id 返回，而不是总返回当前会话。"""
    first_id = web_ui_manager.create_session("/tmp/first", "first", title="第一")
    second_id = web_ui_manager.create_session("/tmp/second", "second", title="第二")

    client = TestClient(web_ui_manager.app)
    first_detail = client.get(f"/api/session/{first_id}")
    second_detail = client.get(f"/api/session/{second_id}")

    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert first_detail.json()["session_id"] == first_id
    assert first_detail.json()["title"] == "第一"
    assert second_detail.json()["session_id"] == second_id
    assert second_detail.json()["title"] == "第二"


def test_session_websocket_submits_to_bound_session(web_ui_manager):
    """session 级 WebSocket 应只提交绑定会话的反馈。"""
    first_id = web_ui_manager.create_session("/tmp/first", "first")
    second_id = web_ui_manager.create_session("/tmp/second", "second")

    first_session = web_ui_manager.get_session(first_id)
    second_session = web_ui_manager.get_session(second_id)
    assert first_session is not None
    assert second_session is not None

    client = TestClient(web_ui_manager.app)
    with client.websocket_connect(f"/ws/{first_id}") as websocket:
        assert websocket.receive_json()["type"] == "connection_established"
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "submit_feedback",
                "feedback": "first via websocket",
                "images": [],
                "settings": {},
            }
        )

    assert first_session.feedback_completed.is_set()
    assert first_session.feedback_result == "first via websocket"
    assert not second_session.feedback_completed.is_set()
