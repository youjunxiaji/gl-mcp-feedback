#!/usr/bin/env python3
"""
MCP Feedback Enhanced 服务器主模块

此模块提供 MCP (Model Context Protocol) 的增强反馈收集功能，
支持智能环境检测，自动使用 Web UI 界面。

主要功能：
- MCP 工具实现
- 界面选择（Web UI）
- 环境检测 (SSH Remote, WSL, Local)
- 国际化支持
- 图片处理与上传
- 项目目录管理

主要 MCP 工具：
- interactive_feedback: 收集用户互动反馈
- get_system_info: 获取系统环境信息

作者: Fábio Ferreira (原作者)
增强: Minidoracat (Web UI, 图片支持, 环境检测)
重构: 模块化设计
"""

import base64
import io
import json
import os
import sys
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import TextContent
from pydantic import Field

# 导入统一的调试功能
from .debug import server_debug_log as debug_log

# 导入错误处理框架
from .utils.error_handler import ErrorHandler, ErrorType

# 导入资源管理器
from .utils.resource_manager import create_temp_file


# ===== 编码初始化 =====
def init_encoding():
    """初始化编码设置，确保正确处理中文字符"""
    try:
        # Windows 特殊处理
        if sys.platform == "win32":
            import msvcrt

            # 设置为二进制模式
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

            # 重新包装为 UTF-8 文本流，并禁用缓冲
            stdin_buffer = getattr(sys.stdin, "buffer", None)
            if stdin_buffer is None and hasattr(sys.stdin, "detach"):
                stdin_buffer = sys.stdin.detach()  # type: ignore[union-attr]

            stdout_buffer = getattr(sys.stdout, "buffer", None)
            if stdout_buffer is None and hasattr(sys.stdout, "detach"):
                stdout_buffer = sys.stdout.detach()  # type: ignore[union-attr]

            sys.stdin = io.TextIOWrapper(
                stdin_buffer,
                encoding="utf-8",
                errors="replace",
                newline=None,  # type: ignore[arg-type]
            )
            sys.stdout = io.TextIOWrapper(
                stdout_buffer,  # type: ignore[arg-type]
                encoding="utf-8",
                errors="replace",
                newline="",
                write_through=True,  # 关键：禁用写入缓冲
            )
        else:
            # 非 Windows 系统的标准设置
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

        # 设置 stderr 编码（用于调试信息）
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

        return True
    except Exception:
        # 如果编码设置失败，尝试基本设置
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except:
            pass
        return False


# 初始化编码（在导入时就执行）
_encoding_initialized = init_encoding()

# ===== 常量定义 =====
SERVER_NAME = "互动式反馈收集 MCP"
SSH_ENV_VARS = ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"]
REMOTE_ENV_VARS = ["REMOTE_CONTAINERS", "CODESPACES"]


# 初始化 MCP 服务器
from . import __version__


# 确保 log_level 设定为正确的大写格式
fastmcp_settings = {}

# 检查环境变量并设定正确的 log_level
env_log_level = os.getenv("FASTMCP_LOG_LEVEL", "").upper()
if env_log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    fastmcp_settings["log_level"] = env_log_level
else:
    # 默认使用 INFO 等级
    fastmcp_settings["log_level"] = "INFO"

mcp: Any = FastMCP(SERVER_NAME)


# ===== 工具函数 =====
def is_wsl_environment() -> bool:
    """
    检测是否在 WSL (Windows Subsystem for Linux) 环境中运行

    Returns:
        bool: True 表示 WSL 环境，False 表示其他环境
    """
    try:
        # 检查 /proc/version 文件是否包含 WSL 标识
        if os.path.exists("/proc/version"):
            with open("/proc/version") as f:
                version_info = f.read().lower()
                if "microsoft" in version_info or "wsl" in version_info:
                    debug_log("检测到 WSL 环境（通过 /proc/version）")
                    return True

        # 检查 WSL 相关环境变量
        wsl_env_vars = ["WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV"]
        for env_var in wsl_env_vars:
            if os.getenv(env_var):
                debug_log(f"检测到 WSL 环境变量: {env_var}")
                return True

        # 检查是否存在 WSL 特有的路径
        wsl_paths = ["/mnt/c", "/mnt/d", "/proc/sys/fs/binfmt_misc/WSLInterop"]
        for path in wsl_paths:
            if os.path.exists(path):
                debug_log(f"检测到 WSL 特有路径: {path}")
                return True

    except Exception as e:
        debug_log(f"WSL 检测过程中发生错误: {e}")

    return False


