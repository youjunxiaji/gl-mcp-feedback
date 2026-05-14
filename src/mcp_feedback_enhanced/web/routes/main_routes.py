#!/usr/bin/env python3
"""
主要路由处理
============

设置 Web UI 的主要路由和处理逻辑。
"""

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ... import __version__
from ...debug import web_debug_log as debug_log
from ..constants import get_message_code as get_msg_code


if TYPE_CHECKING:
    from ..main import WebUIManager


# 使用统一的消息代码系统
# 从 ..constants 导入的 get_msg_code 函数会处理所有消息代码
# 旧的 key 会自动映射到新的常量


def setup_routes(manager: "WebUIManager"):
    """设置路由"""

    def require_hub_auth(request: Request):
        """校验 managed Web Hub 的本地 API token。"""
        if not getattr(manager, "hub_token", ""):
            return None
        expected = f"Bearer {manager.hub_token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "Invalid hub token"},
            )
        return None

    def build_session_info(session) -> dict:
        """构建前端/API 共用的会话信息。"""
        source_info = getattr(session, "source_info", {}) or {}
        source_project_directory = source_info.get(
            "project_directory", session.project_directory
        )
        source_project_name = (
            source_info.get("project_name")
            or Path(source_project_directory or session.project_directory).name
        )
        return {
            "session_id": session.session_id,
            "project_directory": session.project_directory,
            "source_info": source_info,
            "source_project_name": source_project_name,
            "source_project_directory": source_project_directory,
            "summary": session.summary,
            "title": session.title,
            "status": session.status.value,
            "status_message": session.status_message,
            "created_at": int(session.created_at * 1000),
            "last_activity": int(session.last_activity * 1000),
            "feedback_completed": session.feedback_completed.is_set(),
            "images_count": len(session.images),
            "has_websocket": session.websocket is not None,
            "is_current": session == manager.current_session,
            "can_submit": not session.feedback_completed.is_set()
            and not session.is_terminal(),
            "user_messages": session.user_messages,
            "feedback_timeout_seconds": session.feedback_timeout_seconds,
            "feedback_actual_timeout_seconds": session.feedback_actual_timeout_seconds,
            "feedback_deadline_at": (
                int(session.feedback_deadline_at * 1000)
                if session.feedback_deadline_at
                else None
            ),
            "keepalive_auto_extend_max": session.keepalive_auto_extend_max,
            "keepalive_auto_extend_used": session.keepalive_auto_extend_used,
            "keepalive_manual_extend_tokens": session.keepalive_manual_extend_tokens,
            "keepalive_last_reason": session.keepalive_last_reason,
        }

    def build_session_status_response() -> dict:
        """构建当前会话状态响应，同时兼容 /api/status 和 /api/session-status。"""
        current_session = manager.get_current_session()
        if not current_session:
            return {
                "has_session": False,
                "status": "no_session",
                "messageCode": get_msg_code("no_active_session"),
            }

        return {
            "has_session": True,
            "status": "active",
            "session_info": build_session_info(current_session),
        }

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
                    "title": "MCP Feedback Enhanced",
                    "has_session": False,
                    "version": __version__,
                },
            )

        feedback_title = current_session.title or ""
        page_title = (
            f"{feedback_title} · MCP Feedback"
            if feedback_title
            else "Interactive Feedback - 反馈收集"
        )

        return manager.templates.TemplateResponse(
            request,
            "feedback.html",
            {
                "project_directory": current_session.project_directory,
                "summary": current_session.summary,
                "session_id": current_session.session_id,
                "feedback_title": feedback_title,
                "title": page_title,
                "version": __version__,
                "has_session": True,
            },
        )

    @manager.app.get("/sw.js")
    async def service_worker():
        """为 PWA 安装能力提供根作用域 Service Worker。"""
        return FileResponse(
            Path(__file__).resolve().parent.parent / "static" / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @manager.app.get("/api/translations")
    async def get_translations():
        """获取翻译数据 - 从 Web 专用翻译档案加载"""
        translations = {}

        # 获取 Web 翻译档案目录
        web_locales_dir = Path(__file__).parent.parent / "locales"
        supported_languages = ["zh-TW", "zh-CN", "en"]

        for lang_code in supported_languages:
            lang_dir = web_locales_dir / lang_code
            translation_file = lang_dir / "translation.json"

            try:
                if translation_file.exists():
                    with open(translation_file, encoding="utf-8") as f:
                        lang_data = json.load(f)
                        translations[lang_code] = lang_data
                        debug_log(f"成功加载 Web 翻译: {lang_code}")
                else:
                    debug_log(f"Web 翻译档案不存在: {translation_file}")
                    translations[lang_code] = {}
            except Exception as e:
                debug_log(f"加载 Web 翻译档案失败 {lang_code}: {e}")
                translations[lang_code] = {}

        debug_log(f"Web 翻译 API 返回 {len(translations)} 种语言的数据")
        return JSONResponse(content=translations)

    @manager.app.get("/api/hub/health")
    async def hub_health(request: Request):
        """Managed Web Hub 健康检查。"""
        auth_error = require_hub_auth(request)
        if auth_error:
            return auth_error
        return JSONResponse(
            content={
                "app": "gl-mcp-feedback",
                "hub": bool(getattr(manager, "is_hub", False)),
                "version": __version__,
                "pid": os.getpid(),
                "host": manager.host,
                "port": manager.port,
                "active_sessions": len(manager.sessions),
            }
        )

    @manager.app.post("/api/page-heartbeat")
    async def page_heartbeat():
        """记录浏览器页面存活，覆盖等待页尚未建立 WebSocket 的场景。"""
        manager._page_last_seen_at = time.time()
        current_session = manager.get_current_session()
        return JSONResponse(
            content={
                "ok": True,
                "session_count": len(manager.sessions),
                "current_session_id": (
                    current_session.session_id if current_session else None
                ),
            }
        )

    @manager.app.post("/api/hub/sessions")
    async def hub_create_session(request: Request):
        """跨进程注册反馈会话到 managed Web Hub。"""
        auth_error = require_hub_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        project_directory = data.get("project_directory") or "."
        source = data.get("source") or {}
        source.setdefault("project_directory", project_directory)
        session_id = manager.create_session(
            project_directory,
            data.get("summary") or "",
            title=data.get("title") or "",
            session_id=data.get("session_id"),
            source_info=source,
        )
        session = manager.get_session(session_id)
        if not session:
            return JSONResponse(
                status_code=500,
                content={"error": "session_create_failed"},
            )

        timeout = int(data.get("timeout") or 600)
        session.start_feedback_wait_window(timeout)
        await manager.notify_existing_tab_to_refresh()
        if os.getenv("MCP_TEST_MODE", "").lower() != "true":
            await manager.smart_open_browser(manager.get_server_url())
        return JSONResponse(
            content={
                "session_id": session_id,
                "web_url": manager.get_server_url(),
                "session_info": build_session_info(session),
            }
        )

    @manager.app.post("/api/hub/sessions/{session_id}/wait")
    async def hub_wait_session(session_id: str, request: Request):
        """Long-poll 等待 managed Hub 中的反馈结果。"""
        auth_error = require_hub_auth(request)
        if auth_error:
            return auth_error

        session = manager.get_session(session_id)
        if not session:
            return JSONResponse(
                status_code=404,
                content={"error": "session_not_found", "session_id": session_id},
            )

        data = await request.json()
        timeout = int(data.get("timeout") or 600)
        try:
            result = await session.wait_for_feedback(timeout)
            return JSONResponse(content=result)
        except TimeoutError as exc:
            return JSONResponse(
                status_code=408,
                content={"error": "timeout", "message": str(exc)},
            )
        except Exception as exc:
            debug_log(f"Hub 等待会话 {session_id} 失败: {exc}")
            return JSONResponse(
                status_code=500,
                content={"error": "wait_failed", "message": str(exc)},
            )

    @manager.app.get("/api/session-status")
    async def get_session_status(request: Request):
        """获取当前会话状态"""
        return JSONResponse(content=build_session_status_response())

    @manager.app.get("/api/status")
    async def get_status(request: Request):
        """等待页兼容状态接口。"""
        return JSONResponse(content=build_session_status_response())

    @manager.app.get("/api/current-session")
    async def get_current_session(request: Request):
        """获取当前会话详细信息"""
        current_session = manager.get_current_session()

        # 从查询参数获取语言，如果没有则从会话获取，最后使用默认值

        if not current_session:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "No active session",
                    "messageCode": get_msg_code("no_active_session"),
                },
            )

        return JSONResponse(content=build_session_info(current_session))

    @manager.app.get("/api/session/{session_id}")
    async def get_session_detail(session_id: str):
        """获取指定会话详细信息。"""
        session = manager.get_session(session_id)
        if not session:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Session not found",
                    "messageCode": get_msg_code("no_active_session"),
                },
            )

        return JSONResponse(content=build_session_info(session))

    @manager.app.post("/api/session/{session_id}/title")
    async def update_session_title(session_id: str, request: Request):
        """更新指定会话标题，便于前端标签分辨。"""
        session = manager.get_session(session_id)
        if not session:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Session not found",
                    "messageCode": get_msg_code("no_active_session"),
                },
            )

        data = await request.json()
        title = str(data.get("title", "")).strip()[:80]
        session.title = title
        session.last_activity = time.time()

        return JSONResponse(content=build_session_info(session))

    @manager.app.get("/api/all-sessions")
    async def get_all_sessions(request: Request):
        """获取所有会话的实时状态"""

        try:
            sessions_data = []

            for session_id, session in manager.sessions.items():
                sessions_data.append(build_session_info(session))

            # 按创建时间排序（最新的在前）
            sessions_data.sort(key=lambda x: x["created_at"], reverse=True)

            debug_log(f"返回 {len(sessions_data)} 个会话的实时状态")
            return JSONResponse(content={"sessions": sessions_data})

        except Exception as e:
            debug_log(f"获取所有会话状态失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"Failed to get sessions: {e!s}",
                    "messageCode": get_msg_code("get_sessions_failed"),
                },
            )

    @manager.app.post("/api/add-user-message")
    async def add_user_message(request: Request):
        """添加用户消息到当前会话"""

        try:
            data = await request.json()
            current_session = manager.get_current_session()

            if not current_session:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "No active session",
                        "messageCode": get_msg_code("no_active_session"),
                    },
                )

            # 添加用户消息到会话
            current_session.add_user_message(data)

            debug_log(f"用户消息已添加到会话 {current_session.session_id}")
            return JSONResponse(
                content={
                    "status": "success",
                    "messageCode": get_msg_code("user_message_recorded"),
                }
            )

        except Exception as e:
            debug_log(f"添加用户消息失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"Failed to add user message: {e!s}",
                    "messageCode": get_msg_code("add_user_message_failed"),
                },
            )

    @manager.app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, lang: str = "zh-TW"):
        """旧版 WebSocket 端点，默认连接最近会话。"""
        session = manager.get_current_session()
        if not session:
            await websocket.close(code=4004, reason="No active session")
            return

        await connect_session_websocket(websocket, session, lang)

    @manager.app.websocket("/ws/{session_id}")
    async def websocket_session_endpoint(
        websocket: WebSocket, session_id: str, lang: str = "zh-TW"
    ):
        """按 session_id 绑定的 WebSocket 端点。"""
        session = manager.get_session(session_id)
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return

        await connect_session_websocket(websocket, session, lang)

    async def connect_session_websocket(websocket: WebSocket, session, lang: str):
        """建立指定会话的 WebSocket 连接。"""
        await websocket.accept()

        debug_log(f"WebSocket 连接建立，语言由前端处理: {lang}")

        old_websocket = session.websocket
        if old_websocket and old_websocket != websocket:
            debug_log("会话已有 WebSocket 连接，通知同会话旧连接让位后替换")
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
        debug_log(f"WebSocket 连接建立: 会话 {session.session_id}")

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
                        "session_info": build_session_info(session),
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

                active_session = manager.get_session(session.session_id)
                if active_session:
                    if active_session.websocket == websocket:
                        await handle_websocket_message(manager, active_session, message)
                    else:
                        debug_log("此会话 WebSocket 已被新连接替代，关闭当前连接")
                        break
                else:
                    debug_log("会话已不存在，关闭 WebSocket 连接")
                    break

        except WebSocketDisconnect:
            debug_log("WebSocket 连接正常断开")
        except ConnectionResetError:
            debug_log("WebSocket 连接被重置")
        except Exception as e:
            debug_log(f"WebSocket 错误: {e}")
        finally:
            active_session = manager.get_session(session.session_id)
            if active_session and active_session.websocket == websocket:
                active_session.websocket = None
                debug_log("已清理会话中的 WebSocket 连接")

    @manager.app.post("/api/save-settings")
    async def save_settings(request: Request):
        """保存设定到档案"""

        try:
            data = await request.json()

            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_file = config_dir / "ui_settings.json"

            # 保存设定到档案
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            debug_log(f"设定已保存到: {settings_file}")

            return JSONResponse(
                content={
                    "status": "success",
                    "messageCode": get_msg_code("settings_saved"),
                }
            )

        except Exception as e:
            debug_log(f"保存设定失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Save failed: {e!s}",
                    "messageCode": get_msg_code("save_failed"),
                },
            )

    @manager.app.get("/api/load-settings")
    async def load_settings(request: Request):
        """从档案加载设定"""

        try:
            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings = json.load(f)

                debug_log(f"设定已从档案加载: {settings_file}")
                return JSONResponse(content=settings)
            debug_log("配置文件案不存在，返回空设定")
            return JSONResponse(content={})

        except Exception as e:
            debug_log(f"加载设定失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Load failed: {e!s}",
                    "messageCode": get_msg_code("load_failed"),
                },
            )

    @manager.app.post("/api/clear-settings")
    async def clear_settings(request: Request):
        """清除配置文件案"""

        try:
            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                settings_file.unlink()
                debug_log(f"配置文件案已删除: {settings_file}")
            else:
                debug_log("配置文件案不存在，无需删除")

            return JSONResponse(
                content={
                    "status": "success",
                    "messageCode": get_msg_code("settings_cleared"),
                }
            )

        except Exception as e:
            debug_log(f"清除设定失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Clear failed: {e!s}",
                    "messageCode": get_msg_code("clear_failed"),
                },
            )

    @manager.app.get("/api/load-session-history")
    async def load_session_history(request: Request):
        """从档案加载会话历史"""

        try:
            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            history_file = config_dir / "session_history.json"

            if history_file.exists():
                with open(history_file, encoding="utf-8") as f:
                    history_data = json.load(f)

                debug_log(f"会话历史已从档案加载: {history_file}")

                # 确保数据格式相容性
                if isinstance(history_data, dict):
                    # 新格式：包含版本信息和其他元数据
                    sessions = history_data.get("sessions", [])
                    last_cleanup = history_data.get("lastCleanup", 0)
                else:
                    # 旧格式：直接是会话阵列（向后相容）
                    sessions = history_data if isinstance(history_data, list) else []
                    last_cleanup = 0

                # 回传会话历史数据
                return JSONResponse(
                    content={"sessions": sessions, "lastCleanup": last_cleanup}
                )

            debug_log("会话历史档案不存在，返回空历史")
            return JSONResponse(content={"sessions": [], "lastCleanup": 0})

        except Exception as e:
            debug_log(f"加载会话历史失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Load failed: {e!s}",
                    "messageCode": get_msg_code("load_failed"),
                },
            )

    @manager.app.post("/api/save-session-history")
    async def save_session_history(request: Request):
        """保存会话历史到档案"""

        try:
            data = await request.json()

            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            history_file = config_dir / "session_history.json"

            # 建立新格式的数据结构
            history_data = {
                "version": "1.0",
                "sessions": data.get("sessions", []),
                "lastCleanup": data.get("lastCleanup", 0),
                "savedAt": int(time.time() * 1000),  # 当前时间戳
            }

            # 保存会话历史到档案
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

            debug_log(f"会话历史已保存到: {history_file}")
            session_count = len(history_data["sessions"])
            debug_log(f"保存了 {session_count} 个会话记录")

            return JSONResponse(
                content={
                    "status": "success",
                    "messageCode": get_msg_code("session_history_saved"),
                    "params": {"count": session_count},
                }
            )

        except Exception as e:
            debug_log(f"保存会话历史失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Save failed: {e!s}",
                    "messageCode": get_msg_code("save_failed"),
                },
            )

    @manager.app.get("/api/log-level")
    async def get_log_level(request: Request):
        """获取日志等级设定"""

        try:
            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings_data = json.load(f)
                    log_level = settings_data.get("logLevel", "INFO")
                    debug_log(f"从配置文件案加载日志等级: {log_level}")
                    return JSONResponse(content={"logLevel": log_level})
            else:
                # 默认日志等级
                default_log_level = "INFO"
                debug_log(f"使用默认日志等级: {default_log_level}")
                return JSONResponse(content={"logLevel": default_log_level})

        except Exception as e:
            debug_log(f"获取日志等级失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"Failed to get log level: {e!s}",
                    "messageCode": get_msg_code("get_log_level_failed"),
                },
            )

    @manager.app.post("/api/log-level")
    async def set_log_level(request: Request):
        """设定日志等级"""

        try:
            data = await request.json()
            log_level = data.get("logLevel")

            if not log_level or log_level not in ["DEBUG", "INFO", "WARN", "ERROR"]:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Invalid log level",
                        "messageCode": get_msg_code("invalid_log_level"),
                    },
                )

            # 使用统一的配置文件案路径
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_file = config_dir / "ui_settings.json"

            # 加载现有设定或创建新设定
            settings_data = {}
            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings_data = json.load(f)

            # 更新日志等级
            settings_data["logLevel"] = log_level

            # 保存设定到档案
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings_data, f, ensure_ascii=False, indent=2)

            debug_log(f"日志等级已设定为: {log_level}")

            return JSONResponse(
                content={
                    "status": "success",
                    "logLevel": log_level,
                    "messageCode": get_msg_code("log_level_updated"),
                }
            )

        except Exception as e:
            debug_log(f"设定日志等级失败: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": f"Set failed: {e!s}",
                    "messageCode": get_msg_code("set_failed"),
                },
            )


