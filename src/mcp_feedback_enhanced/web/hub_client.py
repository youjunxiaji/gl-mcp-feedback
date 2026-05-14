"""Shared Web Hub client utilities.

This module is used by MCP stdio processes when ``MCP_WEB_SHARE_MODE=shared``.
The MCP process remains the client while a managed Web Hub process owns the
FastAPI UI and all browser-facing sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiohttp
import psutil

from .. import __version__
from ..debug import web_debug_log as debug_log


SHARE_MODE_ENV = "MCP_WEB_SHARE_MODE"
PORT_AUTO_CLEANUP_ENV = "MCP_WEB_PORT_AUTO_CLEANUP"
HUB_IDLE_TIMEOUT_ENV = "MCP_WEB_HUB_IDLE_TIMEOUT"
HUB_RESTART_MAX = 3
DEFAULT_HUB_IDLE_TIMEOUT = 1800


@dataclass
class HubConnection:
    """Connection information for a managed Web Hub."""

    url: str
    host: str
    port: int
    token: str
    pid: int | None = None


@dataclass
class RemoteSessionMeta:
    """Metadata retained by the MCP client so remote sessions can be resumed."""

    session_id: str
    project_directory: str
    summary: str
    title: str
    timeout: int
    hub: HubConnection
    rebuild_count: int = 0


_remote_sessions: dict[str, RemoteSessionMeta] = {}


class HubClientError(RuntimeError):
    """Raised when the managed Web Hub cannot be reached or used."""


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def get_share_mode() -> str:
    """Return the configured Web sharing mode."""
    mode = os.getenv(SHARE_MODE_ENV, "isolated").strip().lower()
    return mode if mode in {"isolated", "shared", "auto"} else "isolated"


def is_shared_mode() -> bool:
    """Whether this MCP process should use the managed shared Web Hub."""
    mode = get_share_mode()
    if mode == "shared":
        return True
    if mode == "auto":
        return _truthy(os.getenv("MCP_WEB_ALWAYS_ON"))
    return False


def get_web_host() -> str:
    return os.getenv("MCP_WEB_HOST", "127.0.0.1")


def get_web_port() -> int:
    raw = os.getenv("MCP_WEB_PORT", "8765")
    try:
        port = int(raw)
    except ValueError:
        return 8765
    return port if 1024 <= port <= 65535 else 8765


def should_auto_cleanup_port() -> bool:
    """Port cleanup is opt-in so MCP processes never kill each other by default."""
    return _truthy(os.getenv(PORT_AUTO_CLEANUP_ENV))


def get_hub_idle_timeout() -> int:
    raw = os.getenv(HUB_IDLE_TIMEOUT_ENV, str(DEFAULT_HUB_IDLE_TIMEOUT))
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_HUB_IDLE_TIMEOUT


def get_run_dir() -> Path:
    base = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache"))
    run_dir = base / "gl-mcp-feedback"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def get_state_file() -> Path:
    return get_run_dir() / "hub.json"


def get_lock_file() -> Path:
    return get_run_dir() / "hub.lock"


def validate_mcp_web_config() -> list[str]:
    """Return AI-readable warnings for incomplete or risky Web configuration."""
    warnings: list[str] = []
    mode = get_share_mode()

    if os.getenv(SHARE_MODE_ENV) and mode == "isolated":
        warnings.append(f"`{SHARE_MODE_ENV}` 的值无效，已回退为 `isolated`。")

    if mode == "shared":
        if not os.getenv("MCP_WEB_PORT"):
            warnings.append(
                "`MCP_WEB_SHARE_MODE=shared` 建议显式设置 `MCP_WEB_PORT`，"
                "否则多个项目不一定复用同一个 Web。"
            )
        if get_web_host() not in {"127.0.0.1", "localhost"}:
            warnings.append(
                "shared Web Hub 建议绑定 `127.0.0.1`，当前 `MCP_WEB_HOST` "
                f"为 `{get_web_host()}`，请确认访问安全边界。"
            )
        if should_auto_cleanup_port():
            warnings.append(
                "`MCP_WEB_PORT_AUTO_CLEANUP=true` 可能终止其他项目的 MCP/Web "
                "进程；shared 模式建议保持 false。"
            )

    return warnings


def format_config_guidance(warnings: list[str] | None = None) -> str:
    """Format configuration warnings with a copyable MCP env example."""
    warnings = warnings or validate_mcp_web_config()
    if not warnings:
        return ""

    bullet_list = "\n".join(f"- {item}" for item in warnings)
    return (
        "=== gl-mcp-feedback 配置提示 ===\n"
        f"{bullet_list}\n\n"
        "建议的 shared Web 配置示例：\n\n"
        "```json\n"
        '"env": {\n'
        '  "MCP_WEB_SHARE_MODE": "shared",\n'
        '  "MCP_WEB_HOST": "127.0.0.1",\n'
        '  "MCP_WEB_PORT": "9790",\n'
        '  "MCP_WEB_PORT_AUTO_CLEANUP": "false",\n'
        '  "MCP_WEB_HUB_IDLE_TIMEOUT": "1800",\n'
        '  "MCP_FEEDBACK_KEEPALIVE_MARGIN": "30"\n'
        "}\n"
        "```\n"
    )


def read_hub_state() -> HubConnection | None:
    """Read the managed Hub state file if it is still valid."""
    state_file = get_state_file()
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        pid = data.get("pid")
        if pid and not psutil.pid_exists(int(pid)):
            state_file.unlink(missing_ok=True)
            return None
        return HubConnection(
            url=str(data["url"]).rstrip("/"),
            host=str(data.get("host") or get_web_host()),
            port=int(data.get("port") or get_web_port()),
            token=str(data["token"]),
            pid=int(pid) if pid else None,
        )
    except Exception as exc:
        debug_log(f"读取 Web Hub 状态失败，清理旧状态: {exc}")
        state_file.unlink(missing_ok=True)
        return None


def write_hub_state(connection: HubConnection) -> None:
    """Persist managed Hub runtime metadata."""
    state_file = get_state_file()
    state_file.write_text(
        json.dumps(
            {
                "url": connection.url,
                "host": connection.host,
                "port": connection.port,
                "pid": connection.pid or os.getpid(),
                "token": connection.token,
                "started_at": time.time(),
                "version": __version__,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@contextmanager
def hub_start_lock():
    """A small cross-process lock to avoid spawning multiple Hubs at once."""
    lock_file = get_lock_file()
    fd: int | None = None
    for _ in range(60):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            break
        except FileExistsError:
            try:
                raw_pid = lock_file.read_text(encoding="utf-8").strip()
                if raw_pid and not psutil.pid_exists(int(raw_pid)):
                    lock_file.unlink(missing_ok=True)
                    continue
            except Exception:
                lock_file.unlink(missing_ok=True)
                continue
            time.sleep(0.1)
    else:
        raise HubClientError("等待 Web Hub 启动锁超时")

    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock_file.unlink(missing_ok=True)


async def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.request(method, url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise HubClientError(f"Hub 请求失败 {resp.status}: {text}")
            try:
                return json.loads(text) if text else {}
            except json.JSONDecodeError as exc:
                raise HubClientError(f"Hub 返回非 JSON 内容: {text[:200]}") from exc


async def hub_health(connection: HubConnection) -> dict[str, Any] | None:
    """Return health payload if the Hub is reachable."""
    try:
        return await _request_json(
            "GET",
            f"{connection.url}/api/hub/health",
            token=connection.token,
            timeout=5,
        )
    except Exception as exc:
        debug_log(f"Web Hub health 检查失败: {exc}")
        return None


async def wait_for_hub_ready(connection: HubConnection, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        health = await hub_health(connection)
        if health and health.get("hub"):
            return True
        await asyncio.sleep(0.2)
    return False


async def ensure_hub_running() -> HubConnection:
    """Find an existing managed Hub or start one from this MCP client."""
    existing = read_hub_state()
    if existing and await wait_for_hub_ready(existing, timeout=1):
        return existing

    with hub_start_lock():
        existing = read_hub_state()
        if existing:
            # Another process may have started the Hub while we waited for the lock.
            # The async check cannot run inside the sync lock block, so return it and
            # validate below.
            candidate = existing
        else:
            candidate = _spawn_hub_process()

    if await wait_for_hub_ready(candidate, timeout=10):
        return candidate

    get_state_file().unlink(missing_ok=True)
    raise HubClientError("Web Hub 启动后未在预期时间内就绪")


def _spawn_hub_process() -> HubConnection:
    host = get_web_host()
    port = get_web_port()
    token = secrets.token_urlsafe(32)
    idle_timeout = get_hub_idle_timeout()
    run_dir = get_run_dir()
    log_file = run_dir / "hub.log"
    state_file = get_state_file()

    cmd = [
        sys.executable,
        "-m",
        "mcp_feedback_enhanced",
        "web-hub",
        "--host",
        host,
        "--port",
        str(port),
        "--token",
        token,
        "--idle-timeout",
        str(idle_timeout),
        "--state-file",
        str(state_file),
    ]
    env = os.environ.copy()
    env["MCP_WEB_HUB"] = "true"
    env["MCP_WEB_HUB_TOKEN"] = token
    env["MCP_WEB_HOST"] = host
    env["MCP_WEB_PORT"] = str(port)
    env[PORT_AUTO_CLEANUP_ENV] = "false"

    debug_log(f"启动 managed Web Hub: {host}:{port}")
    with log_file.open("ab") as log:
        process = subprocess.Popen(
            cmd,
            cwd=str(Path.cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    connection = HubConnection(
        url=f"http://{host}:{port}",
        host=host,
        port=port,
        token=token,
        pid=process.pid,
    )
    # Write a provisional state file so concurrent clients know which token to use.
    write_hub_state(connection)
    return connection


async def register_remote_session(meta: RemoteSessionMeta) -> dict[str, Any]:
    payload = {
        "session_id": meta.session_id,
        "project_directory": meta.project_directory,
        "summary": meta.summary,
        "title": meta.title,
        "timeout": meta.timeout,
        "source": {
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "project_name": Path(meta.project_directory).name or meta.project_directory,
        },
    }
    return await _request_json(
        "POST",
        f"{meta.hub.url}/api/hub/sessions",
        token=meta.hub.token,
        payload=payload,
        timeout=10,
    )


def _calculate_actual_timeout(timeout: int) -> int:
    if timeout <= 30:
        return max(timeout - 1, 5)
    raw_margin = os.getenv("MCP_FEEDBACK_KEEPALIVE_MARGIN", "30")
    try:
        margin = max(int(raw_margin), 1)
    except ValueError:
        margin = 30
    safe_margin = min(margin, max(timeout - 5, 1))
    return max(timeout - safe_margin, 5)


async def wait_remote_session(meta: RemoteSessionMeta) -> dict[str, Any]:
    actual_timeout = _calculate_actual_timeout(meta.timeout)
    request_timeout = actual_timeout + 10
    return await _request_json(
        "POST",
        f"{meta.hub.url}/api/hub/sessions/{meta.session_id}/wait",
        token=meta.hub.token,
        payload={"timeout": meta.timeout},
        timeout=request_timeout,
    )


async def launch_shared_web_feedback_ui(
    project_directory: str,
    summary: str,
    timeout: int = 600,
    *,
    title: str = "",
) -> dict[str, Any]:
    """Register and wait for feedback through the managed shared Web Hub."""
    warnings = validate_mcp_web_config()
    session_id = str(uuid.uuid4())
    hub = await ensure_hub_running()
    meta = RemoteSessionMeta(
        session_id=session_id,
        project_directory=project_directory,
        summary=summary,
        title=title,
        timeout=timeout,
        hub=hub,
    )
    _remote_sessions[session_id] = meta
    return await _register_and_wait_with_rebuild(meta, warnings)


async def continue_shared_web_feedback_session(
    session_id: str, timeout: int = 600
) -> dict[str, Any]:
    meta = _remote_sessions.get(session_id)
    if not meta:
        return {
            "interactive_feedback": (
                f"找不到远程共享 Web 会话 {session_id}。"
                "可能 MCP 进程已重启，请重新调用 interactive_feedback。"
            ),
            "images": [],
            "settings": {},
        }
    meta.timeout = timeout
    return await _register_and_wait_with_rebuild(meta, validate_mcp_web_config())


async def _register_and_wait_with_rebuild(
    meta: RemoteSessionMeta, warnings: list[str]
) -> dict[str, Any]:
    last_error: Exception | None = None
    while meta.rebuild_count <= HUB_RESTART_MAX:
        try:
            await register_remote_session(meta)
            result = await wait_remote_session(meta)
            if warnings:
                result["config_warnings"] = warnings
            return result
        except Exception as exc:
            last_error = exc
            meta.rebuild_count += 1
            debug_log(
                f"共享 Web Hub 等待失败，尝试重建 "
                f"{meta.rebuild_count}/{HUB_RESTART_MAX}: {exc}"
            )
            get_state_file().unlink(missing_ok=True)
            if meta.rebuild_count > HUB_RESTART_MAX:
                break
            meta.hub = await ensure_hub_running()

    return {
        "interactive_feedback": (
            "共享 Web Hub 多次重建失败，无法继续等待反馈。\n"
            f"最后错误: {last_error}\n\n"
            + (format_config_guidance(warnings) if warnings else "")
        ),
        "images": [],
        "settings": {},
    }


def remember_remote_session(meta: RemoteSessionMeta) -> None:
    _remote_sessions[meta.session_id] = meta


def remote_session_snapshot() -> dict[str, dict[str, Any]]:
    return {
        session_id: {**asdict(meta), "hub": asdict(meta.hub)}
        for session_id, meta in _remote_sessions.items()
    }