def is_remote_environment() -> bool:
    """
    检测是否在远程环境中运行

    Returns:
        bool: True 表示远程环境，False 表示本地环境
    """
    # WSL 不应被视为远程环境，因为它可以访问 Windows 浏览器
    if is_wsl_environment():
        debug_log("WSL 环境不被视为远程环境")
        return False

    # 检查 SSH 连接指标
    for env_var in SSH_ENV_VARS:
        if os.getenv(env_var):
            debug_log(f"检测到 SSH 环境变量: {env_var}")
            return True

    # 检查远程开发环境
    for env_var in REMOTE_ENV_VARS:
        if os.getenv(env_var):
            debug_log(f"检测到远程开发环境: {env_var}")
            return True

    # 检查 Docker 容器
    if os.path.exists("/.dockerenv"):
        debug_log("检测到 Docker 容器环境")
        return True

    # Windows 远程桌面检查
    if sys.platform == "win32":
        session_name = os.getenv("SESSIONNAME", "")
        if session_name and "RDP" in session_name:
            debug_log(f"检测到 Windows 远程桌面: {session_name}")
            return True

    # Linux 无显示环境检查（但排除 WSL）
    if (
        sys.platform.startswith("linux")
        and not os.getenv("DISPLAY")
        and not is_wsl_environment()
    ):
        debug_log("检测到 Linux 无显示环境")
        return True

    return False


def save_feedback_to_file(feedback_data: dict, file_path: str | None = None) -> str:
    """
    将反馈数据保存到 JSON 文件

    Args:
        feedback_data: 反馈数据字典
        file_path: 保存路径，若为 None 则自动生成临时文件

    Returns:
        str: 保存的文件路径
    """
    if file_path is None:
        # 使用资源管理器创建临时文件
        file_path = create_temp_file(suffix=".json", prefix="feedback_")

    # 确保目录存在
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    # 复制数据以避免修改原始数据
    json_data = feedback_data.copy()

    # 处理图片数据：将 bytes 转换为 base64 字符串以便 JSON 序列化
    if "images" in json_data and isinstance(json_data["images"], list):
        processed_images = []
        for img in json_data["images"]:
            if isinstance(img, dict) and "data" in img:
                processed_img = img.copy()
                # 如果 data 是 bytes，转换为 base64 字符串
                if isinstance(img["data"], bytes):
                    processed_img["data"] = base64.b64encode(img["data"]).decode(
                        "utf-8"
                    )
                    processed_img["data_type"] = "base64"
                processed_images.append(processed_img)
            else:
                processed_images.append(img)
        json_data["images"] = processed_images

    # 保存数据
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    debug_log(f"反馈数据已保存至: {file_path}")
    return file_path


def create_feedback_text(feedback_data: dict) -> str:
    """
    创建格式化的反馈文本

    Args:
        feedback_data: 反馈数据字典

    Returns:
        str: 格式化后的反馈文本
    """
    text_parts = []

    # 基本反馈内容
    if feedback_data.get("interactive_feedback"):
        text_parts.append(f"=== 用户反馈 ===\n{feedback_data['interactive_feedback']}")

    # 图片直接通过 MCPImage 传递，不在文本中包含 base64（节省 token）
    if feedback_data.get("images"):
        images = feedback_data["images"]
        debug_log(f"共 {len(images)} 张图片将通过 MCPImage 传递")

    return "\n\n".join(text_parts) if text_parts else "用户未提供任何反馈内容。"


