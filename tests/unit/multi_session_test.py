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


def test_session_title_can_be_updated_from_web(web_ui_manager):
    """Web 应能修改会话标题，便于标签分辨。"""
    session_id = web_ui_manager.create_session(
        "/tmp/project", "summary", title="旧标题"
    )

    client = TestClient(web_ui_manager.app)
    response = client.post(
        f"/api/session/{session_id}/title",
        json={"title": "新标题"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "新标题"
    assert web_ui_manager.get_session(session_id).title == "新标题"


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


@pytest.mark.asyncio
async def test_wait_timeout_returns_keepalive_request(web_ui_manager, monkeypatch):
    """等待窗口到期且还有自动额度时，应返回保活请求而不是清理会话。"""
    session_id = web_ui_manager.create_session("/tmp/project", "summary")
    session = web_ui_manager.get_session(session_id)
    assert session is not None

    session.keepalive_auto_extend_max = 1
    monkeypatch.setattr(session, "_calculate_actual_timeout", lambda timeout: 0)

    result = await session.wait_for_feedback(600)

    assert result["keepalive_request"] is True
    assert result["session_id"] == session_id
    assert result["keepalive_reason"] == "auto"
    assert session.keepalive_auto_extend_used == 1
    assert session.status == SessionStatus.WAITING
    assert not session.feedback_completed.is_set()


def test_websocket_extend_wait_adds_manual_token(web_ui_manager):
    """反馈区手动延长等待不应消耗自动延长次数。"""
    session_id = web_ui_manager.create_session("/tmp/project", "summary")
    session = web_ui_manager.get_session(session_id)
    assert session is not None

    client = TestClient(web_ui_manager.app)
    with client.websocket_connect(f"/ws/{session_id}") as websocket:
        assert websocket.receive_json()["type"] == "connection_established"
        websocket.receive_json()
        websocket.send_json({"type": "extend_wait"})
        response = websocket.receive_json()

    assert response["type"] == "wait_extended"
    assert response["session_id"] == session_id
    assert session.keepalive_manual_extend_tokens == 1
    assert session.keepalive_auto_extend_used == 0
