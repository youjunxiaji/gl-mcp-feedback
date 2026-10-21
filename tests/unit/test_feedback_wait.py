#!/usr/bin/env python3
"""
等待回复与心跳测试
==================

Cursor 对每次 MCP 工具调用有 120 秒的空闲超时，每收到这次调用的一条进度通知就重新计时。
等用户回复期间服务端定时发心跳（进度通知），一次调用才能等够时限。
这里验证：心跳按间隔发出、出错不影响等待、用户回复后立即返回、到点报超时、被取消时不挂住，
以及从 MCP 工具到客户端收到进度通知的整条链路、`timeout` 的上限和用户没回复时给 AI 的提示。
"""

import asyncio
from collections.abc import Generator, Sequence

import pytest
from fastmcp import Client
from mcp.types import ContentBlock, TextContent

from mcp_feedback_enhanced import server
from mcp_feedback_enhanced.utils.error_handler import FeedbackTimeoutError
from mcp_feedback_enhanced.web.models import (
    HeartbeatCallback,
    SessionStatus,
    WebFeedbackSession,
)
from tests.helpers import wait_until


class HeartbeatRecorder:
    """记下每次心跳收到的参数"""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    async def __call__(self, elapsed: float, total: float) -> None:
        self.calls.append((elapsed, total))


class FakeLaunch:
    """替身：记下收到的参数，等用户回复期间发几次心跳，再按设定返回或抛错"""

    def __init__(self, *, heartbeats: int = 0, error: Exception | None = None) -> None:
        self.heartbeats = heartbeats
        self.error = error
        self.timeout: int | None = None

    async def __call__(
        self,
        project_dir: str,
        summary: str,
        timeout: int,
        *,
        conversation_id: str = "",
        on_heartbeat: HeartbeatCallback | None = None,
    ) -> dict:
        self.timeout = timeout
        for index in range(self.heartbeats):
            assert on_heartbeat is not None, "工具应当带着心跳回调进来"
            await on_heartbeat(30.0 * (index + 1), 595.0)
        if self.error is not None:
            raise self.error
        return {"interactive_feedback": "收到", "images": []}


def first_text(content: Sequence[ContentBlock]) -> str:
    """工具结果里的第一段文字"""
    block = content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.fixture
def session() -> Generator[WebFeedbackSession, None, None]:
    waiting = WebFeedbackSession("heartbeat-session", "/tmp/project", "摘要")
    yield waiting
    if waiting.cleanup_timer:
        waiting.cleanup_timer.cancel()


def speed_up(
    monkeypatch: pytest.MonkeyPatch,
    session: WebFeedbackSession,
    *,
    interval: float,
    wait: float,
) -> None:
    """把心跳间隔和实际等待时间缩短到测试能承受的量级"""
    monkeypatch.setattr(session, "HEARTBEAT_INTERVAL_SECONDS", interval)
    monkeypatch.setattr(
        WebFeedbackSession, "effective_wait_seconds", staticmethod(lambda _: wait)
    )


class TestEffectiveWaitSeconds:
    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [(600, 595), (31, 26), (30, 29), (10, 9), (3, 5)],
    )
    def test_waits_a_little_less_than_the_callers_limit(
        self, timeout: int, expected: int
    ) -> None:
        assert WebFeedbackSession.effective_wait_seconds(timeout) == expected


