#!/usr/bin/env python3
"""
主要路由处理
============

设置 Web UI 的主要路由和处理逻辑：

- ``/`` 是房间列表页，``/api/rooms`` 是它用的接口
- ``/c/{room_id}/`` 是一个对话的页面，页面用到的接口和 WebSocket 都在这个前缀下
"""

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ... import __version__
from ...debug import web_debug_log as debug_log
from ..constants import get_message_code as get_msg_code
from ..models import MessageNotPendingError, Room, WebFeedbackSession


if TYPE_CHECKING:
    from ..main import WebUIManager


def setup_routes(manager: "WebUIManager"):
    """设置路由"""

    def room_not_found() -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Room not found",
                "messageCode": get_msg_code("ROOM_NOT_FOUND"),
            },
        )

    @manager.app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """房间列表页：每个对话一个入口，没有房间时显示等待提示"""
        return manager.templates.TemplateResponse(
            request,
            "index.html",
            {"version": __version__},
        )

    @manager.app.get("/api/rooms")
    async def list_rooms():
        """所有房间，最近有动静的在前"""
        items = [room.to_list_item() for room in list(manager.rooms.values())]
        items.sort(key=lambda item: item["last_activity"], reverse=True)
        return JSONResponse(content={"rooms": items})

    router = APIRouter(prefix="/c/{room_id}")

    @router.get("/", response_class=HTMLResponse)
    async def room_page(request: Request, room_id: str):
        """一个对话的反馈页面"""
        room = manager.get_room(room_id)
        session = room.current_session if room else None

        if room is None or session is None:
            # 房间不存在、或房间里已经没有会话：回房间列表
            return RedirectResponse("/")

        return manager.templates.TemplateResponse(
            request,
            "feedback.html",
            {
                "project_directory": session.project_directory,
                "version": __version__,
                "room_base": f"/c/{room.room_id}",
                "page_title": room.page_title(),
            },
        )

    @router.get("/api/session-status")
    async def get_session_status(room_id: str):
        """获取房间当前会话状态"""
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        current_session = room.current_session
        if not current_session:
            return JSONResponse(
                content={
                    "has_session": False,
                    "status": "no_session",
                    "messageCode": get_msg_code("no_active_session"),
                }
            )

        return JSONResponse(
            content={
                "has_session": True,
                "status": "active",
                "session_info": {
                    "project_directory": current_session.project_directory,
                    "summary": current_session.summary,
                    "conversation_id": current_session.conversation_id,
                    "feedback_completed": current_session.feedback_completed.is_set(),
                },
            }
        )

    @router.get("/api/current-session")
    async def get_current_session(room_id: str):
        """获取房间当前会话详细信息"""
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        current_session = room.current_session
        if not current_session:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "No active session",
                    "messageCode": get_msg_code("no_active_session"),
                },
            )

        return JSONResponse(
            content={
                "session_id": current_session.session_id,
                "project_directory": current_session.project_directory,
                "summary": current_session.summary,
                "conversation_id": current_session.conversation_id,
                "feedback_completed": current_session.feedback_completed.is_set(),
                "images_count": len(current_session.images),
            }
        )

    @router.get("/api/all-sessions")
    async def get_all_sessions(room_id: str):
        """获取房间里所有会话的实时状态"""
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        try:
            sessions_data = []

            for session in list(room.sessions.values()):
                session_info = {
                    "session_id": session.session_id,
                    "project_directory": session.project_directory,
                    "summary": session.summary,
                    "conversation_id": session.conversation_id,
                    "status": session.status.value,
                    "status_message": session.status_message,
                    "created_at": int(session.created_at * 1000),
                    "last_activity": int(session.last_activity * 1000),
                    "feedback_completed": session.feedback_completed.is_set(),
                    "awaiting_feedback": session.is_awaiting_feedback(),
                    "feedback": session.feedback_result,
                    "feedback_submitted_at": (
                        int(session.feedback_submitted_at * 1000)
                        if session.feedback_submitted_at
                        else None
                    ),
                    "feedback_images": session.feedback_images,
                    "has_websocket": session.websocket is not None,
                    "is_current": session == room.current_session,
                }
                sessions_data.append(session_info)

            # 按创建时间排序（最新的在前）
            sessions_data.sort(key=lambda x: x["created_at"], reverse=True)

            # 用户在 AI 没有等回复时排队发送的消息：排队中的、最近随工具调用送达的、以及随记录里的会话送达过的
            queued_data = [
                {
                    "id": message.message_id,
                    "feedback": message.text,
                    "feedback_images": message.thumbnails,
                    "submitted_at": int(message.submitted_at * 1000),
                    "delivered_at": (
                        int(message.delivered_at * 1000)
                        if message.delivered_at
                        else None
                    ),
                    "delivered_via": message.delivered_via,
                    "delivered_session_id": message.delivered_session_id,
                }
                for message in room.message_queue.visible_messages(set(room.sessions))
            ]

            debug_log(f"房间 {room.room_id} 返回 {len(sessions_data)} 个会话的实时状态")
            return JSONResponse(
                content={
                    "sessions": sessions_data,
                    "queued_messages": queued_data,
                    "queue_limit": room.message_queue.max_pending,
                }
            )

        except Exception as e:
            debug_log(f"获取所有会话状态失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"Failed to get sessions: {e!s}",
                    "messageCode": get_msg_code("get_sessions_failed"),
                },
            )

    @router.put("/api/queue/{message_id}")
    async def update_queued_message(room_id: str, message_id: str, request: Request):
        """修改一条还在排队的消息的文字"""
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        try:
            data = await request.json()
        except ValueError:
            data = {}
        text = str(data.get("feedback", "")) if isinstance(data, dict) else ""

        try:
            room.message_queue.update_text(message_id, text)
        except MessageNotPendingError:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "messageCode": get_msg_code("QUEUE_MESSAGE_NOT_PENDING"),
                },
            )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "messageCode": get_msg_code("QUEUE_MESSAGE_EMPTY"),
                },
            )
        return JSONResponse(
            content={
                "status": "success",
                "messageCode": get_msg_code("QUEUE_MESSAGE_UPDATED"),
            }
        )

    @router.post("/api/queue/take")
    async def take_queued_messages(room_id: str):
        """Cursor 钩子在 AI 调用完一次工具后来取排队的消息

        Returns:
            有排队消息时是 ``{"additional_context": "..."}``，钩子原样交给 Cursor，
            文字会附在那次工具结果后面；没有时是空对象。
        """
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        context = await room.deliver_for_tool_call()
        return JSONResponse(content={"additional_context": context} if context else {})

    @router.post("/api/turn-ended")
    async def turn_ended(room_id: str, request: Request):
        """Cursor 的 stop 钩子在对话的一个回合结束时来报告

        请求体 ``{"status": "completed"}``，status 是钩子拿到的回合结束状态。
        正常结束（completed）的房间几秒后销毁，其他状态只标记。

        Returns:
            ``{"ended": true}`` 表示房间记下了结束标记，``false`` 表示被忽略
            （AI 的调用还在等回复，或者是默认房间）。
        """
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        try:
            data = await request.json()
        except ValueError:
            data = {}
        status = str(data.get("status", "")) if isinstance(data, dict) else ""
        return JSONResponse(content={"ended": manager.end_turn(room, status)})

    @router.delete("/api/queue/{message_id}")
    async def cancel_queued_message(room_id: str, message_id: str):
        """撤回一条还在排队的消息"""
        room = manager.get_room(room_id)
        if room is None:
            return room_not_found()

        try:
            room.message_queue.cancel(message_id)
        except MessageNotPendingError:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "messageCode": get_msg_code("QUEUE_MESSAGE_NOT_PENDING"),
                },
            )
        return JSONResponse(
            content={
                "status": "success",
                "messageCode": get_msg_code("QUEUE_MESSAGE_CANCELLED"),
            }
        )

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, room_id: str):
        """房间页面的 WebSocket 端点"""
        room = manager.get_room(room_id)
        session = room.current_session if room else None
        if room is None or session is None:
            await websocket.close(code=4004, reason="No active session")
            return

        await websocket.accept()

        old_websocket = session.websocket
        if old_websocket and old_websocket != websocket:
            debug_log("会话已有 WebSocket 连接，通知旧连接让位后替换")
            try:
                await old_websocket.send_json(
                    {
                        "type": "connection_replaced",
                        "messageCode": get_msg_code("connection_replaced"),
                    }
                )
                await old_websocket.close(
                    code=4001, reason="Replaced by new connection"
                )
            except Exception as e:
                debug_log(f"关闭旧 WebSocket 连接时出错（可忽略）: {e}")

        session.websocket = websocket
        room.touch()
        debug_log(
            f"WebSocket 连接建立: 房间 {room.room_id} 的当前会话 {session.session_id}"
        )

        # 发送连接成功消息
        try:
            await websocket.send_json(
                {
                    "type": "connection_established",
                    "messageCode": get_msg_code("websocket_connected"),
                }
            )

            # 检查是否有待发送的会话更新
            if room.pending_session_update:
                debug_log("检测到待发送的会话更新，准备发送通知")
                await websocket.send_json(
                    {
                        "type": "session_updated",
                        "action": "new_session_created",
                        "messageCode": get_msg_code("new_session_created"),
                        "session_info": {
                            "project_directory": session.project_directory,
                            "summary": session.summary,
                            "session_id": session.session_id,
                        },
                    }
                )
                room.pending_session_update = False
                debug_log("✅ 已发送会话更新通知到前端")
            else:
                # 发送当前会话状态
                await websocket.send_json(
                    {"type": "status_update", "status_info": session.get_status_info()}
                )
                debug_log("已发送当前会话状态到前端")

        except Exception as e:
            debug_log(f"发送连接确认失败: {e}")

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                current_session = room.current_session
                if current_session:
                    if current_session.websocket == websocket:
                        await handle_websocket_message(room, current_session, message)
                    else:
                        debug_log("此 WebSocket 已被新连接替代，关闭当前连接")
                        break
                else:
                    debug_log("没有活跃会话，关闭 WebSocket 连接")
                    break

        except WebSocketDisconnect:
            debug_log("WebSocket 连接正常断开")
        except ConnectionResetError:
            debug_log("WebSocket 连接被重置")
        except Exception as e:
            debug_log(f"WebSocket 错误: {e}")
        finally:
            current_session = room.current_session
            if current_session and current_session.websocket == websocket:
                current_session.websocket = None
                debug_log("已清理会话中的 WebSocket 连接")

    manager.app.include_router(router)


async def handle_websocket_message(room: Room, session: WebFeedbackSession, data: dict):
    """处理 WebSocket 消息"""
    message_type = data.get("type")

    if message_type == "submit_feedback":
        feedback = data.get("feedback", "")
        images = data.get("images", [])
        settings = data.get("settings", {})
        if session.is_awaiting_feedback():
            # AI 正在等回复：作为对当前提问的回复
            await session.submit_feedback(feedback, images, settings)
        else:
            # AI 没有在等：进入房间的队列，AI 下次调用工具（钩子来取）或提问时一并取走
            await room.enqueue_user_message(session, feedback, images, settings)

    else:
        debug_log(f"未知的消息类型: {message_type}")