def process_images(images_data: list[dict]) -> list[MCPImage]:
    """
    处理图片数据，转换为 MCP 图片对象

    Args:
        images_data: 图片数据列表

    Returns:
        List[MCPImage]: MCP 图片对象列表
    """
    mcp_images = []

    for i, img in enumerate(images_data, 1):
        try:
            if not img.get("data"):
                debug_log(f"图片 {i} 没有数据，跳过")
                continue

            # 检查数据类型并相应处理
            if isinstance(img["data"], bytes):
                image_bytes = img["data"]
                debug_log(
                    f"图片 {i} 使用原始 bytes 数据，大小: {len(image_bytes)} bytes"
                )
            elif isinstance(img["data"], str):
                image_bytes = base64.b64decode(img["data"])
                debug_log(f"图片 {i} 从 base64 解码，大小: {len(image_bytes)} bytes")
            else:
                debug_log(f"图片 {i} 数据类型不支持: {type(img['data'])}")
                continue

            if len(image_bytes) == 0:
                debug_log(f"图片 {i} 数据为空，跳过")
                continue

            # 根据文件名推断格式
            file_name = img.get("name", "image.png")
            if file_name.lower().endswith((".jpg", ".jpeg")):
                image_format = "jpeg"
            elif file_name.lower().endswith(".gif"):
                image_format = "gif"
            else:
                image_format = "png"

            # 创建 MCPImage 对象
            mcp_image = MCPImage(data=image_bytes, format=image_format)
            mcp_images.append(mcp_image)

            debug_log(f"图片 {i} ({file_name}) 处理成功，格式: {image_format}")

        except Exception as e:
            error_id = ErrorHandler.log_error_with_context(
                e,
                context={"operation": "图片处理", "image_index": i},
                error_type=ErrorType.FILE_IO,
            )
            debug_log(f"图片 {i} 处理失败 [错误ID: {error_id}]: {e}")

    debug_log(f"共处理 {len(mcp_images)} 张图片")
    return mcp_images


def create_keepalive_text(result: dict) -> str:
    """创建 MCP 保活续等提示，提醒 AI 使用轻量续等工具。"""
    session_id = result.get("session_id", "")
    timeout = result.get("timeout", 600)
    actual_timeout = result.get("actual_timeout", max(int(timeout) - 30, 5))
    auto_used = result.get("auto_extend_used", 0)
    auto_max = result.get("auto_extend_max", 3)
    manual_tokens = result.get("manual_extend_tokens", 0)
    reason = result.get("keepalive_reason", "auto")
    return (
        "=== MCP 保活请求 ===\n"
        "用户仍在 Web 页面编辑反馈，尚未提交。\n"
        "请立即调用 continue_feedback_session 继续等待同一个会话，"
        "不要重发 summary。\n\n"
        f"session_id: {session_id}\n"
        f"timeout: {timeout}\n"
        f"actual_wait_seconds: {actual_timeout}\n"
        f"reason: {reason}\n"
        f"auto_extend_used: {auto_used}/{auto_max}\n"
        f"manual_extend_tokens: {manual_tokens}"
    )


def create_tool_result_from_feedback(result: dict) -> ToolResult:
    """将 Web 反馈结果转换成 MCP ToolResult。"""
    if result.get("keepalive_request"):
        return ToolResult(
            content=[TextContent(type="text", text=create_keepalive_text(result))]
        )

    if not result:
        return ToolResult(content=[TextContent(type="text", text="用户取消了反馈。")])

    save_feedback_to_file(result)

    feedback_items = []
    if result.get("interactive_feedback"):
        feedback_text = create_feedback_text(result)
        feedback_items.append(TextContent(type="text", text=feedback_text))
        debug_log("文字反馈已添加")

    if result.get("images"):
        mcp_images = process_images(result["images"])
        feedback_items.extend(mcp_images)
        debug_log(f"已添加 {len(mcp_images)} 张图片")

    if not feedback_items:
        feedback_items.append(TextContent(type="text", text="用户未提供任何反馈内容。"))

    debug_log(f"反馈收集完成，共 {len(feedback_items)} 个项目")
    return ToolResult(content=feedback_items)


# ===== MCP Prompt 定义 =====
# This prompt only carries the minimal "activate + act now" directive for the
# current turn. The full interaction contract (when to call, how to write,
# skip handling, etc.) lives in ``interactive_feedback``'s docstring so that
# it ships with the MCP package and applies to every user.
_CHAT_PROMPT = """\
# ACTIVATE: gl-mcp-feedback interaction mode

You MUST use the `interactive_feedback` tool as the PRIMARY channel for
speaking with the user from this turn onward. See that tool's description
for the full contract. Core rules:

1. **Call `interactive_feedback` IMMEDIATELY**. In `summary`, write a
   Markdown brief of your current understanding / progress / plan, then wait
   for the user's feedback before deciding the next step.
2. **NEVER end a turn with a plain-text reply.** Any substantive content you
   want the user to read (analysis, plans, code summaries, questions) MUST
   go through `summary`. The Web UI renders Markdown far better than the
   Cursor chat panel.
3. **While `interactive_feedback` is available, DO NOT use Cursor's built-in
   `AskQuestion` / question-picker tool.** Route every question to the user
   through `interactive_feedback`'s `summary` (ask with a numbered list).
4. **On skip / timeout → call `interactive_feedback` AGAIN** and ask why,
   do NOT assume the conversation is over. Stop only when the user
   explicitly says `结束` / `不用了` / `stop` or equivalent.
5. **`summary` must be substantive.** Empty strings and placeholder phrases
   like "我已完成了您请求的任务。" will be rejected at runtime.
6. Keep `timeout >= 600`. **Reply to the user in the same language they are
   using** (the user's latest message here is in Chinese — so respond in
   Chinese), even though this prompt is in English.\
"""