class TestWaitHeartbeat:
    async def test_heartbeat_is_sent_on_interval_until_the_reply_arrives(
        self, session: WebFeedbackSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        speed_up(monkeypatch, session, interval=0.05, wait=5)
        recorder = HeartbeatRecorder()

        task = asyncio.create_task(
            session.wait_for_feedback(600, on_heartbeat=recorder)
        )
        await wait_until(lambda: len(recorder.calls) >= 3)

        session.feedback_result = "好的"
        session.feedback_completed.set()
        result = await asyncio.wait_for(task, 2)

        assert result["interactive_feedback"] == "好的"
        elapsed = [elapsed for elapsed, _ in recorder.calls]
        assert elapsed == sorted(set(elapsed)), "已等待的秒数必须严格递增"
        assert {total for _, total in recorder.calls} == {5}

    async def test_waits_without_a_heartbeat_callback(
        self, session: WebFeedbackSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        speed_up(monkeypatch, session, interval=0.05, wait=5)

        task = asyncio.create_task(session.wait_for_feedback(600))
        await asyncio.sleep(0.15)
        assert not task.done()

        session.feedback_result = "好的"
        session.feedback_completed.set()
        result = await asyncio.wait_for(task, 2)

        assert result["interactive_feedback"] == "好的"

    async def test_reply_that_is_already_there_returns_at_once(
        self, session: WebFeedbackSession
    ) -> None:
        session.feedback_result = "早到的回复"
        session.feedback_completed.set()

        result = await asyncio.wait_for(session.wait_for_feedback(600), 2)

        assert result["interactive_feedback"] == "早到的回复"

    async def test_failing_heartbeat_does_not_break_the_wait(
        self, session: WebFeedbackSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        speed_up(monkeypatch, session, interval=0.05, wait=5)
        attempts = 0

        async def broken_heartbeat(elapsed: float, total: float) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("连接已断开")

        task = asyncio.create_task(
            session.wait_for_feedback(600, on_heartbeat=broken_heartbeat)
        )
        await wait_until(lambda: attempts >= 2)

        session.feedback_result = "好的"
        session.feedback_completed.set()
        result = await asyncio.wait_for(task, 2)

        assert result["interactive_feedback"] == "好的"

    async def test_no_reply_in_time_raises_feedback_timeout_error(
        self, session: WebFeedbackSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        speed_up(monkeypatch, session, interval=0.05, wait=0.3)
        recorder = HeartbeatRecorder()

        with pytest.raises(FeedbackTimeoutError) as raised:
            await session.wait_for_feedback(600, on_heartbeat=recorder)

        assert isinstance(raised.value, TimeoutError)
        assert raised.value.waited_seconds == 0.3
        assert session.status == SessionStatus.TIMEOUT
        assert recorder.calls, "等待期间应当发过心跳"
        assert all(elapsed < 0.3 for elapsed, _ in recorder.calls), (
            "到点的那一刻不再发心跳"
        )

    async def test_cancelled_wait_ends_the_session_and_frees_the_waiting_thread(
        self, session: WebFeedbackSession
    ) -> None:
        task = asyncio.create_task(session.wait_for_feedback(600))
        await asyncio.sleep(0.1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert session.status == SessionStatus.COMPLETED
        assert session.feedback_completed.is_set(), "等待线程靠这个事件放行"


class TestToolHeartbeat:
    async def test_heartbeats_reach_the_client_as_progress_notifications(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server, "launch_web_feedback_ui", FakeLaunch(heartbeats=2))
        received: list[tuple[float, float | None, str | None]] = []

        async def on_progress(
            progress: float, total: float | None, message: str | None
        ) -> None:
            received.append((progress, total, message))

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "interactive_feedback",
                {"summary": "测试", "timeout": 600},
                progress_handler=on_progress,
            )

        assert "收到" in first_text(result.content)
        assert received == [
            (30.0, 595.0, "等待用户回复，已等 30 秒"),
            (60.0, 595.0, "等待用户回复，已等 60 秒"),
        ]

    async def test_client_without_a_progress_token_still_gets_the_reply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 底层会话的 call_tool 不带进度处理器，请求里就没有进度令牌，发心跳是空操作，不报错
        monkeypatch.setattr(server, "launch_web_feedback_ui", FakeLaunch(heartbeats=2))

        async with Client(server.mcp) as client:
            result = await client.session.call_tool(
                "interactive_feedback", {"summary": "测试"}
            )

        assert not result.isError
        assert "收到" in first_text(result.content)

    @pytest.mark.parametrize(
        ("asked", "expected"),
        [(3600, server.MAX_FEEDBACK_TIMEOUT_SECONDS), (601, 600), (120, 120)],
    )
    async def test_timeout_is_capped_at_the_limit(
        self, monkeypatch: pytest.MonkeyPatch, asked: int, expected: int
    ) -> None:
        fake = FakeLaunch()
        monkeypatch.setattr(server, "launch_web_feedback_ui", fake)

        async with Client(server.mcp) as client:
            await client.call_tool(
                "interactive_feedback", {"summary": "测试", "timeout": asked}
            )

        assert fake.timeout == expected

    async def test_timeout_defaults_to_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeLaunch()
        monkeypatch.setattr(server, "launch_web_feedback_ui", fake)

        async with Client(server.mcp) as client:
            await client.call_tool("interactive_feedback", {"summary": "测试"})

        assert fake.timeout == server.MAX_FEEDBACK_TIMEOUT_SECONDS

    async def test_context_parameter_is_not_part_of_the_tool_schema(self) -> None:
        async with Client(server.mcp) as client:
            tools = await client.list_tools()

        tool = next(tool for tool in tools if tool.name == "interactive_feedback")
        assert set(tool.inputSchema["properties"]) == {
            "project_directory",
            "summary",
            "timeout",
            "conversation_id",
        }

    async def test_user_not_replying_tells_the_ai_to_ask_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            server,
            "launch_web_feedback_ui",
            FakeLaunch(error=FeedbackTimeoutError(595)),
        )

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "interactive_feedback", {"summary": "测试"}, raise_on_error=False
            )

        text = first_text(result.content)
        assert not result.is_error
        assert text.startswith("[interactive_feedback TIMEOUT]")
        assert "595 秒内没有回复" in text
        assert "再次调用 interactive_feedback" in text
        assert f"timeout 照旧传 {server.MAX_FEEDBACK_TIMEOUT_SECONDS}" in text

    async def test_other_timeouts_are_not_mistaken_for_the_user_not_replying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            server,
            "launch_web_feedback_ui",
            FakeLaunch(error=TimeoutError("socket timed out")),
        )

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "interactive_feedback", {"summary": "测试"}, raise_on_error=False
            )

        assert not first_text(result.content).startswith(
            "[interactive_feedback TIMEOUT]"
        )


class TestBuildHeartbeat:
    def test_no_context_means_no_heartbeat(self) -> None:
        assert server.build_heartbeat(None) is None
