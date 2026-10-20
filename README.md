# gl-mcp-feedback

交互式反馈 MCP 服务器。AI 调用 `interactive_feedback` 把工作摘要发到浏览器页面，用户在页面里回复文字和图片，回复再回到 AI，把多次试错合并成一次有效沟通。

适用于支持 MCP 的 AI 编辑器，如 [Cursor](https://www.cursor.com)、[Cline](https://cline.bot)、[Windsurf](https://windsurf.com)。

## 功能

- **微信式对话流**：AI 的摘要（Markdown 渲染，代码块带复制按钮）靠左，用户回复靠右，历史消息可回看。
- **文字与图片回复**：图片可选文件或 `Ctrl/⌘+V` 粘贴，`Ctrl/⌘+Enter` 发送。
- **消息排队**：AI 干活期间用户也能继续发消息，最多排 3 条，排队中可以编辑、撤回。装了 [Cursor 钩子](#cursor-钩子)，AI 调用完任意一次工具就能读到；没装，或 AI 先一步再次提问，则随下一次 `interactive_feedback` 一并带回，不会因为 AI 再次提问而丢失。
- **多对话分开**：每个 Cursor 对话一个页面（`/c/<对话 ID>/`），回复和排队的消息不会串到别的对话；对话 ID 由 Cursor 钩子填入 `conversation_id`，没有钩子的调用共用一个页面。`/` 列出所有对话。
- **新提问提醒**：新一轮提问到达时播放提示音，页面在后台时再弹桌面通知。
- **环境自动检测**：识别 SSH Remote、WSL 等环境，无法打开浏览器时可通过端口转发访问。

## 工具与 Prompt

| 名称 | 说明 |
|------|------|
| `interactive_feedback` | 打开 Web UI 收集反馈。参数：`project_directory`、`summary`（Markdown，必填）、`timeout`（秒，建议 ≥ 600）、`conversation_id`（可选，由 Cursor 钩子填入，AI 不要填） |
| `get_system_info` | 返回平台、Python 版本、是否远程 / WSL 等环境信息 |
| prompt `chat` | 无参数，激活互动模式。在 Cursor 中输入 `/user-gl-mcp-feedback/chat`（前缀随 MCP 配置里的服务器名而定） |

## 安装与配置

需要 Python ≥ 3.12 和 [uv](https://docs.astral.sh/uv/)。在 MCP 配置里加入：

```json
{
    "mcpServers": {
        "gl-mcp-feedback": {
            "command": "uvx",
            "args": ["gl-mcp-feedback@latest"],
            "timeout": 600,
            "autoApprove": ["interactive_feedback"]
        }
    }
}
```

从源码运行：

```json
{
    "mcpServers": {
        "gl-mcp-feedback": {
            "command": "uv",
            "args": ["run", "--directory", "/path/to/gl-mcp-feedback", "gl-mcp-feedback"],
            "timeout": 600
        }
    }
}
```

### 环境变量

在 MCP 配置的 `env` 里设置。

| 变量 | 用途 | 默认 |
|------|------|------|
| `MCP_WEB_HOST` | Web UI 绑定地址 | `127.0.0.1` |
| `MCP_WEB_PORT` | Web UI 端口，范围 `1024-65535`，`0` 表示系统自动分配 | `8765` |
| `MCP_DEBUG` | 调试日志（输出到 stderr），`true` / `false` | `false` |

`MCP_WEB_HOST` 为 `127.0.0.1` 时只能本机访问；SSH 远程开发时可设为 `0.0.0.0`，再用 `http://[远程主机 IP]:8765` 访问。也可以保持默认，改用 SSH 端口转发把远程的 8765 转到本机。详见 [SSH Remote 环境使用指南](docs/zh-CN/ssh-remote/browser-launch-issues.md)。

### Cursor 钩子

仓库里的 `hooks/gl-feedback.sh` 是一个 Cursor 钩子脚本，管三件事：

- 给每次 `interactive_feedback` 填上 Cursor 的对话 ID，让每个对话用自己的页面
- AI 调用完任意一次工具时，把用户排队的消息取走，附在那次工具结果后面交给 AI；消息带图片时，图片存成本地文件，路径写在文字里，AI 用 Read 查看；对话的房间销毁时这些图片一并删除，没被删掉的（比如服务异常退出）24 小时后清理
- 对话正常结束时（`stop`），通知服务销毁这个对话的房间，页面随之关闭

没装钩子，所有对话共用一个页面，排队的消息要等 AI 下次调用 `interactive_feedback` 才送达。脚本需要 bash、jq 和 curl，在用户级的 `~/.cursor/hooks.json` 里登记（`/path/to/gl-mcp-feedback` 换成仓库的位置）：

```json
{
    "version": 1,
    "hooks": {
        "preToolUse": [
            {
                "command": "/path/to/gl-mcp-feedback/hooks/gl-feedback.sh pre",
                "matcher": ".*interactive_feedback.*",
                "timeout": 10
            }
        ],
        "postToolUse": [
            {
                "command": "/path/to/gl-mcp-feedback/hooks/gl-feedback.sh post",
                "timeout": 10
            }
        ],
        "postToolUseFailure": [
            {
                "command": "/path/to/gl-mcp-feedback/hooks/gl-feedback.sh post",
                "timeout": 10
            }
        ],
        "sessionStart": [
            {
                "command": "/path/to/gl-mcp-feedback/hooks/gl-feedback.sh log",
                "timeout": 10
            }
        ],
        "stop": [
            {
                "command": "/path/to/gl-mcp-feedback/hooks/gl-feedback.sh stop",
                "timeout": 10
            }
        ]
    }
}
```

`pre` 给所有项目的对话填对话 ID。Cursor 给 `preToolUse` 的输入里没有 MCP 服务器名，`matcher` 只能按工具名匹配，别的 MCP 服务器如果也有叫 `interactive_feedback` 的工具，它的调用同样会被改写。`log` 把会话事件的几个字段记进 `~/.cache/gl-mcp-feedback/hook-events.log`。

`stop` 在 AI 的每个回合结束时触发：先记一条事件，再把回合的结束状态报告给这个对话的房间。状态是 `completed`（正常结束）时，房间 3 秒后销毁（它送达过的图片文件一并删除），页面收到「对话已结束」并关闭；这 3 秒里对话再提问，房间就留下。房间里还有排队中的消息时也留下，等对话下次提问或调用工具时送达。状态是 `aborted`（被中断）或 `error`（出错）时，房间只标成「已结束」，不销毁。不管有没有收到 `stop`，闲置超过 2 小时的房间都会销毁。

标记文件、登记文件、图片和事件日志都放在 `GL_FEEDBACK_HOME`（默认 `~/.cache/gl-mcp-feedback`），服务端和钩子读同一个变量，一般不用改。

## 推荐的 AI 规则

为了让 AI 稳定地走「先确认 → 再动手 → 最后回收反馈」的流程，建议把下面这段放进 AI 助手的规则（Cursor 的 User Rules / Project Rules、Cline 的 Custom Instructions 等）：

```
gl-mcp-feedback 交互规范：

1. 代码修改完成后，必须调用 interactive_feedback，在 summary 中说明已完成的修改，等待用户测试反馈。
2. 用户提出新需求时，先调用 interactive_feedback 阐述理解和实现思路，等用户确认后再写代码。
3. 工具调用被中断/取消时，立即调用 interactive_feedback 询问用户。
4. MCP 调用超时时，立即重新调用等待反馈（timeout 建议 600 秒以上）。
5. summary 参数使用 Markdown 格式，按需使用标题、列表、文件路径等结构化信息。
6. summary 用 Markdown 时尽量保持简洁：减少代码块嵌套、长串双引号、复杂转义，能用纯文本叙述就用纯文本，能用列表就不要堆段落。代码块仅在贴具体代码/命令时使用。
```

第 1、2 条是核心：改代码前先确认、改代码后回收反馈。第 5、6 条与渲染有关：summary 会在 Web UI 里以 Markdown 展示，写得简洁可读性最好。

## 常见问题

**页面没有收到新的反馈**：多半是 WebSocket 断了，刷新浏览器页面即可。

**排队的消息要等 AI 下次提问才送达**：钩子没生效。确认 `~/.cursor/hooks.json` 里登记了 `post`，脚本有可执行权限（`chmod +x`），jq 和 curl 能找到。

**AI 等后台命令的时候发的消息，要等它等完才读到**：钩子只在工具调用结束时送达。AI 用 AwaitShell 等后台命令期间，Cursor 每秒会触发几次内部轮询，这些触发送出去的内容会被丢弃，所以钩子跳过它们，消息在等待结束后的第一次工具调用结束时送达。

**对话结束了，页面没有自己关掉**：确认 `~/.cursor/hooks.json` 里 `stop` 登记的命令是 `... gl-feedback.sh stop`（不是 `log`）。房间销毁后页面会显示「对话已结束」，并在 3 秒后尝试关闭自己；浏览器只允许脚本关闭自己打开的标签页，从房间列表点进去的页面可能关不掉，手动关闭即可。

**AI 没有调用出 MCP**：确认 MCP 工具状态为绿灯，反复开关一次 MCP，等几秒让它重新连接。

**出现 `Unexpected token 'D'` 之类的解析错误**：调试输出干扰了通信，设置 `MCP_DEBUG=false` 或去掉该变量。

**Web UI 打不开**：检查防火墙，或用 `MCP_WEB_PORT` 换一个端口。

## 开发

```bash
git clone https://github.com/youjunxiaji/gl-mcp-feedback.git
cd gl-mcp-feedback
uv sync

uv run gl-mcp-feedback test --web   # 启动一个测试用的 Web UI
uv run gl-mcp-feedback version      # 查看版本

uv run pytest                       # 测试
uv run ruff check .                 # lint
uv run pyright                      # 类型检查
```

## 致谢

- [noopstudios/interactive-feedback-mcp](https://github.com/noopstudios/interactive-feedback-mcp)（Fábio Ferreira）：原始项目
- [Minidoracat/mcp-feedback-enhanced](https://github.com/Minidoracat/mcp-feedback-enhanced)：本项目直接基于的增强版本
- [sanshao85/mcp-feedback-collector](https://github.com/sanshao85/mcp-feedback-collector)：UI 设计灵感
- [leo108](https://github.com/leo108)：SSH 远程开发支持（`MCP_WEB_HOST`）
- [fireinice](https://github.com/fireinice)：工具文档优化（LLM 指令移至 docstring）

## 授权

MIT，详见 [LICENSE](LICENSE)。
