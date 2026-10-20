# gl-mcp-feedback

交互式反馈 MCP 服务器。AI 调用 `interactive_feedback` 把工作摘要发到浏览器页面，用户在页面里回复文字和图片，回复再回到 AI，把多次试错合并成一次有效沟通。

适用于支持 MCP 的 AI 编辑器，如 [Cursor](https://www.cursor.com)、[Cline](https://cline.bot)、[Windsurf](https://windsurf.com)。

## 功能

- **微信式对话流**：AI 的摘要（Markdown 渲染，代码块带复制按钮）靠左，用户回复靠右，历史消息可回看。
- **文字与图片回复**：图片可选文件或 `Ctrl/⌘+V` 粘贴，`Ctrl/⌘+Enter` 发送。
- **消息排队**：AI 干活期间用户也能继续发消息，最多排 3 条，排队中可以编辑、撤回；AI 下次调用 `interactive_feedback` 时一并带回，不会因为 AI 再次提问而丢失。
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
