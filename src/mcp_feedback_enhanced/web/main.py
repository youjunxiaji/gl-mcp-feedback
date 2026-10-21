#!/usr/bin/env python3
"""
Web UI 主要管理类

基于 FastAPI 的 Web 用户界面主要管理类：一个服务、多个对话房间。
提供完整的反馈收集、图片上传等功能。
"""

import asyncio
import contextlib
import os
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..debug import web_debug_log as debug_log
from ..utils.error_handler import ErrorHandler, ErrorType
from ..utils.memory_monitor import get_memory_monitor
from .models import (
    DEFAULT_ROOM_ID,
    HeartbeatCallback,
    QueueMarker,
    Room,
    RoomRegistration,
    WebFeedbackSession,
)
from .routes import setup_routes
from .utils import get_browser_opener
from .utils.compression_config import get_compression_manager
from .utils.port_manager import PortManager


class WebUIManager:
    """Web UI 管理器：一个服务，每个对话一个房间"""

    # 多久检查一次有没有到期要销毁的房间（秒）
    ROOM_SWEEP_INTERVAL_SECONDS = 0.5

    def __init__(self, host: str = "127.0.0.1", port: int | None = None):
        # 确定偏好主机：环境变数 > 参数 > 默认值 127.0.0.1
        env_host = os.getenv("MCP_WEB_HOST")
        if env_host:
            self.host = env_host
            debug_log(f"使用环境变数指定的主机: {self.host}")
        else:
            self.host = host
            debug_log(f"未设定 MCP_WEB_HOST 环境变数，使用默认主机 {self.host}")

        # 确定偏好端口：环境变数 > 参数 > 默认值 8765
        preferred_port = 8765

        # 检查环境变数 MCP_WEB_PORT
        env_port = os.getenv("MCP_WEB_PORT")
        if env_port:
            try:
                custom_port = int(env_port)
                if custom_port == 0:
                    # 特殊值 0 表示使用系统自动分配的端口
                    preferred_port = 0
                    debug_log("使用环境变数指定的自动端口分配 (0)")
                elif 1024 <= custom_port <= 65535:
                    preferred_port = custom_port
                    debug_log(f"使用环境变数指定的端口: {preferred_port}")
                else:
                    debug_log(
                        f"MCP_WEB_PORT 值无效 ({custom_port})，必须在 1024-65535 范围内或为 0，使用默认端口 8765"
                    )
            except ValueError:
                debug_log(
                    f"MCP_WEB_PORT 格式错误 ({env_port})，必须为数字，使用默认端口 8765"
                )
        else:
            debug_log(f"未设定 MCP_WEB_PORT 环境变数，使用默认端口 {preferred_port}")

        # 使用增强的端口管理，测试模式下禁用自动清理避免权限问题
        auto_cleanup = os.environ.get("MCP_TEST_MODE", "").lower() != "true"

        if port is not None:
            # 如果明确指定了端口，使用指定的端口
            self.port = port
            # 检查指定端口是否可用
            if not PortManager.is_port_available(self.host, self.port):
                debug_log(f"警告：指定的端口 {self.port} 可能已被占用")
                # 在测试模式下，尝试寻找替代端口
                if os.environ.get("MCP_TEST_MODE", "").lower() == "true":
                    debug_log("测试模式：自动寻找替代端口")
                    original_port = self.port
                    self.port = PortManager.find_free_port_enhanced(
                        preferred_port=self.port, auto_cleanup=False, host=self.host
                    )
                    if self.port != original_port:
                        debug_log(f"自动切换到可用端口: {original_port} → {self.port}")
        elif preferred_port == 0:
            # 如果偏好端口为 0，使用系统自动分配
            import socket

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self.host, 0))
                self.port = s.getsockname()[1]
            debug_log(f"系统自动分配端口: {self.port}")
        else:
            # 使用增强的端口管理
            self.port = PortManager.find_free_port_enhanced(
                preferred_port=preferred_port, auto_cleanup=auto_cleanup, host=self.host
            )
        self.app = FastAPI(title="gl-mcp-feedback", lifespan=self._lifespan)

        # 对话房间：键是房间标识（对话标识，没有对话标识的调用共用 default）。
        # 每个房间有自己的会话、排队消息和页面连接；MCP 线程创建房间，Web 线程查询。
        self.rooms: dict[str, Room] = {}
        self._rooms_lock = threading.Lock()

        # 设置压缩和缓存中间件
        self._setup_compression_middleware()

        # 设置内存监控
        self._setup_memory_monitoring()

        self.server_thread: threading.Thread | None = None
        self.server_process = None

        # 初始化基本组件
        self._init_basic_components()

        debug_log(f"WebUIManager 基本初始化完成，将在 {self.host}:{self.port} 启动")
        debug_log("反馈模式: web")

    def _init_basic_components(self):
        """初始化基本组件：静态文件、模板、路由"""
        self._setup_static_files()
        self._setup_templates()
        setup_routes(self)

    def _setup_compression_middleware(self):
        """设置压缩和缓存中间件"""
        # 获取压缩管理器
        compression_manager = get_compression_manager()
        config = compression_manager.config

        # 添加 Gzip 压缩中间件
        self.app.add_middleware(GZipMiddleware, minimum_size=config.minimum_size)

        # 添加缓存和压缩统计中间件
        @self.app.middleware("http")
        async def compression_and_cache_middleware(request: Request, call_next):
            """压缩和缓存中间件"""
            response = await call_next(request)

            # 添加缓存头
            if not config.should_exclude_path(request.url.path):
                cache_headers = config.get_cache_headers(request.url.path)
                for key, value in cache_headers.items():
                    response.headers[key] = value

            # 更新压缩统计（如果可能）
            try:
                content_length = int(response.headers.get("content-length", 0))
                content_encoding = response.headers.get("content-encoding", "")
                was_compressed = "gzip" in content_encoding

                if content_length > 0:
                    # 估算原始大小（如果已压缩，假设压缩比为 30%）
                    original_size = (
                        content_length
                        if not was_compressed
                        else int(content_length / 0.7)
                    )
                    compression_manager.update_stats(
                        original_size, content_length, was_compressed
                    )
            except (ValueError, TypeError):
                # 忽略统计错误，不影响正常响应
                pass

            return response

        debug_log("压缩和缓存中间件设置完成")

    def _setup_memory_monitoring(self):
        """设置内存监控"""
        try:
            self.memory_monitor = get_memory_monitor()

            # 添加 Web 应用特定的警告回调
            def web_memory_alert(alert):
                debug_log(f"Web UI 内存警告 [{alert.level}]: {alert.message}")

                # 根据警告级别触发不同的清理策略
                if alert.level == "critical":
                    # 危险级别：清理过期会话
                    cleaned = self.cleanup_expired_sessions()
                    debug_log(f"内存危险警告触发，清理了 {cleaned} 个过期会话")
                elif alert.level == "emergency":
                    # 紧急级别：强制清理会话
                    cleaned = self.cleanup_sessions_by_memory_pressure(force=True)
                    debug_log(f"内存紧急警告触发，强制清理了 {cleaned} 个会话")

            self.memory_monitor.add_alert_callback(web_memory_alert)

            # 添加会话清理回调到内存监控
            def session_cleanup_callback(force: bool = False):
                """内存监控触发的会话清理回调"""
                try:
                    if force:
                        # 强制清理：包括内存压力清理
                        cleaned = self.cleanup_sessions_by_memory_pressure(force=True)
                        debug_log(f"内存监控强制清理了 {cleaned} 个会话")
                    else:
                        # 常规清理：只清理过期会话
                        cleaned = self.cleanup_expired_sessions()
                        debug_log(f"内存监控清理了 {cleaned} 个过期会话")
                except Exception as e:
                    error_id = ErrorHandler.log_error_with_context(
                        e,
                        context={"operation": "内存监控会话清理", "force": force},
                        error_type=ErrorType.SYSTEM,
                    )
                    debug_log(f"内存监控会话清理失败 [错误ID: {error_id}]: {e}")

            self.memory_monitor.add_cleanup_callback(session_cleanup_callback)

            # 确保内存监控已启动（ResourceManager 可能已经启动了）
            if not self.memory_monitor.is_monitoring:
                self.memory_monitor.start_monitoring()

            debug_log("Web UI 内存监控设置完成，已集成会话清理回调")

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "设置 Web UI 内存监控"},
                error_type=ErrorType.SYSTEM,
            )
            debug_log(f"设置 Web UI 内存监控失败 [错误ID: {error_id}]: {e}")

    def _setup_static_files(self):
        """设置静态文件服务"""
        # Web UI 静态文件
        web_static_path = Path(__file__).parent / "static"
        if web_static_path.exists():
            self.app.mount(
                "/static", StaticFiles(directory=str(web_static_path)), name="static"
            )
        else:
            raise RuntimeError(f"Static files directory not found: {web_static_path}")

    def _setup_templates(self):
        """设置模板引擎"""
        # Web UI 模板
        web_templates_path = Path(__file__).parent / "templates"
        if web_templates_path.exists():
            self.templates = Jinja2Templates(directory=str(web_templates_path))
        else:
            raise RuntimeError(f"Templates directory not found: {web_templates_path}")

    def get_room(self, room_id: str) -> Room | None:
        """按房间标识取房间，不存在时返回 None。

        Args:
            room_id: 房间标识，即对话标识；没有对话标识的调用共用默认房间。
        """
        return self.rooms.get(room_id)

    def get_or_create_room(self, conversation_id: str = "") -> Room:
        """取对话对应的房间，没有就新建。

        Args:
            conversation_id: 钩子填入的对话标识，没有或格式不对时用默认房间。
        """
        with self._rooms_lock:
            return self._get_or_create_room_locked(conversation_id)

    def _get_or_create_room_locked(self, conversation_id: str) -> Room:
        """get_or_create_room 的实现，调用方已经持有 ``_rooms_lock``"""
        room_id = Room.room_id_for(conversation_id)
        room = self.rooms.get(room_id)
        if room is None:
            # 默认房间没有对话标识，钩子找不到它，不写标记文件和登记文件
            is_default = room_id == DEFAULT_ROOM_ID

            def room_url() -> str:
                return self.get_hook_room_url(room_id)

            room = Room(
                room_id,
                None if is_default else QueueMarker(room_id, room_url),
                None if is_default else RoomRegistration(room_id, room_url),
            )
            room.register()
            self.rooms[room_id] = room
            debug_log(f"新建房间: {room_id}")
        return room

    def create_session(
        self, project_directory: str, summary: str, *, conversation_id: str = ""
    ) -> WebFeedbackSession:
        """在对话对应的房间里创建新一轮的会话。

        Args:
            project_directory: 专案目录路径。
            summary: AI 工作摘要（Markdown）。
            conversation_id: 这次调用来自哪个 Cursor 对话，由钩子填入，没有钩子时为空。

        Returns:
            新会话。
        """
        return self.create_session_in_room(
            project_directory, summary, conversation_id=conversation_id
        )[1]

    def create_session_in_room(
        self, project_directory: str, summary: str, *, conversation_id: str = ""
    ) -> tuple[Room, WebFeedbackSession]:
        """在对话对应的房间里创建新一轮的会话，并返回房间。

        取房间和创建会话在同一把锁里完成：房间不会在这中间被销毁，
        提问和 stop 钩子的结束报告也不会交错。

        Args:
            project_directory: 专案目录路径。
            summary: AI 工作摘要（Markdown）。
            conversation_id: 这次调用来自哪个 Cursor 对话，由钩子填入，没有钩子时为空。

        Returns:
            会话所在的房间和新会话。
        """
        with self._rooms_lock:
            room = self._get_or_create_room_locked(conversation_id)
            return room, room.create_session(project_directory, summary)

    def end_turn(self, room: Room, status: str) -> bool:
        """Cursor 的 stop 钩子报告房间对应的对话结束了一个回合。

        和提问共用 ``_rooms_lock``：结束报告和 AI 的新提问同时到时，
        不会把刚提问的对话标成已结束。

        Args:
            room: 对话的房间。
            status: stop 钩子报告的回合结束状态。

        Returns:
            True 表示房间记下了结束标记，False 表示被忽略（默认房间、已销毁的房间、AI 的调用还在等回复）。
        """
        if room.room_id == DEFAULT_ROOM_ID:
            return False
        with self._rooms_lock:
            if self.rooms.get(room.room_id) is not room:
                return False
            return room.end_turn(status)

    async def sweep_rooms(self, now: float | None = None) -> int:
        """销毁到期的房间：页面收到「对话已结束」并断开，会话和钩子文件释放。

        Args:
            now: 当前时间戳（秒），默认取系统时间。

        Returns:
            销毁的房间数量。
        """
        moment = time.time() if now is None else now
        destroyed = 0
        for room in list(self.rooms.values()):
            if self._detach_if_due(room, moment):
                await room.close()
                destroyed += 1
        return destroyed

    def _detach_if_due(self, room: Room, now: float) -> bool:
        """房间仍然到期的话，从房间表里摘掉并删掉钩子文件

        判断到期和摘掉在同一把锁里完成，不会和 AI 的新提问交错。

        Args:
            room: 要检查的房间。
            now: 当前时间戳（秒）。

        Returns:
            True 表示房间已摘掉，调用方接着收尾（room.close）。
        """
        with self._rooms_lock:
            if self.rooms.get(room.room_id) is not room or not room.due_for_destroy(
                now
            ):
                return False
            del self.rooms[room.room_id]
            room.release_files()
        debug_log(f"销毁房间: {room.room_id}")
        return True

    async def _sweep_rooms_forever(self) -> None:
        """服务运行期间，定时销毁到期的房间"""
        while True:
            await asyncio.sleep(self.ROOM_SWEEP_INTERVAL_SECONDS)
            try:
                await self.sweep_rooms()
            except Exception as e:
                debug_log(f"销毁到期房间失败: {e}")

    @contextlib.asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        """Web 服务运行期间，后台定时销毁到期的房间"""
        sweeper = asyncio.create_task(self._sweep_rooms_forever())
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper

    def get_room_url(self, room: Room) -> str:
        """房间页面的地址"""
        return f"{self.get_server_url()}/c/{room.room_id}/"

    def get_hook_room_url(self, room_id: str) -> str:
        """钩子访问房间用的地址（不带结尾的斜杠）

        服务监听在所有网卡时（0.0.0.0），钩子从本机访问，用 127.0.0.1。

        Args:
            room_id: 房间标识。
        """
        host = "127.0.0.1" if self.host in ("", "0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}/c/{room_id}"

    def start_server(self):
        """启动 Web 服务器"""

        def run_server_with_retry():
            max_retries = 5
            retry_count = 0
            original_port = self.port

            while retry_count < max_retries:
                try:
                    # 在尝试启动前先检查端口是否可用
                    if not PortManager.is_port_available(self.host, self.port):
                        debug_log(f"端口 {self.port} 已被占用，自动寻找替代端口")

                        # 查找占用端口的进程信息
                        process_info = PortManager.find_process_using_port(self.port)
                        if process_info:
                            debug_log(
                                f"端口 {self.port} 被进程 {process_info['name']} "
                                f"(PID: {process_info['pid']}) 占用"
                            )

                        # 自动寻找新端口
                        try:
                            new_port = PortManager.find_free_port_enhanced(
                                preferred_port=self.port,
                                auto_cleanup=False,  # 不自动清理其他进程
                                host=self.host,
                            )
                            debug_log(f"自动切换端口: {self.port} → {new_port}")
                            self.port = new_port
                        except RuntimeError as port_error:
                            error_id = ErrorHandler.log_error_with_context(
                                port_error,
                                context={
                                    "operation": "端口查找",
                                    "original_port": original_port,
                                    "current_port": self.port,
                                },
                                error_type=ErrorType.NETWORK,
                            )
                            debug_log(
                                f"无法找到可用端口 [错误ID: {error_id}]: {port_error}"
                            )
                            raise RuntimeError(
                                f"无法找到可用端口，原始端口 {original_port} 被占用"
                            ) from port_error

                    debug_log(
                        f"尝试启动服务器在 {self.host}:{self.port} (尝试 {retry_count + 1}/{max_retries})"
                    )

                    config = uvicorn.Config(
                        app=self.app,
                        host=self.host,
                        port=self.port,
                        log_level="warning",
                        access_log=False,
                    )

                    server_instance = uvicorn.Server(config)

                    # 创建事件循环并启动服务器
                    asyncio.run(server_instance.serve())

                    # 成功启动，显示最终使用的端口
                    if self.port != original_port:
                        debug_log(
                            f"✅ 服务器成功启动在替代端口 {self.port} (原端口 {original_port} 被占用)"
                        )

                    break

                except OSError as e:
                    if e.errno in {
                        10048,
                        98,
                    }:  # Windows: 10048, Linux: 98 (位址已在使用中)
                        retry_count += 1
                        if retry_count < max_retries:
                            debug_log(
                                f"端口 {self.port} 启动失败 (OSError)，尝试下一个端口"
                            )
                            # 尝试下一个端口
                            self.port = self.port + 1
                        else:
                            debug_log("已达到最大重试次数，无法启动服务器")
                            break
                    else:
                        # 使用统一错误处理
                        error_id = ErrorHandler.log_error_with_context(
                            e,
                            context={
                                "operation": "服务器启动",
                                "host": self.host,
                                "port": self.port,
                            },
                            error_type=ErrorType.NETWORK,
                        )
                        debug_log(f"服务器启动错误 [错误ID: {error_id}]: {e}")
                        break
                except Exception as e:
                    # 使用统一错误处理
                    error_id = ErrorHandler.log_error_with_context(
                        e,
                        context={
                            "operation": "服务器运行",
                            "host": self.host,
                            "port": self.port,
                        },
                        error_type=ErrorType.SYSTEM,
                    )
                    debug_log(f"服务器运行错误 [错误ID: {error_id}]: {e}")
                    break

        # 在新线程中启动服务器
        self.server_thread = threading.Thread(target=run_server_with_retry, daemon=True)
        self.server_thread.start()

        # 轮询等待服务器就绪，而非固定 sleep
        import socket

        max_wait = 5.0
        interval = 0.1
        waited = 0.0
        while waited < max_wait:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    break
            except OSError:
                time.sleep(interval)
                waited += interval

        if waited >= max_wait:
            debug_log(f"警告：服务器在 {max_wait} 秒内未就绪，继续执行")
        else:
            debug_log(f"服务器就绪，耗时 {waited:.1f} 秒")

        # 端口可能在启动时换过，登记文件里的房间地址要跟着更新
        for room in list(self.rooms.values()):
            room.register()

    def open_browser(self, url: str):
        """开启浏览器"""
        try:
            browser_opener = get_browser_opener()
            browser_opener(url)
            debug_log(f"已开启浏览器：{url}")
        except Exception as e:
            debug_log(f"无法开启浏览器: {e}")

    async def smart_open_browser(self, room: Room, url: str) -> bool:
        """智能开启浏览器：房间的页面还连着就通知它刷新，没有才新开标签页

        Args:
            room: 刚收到新提问的房间。
            url: 房间页面的地址。

        Returns:
            bool: True 表示房间的页面还连着、只发了刷新通知，False 表示开启了新视窗
        """
        try:
            if await room.check_active_tab():
                debug_log(f"房间 {room.room_id} 的页面还连着，发送刷新通知")
                refresh_success = await room.notify_existing_tab_to_refresh()
                debug_log(f"刷新通知发送结果: {refresh_success}")
                return True

            debug_log(f"房间 {room.room_id} 没有已连接的页面，开启新浏览器视窗")
            self.open_browser(url)
            return False

        except Exception as e:
            debug_log(f"智能浏览器开启失败，回退到普通开启：{e}")
            self.open_browser(url)
            return False

    def get_server_url(self) -> str:
        """获取服务器 URL"""
        return f"http://{self.host}:{self.port}"

    def cleanup_expired_sessions(self) -> int:
        """清理所有房间里已过期的会话

        Returns:
            清理的会话数量。
        """
        cleaned_count = sum(
            room.cleanup_expired_sessions() for room in list(self.rooms.values())
        )
        if cleaned_count > 0:
            debug_log(f"清理了 {cleaned_count} 个过期会话")
        return cleaned_count

    def cleanup_sessions_by_memory_pressure(self, force: bool = False) -> int:
        """内存吃紧时清理所有房间里的会话

        Args:
            force: 为 True 时连每个房间的当前会话也清。

        Returns:
            清理的会话数量。
        """
        cleaned_count = sum(
            room.cleanup_sessions_by_memory_pressure(force)
            for room in list(self.rooms.values())
        )
        if cleaned_count > 0:
            debug_log(f"因内存压力清理了 {cleaned_count} 个会话")
        return cleaned_count

    def stop(self):
        """停止 Web UI 服务"""
        cleanup_start_time = time.time()
        with self._rooms_lock:
            rooms = list(self.rooms.values())
            self.rooms.clear()
        session_count = sum(room.cleanup_all_sessions() for room in rooms)
        cleanup_duration = time.time() - cleanup_start_time
        debug_log(
            f"停止服务时清理了 {session_count} 个会话，耗时: {cleanup_duration:.2f}秒"
        )

        # 停止服务器（注意：uvicorn 的 graceful shutdown 需要额外处理）
        if self.server_thread is not None and self.server_thread.is_alive():
            debug_log("正在停止 Web UI 服务")


# 全域实例
_web_ui_manager: WebUIManager | None = None


def get_web_ui_manager() -> WebUIManager:
    """获取 Web UI 管理器实例"""
    global _web_ui_manager
    if _web_ui_manager is None:
        _web_ui_manager = WebUIManager()
    return _web_ui_manager


async def launch_web_feedback_ui(
    project_directory: str,
    summary: str,
    timeout: int = 600,
    *,
    conversation_id: str = "",
    on_heartbeat: HeartbeatCallback | None = None,
) -> dict:
    """启动 Web 反馈界面并等待用户反馈。

    每个对话有自己的房间和页面：同一个对话的新提问替换它自己上一轮的会话，不影响别的对话。
    用户在 AI 工作期间排队了消息时，直接返回这些消息，不再等待新的回复。

    Args:
        project_directory: 专案目录路径。
        summary: AI 工作摘要（Markdown）。
        timeout: 超时时间（秒）。
        conversation_id: 这次调用来自哪个 Cursor 对话，由钩子填入，没有钩子时为空。
        on_heartbeat: 等用户回复期间定时调用的心跳回调，见 `WebFeedbackSession.wait_for_feedback()`。

    Returns:
        dict: 反馈结果，包含 logs、interactive_feedback 和 images。

    Raises:
        FeedbackTimeoutError: 用户在时限内没有回复。
    """
    manager = get_web_ui_manager()

    room, session = manager.create_session_in_room(
        project_directory, summary, conversation_id=conversation_id
    )

    # 用户在 AI 工作期间排队的消息：直接取走返回，不打开浏览器，也不等用户回复
    queued_result = await room.deliver_queued_messages(session)
    if queued_result is not None:
        debug_log("已取走用户排队的消息，直接返回")
        return queued_result

    # 启动服务器（如果尚未启动）
    if manager.server_thread is None or not manager.server_thread.is_alive():
        manager.start_server()

    feedback_url = manager.get_room_url(room)

    # Web 模式：智能开启浏览器
    has_active_tabs = await manager.smart_open_browser(room, feedback_url)

    debug_log(f"[DEBUG] 房间地址: {feedback_url}")

    # 如果检测到活跃标签页，消息已在 smart_open_browser 中发送，无需额外处理
    if has_active_tabs:
        debug_log("检测到活跃标签页，会话更新通知已发送")

    try:
        # 等待用户反馈，传递 timeout 参数
        result = await session.wait_for_feedback(timeout, on_heartbeat=on_heartbeat)
        debug_log("收到用户反馈")
        return result
    except TimeoutError:
        debug_log("会话超时")
        # 资源已在 wait_for_feedback 中清理，这里只需要记录和重新抛出
        raise
    except Exception as e:
        debug_log(f"会话发生错误: {e}")
        raise
    finally:
        # 注意：不再自动清理会话和停止服务器，保持持久性
        # 会话将保持活跃状态，等待下次 MCP 调用
        debug_log("会话保持活跃状态，等待下次 MCP 调用")


def stop_web_ui():
    """停止 Web UI 服务"""
    global _web_ui_manager
    if _web_ui_manager:
        _web_ui_manager.stop()
        _web_ui_manager = None
        debug_log("Web UI 服务已停止")


# 测试用主函数
if __name__ == "__main__":

    async def main():
        try:
            project_dir = os.getcwd()
            summary = """# Markdown 功能测试

## 🎯 任务完成摘要

我已成功为 **gl-mcp-feedback** 专案实现了 Markdown 语法显示功能！

### ✅ 完成的功能

1. **标题支援** - 支援 H1 到 H6 标题
2. **文字格式化**
   - **粗体文字** 使用双星号
   - *斜体文字* 使用单星号
   - `行内代码` 使用反引号
3. **代码区块**
4. **列表功能**
   - 无序列表项目
   - 有序列表项目

### 📋 技术实作

```javascript
// 使用 marked.js 进行 Markdown 解析
const renderedContent = this.renderMarkdownSafely(summary);
element.innerHTML = renderedContent;
```

### 🔗 相关连结

- [marked.js 官方文档](https://marked.js.org/)
- [DOMPurify 安全清理](https://github.com/cure53/DOMPurify)

> **注意**: 此功能包含 XSS 防护，使用 DOMPurify 进行 HTML 清理。

---

**测试状态**: ✅ 功能正常运作"""

            debug_log("启动 Web UI 测试...")
            debug_log(f"专案目录: {project_dir}")
            debug_log("等待用户反馈...")

            result = await launch_web_feedback_ui(project_dir, summary)

            debug_log("收到反馈结果:")
            debug_log(f"命令日志: {result.get('logs', '')}")
            debug_log(f"互动反馈: {result.get('interactive_feedback', '')}")
            debug_log(f"图片数量: {len(result.get('images', []))}")

        except KeyboardInterrupt:
            debug_log("\n用户取消操作")
        except Exception as e:
            debug_log(f"错误: {e}")
        finally:
            stop_web_ui()

    asyncio.run(main())
