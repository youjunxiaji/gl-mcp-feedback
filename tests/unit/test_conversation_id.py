#!/usr/bin/env python3
"""
对话标识（conversation_id）测试
==============================

Cursor 钩子把对话标识填进 interactive_feedback 的入参，服务端接住、存进会话并通过接口返回。
"""

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_feedback_enhanced import server
from mcp_feedback_enhanced.web.main import WebUIManager
from mcp_feedback_enhanced.web.models.feedback_session import WebFeedbackSession


CONVERSATION_ID = "b4b67a30-2d12-417a-bfb3-d279e3c0efe4"


class TestNormalizeConversationId:
    """钩子填入的对话标识只认 UUID 一类的字符"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (CONVERSATION_ID, CONVERSATION_ID),
            (f"  {CONVERSATION_ID}\n", CONVERSATION_ID),
            ("abc_DEF-123", "abc_DEF-123"),
            ("a" * 128, "a" * 128),
            ("", ""),
            (None, ""),
            ("   ", ""),
            ("a" * 129, ""),
            ("has space", ""),
            ("../etc/passwd", ""),
            ("<script>", ""),
            ("对话", ""),
        ],
    )
    def test_normalize(self, raw: str | None, expected: str):
        assert WebFeedbackSession.normalize_conversation_id(raw) == expected


class TestSessionApi:
    """会话接口和页面带上对话标识"""

    def test_api_includes_conversation_id(self, web_ui_manager: WebUIManager):
        web_ui_manager.create_session(
            "/tmp/project", "摘要", conversation_id=CONVERSATION_ID
        )
        client = TestClient(web_ui_manager.app)

        status = client.get("/api/session-status").json()
        assert status["session_info"]["conversation_id"] == CONVERSATION_ID

        current = client.get("/api/current-session").json()
        assert current["conversation_id"] == CONVERSATION_ID

        sessions = client.get("/api/all-sessions").json()["sessions"]
        assert [session["conversation_id"] for session in sessions] == [CONVERSATION_ID]

    def test_missing_or_invalid_id_becomes_empty(self, web_ui_manager: WebUIManager):
        web_ui_manager.create_session("/tmp/project", "没有钩子")
        web_ui_manager.create_session(
            "/tmp/project", "乱填的标识", conversation_id="../x y"
        )
        client = TestClient(web_ui_manager.app)

        sessions = client.get("/api/all-sessions").json()["sessions"]
        assert len(sessions) == 2
        assert {session["conversation_id"] for session in sessions} == {""}

    def test_page_has_conversation_badge(self, web_ui_manager: WebUIManager):
        web_ui_manager.create_session(
            "/tmp/project", "摘要", conversation_id=CONVERSATION_ID
        )
        page = TestClient(web_ui_manager.app).get("/")

        assert page.status_code == 200
        assert 'id="conversationBadge"' in page.text


class TestInteractiveFeedbackTool:
    """interactive_feedback 工具接受对话标识并交给 Web UI"""

    async def test_schema_has_optional_conversation_id(self):
        tool = await server.mcp.get_tool("interactive_feedback")
        schema = tool.parameters

        assert schema["properties"]["conversation_id"]["default"] == ""
        assert "conversation_id" not in schema.get("required", [])

    async def test_conversation_id_is_passed_to_web_ui(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict[str, str] = {}

        async def fake_launch(project_dir: str, summary: str, timeout: int, **kwargs):
            captured.update(kwargs)
            return {"interactive_feedback": "好的", "images": [], "settings": {}}

        monkeypatch.setattr(server, "launch_web_feedback_ui", fake_launch)
        monkeypatch.setattr(
            server, "save_feedback_to_file", lambda data, file_path=None: ""
        )

        async with Client(server.mcp) as client:
            await client.call_tool(
                "interactive_feedback",
                {"summary": "摘要", "conversation_id": CONVERSATION_ID},
            )

        assert captured == {"conversation_id": CONVERSATION_ID}

    async def test_unknown_parameter_is_rejected(self):
        async with Client(server.mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "interactive_feedback", {"summary": "摘要", "question": "选哪个？"}
                )
