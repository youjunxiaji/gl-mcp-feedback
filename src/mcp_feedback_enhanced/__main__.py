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
import os
import sys
import warnings


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

    hub_parser = subparsers.add_parser("web-hub", help=argparse.SUPPRESS)
    hub_parser.add_argument("--host", default="127.0.0.1")
    hub_parser.add_argument("--port", type=int, default=8765)
    hub_parser.add_argument("--token", required=True)
    hub_parser.add_argument("--idle-timeout", type=int, default=1800)
    hub_parser.add_argument("--state-file", default="")

    # 测试命令
    test_parser = subparsers.add_parser("test", help="执行测试")
    test_parser.add_argument(
        "--web", action="store_true", help="测试 Web UI (自动持续运行)"
    )
    test_parser.add_argument(
        "--timeout", type=int, default=600, help="测试超时时间 (秒)"
    )

    # 版本命令
    subparsers.add_parser("version", help="显示版本信息")

    args = parser.parse_args()

    if args.command == "test":
        run_tests(args)
    elif args.command == "web-hub":
        run_web_hub(args)
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


def run_web_hub(args):
    """启动由 MCP client 维护的共享 Web Hub。"""
    import time
    from pathlib import Path

    from .web.hub_client import HubConnection, write_hub_state
    from .web.main import WebUIManager
    from .web.utils.port_manager import PortManager

    os.environ["MCP_WEB_HUB"] = "true"
    os.environ["MCP_WEB_HUB_TOKEN"] = args.token
    os.environ["MCP_WEB_HOST"] = args.host
    os.environ["MCP_WEB_PORT"] = str(args.port)
    os.environ["MCP_WEB_PORT_AUTO_CLEANUP"] = "false"

    if not PortManager.is_port_available(args.host, args.port):
        print(
            f"Web Hub 端口 {args.host}:{args.port} 已被占用，无法启动 shared Hub。",
            file=sys.stderr,
        )
        sys.exit(2)

    manager = WebUIManager(port=args.port)
    manager.ensure_server_running()

    connection = HubConnection(
        url=f"http://{manager.host}:{manager.port}",
        host=manager.host,
        port=manager.port,
        token=args.token,
        pid=os.getpid(),
    )
    write_hub_state(connection)

    state_path = Path(args.state_file) if args.state_file else None
    idle_timeout = max(int(args.idle_timeout), 0)
    idle_since = time.time()

    try:
        while True:
            has_pending_session = any(
                not session.feedback_completed.is_set() and not session.is_terminal()
                for session in manager.sessions.values()
            )
            if has_pending_session:
                idle_since = time.time()
            elif idle_timeout > 0 and time.time() - idle_since >= idle_timeout:
                break
            time.sleep(5)
    finally:
        if state_path and state_path.exists():
            try:
                state_path.unlink()
            except OSError:
                pass


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
        success = test_web_ui_simple(args.timeout)
        if not success:
            sys.exit(1)
    else:
        print("❌ 测试功能已简化")
        print("💡 可用的测试选项：")
        print("  --web         测试 Web UI")
        print("💡 对于开发者：使用 'uv run pytest' 执行完整测试")
        sys.exit(1)


def test_web_ui_simple(timeout: int = 600):
    """简单的 Web UI 测试"""
    try:
        import tempfile
        import time
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
验证 **combinedSummaryContent** 区域的 Markdown 语法显示功能

### ✨ 支援的语法特性

##### 五级标题
###### 六级标题

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
2. 加载 marked.js
3. 通过内建 allowlist 清理 HTML
4. 渲染内容

#### 连结和引用
- 专案连结：[MCP Feedback Enhanced](https://github.com/example/mcp-feedback-enhanced)
- 文档连结：[Marked.js 官方文档](https://marked.js.org/)

> **重要提示：** Markdown 与允许的 HTML 块都会经过内建 allowlist 清理。

#### HTML 块范例
<details open>
<summary>可展开 HTML 块</summary>
<div>
  <p>这里包含 <mark>高亮文字</mark> 和 <kbd>Ctrl</kbd> + <kbd>Enter</kbd>。</p>
  <p><abbr title="Model Context Protocol">MCP</abbr> 工具提示：<span title="用于收集用户交互反馈的 MCP 工具">interactive_feedback</span></p>
</div>
</details>

#### 表格范例
| 功能 | 状态 | 说明 |
|------|------|------|
| 标题渲染 | ✅ | 支援 H1-H6 |
| 代码区块 | ✅ | 基础代码块显示 |
| 列表功能 | ✅ | 有序/无序列表 |
| HTML 块 | ✅ | 支援 allowlist 内的安全 HTML |

---

### 🔒 安全特性
- XSS 防护：使用内建 allowlist 清理
- 白名单标签：仅允许安全的 HTML 标签与属性
- URL 验证：限制允许的 URL 协议

### 📝 测试结果
如果您能看到上述内容以正确的格式显示，表示 Markdown 渲染功能运作正常！"""

            secondary_markdown_test_content = """# 第二个测试会话

这个会话用于测试多会话标签、横向拖动、隐藏标签和移动端布局。

```bash
echo "copy from second session"
```

请尝试关闭其中一个标签，确认它只从顶部标签栏隐藏，不影响会话管理列表。
"""

            created_session_id = manager.create_session(
                temp_dir, markdown_test_content, title="Markdown 渲染测试"
            )
            second_session_id = manager.create_session(
                temp_dir, secondary_markdown_test_content, title="移动端标签测试"
            )

            if created_session_id:
                print("✅ 会话创建成功")
                for session_id in (created_session_id, second_session_id):
                    session = manager.get_session(session_id)
                    if not session:
                        continue
                    actual_timeout = session.start_feedback_wait_window(timeout)
                    print(
                        f"⏱️  会话 {session_id[:8]} 倒计时已初始化: "
                        f"{actual_timeout} 秒 (MCP 参数: {timeout} 秒)"
                    )

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

                    print("📝 Web UI 测试完成，进入持续模式...")
                    print("💡 提示：服务器将持续运行，可在浏览器中测试互动功能")
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
