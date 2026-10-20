#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
gl-mcp-feedback 的 Cursor 钩子，用法：gl-feedback.py <模式>

    pre   preToolUse，只挂在 interactive_feedback 上：
          先删掉入参里的 title 和 conversation_id（服务端不认 title，对话标识只认钩子填的），
          再把本次对话的标识写进 conversation_id，所有项目的对话都填。
          preToolUse 的输入里没有 MCP 服务器名，只能按工具名匹配：
          别的 MCP 服务器如果也有叫 interactive_feedback 的工具，它的调用同样会被改写。
    post  postToolUse 和 postToolUseFailure，挂在所有工具上：
          这个对话有排队的消息就向服务端取走，用 additional_context 附在工具结果后面交给 AI。
    log   sessionStart：只把事件写进 hook-events.log。
    stop  stop（agent 每个回合结束时触发）：先写进 hook-events.log，
          再看这个对话有没有房间，有就向服务端报告回合结束（带上 status：completed、aborted、error）。
          正常结束（completed）的房间几秒后销毁，页面随之关闭；其他状态只给房间标上已结束。

post 在每次工具调用结束时都会触发，没有排队消息时要尽快返回：
只从输入开头取对话标识，看标记文件 $GL_FEEDBACK_HOME/queue/<对话标识> 在不在，不在就直接返回；
真的要联网时才导入 http.client（导入它要 9 毫秒左右，是整个脚本最大的一笔启动开销）。
stop 靠登记文件 $GL_FEEDBACK_HOME/rooms/<对话标识> 找到房间，房间存在期间一直在，不在说明这个对话没有房间。
两种文件都是两行：房间的地址、写它的服务进程号；进程已经不在了，说明文件是过期的，删掉。

AwaitShell 等后台任务时，Cursor 每秒几次对终端输出文件发起内部 Read，每次都触发 postToolUse，
这些事件返回的 additional_context 会被丢弃；这时取走消息，消息就丢了，所以认出它们、不取，留给下一次真正的工具调用。

任何异常都放行：输出合法 JSON，退出码 0（preToolUse 输出非法 JSON 会拦住调用）。
输出只含 ASCII，非 ASCII 字符写成 \\uXXXX 转义：Windows 上 PowerShell 按控制台代码页转码钩子的输出，ASCII 不受影响。
输入按 utf-8-sig 解码：Windows 上经 PowerShell 管道传进来的 JSON 开头可能带 UTF-8 BOM。

只依赖 Python 标准库，由 `uv run --script` 运行。脚本头里的 requires-python 和项目一致，uv 选的就是运行 MCP 服务的那个解释器。

环境变量：
    GL_FEEDBACK_HOME  标记文件、登记文件、图片和事件日志所在目录，默认 ~/.cache/gl-mcp-feedback，服务端读的是同一个变量
