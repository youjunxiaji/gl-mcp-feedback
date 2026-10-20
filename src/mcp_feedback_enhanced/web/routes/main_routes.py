#!/usr/bin/env python3
"""
主要路由处理
============

设置 Web UI 的主要路由和处理逻辑。
"""

import json
from typing import TYPE_CHECKING

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ... import __version__
from ...debug import web_debug_log as debug_log
from ..constants import get_message_code as get_msg_code
from ..models import MessageNotPendingError


if TYPE_CHECKING:
    from ..main import WebUIManager


# 使用统一的消息代码系统
# 从 ..constants 导入的 get_msg_code 函数会处理所有消息代码
# 旧的 key 会自动映射到新的常量


def setup_routes(manager: "WebUIManager"):
    """设置路由"""

    @manager.app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """统一反馈页面 - 重构后的主页面"""
        # 获取当前活跃会话
        current_session = manager.get_current_session()

        if not current_session:
            # 没有活跃会话时显示等待页面
            return manager.templates.TemplateResponse(
                request,
                "index.html",
                {
                    "title": "gl-mcp-feedback",
                    "has_session": False,
                    "version": __version__,
                },
            )

        feedback_title = current_session.title or ""
        page_title = (
            f"{feedback_title} · gl-mcp-feedback"
            if feedback_title
            else "gl-mcp-feedback"
        )

        return manager.templates.TemplateResponse(
            request,
            "feedback.html",
            {
                "project_directory": current_session.project_directory,
                "session_id": current_session.session_id,
                "title": page_title,
                "version": __version__,
                "has_session": True,
            },
        )

    @manager.app.get("/api/session-status")
    async def get_session_status():
        """获取当前会话状态"""
        current_session = manager.get_current_session()

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
                    "title": current_session.title,
                    "feedback_completed": current_session.feedback_completed.is_set(),
                },
            }
        )

    @manager.app.get("/api/current-session")
    async def get_current_session(request: Request):
        """获取当前会话详细信息"""
        current_session = manager.get_current_session()

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
                "title": current_session.title,
                "feedback_completed": current_session.feedback_completed.is_set(),
                "images_count": len(current_session.images),
            }
        )

    @manager.app.get("/api/all-sessions")
    async def get_all_sessions(request: Request):
        """获取所有会话的实时状态"""

        try:
            sessions_data = []

            for session_id, session in manager.sessions.items():
                session_info = {
                    "session_id": session.session_id,
                    "project_directory": session.project_directory,
                    "summary": session.summary,
                    "title": session.title,
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
                    "is_current": session == manager.current_session,
                }
                sessions_data.append(session_info)

            # 按创建时间排序（最新的在前）
            sessions_data.sort(key=lambda x: x["created_at"], reverse=True)

            # 用户在 AI 没有等回复时排队发送的消息（排队中的，以及随记录里的会话送达过的）
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
                    "delivered_session_id": message.delivered_session_id,
                }
                for message in manager.message_queue.visible_messages(
                    set(manager.sessions)
                )
            ]

            debug_log(f"返回 {len(sessions_data)} 个会话的实时状态")
            return JSONResponse(
                content={
                    "sessions": sessions_data,
                    "queued_messages": queued_data,
                    "queue_limit": manager.message_queue.max_pending,
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

    @manager.app.put("/api/queue/{message_id}")
    async def update_queued_message(message_id: str, request: Request):
        """修改一条还在排队的消息的文字"""
        try:
            data = await request.json()
        except ValueError:
            data = {}
        text = str(data.get("feedback", "")) if isinstance(data, dict) else ""

        try:
            manager.message_queue.update_text(message_id, text)
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

    @manager.app.delete("/api/queue/{message_id}")
    async def cancel_queued_message(message_id: str):
        """撤回一条还在排队的消息"""
        try:
            manager.message_queue.cancel(message_id)
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

    @manager.app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket 端点"""
        # 获取当前活跃会话
        session = manager.get_current_session()
        if not session:
            await websocket.close(code=4004, reason="No active session")
            return

        await websocket.accept()

        old_websocket = session.websocket
        if old_websocket and old_websocket != websocket:
            debug_log("会话已有 WebSocket 连接，通知旧连接让位后替换")
            try:
                await old_websocket.send_json({
                    "type": "connection_replaced",
                    "messageCode": get_msg_code("connection_replaced"),
                })
                await old_websocket.close(code=4001, reason="Replaced by new connection")
            except Exception as e:
                debug_log(f"关闭旧 WebSocket 连接时出错（可忽略）: {e}")

        session.websocket = websocket
        debug_log(f"WebSocket 连接建立: 当前活跃会话 {session.session_id}")

        # 发送连接成功消息
        try:
            await websocket.send_json(
                {
                    "type": "connection_established",
                    "messageCode": get_msg_code("websocket_connected"),
                }
            )

            # 检查是否有待发送的会话更新
            if getattr(manager, "_pending_session_update", False):
                debug_log("检测到待发送的会话更新，准备发送通知")
                await websocket.send_json(
                    {
                        "type": "session_updated",
                        "action": "new_session_created",
                        "messageCode": get_msg_code("new_session_created"),
                        "session_info": {
                            "project_directory": session.project_directory,
                            "summary": session.summary,
                            "title": session.title,
                            "session_id": session.session_id,
                        },
                    }
                )
                manager._pending_session_update = False
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

                current_session = manager.get_current_session()
                if current_session:
                    if current_session.websocket == websocket:
                        await handle_websocket_message(manager, current_session, message)
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
            current_session = manager.get_current_session()
            if current_session and current_session.websocket == websocket:
                current_session.websocket = None
                debug_log("已清理会话中的 WebSocket 连接")


async def handle_websocket_message(manager: "WebUIManager", session, data: dict):
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
            # AI 没有在等：进入队列，AI 下次提问时一并取走
            await manager.enqueue_user_message(session, feedback, images, settings)

    else:
        debug_log(f"未知的消息类型: {message_type}")