@mcp.prompt(name="chat")
def gl_mcp_feedback() -> str:
    """通过 Web UI 与用户开始互动反馈对话。

    使用此 prompt 快速启动反馈交流。
    AI 会调用 interactive_feedback 工具打开 Web UI，
    通过浏览器界面与用户进行互动。
    """
    return _CHAT_PROMPT


# ===== MCP 工具定义 =====
@mcp.tool()
async def interactive_feedback(
    project_directory: Annotated[
        str, Field(description="Project directory path.")
    ] = ".",
    summary: Annotated[
        str,
        Field(
            description=(
                "Markdown summary of the AI's current work / plan / question, "
                "rendered in the Web UI. MUST be filled in with substantive "
                "content; empty strings and placeholder phrases (e.g. "
                '"我已完成了您请求的任务。") are rejected at runtime.'
            )
        ),
    ] = "",
    title: Annotated[
        str,
        Field(
            description=(
                "Optional short headline (max ~30 chars) for this feedback "
                "round, displayed in the browser tab and at the top of the "
                'AI summary panel. Should be concise (e.g. "需要你提供更多信息", '
                '"修复完成，请验证"). Leave empty when there\'s no good headline.'
            )
        ),
    ] = "",
    timeout: Annotated[
        int, Field(description="Seconds to wait for user feedback. Keep >= 600.")
    ] = 600,
) -> ToolResult:
    """Open a Web UI to collect interactive feedback from the user.

    This tool is the PRIMARY communication channel with the user. Route
    substantive content (analyses, plans, code summaries, questions needing
    confirmation) through this tool's ``summary`` parameter rather than the
    Cursor chat, because the Web UI renders Markdown far better.

    MANDATORY call scenarios — do NOT end the turn without calling:
        1. After any code modification — summarize the change, wait for
           the user to test.
        2. Before implementing a new requirement — explain your
           understanding and approach, wait for confirmation.
        3. When a tool call is cancelled/interrupted — ask the user the
           next step.
        4. When this MCP call times out — immediately re-call; do not
           assume the user has left.
        5. When the user explicitly skips without giving feedback —
           immediately re-call and ask why they skipped.
        6. At the end of any multi-step task stage — check in.
        7. Whenever you would otherwise ask the user a question in plain
           chat — route it through this tool's ``summary`` instead.

    STRICT PROHIBITIONS:
        - While this tool is available, DO NOT invoke Cursor's built-in
          ``AskQuestion`` / question-picker tool. Every question to the
          user MUST go through this tool's ``summary`` (ask with a
          numbered list inside the Markdown).
        - DO NOT end your turn with a plain-text reply when you still
          have output intended for the user to read. Put that output
          into ``summary`` and call this tool instead.
        - Allowed parameters: ``project_directory``, ``summary``,
          ``title``, ``timeout``. ``title`` is OPTIONAL — pass a short
          headline (≤30 chars) for the Web UI's tab title and summary
          header. Names such as ``question``, ``choices``, ``options``
          belong to ``AskQuestion`` — NEVER forward them to this tool.
          Passing any other keyword argument will cause the call to
          fail schema validation on the server.

    Only stop calling when the user explicitly says ``结束`` / ``不用了`` /
    ``stop`` / ``done`` or an equivalent termination phrase.

    ``summary`` writing rules:
        - Write in the same language the user is currently using.
        - Use Markdown: headings (##), lists, tables, code fences for
          snippets, bold for key decisions.
        - Include modified file paths, key trade-offs, and explicit
          questions for the user when applicable.
        - NEVER pass an empty string or the placeholder phrase
          ``我已完成了您请求的任务。``. The tool rejects such values at
          runtime and returns a correction notice that you must follow
          by re-calling with a real summary.

    ``title`` writing rules:
        - Optional. Leave empty when ``summary`` is self-explanatory.
        - Keep concise (≤30 chars) — it's a headline, not a sentence.
        - Use the same language as the user.
        - Examples: ``需要你提供更多信息`` / ``修复完成，请验证`` /
          ``实现方案确认`` / ``Need more context``.

    Args:
        project_directory: Absolute or relative project path. Defaults to cwd.
        summary: Required. Markdown summary of the AI's current work / plan
            / question. No empty string, no placeholder phrase.
        title: Optional short headline shown in the Web UI tab and summary
            header. Leave empty if there's no concise headline.
        timeout: Seconds to wait for user feedback. Keep ``>= 600``.

    Returns:
        ToolResult: TextContent with the user's text feedback plus
            MCPImage objects for any uploaded images. An empty result
            means the user skipped — you MUST re-call this tool to ask why.
    """
    # summary 运行时校验：阻止 AI 使用空串或占位套话偷懒
    # 规范化：去首尾空白 + 去末尾中英文标点，让“我已完成了您请求的任务”“…。”“… .”都能命中
    normalized_summary = summary.strip().rstrip("。.!?！？ \t\n")
    invalid_summary_placeholders = {
        "",
        "我已完成了您请求的任务",
    }
    if normalized_summary in invalid_summary_placeholders:
        guidance = (
            "[interactive_feedback REJECTED]\n"
            "The `summary` you passed is empty or a placeholder phrase; "
            "it will NOT be shown to the user.\n"
            "You MUST call this tool AGAIN with a real Markdown summary containing:\n"
            "  1) Your current understanding / progress / proposed plan.\n"
            "  2) File paths touched and key trade-offs.\n"
            "  3) Explicit question(s) the user needs to answer.\n"
            "Write the summary in the user's language (NOT English, unless "
            "the user is writing in English). Do NOT reply to the user "
            "with plain Cursor chat text in the meantime."
        )
        debug_log(f"summary validation failed: {summary!r}")
        return ToolResult(content=[TextContent(type="text", text=guidance)])

    is_remote = is_remote_environment()
    is_wsl = is_wsl_environment()

    debug_log(f"环境检测结果 - 远程: {is_remote}, WSL: {is_wsl}")
    debug_log("使用界面: Web UI")

    try:
        # 确保项目目录存在
        if not os.path.exists(project_directory):
            project_directory = os.getcwd()
        project_directory = os.path.abspath(project_directory)

        # 使用 Web 模式
        debug_log("反馈模式: web")

        result = await launch_web_feedback_ui(
            project_directory, summary, timeout, title=title
        )

        return create_tool_result_from_feedback(result)

    except Exception as e:
        error_id = ErrorHandler.log_error_with_context(
            e,
            context={"operation": "反馈收集", "project_dir": project_directory},
            error_type=ErrorType.SYSTEM,
        )

        # 生成用户友好的错误信息
        user_error_msg = ErrorHandler.format_user_error(e, include_technical=False)
        debug_log(f"反馈收集错误 [错误ID: {error_id}]: {e!s}")

        return ToolResult(content=[TextContent(type="text", text=user_error_msg)])