"""

import contextlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


# 事件日志超过这个大小（字节）就换新文件，旧的留一份
LOG_MAX_BYTES = 1024 * 1024
# 对话标识一般在输入开头，工具输出特别大时不必整段扫描
HEAD_CHARS = 65536
# 工具名和入参紧跟在对话标识后面，认内部轮询只看开头这一小段
POLL_SCAN_CHARS = 4096
# 连接房间的超时，以及连上之后等回复的超时（秒）
CONNECT_TIMEOUT_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 5
# 房间的回复最多读这么多字节
MAX_REPLY_BYTES = 10 * 1024 * 1024

# 对话标识的合法格式，和服务端房间标识的规则一致
CONVERSATION_ID = re.compile(r"[0-9A-Za-z_-]{1,128}")
# 输入里的 "conversation_id": "<标识>"
CONVERSATION_ID_FIELD = re.compile(r'"conversation_id"\s*:\s*"([0-9A-Za-z_-]{1,128})"')
# stop 报告的状态只认字母和下划线，其余写成 unknown
STATUS = re.compile(r"[A-Za-z_]{1,32}")
# 内部轮询：工具是 Read，读的是 .../terminals/<编号>.txt。JSON 里 Windows 路径的反斜杠写成两个
TOOL_IS_READ = re.compile(r'"tool_name"\s*:\s*"Read"')
TERMINAL_FILE = re.compile(
    r'"file_path"\s*:\s*"[^"]*(?:/|\\\\)terminals(?:/|\\\\)[^"/\\]*\.txt"'
)

# 事件日志只记这几个字段：（日志里的名字，输入里的名字）。不含用户输入、AI 的摘要、命令和文件内容
LOG_FIELDS = (
    ("event", "hook_event_name"),
    ("conversation_id", "conversation_id"),
    ("session_id", "session_id"),
    ("generation_id", "generation_id"),
    ("status", "status"),
    ("loop_count", "loop_count"),
    ("is_background_agent", "is_background_agent"),
    ("composer_mode", "composer_mode"),
    ("cursor_version", "cursor_version"),
    ("workspace_roots", "workspace_roots"),
    ("transcript_path", "transcript_path"),
)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


def feedback_home() -> Path:
    """标记文件、登记文件、图片和事件日志所在的目录，服务端读的是同一个变量"""
    configured = os.environ.get("GL_FEEDBACK_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "gl-mcp-feedback"


def parse_object(text: str) -> dict[str, Any] | None:
    """把文本解析成 JSON 对象，不是合法的 JSON 对象时返回 None"""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def extract_conversation_id(text: str) -> str | None:
    """从输入里取对话标识：先在开头一段里用正则找，找不到再完整解析

    Args:
        text: 钩子的输入

    Returns:
        合法的对话标识；找不到或格式不对时为 None
    """
    match = CONVERSATION_ID_FIELD.search(text[:HEAD_CHARS])
    if match:
        return match.group(1)
    data = parse_object(text)
    conversation_id = data.get("conversation_id") if data else None
    if isinstance(conversation_id, str) and CONVERSATION_ID.fullmatch(conversation_id):
        return conversation_id
    return None


def is_terminal_poll(text: str) -> bool:
    """是不是 AwaitShell 轮询终端输出文件产生的事件：工具是 Read，读的是 .../terminals/<编号>.txt"""
    head = text[:POLL_SCAN_CHARS]
    return bool(TOOL_IS_READ.search(head)) and bool(TERMINAL_FILE.search(head))


def process_is_alive(pid: int) -> bool:
    """进程还在不在

    Args:
        pid: 进程号

    Returns:
        进程还在时为 True；进程号不合法、进程已经不在时为 False
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            # 打不开：没有访问权限说明进程在，别的原因（比如没有这个进程）说明不在
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程在，只是不属于当前用户
        return True
    except OSError:
        return False
    return True


