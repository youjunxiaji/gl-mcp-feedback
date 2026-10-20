#!/usr/bin/env python3
"""
钩子送达排队消息测试
====================

排队的消息有两条路送到 AI 手里：随 AI 的下一次提问，或者随 AI 的任意一次工具调用（钩子来取）。
这里覆盖队列的两种送达途径、标记文件、图片文件、取走接口，以及钩子脚本本身。
"""

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from websockets.asyncio.client import ClientConnection, connect

from mcp_feedback_enhanced.web.main import WebUIManager, launch_web_feedback_ui
from mcp_feedback_enhanced.web.models import (
    DeliveryChannel,
    MessageQueue,
    QueueMarker,
    Room,
)
from mcp_feedback_enhanced.web.models.message_queue import MAX_TOOL_CALL_RECORDS
from mcp_feedback_enhanced.web.models.queue_files import (
    IMAGE_RETENTION_SECONDS,
    prune_message_images,
    save_message_images,
)
from tests.helpers import wait_until


CONVERSATION_A = "a1b2c3d4-0000-4000-8000-000000000001"
CONVERSATION_B = "b2c3d4e5-0000-4000-8000-000000000002"

ROOM_URL = "http://127.0.0.1:8765/c/room"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16

HOOK_SCRIPT = Path(__file__).resolve().parents[2] / "hooks" / "gl-feedback.sh"
PROJECT_ROOT = "/work/project"

requires_hook_tools = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("curl") is None,
    reason="钩子脚本需要 jq 和 curl",
)


def image(data: bytes, name: str = "图.png") -> dict[str, Any]:
    """队列里的图片，和 WebFeedbackSession.process_images 的输出同一格式"""
    return {"name": name, "data": data, "size": len(data)}


def upload(data: bytes, name: str = "图.png") -> dict[str, Any]:
    """页面上传的图片，data 是 base64 文本"""
    return {"name": name, "data": base64.b64encode(data).decode(), "size": len(data)}


