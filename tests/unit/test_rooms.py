#!/usr/bin/env python3
"""
对话房间测试
============

一个对话一个房间：会话、排队消息、页面连接各自独立，
一个对话的新提问不会替换另一个对话的会话，也不会取走另一个对话排队的消息。
对话结束后房间的销毁也在这里：stop 钩子的报告、到期判断、销毁时对页面的通知和钩子文件的清理。
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

from mcp_feedback_enhanced.web.main import WebUIManager, launch_web_feedback_ui
from mcp_feedback_enhanced.web.models import (
    DEFAULT_ROOM_ID,
    Room,
    SessionStatus,
    TurnStatus,
)
from tests.helpers import wait_until


CONVERSATION_A = "a1b2c3d4-0000-4000-8000-000000000001"
CONVERSATION_B = "b2c3d4e5-0000-4000-8000-000000000002"


class FakeWebSocket:
    """只记录发出的消息的 WebSocket 替身"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


class ClosablePage(FakeWebSocket):
    """还能记录关闭动作的页面连接替身"""

    def __init__(self) -> None:
        super().__init__()
        self.closed_with: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_with = (code, reason or "")


class HangingPage(FakeWebSocket):
    """发送永远不返回的页面连接替身"""

    async def send_json(self, data: dict[str, Any]) -> None:
        await asyncio.Event().wait()


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

    def test_room_page_logo_links_to_the_room_list(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        assert '<a id="homeLink" href="/"' in page.text

    def test_room_page_handles_the_conversation_ending(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        assert "data.type === 'room_closed'" in page.text
        assert "const handleRoomClosed = () => {" in page.text

    def test_room_page_shows_the_time_under_each_bubble(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        assert "const createMessageMeta = (item) => {" in page.text
        assert "createTimeSeparator" not in page.text
        assert "wrap.title" not in page.text

    def test_bubbles_stay_inside_their_column(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        # 气泡按内容定宽，必须限制在外层宽度（最宽 2/3）里，否则代码块的长行会把它撑出屏幕
        assert "max-w-[90%] sm:max-w-[66.667%]" in page.text
        for bubble in ("bubble-ai", "bubble-user"):
            assert f"bubble.className = '{bubble} min-w-0 max-w-full " in page.text

    def test_composer_buttons_have_the_same_height(self, client: TestClient):
        page = client.get(f"/c/{CONVERSATION_A}/")

        heights = re.findall(
            r'id="(?:attachBtn|submitBtn)"[^>]*class="[^"]*?\bh-\[(\d+px)\]', page.text
        )
        assert len(heights) == 2
        assert heights[0] == heights[1]

    def test_room_list_marks_ended_conversations(self, client: TestClient):
        page = client.get("/")

        assert "room.ended" in page.text
        assert "createChip('已结束'" in page.text

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


def finished_room(conversation_id: str = CONVERSATION_A) -> Room:
    """AI 的上一次调用已经结束、没有在等回复的房间"""
    room = Room(conversation_id)
    room.create_session("/tmp/project", "摘要").close_with_queued_messages(0)
    return room


class TestRoomLifecycle:
    """房间什么时候该销毁：对话正常结束，或者闲置太久"""

    def test_turn_end_marks_the_room(self):
        room = finished_room()
        assert not room.is_ended

        assert room.end_turn(TurnStatus.COMPLETED) is True

        assert room.is_ended
        assert room.ended_status == TurnStatus.COMPLETED
        assert room.ended_at is not None

    def test_turn_end_is_ignored_while_the_call_waits(self):
        room = Room(CONVERSATION_A)
        room.create_session("/tmp/project", "摘要")

        assert room.end_turn(TurnStatus.COMPLETED) is False

        assert not room.is_ended

    def test_a_new_question_revokes_the_mark(self):
        room = finished_room()
        room.end_turn(TurnStatus.COMPLETED)

        room.create_session("/tmp/project", "下一问")

        assert not room.is_ended
        assert room.ended_status is None

    async def test_a_tool_call_delivery_revokes_the_mark(self):
        room = finished_room()
        room.message_queue.enqueue("排队的消息", [], [])
        room.end_turn(TurnStatus.COMPLETED)

        assert await room.deliver_for_tool_call() is not None

        assert not room.is_ended

    @pytest.mark.parametrize(
        ("status", "seconds_after_end", "pending", "expected"),
        [
            # 正常结束：过了缓冲时间就销毁
            (TurnStatus.COMPLETED, Room.ENDED_DESTROY_DELAY_SECONDS - 1, 0, False),
            (TurnStatus.COMPLETED, Room.ENDED_DESTROY_DELAY_SECONDS, 0, True),
            (TurnStatus.COMPLETED, 600, 0, True),
            # 还有排队中的消息：留着，等对话下次提问或调用工具时送达
            (TurnStatus.COMPLETED, 600, 1, False),
            # 被中断、出错：只标记，不销毁
            (TurnStatus.ABORTED, 600, 0, False),
            (TurnStatus.ERROR, 600, 0, False),
            # 闲置太久，不管是什么状态、有没有排队消息都销毁
            (TurnStatus.ABORTED, Room.IDLE_DESTROY_SECONDS + 1, 0, True),
            (TurnStatus.ERROR, Room.IDLE_DESTROY_SECONDS + 1, 0, True),
            (TurnStatus.COMPLETED, Room.IDLE_DESTROY_SECONDS + 1, 1, True),
        ],
    )
    def test_due_after_the_conversation_ended(
        self, status: TurnStatus, seconds_after_end: float, pending: int, expected: bool
    ):
        room = finished_room()
        for _ in range(pending):
            room.message_queue.enqueue("排队中", [], [])
        room.end_turn(status)
        assert room.ended_at is not None

        assert room.due_for_destroy(room.ended_at + seconds_after_end) is expected

    def test_a_room_that_never_got_stop_is_destroyed_when_idle(self):
        room = finished_room()
        last_activity = room.last_activity

        assert not room.due_for_destroy(last_activity + Room.IDLE_DESTROY_SECONDS - 1)
        assert room.due_for_destroy(last_activity + Room.IDLE_DESTROY_SECONDS)

    def test_a_waiting_call_keeps_the_room_whatever_the_time(self):
        room = Room(CONVERSATION_A)
        room.create_session("/tmp/project", "摘要")
        far_future = time.time() + 10 * Room.IDLE_DESTROY_SECONDS

        assert not room.due_for_destroy(far_future)

    def test_the_default_room_is_never_destroyed(self):
        room = Room(DEFAULT_ROOM_ID)
        room.create_session("/tmp/project", "摘要").close_with_queued_messages(0)
        room.ended_at = 0
        room.ended_status = TurnStatus.COMPLETED

        assert not room.due_for_destroy(time.time() + 10 * Room.IDLE_DESTROY_SECONDS)

    def test_queue_activity_counts_as_room_activity(self):
        room = finished_room()
        room.touched_at = 0
        for session in room.sessions.values():
            session.last_activity = 0

        room.message_queue.enqueue("有动静", [], [])

        assert room.last_activity > time.time() - 5

    def test_list_item_tells_whether_the_conversation_ended(self):
        room = finished_room()
        assert room.to_list_item()["ended"] is False

        room.end_turn(TurnStatus.ABORTED)

        assert room.to_list_item()["ended"] is True

    async def test_closing_tells_the_page_and_releases_the_sessions(self):
        room = finished_room()
        session = room.current_session
        assert session is not None
        page = ClosablePage()
        session.websocket = cast(WebSocket, page)

        await room.close()

        assert page.sent == [{"type": "room_closed"}]
        assert page.closed_with == (1000, "对话已结束")
        assert room.sessions == {}
        assert room.current_session is None
        assert session.feedback_completed.is_set()

    async def test_closing_tells_a_shared_page_connection_only_once(self):
        room = Room(CONVERSATION_A)
        first = room.create_session("/tmp/project", "第一问")
        page = ClosablePage()
        first.websocket = cast(WebSocket, page)
        room.create_session("/tmp/project", "第二问")

        await room.close()

        assert page.sent == [{"type": "room_closed"}]

    async def test_a_stuck_page_does_not_block_closing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(Room, "NOTIFY_TIMEOUT_SECONDS", 0.1)
        room = finished_room()
        session = room.current_session
        assert session is not None
        session.websocket = cast(WebSocket, HangingPage())

        await asyncio.wait_for(room.close(), 5)

        assert room.sessions == {}


class TestRoomDestruction:
    """服务销毁到期的房间"""

    @staticmethod
    def finished_conversation(manager: WebUIManager, conversation_id: str) -> Room:
        session = manager.create_session(
            "/tmp/project", "提问", conversation_id=conversation_id
        )
        session.close_with_queued_messages(0)
        room = manager.get_room(conversation_id)
        assert room is not None
        return room

    @staticmethod
    def ended_at(room: Room) -> float:
        assert room.ended_at is not None
        return room.ended_at

    async def test_sweep_destroys_only_the_rooms_that_are_due(
        self, web_ui_manager: WebUIManager
    ):
        ended = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        waiting = web_ui_manager.get_or_create_room(CONVERSATION_B)
        waiting.create_session("/tmp/project", "还在等回复")
        default = web_ui_manager.get_or_create_room("")
        default.create_session("/tmp/project", "默认房间").close_with_queued_messages(0)
        assert web_ui_manager.end_turn(ended, TurnStatus.COMPLETED) is True
        now = self.ended_at(ended) + 600

        assert await web_ui_manager.sweep_rooms(now) == 1

        assert web_ui_manager.get_room(CONVERSATION_A) is None
        assert web_ui_manager.get_room(CONVERSATION_B) is waiting
        assert web_ui_manager.get_room(DEFAULT_ROOM_ID) is default
        assert await web_ui_manager.sweep_rooms(now) == 0

    async def test_destruction_closes_the_page_and_removes_the_hook_files(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        room = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        session = room.current_session
        assert session is not None
        page = ClosablePage()
        session.websocket = cast(WebSocket, page)
        registration = isolated_feedback_home / "rooms" / CONVERSATION_A
        assert registration.exists()
        web_ui_manager.end_turn(room, TurnStatus.COMPLETED)

        await web_ui_manager.sweep_rooms(self.ended_at(room) + 600)

        assert page.sent == [{"type": "room_closed"}]
        assert page.closed_with == (1000, "对话已结束")
        assert not registration.exists()
        assert room.sessions == {}

    async def test_a_room_with_queued_messages_stays_until_they_are_delivered(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        room = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        room.message_queue.enqueue("还没被取走的消息", [], [])
        web_ui_manager.end_turn(room, TurnStatus.COMPLETED)
        marker = isolated_feedback_home / "queue" / CONVERSATION_A

        assert await web_ui_manager.sweep_rooms(self.ended_at(room) + 600) == 0
        assert web_ui_manager.get_room(CONVERSATION_A) is room
        assert marker.exists()

        # 对话下次提问时消息送达，房间随之继续使用，不再销毁
        session = room.create_session("/tmp/project", "下一问")
        assert await room.deliver_queued_messages(session) is not None
        assert not room.is_ended
        assert await web_ui_manager.sweep_rooms(time.time() + 600) == 0
        assert web_ui_manager.get_room(CONVERSATION_A) is room

    async def test_a_new_question_saves_the_room_from_destruction(
        self, web_ui_manager: WebUIManager
    ):
        room = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        web_ui_manager.end_turn(room, TurnStatus.COMPLETED)
        now = self.ended_at(room) + 600

        web_ui_manager.create_session(
            "/tmp/project", "三秒内又提问了", conversation_id=CONVERSATION_A
        )

        assert await web_ui_manager.sweep_rooms(now) == 0
        assert web_ui_manager.get_room(CONVERSATION_A) is room

    async def test_asking_again_after_destruction_opens_a_fresh_room(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        old_room = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        web_ui_manager.end_turn(old_room, TurnStatus.COMPLETED)
        await web_ui_manager.sweep_rooms(self.ended_at(old_room) + 600)

        new_room, session = web_ui_manager.create_session_in_room(
            "/tmp/project", "对话结束后又来了", conversation_id=CONVERSATION_A
        )

        assert new_room is not old_room
        assert new_room.current_session is session
        assert web_ui_manager.get_room(CONVERSATION_A) is new_room
        assert (isolated_feedback_home / "rooms" / CONVERSATION_A).exists()

    def test_create_session_in_room_returns_the_conversation_room(
        self, web_ui_manager: WebUIManager
    ):
        room, session = web_ui_manager.create_session_in_room(
            "/tmp/project", "提问", conversation_id=CONVERSATION_A
        )

        assert room is web_ui_manager.get_room(CONVERSATION_A)
        assert room.current_session is session
        assert (
            web_ui_manager.create_session_in_room(
                "/tmp/project", "再问", conversation_id=CONVERSATION_A
            )[0]
            is room
        )

    def test_turn_end_of_a_room_that_is_gone_is_ignored(
        self, web_ui_manager: WebUIManager
    ):
        room = self.finished_conversation(web_ui_manager, CONVERSATION_A)
        web_ui_manager.rooms.clear()

        assert web_ui_manager.end_turn(room, TurnStatus.COMPLETED) is False
        assert not room.is_ended

    def test_turn_end_of_the_default_room_is_ignored(
        self, web_ui_manager: WebUIManager
    ):
        web_ui_manager.create_session("/tmp/project", "默认房间")
        room = web_ui_manager.get_room(DEFAULT_ROOM_ID)
        assert room is not None
        room.current_session = None

        assert web_ui_manager.end_turn(room, TurnStatus.COMPLETED) is False
        assert not room.is_ended


class TestRoomRegistrationFiles:
    """stop 钩子靠登记文件找到房间"""

    def test_rooms_are_registered_with_the_server_address(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        web_ui_manager.create_session(
            "/tmp/project", "提问", conversation_id=CONVERSATION_A
        )
        web_ui_manager.create_session("/tmp/project", "没有对话标识")

        address, pid = (
            (isolated_feedback_home / "rooms" / CONVERSATION_A)
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert address == f"http://127.0.0.1:{web_ui_manager.port}/c/{CONVERSATION_A}"
        assert pid == str(os.getpid())
        # 默认房间没有对话标识，钩子找不到它，不登记
        assert [path.name for path in (isolated_feedback_home / "rooms").iterdir()] == [
            CONVERSATION_A
        ]

    def test_registration_follows_a_changed_server_port(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        room = web_ui_manager.get_or_create_room(CONVERSATION_A)

        web_ui_manager.port = 54321
        room.register()

        registration = isolated_feedback_home / "rooms" / CONVERSATION_A
        assert registration.read_text(encoding="utf-8").startswith(
            f"http://127.0.0.1:54321/c/{CONVERSATION_A}\n"
        )

    def test_stopping_the_service_removes_every_registration(
        self, web_ui_manager: WebUIManager, isolated_feedback_home: Path
    ):
        for conversation_id in (CONVERSATION_A, CONVERSATION_B):
            web_ui_manager.create_session(
                "/tmp/project", "提问", conversation_id=conversation_id
            )

        web_ui_manager.stop()

        assert list((isolated_feedback_home / "rooms").iterdir()) == []


class TestTurnEndedRoute:
    """stop 钩子调用的结束报告接口"""

    @pytest.fixture
    def client(self, web_ui_manager: WebUIManager) -> TestClient:
        for conversation_id in (CONVERSATION_A, CONVERSATION_B):
            web_ui_manager.create_session(
                "/tmp/project", "提问", conversation_id=conversation_id
            ).close_with_queued_messages(0)
        return TestClient(web_ui_manager.app)

    @staticmethod
    def room_of(manager: WebUIManager, conversation_id: str) -> Room:
        room = manager.get_room(conversation_id)
        assert room is not None
        return room

    def test_completed_turn_marks_only_its_own_room(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        response = client.post(
            f"/c/{CONVERSATION_A}/api/turn-ended", json={"status": "completed"}
        )

        assert response.status_code == 200
        assert response.json() == {"ended": True}
        assert self.room_of(web_ui_manager, CONVERSATION_A).is_ended
        assert not self.room_of(web_ui_manager, CONVERSATION_B).is_ended
        rooms = {
            room["room_id"]: room for room in client.get("/api/rooms").json()["rooms"]
        }
        assert rooms[CONVERSATION_A]["ended"] is True
        assert rooms[CONVERSATION_B]["ended"] is False

    @pytest.mark.parametrize(
        "body", [None, "not json", "[1]", '{"status": 3}', '{"status": "weird"}']
    )
    def test_an_unusable_report_only_marks_the_room(
        self, client: TestClient, web_ui_manager: WebUIManager, body: str | None
    ):
        response = client.post(f"/c/{CONVERSATION_A}/api/turn-ended", content=body)

        assert response.json() == {"ended": True}
        room = self.room_of(web_ui_manager, CONVERSATION_A)
        assert room.is_ended
        assert room.ended_status != TurnStatus.COMPLETED
        assert not room.due_for_destroy(time.time() + 600)

    def test_a_waiting_call_ignores_the_report(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room = self.room_of(web_ui_manager, CONVERSATION_A)
        room.create_session("/tmp/project", "还在等回复")

        response = client.post(
            f"/c/{CONVERSATION_A}/api/turn-ended", json={"status": "completed"}
        )

        assert response.json() == {"ended": False}
        assert not room.is_ended

    def test_unknown_room_is_404(self, client: TestClient):
        response = client.post(
            "/c/not-created/api/turn-ended", json={"status": "completed"}
        )

        assert response.status_code == 404
        assert response.json()["messageCode"] == "room.notFound"