@mcp.tool()
async def continue_feedback_session(
    session_id: Annotated[
        str,
        Field(
            description=(
                "Existing Web feedback session id returned by an MCP keepalive "
                "request. Continue waiting for that session without resending "
                "summary/title."
            )
        ),
    ],
    timeout: Annotated[
        int,
        Field(
            description=(
                "Seconds to wait before returning another keepalive request. "
                "Use 600 unless the MCP client configuration requires a smaller value."
            )
        ),
    ] = 600,
) -> ToolResult:
    """Continue waiting for an existing feedback session without creating a new one."""
    try:
        debug_log(f"继续等待 Web 反馈会话: {session_id}, timeout={timeout}")
        from .web import continue_web_feedback_session as web_continue  # noqa: PLC0415

        result = await web_continue(session_id, timeout)
        return create_tool_result_from_feedback(result)
    except Exception as e:
        error_id = ErrorHandler.log_error_with_context(
            e,
            context={"operation": "反馈会话续等", "session_id": session_id},
            error_type=ErrorType.SYSTEM,
        )
        user_error_msg = ErrorHandler.format_user_error(e, include_technical=False)
        debug_log(f"反馈会话续等错误 [错误ID: {error_id}]: {e!s}")
        return ToolResult(content=[TextContent(type="text", text=user_error_msg)])