def page_message(
    text: str, images: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """页面通过 WebSocket 发来的消息"""
    return {
        "type": "submit_feedback",
        "feedback": text,
        "images": images or [],
        "settings": {},
    }


def make_room(conversation_id: str = CONVERSATION_A) -> Room:
    return Room(conversation_id, QueueMarker(conversation_id, lambda: ROOM_URL))


class FakeWebSocket:
    """只记录发出的消息的 WebSocket 替身"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


class BrokenWebSocket:
    """发送总是出错的 WebSocket 替身"""

    async def send_json(self, data: dict[str, Any]) -> None:
        raise RuntimeError("连接已断开")


class HangingWebSocket:
    """发送永远不返回的 WebSocket 替身"""

    async def send_json(self, data: dict[str, Any]) -> None:
        await asyncio.Event().wait()


class TestMessageQueueChannels:
    """两条送达途径共用一个队列，谁先到谁取走"""

    def test_tool_call_delivery_takes_everything_in_order(self):
        queue = MessageQueue()
        queue.enqueue("第一条", [image(PNG)], [{"name": "图.png", "thumbnail": "t"}])
        queue.enqueue("第二条", [], [])

        delivered = queue.deliver_with_tool_call()

        assert [message.text for message in delivered] == ["第一条", "第二条"]
        assert delivered[0].images[0]["data"] == PNG
        assert all(
            message.delivered_via is DeliveryChannel.TOOL_CALL
            and message.delivered_at is not None
            and message.delivered_session_id is None
            for message in delivered
        )
        assert queue.pending_count() == 0

    def test_delivered_records_keep_thumbnails_but_release_images(self):
        queue = MessageQueue()
        thumbnails = [{"name": "图.png", "thumbnail": "data:image/jpeg;base64,AA=="}]
        queue.enqueue("带图", [image(PNG)], thumbnails)

        queue.deliver_with_tool_call()
        [record] = queue.visible_messages(set())

        assert record.images == []
        assert record.image_count == 1
        assert record.thumbnails == thumbnails
        assert record.delivered_via is DeliveryChannel.TOOL_CALL

    def test_question_delivery_records_the_session(self):
        queue = MessageQueue()
        queue.enqueue("随提问", [], [])

        [delivered] = queue.deliver_with_question("session-1")

        assert delivered.delivered_via is DeliveryChannel.QUESTION
        assert delivered.delivered_session_id == "session-1"

    def test_a_message_is_delivered_only_once(self):
        queue = MessageQueue()
        queue.enqueue("只送一次", [], [])

        assert len(queue.deliver_with_tool_call()) == 1
        assert queue.deliver_with_tool_call() == []
        assert queue.deliver_with_question("session-1") == []

    def test_concurrent_takers_never_get_the_same_message(self):
        queue = MessageQueue()
        for index in range(3):
            queue.enqueue(f"消息 {index}", [], [])
        taken: list[list[str]] = []
        barrier = threading.Barrier(8)

        def take(by_tool_call: bool) -> None:
            barrier.wait()
            messages = (
                queue.deliver_with_tool_call()
                if by_tool_call
                else queue.deliver_with_question("session-1")
            )
            taken.append([message.message_id for message in messages])

        threads = [
            threading.Thread(target=take, args=(index % 2 == 0,)) for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        ids = [message_id for batch in taken for message_id in batch]
        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_records_follow_their_channel_rules(self):
        queue = MessageQueue()
        queue.enqueue("随提问", [], [])
        queue.deliver_with_question("session-1")
        queue.enqueue("随工具", [], [])
        queue.deliver_with_tool_call()
        queue.enqueue("排队中", [], [])

        kept = queue.visible_messages({"session-1"})
        assert [message.text for message in kept] == ["随提问", "随工具", "排队中"]

        # 随提问送达的记录跟着会话走，会话没了它也丢弃；随工具调用的和排队中的不受影响
        kept = queue.visible_messages(set())
        assert [message.text for message in kept] == ["随工具", "排队中"]

    def test_tool_call_records_keep_only_the_latest(self):
        queue = MessageQueue()
        queue.enqueue("随提问", [], [])
        queue.deliver_with_question("session-1")
        for index in range(MAX_TOOL_CALL_RECORDS + 5):
            queue.enqueue(f"第 {index} 条", [], [])
            queue.deliver_with_tool_call()
        queue.enqueue("还在排队", [], [])

        texts = [message.text for message in queue.visible_messages({"session-1"})]

        assert len(texts) == MAX_TOOL_CALL_RECORDS + 2
        assert texts[0] == "随提问"
        assert texts[1] == "第 5 条"
        assert texts[-2] == f"第 {MAX_TOOL_CALL_RECORDS + 4} 条"
        assert texts[-1] == "还在排队"

    def test_pending_callback_fires_when_the_count_can_change(self):
        counts: list[int] = []
        queue = MessageQueue(
            on_pending_changed=lambda: counts.append(queue.pending_count())
        )

        first = queue.enqueue("甲", [], [])
        queue.enqueue("乙", [], [])
        queue.update_text(first.message_id, "改过")
        queue.cancel(first.message_id)
        queue.deliver_with_tool_call()
        queue.deliver_with_tool_call()

        # 加入两次、撤回一次、取走一次；改文字和空取不触发
        assert counts == [1, 2, 1, 0]

    def test_callback_errors_do_not_break_the_queue(self):
        def explode() -> None:
            raise RuntimeError("回调出错")

        queue = MessageQueue(on_pending_changed=explode)

        message = queue.enqueue("甲", [], [])
        assert queue.pending_count() == 1
        queue.cancel(message.message_id)
        assert queue.pending_count() == 0


class TestToolCallContext:
    """附在工具结果后面、交给 AI 的说明"""

    def test_lists_messages_in_order_with_image_paths(self):
        queue = MessageQueue()
        with_image = queue.enqueue("看看这张图", [image(PNG)], [])
        queue.enqueue("", [image(JPEG)], [])
        queue.enqueue("第三条", [], [])
        messages = queue.deliver_with_tool_call()
        paths = {with_image.message_id: [Path("/tmp/images/m1/1.png")]}

        context = MessageQueue.build_tool_call_context(messages, paths)

        assert context.startswith("（你工作期间，用户追加了 3 条消息")
        assert "处理完再调用 interactive_feedback 汇报" in context
        assert "（附 1 张图片，已存成本地文件，用 Read 查看）" in context
        assert "图片：/tmp/images/m1/1.png" in context
        assert context.index("看看这张图") < context.index("（无文字）")
        assert context.index("（无文字）") < context.index("第三条")
        assert context.count("[1] ") == context.count("[2] ") == 1

    def test_images_that_could_not_be_saved_are_not_mentioned(self):
        queue = MessageQueue()
        queue.enqueue("图没存成", [image(PNG)], [])

        context = MessageQueue.build_tool_call_context(
            queue.deliver_with_tool_call(), {}
        )

        assert "图片：" not in context
        assert "附 1 张图片" not in context
        assert "图没存成" in context


class TestQueueMarker:
    """队列里有排队中的消息时，房间的标记文件存在，钩子靠它判断要不要来取"""

    def test_marker_follows_pending_messages(self, isolated_feedback_home: Path):
        marker = QueueMarker(CONVERSATION_A, lambda: ROOM_URL)
        room = Room(CONVERSATION_A, marker)
        path = isolated_feedback_home / "queue" / CONVERSATION_A
        assert marker.path == path
        assert not path.exists()

        first = room.message_queue.enqueue("甲", [], [])
        assert path.read_text(encoding="utf-8") == f"{ROOM_URL}\n{os.getpid()}\n"
        second = room.message_queue.enqueue("乙", [], [])
        room.message_queue.cancel(first.message_id)
        assert path.exists()
        room.message_queue.cancel(second.message_id)
        assert not path.exists()
        assert list(path.parent.iterdir()) == []

    async def test_marker_disappears_once_a_tool_call_takes_the_messages(self):
        room = make_room()
        assert room.marker is not None
        room.message_queue.enqueue("甲", [], [])
        assert room.marker.path.exists()

        assert await room.deliver_for_tool_call() is not None

        assert not room.marker.path.exists()

    async def test_marker_disappears_once_a_question_takes_the_messages(self):
        room = make_room()
        assert room.marker is not None
        session = room.create_session("/tmp/project", "摘要")
        room.message_queue.enqueue("甲", [], [])
        assert room.marker.path.exists()

        assert await room.deliver_queued_messages(session) is not None

        assert not room.marker.path.exists()

    def test_marker_is_removed_when_the_room_is_cleaned_up(self):
        room = make_room()
        assert room.marker is not None
        room.message_queue.enqueue("甲", [], [])

        room.cleanup_all_sessions()

        assert not room.marker.path.exists()

    def test_room_url_is_looked_up_when_the_marker_is_written(self):
        urls = ["http://127.0.0.1:1/c/x", "http://127.0.0.1:2/c/x"]
        marker = QueueMarker(CONVERSATION_A, lambda: urls.pop(0))
        room = Room(CONVERSATION_A, marker)

        room.message_queue.enqueue("甲", [], [])
        assert marker.path.read_text(encoding="utf-8").startswith(
            "http://127.0.0.1:1/c/x\n"
        )
        room.message_queue.enqueue("乙", [], [])
        assert marker.path.read_text(encoding="utf-8").startswith(
            "http://127.0.0.1:2/c/x\n"
        )

    def test_marker_errors_do_not_break_the_queue(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        monkeypatch.setenv("GL_FEEDBACK_HOME", str(blocker))
        room = make_room()

        message = room.message_queue.enqueue("甲", [], [])
        assert room.message_queue.pending_count() == 1
        room.message_queue.cancel(message.message_id)
        assert room.message_queue.pending_count() == 0

    def test_default_room_has_no_marker(self, web_ui_manager: WebUIManager):
        assert web_ui_manager.get_or_create_room("").marker is None

        room = web_ui_manager.get_or_create_room(CONVERSATION_A)
        assert room.marker is not None
        assert room.marker.room_id == CONVERSATION_A

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("127.0.0.1", "127.0.0.1"),
            ("0.0.0.0", "127.0.0.1"),  # noqa: S104
            ("::", "127.0.0.1"),
            ("", "127.0.0.1"),
            ("192.168.1.5", "192.168.1.5"),
        ],
    )
    def test_hook_room_url(
        self, web_ui_manager: WebUIManager, host: str, expected: str
    ):
        web_ui_manager.host = host
        web_ui_manager.port = 8765

        assert web_ui_manager.get_hook_room_url(CONVERSATION_A) == (
            f"http://{expected}:8765/c/{CONVERSATION_A}"
        )

    def test_marker_url_points_at_the_room(self, web_ui_manager: WebUIManager):
        web_ui_manager.host = "127.0.0.1"
        web_ui_manager.port = 8765
        room = web_ui_manager.get_or_create_room(CONVERSATION_A)

        room.message_queue.enqueue("甲", [], [])

        assert room.marker is not None
        assert room.marker.path.read_text(encoding="utf-8").splitlines()[0] == (
            f"http://127.0.0.1:8765/c/{CONVERSATION_A}"
        )


class TestMessageImages:
    """钩子送达只能传文字，图片先存成本地文件"""

    def test_images_are_saved_with_the_right_extension(
        self, isolated_feedback_home: Path
    ):
        queue = MessageQueue()
        queue.enqueue(
            "多图",
            [image(PNG), image(JPEG), image(GIF), image(WEBP), image(b"unknown")],
            [],
        )
        queue.enqueue("没图", [], [])
        with_images, without_images = queue.deliver_with_tool_call()

        saved = save_message_images([with_images, without_images])

        assert set(saved) == {with_images.message_id}
        paths = saved[with_images.message_id]
        assert [path.name for path in paths] == [
            "1.png",
            "2.jpg",
            "3.gif",
            "4.webp",
            "5.png",
        ]
        assert paths[1].read_bytes() == JPEG
        images_root = isolated_feedback_home / "images"
        assert all(path.is_relative_to(images_root) for path in paths)

    def test_old_image_folders_are_pruned(self, isolated_feedback_home: Path):
        root = isolated_feedback_home / "images"
        old, fresh = root / "old", root / "fresh"
        for folder in (old, fresh):
            folder.mkdir(parents=True)
            (folder / "1.png").write_bytes(PNG)
        expired = time.time() - IMAGE_RETENTION_SECONDS - 60
        os.utime(old, (expired, expired))

        prune_message_images()

        assert not old.exists()
        assert fresh.exists()

    def test_saving_prunes_old_folders(self, isolated_feedback_home: Path):
        old = isolated_feedback_home / "images" / "old"
        old.mkdir(parents=True)
        expired = time.time() - IMAGE_RETENTION_SECONDS - 60
        os.utime(old, (expired, expired))

        save_message_images([])

        assert not old.exists()

    def test_unwritable_images_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        monkeypatch.setenv("GL_FEEDBACK_HOME", str(blocker))
        queue = MessageQueue()
        queue.enqueue("图存不下", [image(PNG)], [])

        assert save_message_images(queue.deliver_with_tool_call()) == {}


class TestRoomToolCallDelivery:
    """房间替钩子取走消息"""

    async def test_delivery_returns_context_and_notifies_the_page(self):
        room = make_room()
        session = room.create_session("/tmp/project", "摘要")
        websocket = FakeWebSocket()
        session.websocket = cast(WebSocket, websocket)
        room.message_queue.enqueue("先改这个", [], [])
        room.message_queue.enqueue("再改那个", [], [])

        context = await room.deliver_for_tool_call()

        assert context is not None
        assert "用户追加了 2 条消息" in context
        assert context.index("先改这个") < context.index("再改那个")
        assert websocket.sent == [{"type": "queue_delivered", "count": 2}]
        assert room.message_queue.pending_count() == 0
        # 提问本身不受影响，仍在等用户回复
        assert session.is_awaiting_feedback()

    async def test_empty_queue_returns_none_and_stays_quiet(self):
        room = make_room()
        session = room.create_session("/tmp/project", "摘要")
        websocket = FakeWebSocket()
        session.websocket = cast(WebSocket, websocket)

        assert await room.deliver_for_tool_call() is None
        assert websocket.sent == []

    async def test_images_become_local_files_named_in_the_context(
        self, isolated_feedback_home: Path
    ):
        room = make_room()
        room.message_queue.enqueue("看图", [image(PNG)], [])

        context = await room.deliver_for_tool_call()

        [saved] = (isolated_feedback_home / "images").glob("*/1.png")
        assert saved.read_bytes() == PNG
        assert context is not None
        assert f"图片：{saved}" in context

    async def test_works_without_an_open_page(self):
        room = make_room()
        room.message_queue.enqueue("页面关了", [], [])

        context = await room.deliver_for_tool_call()

        assert context is not None
        assert "页面关了" in context

    async def test_page_errors_do_not_lose_the_messages(self):
        room = make_room()
        session = room.create_session("/tmp/project", "摘要")
        session.websocket = cast(WebSocket, BrokenWebSocket())
        room.message_queue.enqueue("页面连接坏了", [], [])

        context = await room.deliver_for_tool_call()

        assert context is not None
        assert "页面连接坏了" in context

    async def test_a_stuck_page_does_not_hold_up_the_hook(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(Room, "NOTIFY_TIMEOUT_SECONDS", 0.05)
        room = make_room()
        session = room.create_session("/tmp/project", "摘要")
        session.websocket = cast(WebSocket, HangingWebSocket())
        room.message_queue.enqueue("页面卡住了", [], [])

        context = await asyncio.wait_for(room.deliver_for_tool_call(), 2)

        assert context is not None
        assert "页面卡住了" in context

    async def test_tool_call_delivery_then_question_gets_nothing_twice(self):
        room = make_room()
        room.message_queue.enqueue("只能送一次", [], [])
        assert await room.deliver_for_tool_call() is not None

        session = room.create_session("/tmp/project", "下一问")

        assert await room.deliver_queued_messages(session) is None
        assert session.is_awaiting_feedback()

    async def test_question_delivery_then_tool_call_gets_nothing_twice(self):
        room = make_room()
        room.message_queue.enqueue("只能送一次", [], [])
        session = room.create_session("/tmp/project", "下一问")
        assert await room.deliver_queued_messages(session) is not None

        assert await room.deliver_for_tool_call() is None


class TestTakeRoute:
    """钩子调用的取走接口"""

    @pytest.fixture
    def client(self, web_ui_manager: WebUIManager) -> TestClient:
        for conversation_id in (CONVERSATION_A, CONVERSATION_B):
            web_ui_manager.create_session(
                "/tmp/project", "提问", conversation_id=conversation_id
            )
        return TestClient(web_ui_manager.app)

    @staticmethod
    def room_of(manager: WebUIManager, conversation_id: str) -> Room:
        room = manager.get_room(conversation_id)
        assert room is not None
        return room

    def test_take_returns_the_context_once(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room = self.room_of(web_ui_manager, CONVERSATION_A)
        room.message_queue.enqueue("要取走的消息", [], [])

        response = client.post(f"/c/{CONVERSATION_A}/api/queue/take")

        assert response.status_code == 200
        body = response.json()
        assert list(body) == ["additional_context"]
        assert "要取走的消息" in body["additional_context"]
        assert client.post(f"/c/{CONVERSATION_A}/api/queue/take").json() == {}

    def test_take_only_touches_its_own_room(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room_a = self.room_of(web_ui_manager, CONVERSATION_A)
        room_a.message_queue.enqueue("只给 A", [], [])

        assert client.post(f"/c/{CONVERSATION_B}/api/queue/take").json() == {}
        assert room_a.message_queue.pending_count() == 1

    def test_take_from_an_unknown_room_is_404(self, client: TestClient):
        response = client.post("/c/not-created/api/queue/take")

        assert response.status_code == 404
        assert response.json()["messageCode"] == "room.notFound"

    def test_sessions_api_reports_how_messages_were_delivered(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room = self.room_of(web_ui_manager, CONVERSATION_A)
        room.message_queue.enqueue("随工具", [], [])
        client.post(f"/c/{CONVERSATION_A}/api/queue/take")
        room.message_queue.enqueue("随提问", [], [])
        session = room.create_session("/tmp/project", "下一问")
        room.message_queue.deliver_with_question(session.session_id)
        room.message_queue.enqueue("还在排队", [], [])

        queued = client.get(f"/c/{CONVERSATION_A}/api/all-sessions").json()[
            "queued_messages"
        ]

        assert [
            (item["feedback"], item["delivered_via"], item["delivered_session_id"])
            for item in queued
        ] == [
            ("随工具", "tool_call", None),
            ("随提问", "question", session.session_id),
            ("还在排队", None, None),
        ]
        assert [item["delivered_at"] is not None for item in queued] == [
            True,
            True,
            False,
        ]

    def test_cancelling_a_taken_message_is_a_conflict(
        self, client: TestClient, web_ui_manager: WebUIManager
    ):
        room = self.room_of(web_ui_manager, CONVERSATION_A)
        message = room.message_queue.enqueue("刚被取走", [], [])
        client.post(f"/c/{CONVERSATION_A}/api/queue/take")

        response = client.delete(f"/c/{CONVERSATION_A}/api/queue/{message.message_id}")

        assert response.status_code == 409


def run_hook(
    mode: str,
    payload: dict[str, Any] | str,
    home: Path,
    *,
    inject_root: str = PROJECT_ROOT,
) -> dict[str, Any]:
    """用给定的输入运行钩子脚本，要求它输出一个合法的 JSON 对象、退出码为 0"""
    environment = {
        **os.environ,
        "GL_FEEDBACK_HOME": str(home),
        "GL_INJECT_ROOT": inject_root,
    }
    result = subprocess.run(
        [str(HOOK_SCRIPT), mode],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert isinstance(output, dict)
    return output


def pre_payload(
    tool_input: dict[str, Any] | str,
    *,
    conversation_id: str = CONVERSATION_A,
    workspace_roots: tuple[str, ...] = (PROJECT_ROOT,),
) -> dict[str, Any]:
    return {
        "hook_event_name": "preToolUse",
        "conversation_id": conversation_id,
        "workspace_roots": list(workspace_roots),
        "tool_name": "MCP:interactive_feedback",
        "tool_input": tool_input,
    }


def post_payload(conversation_id: str = CONVERSATION_A) -> dict[str, Any]:
    return {
        "hook_event_name": "postToolUse",
        "conversation_id": conversation_id,
        "tool_name": "Shell",
    }


def read_payload(
    file_path: str, conversation_id: str = CONVERSATION_A
) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "hook_event_name": "postToolUse",
    }


def write_marker(home: Path, room_id: str, url: str, pid: int) -> Path:
    marker = home / "queue" / room_id
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{url}\n{pid}\n", encoding="utf-8")
    return marker


def dead_pid() -> int:
    """一个已经结束的进程的编号"""
    process = subprocess.Popen(["true"])
    process.wait()
    return process.pid


def unused_port() -> int:
    """一个此刻没有进程监听的端口"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubQueueServer:
    """假的房间服务：记下收到的请求，按设定回复"""

    def __init__(self) -> None:
        self.reply: tuple[int, str] = (200, "{}")
        self.requests: list[tuple[str, str]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                stub.requests.append((self.command, self.path))
                status, body = stub.reply
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def room_url(self, room_id: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/c/{room_id}"


@pytest.fixture
def stub_server() -> Generator[StubQueueServer, None, None]:
    stub = StubQueueServer()
    stub.thread.start()
    yield stub
    stub.server.shutdown()
    stub.server.server_close()


@requires_hook_tools
class TestHookPre:
    """preToolUse：给 interactive_feedback 的入参填对话标识"""

    def test_fills_in_the_conversation_id_and_drops_title(self, tmp_path: Path):
        output = run_hook(
            "pre",
            pre_payload(
                {"summary": "摘要", "title": "标题", "project_directory": "/work/p"}
            ),
            tmp_path,
        )

        assert output == {
            "permission": "allow",
            "updated_input": {
                "summary": "摘要",
                "project_directory": "/work/p",
                "conversation_id": CONVERSATION_A,
            },
        }

    def test_overrides_an_id_the_ai_made_up(self, tmp_path: Path):
        output = run_hook(
            "pre",
            pre_payload({"summary": "摘要", "conversation_id": "ai-made-this-up"}),
            tmp_path,
        )

        assert output["updated_input"]["conversation_id"] == CONVERSATION_A

    def test_accepts_tool_input_given_as_a_json_string(self, tmp_path: Path):
        output = run_hook(
            "pre", pre_payload(json.dumps({"summary": "摘要", "title": "t"})), tmp_path
        )

        assert output["updated_input"] == {
            "summary": "摘要",
            "conversation_id": CONVERSATION_A,
        }

    def test_other_projects_only_get_the_stray_fields_removed(self, tmp_path: Path):
        output = run_hook(
            "pre",
            pre_payload(
                {"summary": "摘要", "title": "t", "conversation_id": "ai-made-this-up"},
                workspace_roots=("/work/other",),
            ),
            tmp_path,
        )

        assert output == {"permission": "allow", "updated_input": {"summary": "摘要"}}

    def test_clean_calls_of_other_projects_are_left_alone(self, tmp_path: Path):
        output = run_hook(
            "pre",
            pre_payload({"summary": "摘要"}, workspace_roots=("/work/other",)),
            tmp_path,
        )

        assert output == {"permission": "allow"}

    @pytest.mark.parametrize(
        "conversation_id", ["", "../etc/passwd", "has space", "a" * 129]
    )
    def test_never_injects_an_invalid_id(self, tmp_path: Path, conversation_id: str):
        output = run_hook(
            "pre",
            pre_payload({"summary": "摘要"}, conversation_id=conversation_id),
            tmp_path,
        )

        assert output == {"permission": "allow"}

    @pytest.mark.parametrize("payload", ["", "not json", "[]", '{"tool_input": 3}'])
    def test_lets_the_call_through_when_the_input_is_unusable(
        self, tmp_path: Path, payload: str
    ):
        assert run_hook("pre", payload, tmp_path) == {"permission": "allow"}


@requires_hook_tools
class TestHookPost:
    """postToolUse：这个对话有排队的消息就取走，交给 AI"""

    def test_nothing_happens_without_a_marker(self, tmp_path: Path):
        assert run_hook("post", post_payload(), tmp_path) == {}

    @pytest.mark.parametrize(
        "payload", ["", "not json", "{}", '{"tool_name": "Shell"}']
    )
    def test_nothing_happens_without_a_conversation_id(
        self, tmp_path: Path, payload: str
    ):
        marker = write_marker(
            tmp_path, CONVERSATION_A, "http://127.0.0.1:1/c/x", dead_pid()
        )

        assert run_hook("post", payload, tmp_path) == {}
        assert marker.exists()

    def test_ids_cannot_escape_the_queue_folder(self, tmp_path: Path):
        (tmp_path / "queue").mkdir()
        outside = tmp_path / "outside"
        outside.write_text(f"http://127.0.0.1:1/x\n{dead_pid()}\n", encoding="utf-8")

        assert run_hook("post", post_payload("../outside"), tmp_path) == {}
        assert outside.exists()

    def test_a_marker_of_a_dead_server_is_removed(self, tmp_path: Path):
        marker = write_marker(
            tmp_path, CONVERSATION_A, "http://127.0.0.1:1/c/x", dead_pid()
        )

        assert run_hook("post", post_payload(), tmp_path) == {}
        assert not marker.exists()

    def test_the_marker_stays_when_the_server_does_not_answer(self, tmp_path: Path):
        marker = write_marker(
            tmp_path,
            CONVERSATION_A,
            f"http://127.0.0.1:{unused_port()}/c/{CONVERSATION_A}",
            os.getpid(),
        )

        assert run_hook("post", post_payload(), tmp_path) == {}
        assert marker.exists()

    def test_the_server_reply_is_passed_through(
        self, tmp_path: Path, stub_server: StubQueueServer
    ):
        stub_server.reply = (200, '{"additional_context": "来自服务端的说明"}')
        write_marker(
            tmp_path, CONVERSATION_A, stub_server.room_url(CONVERSATION_A), os.getpid()
        )

        output = run_hook("post", post_payload(), tmp_path)

        assert output == {"additional_context": "来自服务端的说明"}
        assert stub_server.requests == [("POST", f"/c/{CONVERSATION_A}/api/queue/take")]

    @pytest.mark.parametrize(
        "payload",
        [
            read_payload(
                "/Users/gulei/.cursor/projects/Users-gulei-Documents-demo/terminals/369082.txt"
            ),
            read_payload("/home/dev/.cursor/projects/demo/terminals/1.txt"),
            # 冒号两边带空格
            (
                f'{{"conversation_id" : "{CONVERSATION_A}", "tool_name" : "Read", '
                '"tool_input" : {"file_path" : "/p/terminals/9.txt"}}'
            ),
        ],
        ids=["cursor-project", "other-home", "spaces"],
    )
    def test_internal_polls_of_terminal_files_leave_the_messages(
        self,
        tmp_path: Path,
        stub_server: StubQueueServer,
        payload: dict[str, Any] | str,
    ):
        # AwaitShell 轮询终端输出文件产生的事件，送出去的内容会被丢弃，不能在这时取走
        stub_server.reply = (200, '{"additional_context": "不该被取走"}')
        marker = write_marker(
            tmp_path, CONVERSATION_A, stub_server.room_url(CONVERSATION_A), os.getpid()
        )

        assert run_hook("post", payload, tmp_path) == {}

        assert stub_server.requests == []
        assert marker.exists()

    @pytest.mark.parametrize(
        "payload",
        [
            read_payload("/Users/gulei/Documents/demo/hooks/gl-feedback.sh"),
            # 路径里有 terminals，但不是终端输出文件
            read_payload("/Users/gulei/demo/terminals/notes.md"),
            read_payload("/Users/gulei/demo/terminals.txt"),
            # 不是 Read，只是命令里提到了终端输出文件
            {
                "conversation_id": CONVERSATION_A,
                "tool_name": "Shell",
                "tool_input": {"command": "cat /p/terminals/1.txt"},
            },
        ],
        ids=["real-read", "not-a-txt", "not-a-folder", "shell-command"],
    )
    def test_other_tool_calls_still_deliver(
        self, tmp_path: Path, stub_server: StubQueueServer, payload: dict[str, Any]
    ):
        stub_server.reply = (200, '{"additional_context": "来自服务端的说明"}')
        write_marker(
            tmp_path, CONVERSATION_A, stub_server.room_url(CONVERSATION_A), os.getpid()
        )

        output = run_hook("post", payload, tmp_path)

        assert output == {"additional_context": "来自服务端的说明"}
        assert len(stub_server.requests) == 1

    @pytest.mark.parametrize(
        "reply",
        [
            (200, ""),
            (200, "<html>不是 JSON</html>"),
            (500, "Internal Server Error"),
            (404, '{"detail": "Not Found"}'),
        ],
    )
    def test_replies_that_are_not_a_success_object_are_ignored(
        self, tmp_path: Path, stub_server: StubQueueServer, reply: tuple[int, str]
    ):
        stub_server.reply = reply
        write_marker(
            tmp_path, CONVERSATION_A, stub_server.room_url(CONVERSATION_A), os.getpid()
        )

        assert run_hook("post", post_payload(), tmp_path) == {}

    @pytest.mark.parametrize(
        "payload",
        [
            # 冒号两边带空格
            f'{{"conversation_id" :  "{CONVERSATION_A}", "tool_name": "Shell"}}',
            # 排在很大的工具输出后面，开头一段里找不到，要走完整解析
            json.dumps(
                {"tool_output": "x" * 100_000, "conversation_id": CONVERSATION_A}
            ),
            # 工具输出里恰好出现同样的字样（被转义了），不能当成对话标识
            json.dumps(
                {
                    "tool_output": '"conversation_id": "other"',
                    "conversation_id": CONVERSATION_A,
                }
            ),
        ],
        ids=["spaces", "after-huge-output", "escaped-lookalike"],
    )
    def test_finds_the_conversation_id_in_different_payload_shapes(
        self, tmp_path: Path, payload: str
    ):
        # 死进程的标记会被钩子删掉，借它判断钩子确实找到了对话标识
        marker = write_marker(
            tmp_path, CONVERSATION_A, "http://127.0.0.1:1/c/x", dead_pid()
        )
        lookalike = write_marker(
            tmp_path, "other", "http://127.0.0.1:1/c/x", dead_pid()
        )

        assert run_hook("post", payload, tmp_path) == {}

        assert not marker.exists()
        assert lookalike.exists()


@requires_hook_tools
class TestHookLog:
    """sessionStart、sessionEnd、stop：只记录事件"""

    def test_records_only_whitelisted_fields(self, tmp_path: Path):
        output = run_hook(
            "log",
            {
                "hook_event_name": "stop",
                "conversation_id": CONVERSATION_A,
                "status": "completed",
                "workspace_roots": [PROJECT_ROOT],
                "prompt": "不该被记录的用户输入",
                "tool_input": {"summary": "不该被记录的摘要"},
            },
            tmp_path,
        )

        assert output == {}
        log_text = (tmp_path / "hook-events.log").read_text(encoding="utf-8")
        [line] = log_text.splitlines()
        record = json.loads(line)
        assert record["event"] == "stop"
        assert record["conversation_id"] == CONVERSATION_A
        assert record["status"] == "completed"
        assert record["workspace_roots"] == [PROJECT_ROOT]
        assert "不该被记录" not in log_text

    def test_starts_a_new_file_when_the_log_is_too_big(self, tmp_path: Path):
        log = tmp_path / "hook-events.log"
        log.write_bytes(b"x" * (1024 * 1024 + 1))

        run_hook("log", {"hook_event_name": "sessionStart"}, tmp_path)

        assert (tmp_path / "hook-events.log.1").stat().st_size == 1024 * 1024 + 1
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1


@requires_hook_tools
class TestHookScript:
    """不论什么输入，脚本都输出合法 JSON、退出码为 0"""

    @pytest.mark.parametrize("mode", ["pre", "post", "log", "unknown", ""])
    @pytest.mark.parametrize("payload", ["", "not json", "{}", "[1, 2]"])
    def test_always_answers_with_valid_json(
        self, tmp_path: Path, mode: str, payload: str
    ):
        output = run_hook(mode, payload, tmp_path)

        assert output == ({"permission": "allow"} if mode == "pre" else {})

    def test_unknown_mode_does_nothing(self, tmp_path: Path):
        run_hook("unknown", post_payload(), tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_script_is_executable(self):
        assert os.access(HOOK_SCRIPT, os.X_OK)


async def run_hook_async(
    mode: str, payload: dict[str, Any] | str, home: Path
) -> dict[str, Any]:
    return await asyncio.to_thread(run_hook, mode, payload, home)


async def receive_until(
    page: ClientConnection, condition: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """读页面收到的消息，直到遇到满足条件的那条"""
    async with asyncio.timeout(10):
        while True:
            message = json.loads(await page.recv())
            if condition(message):
                return message


@requires_hook_tools
class TestHookEndToEnd:
    """真实的 Web 服务 + 页面 WebSocket + 钩子脚本"""

    @staticmethod
    async def start_conversation(
        live_manager: tuple[WebUIManager, list[str]], conversation_id: str
    ) -> tuple[WebUIManager, Room, ClientConnection]:
        """AI 提了一问并得到回复，之后进入「AI 干活」阶段，页面仍连着"""
        manager, opened_urls = live_manager
        call = asyncio.create_task(
            launch_web_feedback_ui(
                "/tmp/project", "提问", 30, conversation_id=conversation_id
            )
        )
        await wait_until(lambda: len(opened_urls) >= 1)
        room = manager.get_room(conversation_id)
        assert room is not None

        page = await connect(f"ws://127.0.0.1:{manager.port}/c/{conversation_id}/ws")
        await page.send(json.dumps(page_message("好的")))
        result = await asyncio.wait_for(call, 10)
        assert result["interactive_feedback"] == "好的"
        return manager, room, page

    async def test_hook_delivers_messages_queued_while_the_ai_works(
        self, live_manager: tuple[WebUIManager, list[str]], isolated_feedback_home: Path
    ):
        manager, room, page = await self.start_conversation(
            live_manager, CONVERSATION_A
        )
        home = isolated_feedback_home
        async with page:
            await page.send(json.dumps(page_message("干活时发的第一条")))
            await page.send(json.dumps(page_message("看看这张图", [upload(PNG)])))
            await wait_until(lambda: room.message_queue.pending_count() == 2)
            assert room.marker is not None
            assert room.marker.path.exists()

            # 别的对话的钩子取不到
            assert (
                await run_hook_async("post", post_payload(CONVERSATION_B), home) == {}
            )
            assert room.message_queue.pending_count() == 2

            # AI 调用完一次工具：钩子取走
            output = await run_hook_async("post", post_payload(), home)

            context = output["additional_context"]
            assert context.index("干活时发的第一条") < context.index("看看这张图")
            [saved] = (home / "images").glob("*/1.png")
            assert saved.read_bytes() == PNG
            assert f"图片：{saved}" in context
            assert room.message_queue.pending_count() == 0
            assert not room.marker.path.exists()

            # 页面收到「已送达」通知，对话记录里这两条标着随工具调用送达
            delivered = await receive_until(
                page, lambda message: message["type"] == "queue_delivered"
            )
            assert delivered["count"] == 2
            async with httpx.AsyncClient(trust_env=False) as http:
                response = await http.get(
                    f"http://127.0.0.1:{manager.port}"
                    f"/c/{CONVERSATION_A}/api/all-sessions"
                )
            records = response.json()["queued_messages"]
            assert [record["delivered_via"] for record in records] == [
                "tool_call",
                "tool_call",
            ]

            # 取过一次，再取什么都没有
            assert await run_hook_async("post", post_payload(), home) == {}

    async def test_messages_taken_by_the_next_question_are_not_delivered_again(
        self, live_manager: tuple[WebUIManager, list[str]], isolated_feedback_home: Path
    ):
        _, room, page = await self.start_conversation(live_manager, CONVERSATION_A)
        async with page:
            await page.send(json.dumps(page_message("干活时发的")))
            await wait_until(lambda: room.message_queue.pending_count() == 1)
            assert room.marker is not None

            result = await asyncio.wait_for(
                launch_web_feedback_ui(
                    "/tmp/project", "下一问", 30, conversation_id=CONVERSATION_A
                ),
                10,
            )

            assert "干活时发的" in result["interactive_feedback"]
            assert not room.marker.path.exists()
            assert (
                await run_hook_async("post", post_payload(), isolated_feedback_home)
                == {}
            )

    async def test_a_message_sent_after_the_hook_waits_for_the_next_one(
        self, live_manager: tuple[WebUIManager, list[str]], isolated_feedback_home: Path
    ):
        _, room, page = await self.start_conversation(live_manager, CONVERSATION_A)
        home = isolated_feedback_home
        async with page:
            await page.send(json.dumps(page_message("第一批")))
            await wait_until(lambda: room.message_queue.pending_count() == 1)
            first = await run_hook_async("post", post_payload(), home)
            assert "第一批" in first["additional_context"]

            await page.send(json.dumps(page_message("第二批")))
            await wait_until(lambda: room.message_queue.pending_count() == 1)
            second = await run_hook_async("post", post_payload(), home)

            assert "第二批" in second["additional_context"]
            assert "第一批" not in second["additional_context"]
