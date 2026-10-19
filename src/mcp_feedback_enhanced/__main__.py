#!/usr/bin/env python3
"""
MCP Interactive Feedback Enhanced - 主程序入口
==============================================

此档案允许包透过 `python -m mcp_feedback_enhanced` 执行。

使用方法:
  python -m mcp_feedback_enhanced        # 启动 MCP 服务器
  python -m mcp_feedback_enhanced test   # 执行测试
"""

import argparse
import asyncio
import base64
import os
import sys
import threading
import time
import warnings
from typing import TYPE_CHECKING, NamedTuple


if TYPE_CHECKING:
    from .web.main import WebUIManager


# 抑制 Windows 上的 asyncio ResourceWarning
if sys.platform == "win32":
    warnings.filterwarnings(
        "ignore", category=ResourceWarning, message=".*unclosed transport.*"
    )
    warnings.filterwarnings("ignore", category=ResourceWarning, message=".*unclosed.*")

    # 设置 asyncio 事件循环策略以减少警告
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass


def main():
    """主程序入口点"""
    parser = argparse.ArgumentParser(
        description="MCP Feedback Enhanced Enhanced - 互动式反馈收集 MCP 服务器"
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 服务器命令（默认）
    subparsers.add_parser("server", help="启动 MCP 服务器（默认）")

    # 测试命令
    test_parser = subparsers.add_parser("test", help="执行测试")
    test_parser.add_argument(
        "--web", action="store_true", help="测试 Web UI (自动持续运行)"
    )
    test_parser.add_argument(
        "--timeout", type=int, default=60, help="测试超时时间 (秒)"
    )

    # 版本命令
    subparsers.add_parser("version", help="显示版本信息")

    args = parser.parse_args()

    if args.command == "test":
        run_tests(args)
    elif args.command == "version":
        show_version()
    elif args.command == "server" or args.command is None:
        run_server()
    else:
        # 不应该到达这里
        parser.print_help()
        sys.exit(1)


def run_server():
    """启动 MCP 服务器"""
    from .server import main as server_main

    return server_main()


def run_tests(args):
    """执行测试"""
    # 启用调试模式以显示测试过程
    os.environ["MCP_DEBUG"] = "true"

    # 在 Windows 上抑制 asyncio 警告
    if sys.platform == "win32":
        import warnings

        # 设置更全面的警告抑制
        os.environ["PYTHONWARNINGS"] = (
            "ignore::ResourceWarning,ignore::DeprecationWarning"
        )
        warnings.filterwarnings("ignore", category=ResourceWarning)
        warnings.filterwarnings("ignore", message=".*unclosed transport.*")
        warnings.filterwarnings("ignore", message=".*I/O operation on closed pipe.*")
        warnings.filterwarnings("ignore", message=".*unclosed.*")
        # 抑制 asyncio 相关的所有警告
        warnings.filterwarnings("ignore", module="asyncio.*")

    if args.web:
        print("🧪 执行 Web UI 测试...")
        success = test_web_ui_simple()
        if not success:
            sys.exit(1)
    else:
        print("❌ 测试功能已简化")
        print("💡 可用的测试选项：")
        print("  --web         测试 Web UI")
        print("💡 对于开发者：使用 'uv run pytest' 执行完整测试")
        sys.exit(1)


def _build_demo_image(label: str, start_color: str, end_color: str) -> dict:
    """生成测试用的示例图片（SVG 渐变图），同时用作缩略图

    Args:
        label: 图片上显示的文字，同时作为文件名
        start_color: 渐变起始颜色
        end_color: 渐变结束颜色

    Returns:
        可直接传给 submit_feedback 的图片字典
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{start_color}"/>'
        f'<stop offset="1" stop-color="{end_color}"/>'
        "</linearGradient></defs>"
        '<rect width="320" height="200" fill="url(#g)"/>'
        '<text x="160" y="108" font-size="28" fill="white" text-anchor="middle" '
        f'font-family="sans-serif">{label}</text></svg>'
    )
    raw = svg.encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "name": f"{label}.svg",
        "data": encoded,
        "size": len(raw),
        "thumbnail": f"data:image/svg+xml;base64,{encoded}",
    }


class _DemoRound(NamedTuple):
    """测试用的一轮历史对话：AI 的提问加上用户的回复"""

    title: str
    summary: str
    feedback: str
    images: list[dict]
    minutes_ago: int
    reply_after_seconds: int


def _seed_demo_rounds(manager: "WebUIManager", project_directory: str) -> None:
    """往测试用的管理器里灌入两轮带用户回复的历史对话

    这样页面一打开就能看到多轮消息、时间分隔和图片缩略图。

    Args:
        manager: Web UI 管理器
        project_directory: 会话使用的项目目录
    """
    now = time.time()
    demo_rounds = [
        _DemoRound(
            title="确认方案",
            summary=(
                "我打算把工作区改成**聊天对话**的样式：\n\n"
                "- AI 的消息靠左，你的回复靠右\n"
                "- 每条消息最宽占屏幕的三分之二\n"
                "- 输入框固定在窗口底部\n\n"
                "这样可以吗？"
            ),
            feedback="可以，就按这个方案来。",
            images=[],
            minutes_ago=42,
            reply_after_seconds=90,
        ),
        _DemoRound(
            title="样式预览",
            summary=(
                "第一版已经做好了，主要改动：\n\n"
                "1. 工作区改成消息流 + 底部输入框\n"
                "2. 历史对话保存在内存里，刷新页面不会丢\n\n"
                "核心渲染逻辑大致如下：\n\n"
                "```javascript\n"
                "const row = createMessageRow(message);\n"
                "chatList.append(row);\n"
                "```\n\n"
                "请看看效果，有哪里需要调整？"
            ),
            feedback="整体不错，气泡的圆角想再大一点。\n另外附了两张参考图，你对照看看。",
            images=[
                _build_demo_image("参考图 A", "#2563eb", "#7c3aed"),
                _build_demo_image("参考图 B", "#059669", "#0ea5e9"),
            ],
            minutes_ago=12,
            reply_after_seconds=180,
        ),
    ]

    for demo in demo_rounds:
        session_id = manager.create_session(
            project_directory, demo.summary, title=demo.title
        )
        session = manager.get_session(session_id)
        if session is None:
            continue
        session.created_at = now - demo.minutes_ago * 60
        asyncio.run(session.submit_feedback(demo.feedback, demo.images, {}))
        session.feedback_submitted_at = session.created_at + demo.reply_after_seconds
        session.last_activity = session.feedback_submitted_at


def _simulate_ai_followups(
    manager: "WebUIManager",
    project_directory: str,
    work_seconds: int,
    max_rounds: int,
) -> None:
    """模拟 AI 的后续调用，用来在测试服务器里体验消息队列

    测试服务器里没有真正的 AI：当前这一轮被回复之后，「AI」先干活 work_seconds 秒，
    再用和真实调用相同的逻辑发起下一次提问。这期间排队的消息会被取走并打印出来，
    队列是空的就等用户回复，如此循环 max_rounds 次。

    Args:
        manager: Web UI 管理器
        project_directory: 会话使用的项目目录
        work_seconds: 每次「干活」的秒数
        max_rounds: 最多模拟几次后续提问
    """
    for round_number in range(1, max_rounds + 1):
        session = manager.get_current_session()
        if session is None:
            return
        while not session.feedback_completed.wait(0.5):
            pass

        print(
            f"🤖 模拟 AI：开始干活，{work_seconds} 秒后再次提问，这期间发送的消息会进入队列"
        )
        time.sleep(work_seconds)

        title = f"模拟 AI 的第 {round_number} 次提问"
        summary = (
            f"这是**{title}**。\n\n"
            "- 你在「AI 干活」期间排队的消息，会随这次调用一并送达\n"
            "- 队列是空的话，这一轮就等你回复"
        )
        new_session = manager.get_session(
            manager.create_session(project_directory, summary, title=title)
        )
        if new_session is None:
            return

        result = asyncio.run(manager.deliver_queued_messages(new_session))
        if result is None:
            print("🤖 模拟 AI：队列是空的，等你回复这一轮")
        else:
            print("🤖 模拟 AI：取走了排队的消息，AI 会收到：")
            print(result["interactive_feedback"])


def test_web_ui_simple():
    """简单的 Web UI 测试"""
    try:
        import tempfile
        import webbrowser

        from .web.main import WebUIManager

        # 设置测试模式，禁用自动清理避免权限问题
        os.environ["MCP_TEST_MODE"] = "true"
        os.environ["MCP_WEB_HOST"] = "127.0.0.1"
        # 设置更高的端口范围避免系统保留端口
        os.environ["MCP_WEB_PORT"] = "9765"

        print("🔧 创建 Web UI 管理器...")
        manager = WebUIManager()  # 使用环境变数控制主机和端口

        # 显示最终使用的端口（可能因端口占用而自动切换）
        if manager.port != 9765:
            print(f"💡 端口 9765 被占用，已自动切换到端口 {manager.port}")

        print("🔧 创建测试会话...")
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_test_content = """# Web UI 测试 - Markdown 渲染功能

## 🎯 测试目标
验证 AI 消息气泡里的 Markdown 语法显示功能

### ✨ 支援的语法特性

#### 文字格式
- **粗体文字** 使用双星号
- *斜体文字* 使用单星号
- ~~删除线文字~~ 使用双波浪号
- `行内代码` 使用反引号

#### 代码区块
```javascript
// JavaScript 范例
function renderMarkdown(content) {
    return marked.parse(content);
}
```

```python
# Python 范例
def process_feedback(data):
    return {"status": "success", "data": data}
```

#### 列表功能
**无序列表：**
- 第一个项目
- 第二个项目
  - 巢状项目 1
  - 巢状项目 2
- 第三个项目

**有序列表：**
1. 初始化 Markdown 渲染器
2. 加载 marked.js 和 DOMPurify
3. 配置安全选项
4. 渲染内容

#### 连结和引用
- 专案连结：[MCP Feedback Enhanced](https://github.com/example/mcp-feedback-enhanced)
- 文档连结：[Marked.js 官方文档](https://marked.js.org/)

> **重要提示：** 所有 HTML 输出都经过 DOMPurify 清理，确保安全性。

#### 表格范例
| 功能 | 状态 | 说明 |
|------|------|------|
| 标题渲染 | ✅ | 支援 H1-H6 |
| 代码高亮 | ✅ | 基本语法高亮 |
| 列表功能 | ✅ | 有序/无序列表 |
| 连结处理 | ✅ | 安全连结渲染 |

---

### 🔒 安全特性
- XSS 防护：使用 DOMPurify 清理
- 白名单标签：仅允许安全的 HTML 标签
- URL 验证：限制允许的 URL 协议

### 📝 测试结果
如果您能看到上述内容以正确的格式显示，表示 Markdown 渲染功能运作正常！"""

            # 先灌入两轮历史对话，最后一轮才是等待回复的当前会话
            _seed_demo_rounds(manager, temp_dir)
            created_session_id = manager.create_session(
                temp_dir, markdown_test_content, title="Markdown 渲染测试"
            )

            if created_session_id:
                print("✅ 会话创建成功")

                print("🚀 启动 Web 服务器...")
                manager.start_server()
                time.sleep(5)  # 等待服务器完全启动

                if (
                    manager.server_thread is not None
                    and manager.server_thread.is_alive()
                ):
                    print("✅ Web 服务器启动成功")
                    url = f"http://{manager.host}:{manager.port}"
                    print(f"🌐 服务器运行在: {url}")

                    # 如果端口有变更，额外提醒
                    if manager.port != 9765:
                        print(
                            f"📌 注意：由于端口 9765 被占用，服务已切换到端口 {manager.port}"
                        )

                    # 尝试开启浏览器
                    print("🌐 正在开启浏览器...")
                    try:
                        webbrowser.open(url)
                        print("✅ 浏览器已开启")
                    except Exception as e:
                        print(f"⚠️  无法自动开启浏览器: {e}")
                        print(f"💡 请手动开启浏览器并访问: {url}")

                    # 模拟 AI 的后续调用，用来体验「AI 干活期间发送的消息会排队」
                    threading.Thread(
                        target=_simulate_ai_followups,
                        args=(manager, temp_dir, 20, 4),
                        daemon=True,
                    ).start()

                    print("📝 Web UI 测试完成，进入持续模式...")
                    print("💡 提示：服务器将持续运行，可在浏览器中测试互动功能")
                    print(
                        "💡 回复当前的提问后，模拟 AI 会干活 20 秒再次提问；"
                        "这期间发送的消息会排队，被取走时会在这里打印"
                    )
                    print("💡 按 Ctrl+C 停止服务器")

                    try:
                        # 保持服务器运行
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print("\n🛑 停止服务器...")
                        return True
                else:
                    print("❌ Web 服务器启动失败")
                    return False
            else:
                print("❌ 会话创建失败")
                return False

    except Exception as e:
        print(f"❌ Web UI 测试失败: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        # 清理测试环境变数
        os.environ.pop("MCP_TEST_MODE", None)
        os.environ.pop("MCP_WEB_HOST", None)
        os.environ.pop("MCP_WEB_PORT", None)


async def wait_for_process(process):
    """等待进程结束"""
    try:
        # 等待进程自然结束
        await process.wait()

        # 确保管道正确关闭
        try:
            if hasattr(process, "stdout") and process.stdout:
                process.stdout.close()
            if hasattr(process, "stderr") and process.stderr:
                process.stderr.close()
            if hasattr(process, "stdin") and process.stdin:
                process.stdin.close()
        except Exception as close_error:
            print(f"关闭进程管道时出错: {close_error}")

    except Exception as e:
        print(f"等待进程时出错: {e}")


def show_version():
    """显示版本信息"""
    from . import __author__, __version__

    print(f"MCP Feedback Enhanced Enhanced v{__version__}")
    print(f"作者: {__author__}")
    print("GitHub: https://github.com/Minidoracat/mcp-feedback-enhanced")


if __name__ == "__main__":
    main()