async def launch_web_feedback_ui(
    project_dir: str, summary: str, timeout: int, *, title: str = ""
) -> dict:
    """启动 Web UI 收集反馈，支持自定义超时时间。

    Args:
        project_dir: 项目目录路径。
        summary: AI 工作摘要（Markdown）。
        timeout: 超时时间（秒）。
        title: 可选的反馈标题，显示在浏览器标签和摘要面板顶部。

    Returns:
        dict: 收集到的反馈数据。
    """
    debug_log(f"启动 Web UI 界面，超时时间: {timeout} 秒")

    try:
        from .web import launch_web_feedback_ui as web_launch

        return await web_launch(project_dir, summary, timeout, title=title)
    except ImportError as e:
        error_id = ErrorHandler.log_error_with_context(
            e,
            context={"operation": "Web UI 模块导入", "module": "web"},
            error_type=ErrorType.DEPENDENCY,
        )
        user_error_msg = ErrorHandler.format_user_error(
            e, ErrorType.DEPENDENCY, include_technical=False
        )
        debug_log(f"Web UI 模块导入失败 [错误ID: {error_id}]: {e}")

        return {
            "interactive_feedback": user_error_msg,
            "images": [],
        }


@mcp.tool()
def get_system_info() -> str:
    """
    获取系统环境信息

    Returns:
        str: JSON 格式的系统信息
    """
    is_remote = is_remote_environment()
    is_wsl = is_wsl_environment()

    system_info = {
        "平台": sys.platform,
        "Python 版本": sys.version.split()[0],
        "WSL 环境": is_wsl,
        "远程环境": is_remote,
        "界面类型": "Web UI",
        "环境变量": {
            "SSH_CONNECTION": os.getenv("SSH_CONNECTION"),
            "SSH_CLIENT": os.getenv("SSH_CLIENT"),
            "DISPLAY": os.getenv("DISPLAY"),
            "VSCODE_INJECTION": os.getenv("VSCODE_INJECTION"),
            "SESSIONNAME": os.getenv("SESSIONNAME"),
            "WSL_DISTRO_NAME": os.getenv("WSL_DISTRO_NAME"),
            "WSL_INTEROP": os.getenv("WSL_INTEROP"),
            "WSLENV": os.getenv("WSLENV"),
        },
    }

    return json.dumps(system_info, ensure_ascii=False, indent=2)


# ===== 主程序入口 =====
def main():
    """主要入口点，用于包执行
    收集用户的互动反馈，支持文字和图片
    此工具使用 Web UI 界面收集用户反馈，支持智能环境检测。

    用户可以：
    1. 提供文字反馈
    2. 上传图片作为反馈
    3. 查看 AI 的工作摘要

    调试模式：
    - 设置环境变量 MCP_DEBUG=true 可启用详细调试输出
    - 生产环境建议关闭调试模式以避免输出干扰
    """
    debug_enabled = os.getenv("MCP_DEBUG", "").lower() in ("true", "1", "yes", "on")

    if debug_enabled:
        debug_log("🚀 启动互动式反馈收集 MCP 服务器")
        debug_log(f"   服务器名称: {SERVER_NAME}")
        debug_log(f"   版本: {__version__}")
        debug_log(f"   平台: {sys.platform}")
        debug_log(f"   编码初始化: {'成功' if _encoding_initialized else '失败'}")
        debug_log(f"   远程环境: {is_remote_environment()}")
        debug_log(f"   WSL 环境: {is_wsl_environment()}")
        debug_log("   界面类型: Web UI")
        debug_log("   等待来自 AI 助手的调用...")
        debug_log("准备启动 MCP 服务器...")
        debug_log("调用 mcp.run()...")

    try:
        if os.getenv("MCP_WEB_ALWAYS_ON", "").lower() in ("true", "1", "yes", "on"):
            try:
                from .web import get_web_ui_manager  # noqa: PLC0415

                get_web_ui_manager().ensure_server_running()
                debug_log("MCP_WEB_ALWAYS_ON 已启用，Web 等待页已启动")
            except Exception as e:
                debug_log(f"MCP_WEB_ALWAYS_ON 启动失败，继续启动 MCP: {e}")

        mcp.run()
    except KeyboardInterrupt:
        if debug_enabled:
            debug_log("收到中断信号，正常退出")
        sys.exit(0)
    except Exception as e:
        if debug_enabled:
            debug_log(f"MCP 服务器启动失败: {e}")
            import traceback

            debug_log(f"详细错误: {traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