def read_hook_file(path: Path) -> str | None:
    """读标记文件或登记文件，返回房间的地址

    文件不存在、内容不全时返回 None。写文件的服务进程已经不在了，说明文件是过期的，删掉后同样返回 None。

    Args:
        path: 标记文件或登记文件的路径

    Returns:
        房间的地址；没有可用的地址时为 None
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    url = lines[0].strip() if lines else ""
    if not url:
        return None
    pid_text = lines[1].strip() if len(lines) > 1 else ""
    if pid_text:
        try:
            alive = process_is_alive(int(pid_text))
        except ValueError:
            alive = False
        if not alive:
            with contextlib.suppress(OSError):
                path.unlink()
            return None
    return url


def request_room(
    url: str, suffix: str, body: bytes | None = None
) -> tuple[int, bytes] | None:
    """向房间发一个 POST

    只认 http 地址，不走代理（房间在本机）。

    Args:
        url: 房间的地址
        suffix: 接口路径，接在房间地址后面
        body: JSON 请求体；没有请求体时为 None

    Returns:
        （状态码，回复内容）；地址不可用、连不上、超时时为 None
    """
    import http.client

    try:
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            return None
        connection = http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=CONNECT_TIMEOUT_SECONDS
        )
    except ValueError:
        return None
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(REQUEST_TIMEOUT_SECONDS)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request("POST", parts.path.rstrip("/") + suffix, body, headers)
        response = connection.getresponse()
        return response.status, response.read(MAX_REPLY_BYTES)
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def run_pre(text: str) -> dict[str, Any]:
    """preToolUse：删掉入参里的 title 和 conversation_id，再填上 Cursor 给的对话标识"""
    allow: dict[str, Any] = {"permission": "allow"}
    data = parse_object(text)
    if data is None:
        return allow
    tool_input = data.get("tool_input")
    if isinstance(tool_input, str):
        tool_input = parse_object(tool_input)
    if not isinstance(tool_input, dict):
        return allow
    cleaned = {
        key: value
        for key, value in tool_input.items()
        if key not in ("title", "conversation_id")
    }
    conversation_id = data.get("conversation_id")
    if isinstance(conversation_id, str) and CONVERSATION_ID.fullmatch(conversation_id):
        return {
            **allow,
            "updated_input": {**cleaned, "conversation_id": conversation_id},
        }
    if cleaned != tool_input:
        return {**allow, "updated_input": cleaned}
    return allow


def run_post(text: str) -> dict[str, Any]:
    """postToolUse：这个对话有排队的消息就向服务端取走，交给 AI"""
    conversation_id = extract_conversation_id(text)
    if conversation_id is None:
        return {}
    marker = feedback_home() / "queue" / conversation_id
    if not marker.is_file() or is_terminal_poll(text):
        return {}
    url = read_hook_file(marker)
    if url is None:
        return {}
    reply = request_room(url, "/api/queue/take")
    if reply is None:
        return {}
    status, body = reply
    if not 200 <= status < 300:
        return {}
    return parse_object(body.decode("utf-8", errors="replace")) or {}


def run_log(text: str) -> dict[str, Any]:
    """sessionStart：把事件的几个字段追加到事件日志"""
    data = parse_object(text)
    if data is None:
        return {}
    record: dict[str, Any] = {
        "ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    }
    for name, source in LOG_FIELDS:
        value = data.get(source)
        if value is not None:
            record[name] = value
    home = feedback_home()
    home.mkdir(parents=True, exist_ok=True)
    log_file = home / "hook-events.log"
    if log_file.is_file() and log_file.stat().st_size > LOG_MAX_BYTES:
        os.replace(log_file, log_file.with_name(f"{log_file.name}.1"))
    with log_file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")
    return {}


def run_stop(text: str) -> dict[str, Any]:
    """stop：写事件日志，再向这个对话的房间报告回合结束"""
    run_log(text)
    conversation_id = extract_conversation_id(text)
    if conversation_id is None:
        return {}
    url = read_hook_file(feedback_home() / "rooms" / conversation_id)
    if url is None:
        return {}
    data = parse_object(text) or {}
    status = data.get("status")
    if not (isinstance(status, str) and STATUS.fullmatch(status)):
        status = "unknown"
    request_room(url, "/api/turn-ended", json.dumps({"status": status}).encode())
    return {}


HANDLERS = {"pre": run_pre, "post": run_post, "log": run_log, "stop": run_stop}


def read_input() -> str:
    """读标准输入；读不出来时当作没有输入"""
    try:
        return sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    except (OSError, ValueError, AttributeError):
        return ""


def main() -> int:
    """按模式处理输入，输出一个 JSON 对象；不论发生什么都以 0 退出"""
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    text = read_input()
    response: dict[str, Any] = {"permission": "allow"} if mode == "pre" else {}
    handler = HANDLERS.get(mode)
    if handler is not None:
        with contextlib.suppress(Exception):
            response = handler(text)
    with contextlib.suppress(OSError):
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