async def handle_websocket_message(manager: "WebUIManager", session, data: dict):
    """处理 WebSocket 消息"""
    message_type = data.get("type")

    if message_type == "submit_feedback":
        # 提交反馈
        feedback = data.get("feedback", "")
        images = data.get("images", [])
        settings = data.get("settings", {})
        await session.submit_feedback(feedback, images, settings)

    elif message_type == "get_status":
        # 获取会话状态
        if session.websocket:
            try:
                await session.websocket.send_json(
                    {"type": "status_update", "status_info": session.get_status_info()}
                )
            except Exception as e:
                debug_log(f"发送状态更新失败: {e}")

    elif message_type == "heartbeat":
        # WebSocket 心跳处理（简化版）
        # 更新心跳时间
        session.last_heartbeat = time.time()
        session.last_activity = time.time()

        # 发送心跳回应
        if session.websocket:
            try:
                await session.websocket.send_json(
                    {
                        "type": "heartbeat_response",
                        "timestamp": data.get("timestamp", 0),
                    }
                )
            except Exception as e:
                debug_log(f"发送心跳回应失败: {e}")

    elif message_type == "user_timeout":
        # 用户设置的超时已到
        debug_log(f"收到用户超时通知: {session.session_id}")
        # 清理会话资源
        await session._cleanup_resources_on_timeout()
        # 重构：不再自动停止服务器，保持服务器运行以支援持久性

    elif message_type == "pong":
        # 处理来自前端的 pong 回应（用于连接检测）
        debug_log(f"收到 pong 回应，时间戳: {data.get('timestamp', 'N/A')}")
        # 可以在这里记录延迟或更新连接状态

    elif message_type == "update_timeout_settings":
        # 处理超时设定更新
        settings = data.get("settings", {})
        debug_log(f"收到超时设定更新: {settings}")
        if settings.get("enabled"):
            session.update_timeout_settings(
                enabled=True, timeout_seconds=settings.get("seconds", 3600)
            )
        else:
            session.update_timeout_settings(enabled=False)

    elif message_type == "extend_wait":
        debug_log(f"收到手动延长等待请求: {session.session_id}")
        session.add_manual_keepalive_extension()
        if session.websocket:
            await session.websocket.send_json(
                {
                    "type": "wait_extended",
                    "session_id": session.session_id,
                    "session_info": {
                        "session_id": session.session_id,
                        "feedback_timeout_seconds": session.feedback_timeout_seconds,
                        "feedback_actual_timeout_seconds": (
                            session.feedback_actual_timeout_seconds
                        ),
                        "feedback_deadline_at": (
                            int(session.feedback_deadline_at * 1000)
                            if session.feedback_deadline_at
                            else None
                        ),
                        "keepalive_auto_extend_max": session.keepalive_auto_extend_max,
                        "keepalive_auto_extend_used": (
                            session.keepalive_auto_extend_used
                        ),
                        "keepalive_manual_extend_tokens": (
                            session.keepalive_manual_extend_tokens
                        ),
                    },
                }
            )

    else:
        debug_log(f"未知的消息类型: {message_type}")


async def _delayed_server_stop(manager: "WebUIManager"):
    """延迟停止服务器"""
    import asyncio

    await asyncio.sleep(5)  # 等待 5 秒让前端有时间关闭
    from ..main import stop_web_ui

    stop_web_ui()
    debug_log("Web UI 服务器已因用户超时而停止")
