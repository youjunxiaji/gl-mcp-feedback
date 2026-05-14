#!/usr/bin/env python3
"""多会话 Web UI 行为测试。"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import mcp_feedback_enhanced.web.utils.browser as browser_utils
from mcp_feedback_enhanced.web.hub_client import validate_mcp_web_config
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


def test_create_session_can_reuse_client_session_id_and_source(web_ui_manager):
    """共享 Hub 注册时应能使用 client 生成的 session_id 和来源信息。"""
    session_id = web_ui_manager.create_session(
        "/tmp/shared-project",
        "summary",
        title="共享会话",
        session_id="client-session-id",
        source_info={
            "project_name": "shared-project",
            "project_directory": "/tmp/shared-project",
            "pid": 123,
        },
    )

    client = TestClient(web_ui_manager.app)
    response = client.get(f"/api/session/{session_id}")

    assert session_id == "client-session-id"
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "client-session-id"
    assert payload["source_project_name"] == "shared-project"
    assert payload["source_project_directory"] == "/tmp/shared-project"


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


def test_hub_health_requires_token_when_configured(web_ui_manager):
    """Managed Hub API 配置 token 后应拒绝未授权请求。"""
    web_ui_manager.hub_token = "secret-token"  # noqa: S105
    web_ui_manager.is_hub = True
    client = TestClient(web_ui_manager.app)

    unauthorized = client.get("/api/hub/health")
    authorized = client.get(
        "/api/hub/health", headers={"Authorization": "Bearer secret-token"}
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["hub"] is True


def test_page_heartbeat_marks_browser_presence(web_ui_manager):
    """等待页没有 WebSocket 时，也应能标记同一个页面仍存活。"""
    client = TestClient(web_ui_manager.app)

    response = client.post("/api/page-heartbeat")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["session_count"] == 0
    assert web_ui_manager._page_last_seen_at > 0


def test_hub_session_registration_includes_source(web_ui_manager):
    """Hub session 注册接口应创建会话并返回来源项目字段。"""
    web_ui_manager.hub_token = "secret-token"  # noqa: S105
    client = TestClient(web_ui_manager.app)

    response = client.post(
        "/api/hub/sessions",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "session_id": "remote-session-id",
            "project_directory": "/tmp/remote-project",
            "summary": "remote summary",
            "title": "远程会话",
            "timeout": 600,
            "source": {
                "project_name": "remote-project",
                "project_directory": "/tmp/remote-project",
                "pid": 456,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "remote-session-id"
    assert payload["session_info"]["source_project_name"] == "remote-project"
    assert web_ui_manager.get_session("remote-session-id") is not None


@pytest.mark.asyncio
async def test_hub_wait_returns_submitted_feedback(web_ui_manager):
    """Hub long-poll wait 应返回已提交的反馈结果。"""
    web_ui_manager.hub_token = "secret-token"  # noqa: S105
    session_id = web_ui_manager.create_session(
        "/tmp/remote", "summary", session_id="wait-session-id"
    )
    session = web_ui_manager.get_session(session_id)
    assert session is not None
    await session.submit_feedback("remote feedback", [], {})

    client = TestClient(web_ui_manager.app)
    response = client.post(
        f"/api/hub/sessions/{session_id}/wait",
        headers={"Authorization": "Bearer secret-token"},
        json={"timeout": 600},
    )

    assert response.status_code == 200
    assert response.json()["interactive_feedback"] == "remote feedback"


@pytest.mark.asyncio
async def test_smart_open_browser_suppresses_immediate_duplicate(web_ui_manager):
    """并发注册时，页面尚未建立 WebSocket 也不应重复打开浏览器。"""
    opened_urls = []

    async def no_active_tabs():
        return False

    async def refresh_existing_tabs():
        return True

    web_ui_manager._check_active_tabs = no_active_tabs
    web_ui_manager.notify_existing_tab_to_refresh = refresh_existing_tabs
    web_ui_manager.open_browser = opened_urls.append

    first = await web_ui_manager.smart_open_browser("http://127.0.0.1:9790")
    second = await web_ui_manager.smart_open_browser("http://127.0.0.1:9790")

    assert first is False
    assert second is True
    assert opened_urls == ["http://127.0.0.1:9790"]


@pytest.mark.asyncio
async def test_smart_open_browser_serializes_concurrent_opens(web_ui_manager):
    """并发注册同时请求打开页面时，也只能真正打开一次。"""
    opened_urls = []

    async def no_active_tabs():
        return False

    web_ui_manager._check_active_tabs = no_active_tabs
    web_ui_manager.open_browser = opened_urls.append

    results = await asyncio.gather(
        web_ui_manager.smart_open_browser("http://127.0.0.1:9790"),
        web_ui_manager.smart_open_browser("http://127.0.0.1:9790"),
    )

    assert sorted(results) == [False, True]
    assert opened_urls == ["http://127.0.0.1:9790"]


@pytest.mark.asyncio
async def test_smart_open_browser_uses_page_heartbeat(web_ui_manager):
    """已有等待页 heartbeat 时，注册新 session 不应再打开新页面。"""
    opened_urls = []

    web_ui_manager._page_last_seen_at = time.time()
    web_ui_manager.open_browser = opened_urls.append

    result = await web_ui_manager.smart_open_browser("http://127.0.0.1:9790")

    assert result is True
    assert opened_urls == []


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


def test_shared_config_validation_warns_for_missing_port(monkeypatch):
    """shared 模式配置不完整时应生成 AI 可读提示。"""
    monkeypatch.setenv("MCP_WEB_SHARE_MODE", "shared")
    monkeypatch.delenv("MCP_WEB_PORT", raising=False)
    monkeypatch.setenv("MCP_WEB_PORT_AUTO_CLEANUP", "true")

    warnings = validate_mcp_web_config()

    assert any("MCP_WEB_PORT" in warning for warning in warnings)
    assert any("MCP_WEB_PORT_AUTO_CLEANUP" in warning for warning in warnings)


def test_wsl_browser_open_uses_single_successful_cmd_start(monkeypatch):
    """cmd.exe start 成功时不应因输出解码或 fallback 再打开一次。"""
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setattr(browser_utils.subprocess, "run", fake_run)

    browser_utils.open_browser_in_wsl("http://127.0.0.1:9790")

    assert len(calls) == 1
    assert calls[0][0] == ["cmd.exe", "/c", "start", "", "http://127.0.0.1:9790"]
    assert calls[0][1]["errors"] == "replace"
