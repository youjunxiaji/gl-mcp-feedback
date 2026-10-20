#!/usr/bin/env python3
"""
对话房间测试
============

一个对话一个房间：会话、排队消息、页面连接各自独立，
一个对话的新提问不会替换另一个对话的会话，也不会取走另一个对话排队的消息。
"""

import asyncio
import json
import time
from collections.abc import Callable, Generator
from typing import Any, cast

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

from mcp_feedback_enhanced.web import main as web_main
from mcp_feedback_enhanced.web.main import WebUIManager, launch_web_feedback_ui
from mcp_feedback_enhanced.web.models import DEFAULT_ROOM_ID, Room, SessionStatus


CONVERSATION_A = "a1b2c3d4-0000-4000-8000-000000000001"
CONVERSATION_B = "b2c3d4e5-0000-4000-8000-000000000002"


class FakeWebSocket:
    """只记录发出的消息的 WebSocket 替身"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def reply_message(text: str) -> dict[str, Any]:
    return {"type": "submit_feedback", "feedback": text, "images": [], "settings": {}}


def receive_notification(websocket: WebSocketTestSession) -> dict[str, Any]:
    """跳过连接时的状态消息，取下一条通知"""
    for _ in range(5):
        message = websocket.receive_json()
        if message["type"] == "notification":
            return message
    raise AssertionError("没有收到通知")


class TestRoom:
    """房间自己的行为"""

    @pytest.mark.parametrize(
        ("conversation_id", "expected"),
        [
            (CONVERSATION_A, CONVERSATION_A),
            ("abc_DEF-123", "abc_DEF-123"),
            ("", DEFAULT_ROOM_ID),
            (None, DEFAULT_ROOM_ID),
            ("has space", DEFAULT_ROOM_ID),
            ("../etc/passwd", DEFAULT_ROOM_ID),
            ("a" * 129, DEFAULT_ROOM_ID),
        ],
    )
    def test_room_id_for(self, conversation_id: str | None, expected: str):
        assert Room.room_id_for(conversation_id) == expected

    def test_default_room_has_no_conversation_id(self):
        room = Room(DEFAULT_ROOM_ID)

        assert room.conversation_id == ""
        assert room.create_session("/tmp/project", "摘要").conversation_id == ""

    def test_sessions_carry_the_room_conversation_id(self):
        room = Room(CONVERSATION_A)

        assert room.create_session("/tmp/project", "摘要").conversation_id == (
            CONVERSATION_A
        )

    def test_new_session_completes_the_old_one(self):
        room = Room(CONVERSATION_A)
        first = room.create_session("/tmp/project", "第一问")
        second = room.create_session("/tmp/project", "第二问")

        assert room.current_session is second
        assert first.status == SessionStatus.COMPLETED
        assert not first.is_awaiting_feedback()
        assert second.is_awaiting_feedback()
        assert set(room.sessions) == {first.session_id, second.session_id}

    def test_websocket_moves_to_the_new_session(self):
        room = Room(CONVERSATION_A)
        first = room.create_session("/tmp/project", "第一问")
        assert room.pending_session_update is True

        websocket = FakeWebSocket()
        first.websocket = cast(WebSocket, websocket)
        room.pending_session_update = False
        second = room.create_session("/tmp/project", "第二问")

        assert second.websocket is websocket
        assert room.pending_session_update is False

    def test_keeps_only_the_latest_sessions(self):
        room = Room(CONVERSATION_A)
        sessions = [
            room.create_session("/tmp/project", f"第 {index} 问")
            for index in range(Room.MAX_SESSIONS + 2)
        ]

        assert len(room.sessions) == Room.MAX_SESSIONS
        assert room.current_session is sessions[-1]
        assert sessions[-1].session_id in room.sessions

    def test_page_title(self):
        room = Room(CONVERSATION_A)
        assert room.page_title() == "gl-mcp-feedback · a1b2"
        assert Room(DEFAULT_ROOM_ID).page_title() == "gl-mcp-feedback"

        room.create_session("/Users/gulei/Documents/my-project", "摘要")
        assert room.page_title() == "my-project · a1b2"

        default_room = Room(DEFAULT_ROOM_ID)
        default_room.create_session("/Users/gulei/Documents/my-project", "摘要")
        assert default_room.page_title() == "my-project"

    async def test_deliver_queued_messages(self):
        room = Room(CONVERSATION_A)
        room.message_queue.enqueue("排队的第一条", [], [])
        room.message_queue.enqueue("排队的第二条", [], [])
        session = room.create_session("/tmp/project", "摘要")

        result = await room.deliver_queued_messages(session)

        assert result is not None
        assert "排队的第一条" in result["interactive_feedback"]
        assert "排队的第二条" in result["interactive_feedback"]
        assert session.feedback_completed.is_set()
        assert not session.is_awaiting_feedback()
        assert room.message_queue.pending_count() == 0
        assert room.pending_session_update is False

    async def test_deliver_returns_none_when_queue_is_empty(self):
        room = Room(CONVERSATION_A)
        session = room.create_session("/tmp/project", "摘要")

        assert await room.deliver_queued_messages(session) is None
        assert session.is_awaiting_feedback()

    async def test_enqueue_notifies_the_page(self):
        room = Room(CONVERSATION_A)
        session = room.create_session("/tmp/project", "摘要")
        websocket = FakeWebSocket()
        session.websocket = cast(WebSocket, websocket)

        await room.enqueue_user_message(session, "第一条", [])
        await room.enqueue_user_message(session, "   ", [])
        for _ in range(room.message_queue.max_pending):
            await room.enqueue_user_message(session, "再来一条", [])

        codes = [message["code"] for message in websocket.sent]
        assert codes[0] == "queue.messageQueued"
        assert websocket.sent[0]["message_id"]
        assert codes[1] == "queue.messageEmpty"
        assert codes[-1] == "queue.full"
        assert room.message_queue.pending_count() == room.message_queue.max_pending


class TestRoomIsolation:
    """两个对话互不影响"""

    def test_new_conversation_does_not_replace_the_other(
        self, web_ui_manager: WebUIManager
    ):
        session_a = web_ui_manager.create_session(
            "/tmp/project-a", "A 的提问", conversation_id=CONVERSATION_A
        )
        session_b = web_ui_manager.create_session(
            "/tmp/project-b", "B 的提问", conversation_id=CONVERSATION_B
        )

        room_a = web_ui_manager.get_room(CONVERSATION_A)
        room_b = web_ui_manager.get_room(CONVERSATION_B)
        assert room_a is not None
        assert room_b is not None
        assert room_a.current_session is session_a
        assert room_b.current_session is session_b
        assert session_a.is_awaiting_feedback()
        assert session_b.is_awaiting_feedback()

    def test_same_conversation_reuses_its_room(self, web_ui_manager: WebUIManager):
        first = web_ui_manager.get_or_create_room(CONVERSATION_A)
        second = web_ui_manager.get_or_create_room(CONVERSATION_A)

        assert first is second
        assert web_ui_manager.get_room("not-created") is None

    def test_calls_without_conversation_id_share_the_default_room(
        self, web_ui_manager: WebUIManager
    ):
        web_ui_manager.create_session("/tmp/project", "没有钩子")
        web_ui_manager.create_session(
            "/tmp/project", "乱填的标识", conversation_id="a b"
        )

        room = web_ui_manager.get_room(DEFAULT_ROOM_ID)
        assert room is not None
        assert len(room.sessions) == 2
        assert set(web_ui_manager.rooms) == {DEFAULT_ROOM_ID}

    async def test_queues_are_separate(self, web_ui_manager: WebUIManager):
        room_a = web_ui_manager.get_or_create_room(CONVERSATION_A)
        room_b = web_ui_manager.get_or_create_room(CONVERSATION_B)
        room_a.message_queue.enqueue("只给 A 的消息", [], [])

        session_b = room_b.create_session("/tmp/project-b", "B 的提问")
        assert await room_b.deliver_queued_messages(session_b) is None
        assert room_a.message_queue.pending_count() == 1

        session_a = room_a.create_session("/tmp/project-a", "A 的提问")
        result = await room_a.deliver_queued_messages(session_a)
        assert result is not None
        assert "只给 A 的消息" in result["interactive_feedback"]

    def test_cleanup_covers_every_room(self, web_ui_manager: WebUIManager):
        session_a = web_ui_manager.create_session(
            "/tmp/project-a", "A 的提问", conversation_id=CONVERSATION_A
        )
        session_b = web_ui_manager.create_session(
            "/tmp/project-b", "B 的提问", conversation_id=CONVERSATION_B
        )
        session_a.last_activity = 0
        session_b.last_activity = 0

        assert web_ui_manager.cleanup_expired_sessions() == 2

        for conversation_id in (CONVERSATION_A, CONVERSATION_B):
            room = web_ui_manager.get_room(conversation_id)
            assert room is not None
            assert room.sessions == {}
            assert room.current_session is None

    def test_forced_memory_cleanup_covers_every_room(
        self, web_ui_manager: WebUIManager
    ):
        idle_since = time.time() - 700
        for conversation_id in (CONVERSATION_A, CONVERSATION_B):
            session = web_ui_manager.create_session(
                "/tmp/project", "摘要", conversation_id=conversation_id
            )
            session.last_activity = idle_since

        assert web_ui_manager.cleanup_sessions_by_memory_pressure(force=True) == 2

    def test_stop_clears_every_room(self, web_ui_manager: WebUIManager):
        web_ui_manager.create_session("/tmp/a", "A", conversation_id=CONVERSATION_A)
        web_ui_manager.create_session("/tmp/b", "B", conversation_id=CONVERSATION_B)

        web_ui_manager.stop()

        assert web_ui_manager.rooms == {}


class TestRoomRoutes:
    """页面和接口按房间区分"""

    @pytest.fixture
    def client(self, web_ui_manager: WebUIManager) -> TestClient:
        web_ui_manager.create_session(
            "/Users/gulei/Documents/project-a",
            "A 的提问",
            conversation_id=CONVERSATION_A,
        )
        web_ui_manager.create_session(
            "/Users/gulei/Documents/project-b",
            "B 的提问",
            conversation_id=CONVERSATION_B,
        )
        return TestClient(web_ui_manager.app)

    def test_root_is_the_room_list_page(self, client: TestClient):
        page = client.get("/")

        assert page.status_code == 200
        assert 'id="roomList"' in page.text

    def test_room_list(self, client: TestClient, web_ui_manager: WebUIManager):
        room_a = web_ui_manager.get_room(CONVERSATION_A)
        assert room_a is not None
        room_a.message_queue.enqueue("排队中", [], [])

        rooms = client.get("/api/rooms").json()["rooms"]

        by_id = {room["room_id"]: room for room in rooms}
        assert set(by_id) == {CONVERSATION_A, CONVERSATION_B}
        assert by_id[CONVERSATION_A]["title"] == "project-a · a1b2"
        assert by_id[CONVERSATION_A]["project_directory"] == (
            "/Users/gulei/Documents/project-a"
        )
        assert by_id[CONVERSATION_A]["awaiting_feedback"] is True
        assert by_id[CONVERSATION_A]["pending_count"] == 1
        assert by_id[CONVERSATION_B]["pending_count"] == 0

    def test_room_page(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        assert page.status_code == 200
        assert "<title>project-a · a1b2</title>" in page.text
        assert f'data-room-base="/c/{CONVERSATION_A}"' in page.text
        assert "const ROOM_BASE = document.body.dataset.roomBase;" in page.text
        assert "/Users/gulei/Documents/project-a" in page.text
        assert "project-b" not in page.text

    def test_unknown_room_page_goes_back_to_the_list(self, client: TestClient):
        response = client.get("/c/not-created/", follow_redirects=False)

        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/"

    def test_room_without_session_goes_back_to_the_list(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room = web_ui_manager.get_room(CONVERSATION_A)
        assert room is not None
        room.current_session = None

        response = client.get(f"/c/{CONVERSATION_A}/", follow_redirects=False)

        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/"

    def test_sessions_api_only_shows_its_own_room(self, client: TestClient):
        for conversation_id, summary in (
            (CONVERSATION_A, "A 的提问"),
            (CONVERSATION_B, "B 的提问"),
        ):
            data = client.get(f"/c/{conversation_id}/api/all-sessions").json()

            assert [session["summary"] for session in data["sessions"]] == [summary]
            assert data["sessions"][0]["is_current"] is True
            assert data["sessions"][0]["conversation_id"] == conversation_id

    @pytest.mark.parametrize(
        "path",
        ["api/session-status", "api/current-session", "api/all-sessions"],
    )
    def test_unknown_room_api_is_404(self, client: TestClient, path: str):
        response = client.get(f"/c/not-created/{path}")

        assert response.status_code == 404
        assert response.json()["messageCode"] == "room.notFound"

    def test_queue_endpoints_only_touch_their_own_room(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room_a = web_ui_manager.get_room(CONVERSATION_A)
        assert room_a is not None
        message = room_a.message_queue.enqueue("排队中", [], [])
        path = f"api/queue/{message.message_id}"

        # 别的对话改不了、撤不回
        other = client.put(f"/c/{CONVERSATION_B}/{path}", json={"feedback": "篡改"})
        assert other.status_code == 409
        assert client.delete(f"/c/{CONVERSATION_B}/{path}").status_code == 409
        assert client.put("/c/not-created/" + path, json={}).status_code == 404

        # 自己的房间可以改、可以撤回
        assert (
            client.put(
                f"/c/{CONVERSATION_A}/{path}", json={"feedback": "改过"}
            ).status_code
            == 200
        )
        queued = client.get(f"/c/{CONVERSATION_A}/api/all-sessions").json()[
            "queued_messages"
        ]
        assert [item["feedback"] for item in queued] == ["改过"]
        assert client.delete(f"/c/{CONVERSATION_A}/{path}").status_code == 200
        assert room_a.message_queue.pending_count() == 0


class TestRoomWebSocket:
    """WebSocket 按房间区分"""

    def test_reply_goes_to_its_own_room(self, web_ui_manager: WebUIManager):
        session_a = web_ui_manager.create_session(
            "/tmp/project-a", "A 的提问", conversation_id=CONVERSATION_A
        )
        session_b = web_ui_manager.create_session(
            "/tmp/project-b", "B 的提问", conversation_id=CONVERSATION_B
        )
        client = TestClient(web_ui_manager.app)

        with client.websocket_connect(f"/c/{CONVERSATION_A}/ws") as websocket:
            websocket.send_json(reply_message("给 A 的回复"))
            notification = receive_notification(websocket)

        assert notification["code"] == "session.feedbackSubmitted"
        assert session_a.feedback_result == "给 A 的回复"
        assert session_a.feedback_completed.is_set()
        assert session_b.is_awaiting_feedback()
        assert session_b.feedback_result is None

    def test_message_without_a_waiting_call_is_queued_in_its_room(
        self, web_ui_manager: WebUIManager
    ):
        session_a = web_ui_manager.create_session(
            "/tmp/project-a", "A 的提问", conversation_id=CONVERSATION_A
        )
        web_ui_manager.create_session(
            "/tmp/project-b", "B 的提问", conversation_id=CONVERSATION_B
        )
        session_a.close_with_queued_messages(0)
        client = TestClient(web_ui_manager.app)

        with client.websocket_connect(f"/c/{CONVERSATION_A}/ws") as websocket:
            websocket.send_json(reply_message("AI 干活时发的"))
            notification = receive_notification(websocket)

        room_a = web_ui_manager.get_room(CONVERSATION_A)
        room_b = web_ui_manager.get_room(CONVERSATION_B)
        assert room_a is not None
        assert room_b is not None
        assert notification["code"] == "queue.messageQueued"
        assert room_a.message_queue.pending_count() == 1
        assert room_b.message_queue.pending_count() == 0

    def test_unknown_room_is_rejected(self, web_ui_manager: WebUIManager):
        web_ui_manager.create_session("/tmp/project", "摘要")
        client = TestClient(web_ui_manager.app)

        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/c/not-created/ws"),
        ):
            pass


async def wait_until(condition: Callable[[], bool], timeout: float = 10) -> None:
    """等条件成立，超时就让测试失败"""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


@pytest.fixture
def live_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[WebUIManager, list[str]], None, None]:
    """真正启动 Web 服务的管理器，打开浏览器改成记下地址"""
    monkeypatch.setenv("MCP_TEST_MODE", "true")
    monkeypatch.setenv("MCP_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_WEB_PORT", "0")
    monkeypatch.setattr(web_main, "_web_ui_manager", None)

    manager = web_main.get_web_ui_manager()
    opened_urls: list[str] = []
    monkeypatch.setattr(manager, "open_browser", opened_urls.append)
    yield manager, opened_urls
    web_main.stop_web_ui()


class TestLaunchEndToEnd:
    """走完整的调用流程：interactive_feedback 的后端 + 真实的 Web 服务 + WebSocket 页面"""

    @staticmethod
    def websocket_url(manager: WebUIManager, conversation_id: str) -> str:
        return f"ws://127.0.0.1:{manager.port}/c/{conversation_id}/ws"

    async def test_two_conversations_reply_independently(
        self, live_manager: tuple[WebUIManager, list[str]]
    ):
        manager, opened_urls = live_manager
        call_a = asyncio.create_task(
            launch_web_feedback_ui(
                "/tmp/project-a", "A 的提问", 30, conversation_id=CONVERSATION_A
            )
        )
        call_b = asyncio.create_task(
            launch_web_feedback_ui(
                "/tmp/project-b", "B 的提问", 30, conversation_id=CONVERSATION_B
            )
        )
        await wait_until(lambda: len(opened_urls) == 2)

        # 每个对话各开一个自己的页面
        base = f"http://127.0.0.1:{manager.port}"
        assert sorted(opened_urls) == sorted(
            [f"{base}/c/{CONVERSATION_A}/", f"{base}/c/{CONVERSATION_B}/"]
        )

        # 先回复 B：B 的调用拿到回复，A 的调用仍在等
        async with connect(self.websocket_url(manager, CONVERSATION_B)) as page_b:
            await page_b.send(json.dumps(reply_message("给 B 的回复")))
            result_b = await asyncio.wait_for(call_b, 10)
        assert result_b["interactive_feedback"] == "给 B 的回复"
        assert not call_a.done()
        room_a = manager.get_room(CONVERSATION_A)
        assert room_a is not None
        assert room_a.current_session is not None
        assert room_a.current_session.is_awaiting_feedback()

        # 再回复 A
        async with connect(self.websocket_url(manager, CONVERSATION_A)) as page_a:
            await page_a.send(json.dumps(reply_message("给 A 的回复")))
            result_a = await asyncio.wait_for(call_a, 10)
        assert result_a["interactive_feedback"] == "给 A 的回复"

    async def test_queued_messages_go_to_their_own_conversation(
        self, live_manager: tuple[WebUIManager, list[str]]
    ):
        manager, opened_urls = live_manager

        # A 提问并得到回复
        first_call_a = asyncio.create_task(
            launch_web_feedback_ui(
                "/tmp/project-a", "A 的第一问", 30, conversation_id=CONVERSATION_A
            )
        )
        await wait_until(lambda: len(opened_urls) == 1)
        room_a = manager.get_room(CONVERSATION_A)
        assert room_a is not None

        async with connect(self.websocket_url(manager, CONVERSATION_A)) as page_a:
            await page_a.send(json.dumps(reply_message("好的")))
            first_result = await asyncio.wait_for(first_call_a, 10)
            assert first_result["interactive_feedback"] == "好的"

            # AI 干活期间，A 的页面又发来一条消息：进 A 的队列
            await page_a.send(json.dumps(reply_message("A 排队的消息")))
            await wait_until(lambda: room_a.message_queue.pending_count() == 1)

        # B 提问：取不到 A 的消息，照常等用户回复
        call_b = asyncio.create_task(
            launch_web_feedback_ui(
                "/tmp/project-b", "B 的提问", 30, conversation_id=CONVERSATION_B
            )
        )
        await wait_until(lambda: len(opened_urls) == 2)
        assert not call_b.done()
        assert room_a.message_queue.pending_count() == 1

        # A 再次提问：立刻拿到自己排队的消息，不用等用户
        second_result = await asyncio.wait_for(
            launch_web_feedback_ui(
                "/tmp/project-a", "A 的第二问", 30, conversation_id=CONVERSATION_A
            ),
            10,
        )
        assert "A 排队的消息" in second_result["interactive_feedback"]
        assert not call_b.done()
        assert len(opened_urls) == 2

        # 收尾：回复 B
        async with connect(self.websocket_url(manager, CONVERSATION_B)) as page_b:
            await page_b.send(json.dumps(reply_message("给 B 的回复")))
            result_b = await asyncio.wait_for(call_b, 10)
        assert result_b["interactive_feedback"] == "给 B 的回复"
