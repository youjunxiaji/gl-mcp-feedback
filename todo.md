# 待办

只列没做的事，做完就删。

## 会话乱入与对话结束清理

两个症状同一个根：Cursor 只起一个 MCP 进程，所有对话共用，服务端分不出调用来自哪个对话。

### 症状

- 新开对话不会用 8766，而是闯进 8765 的页面：新对话的提问出现在旧对话的页面里
- 旧对话还在等回复时，新对话一调用，旧会话就被标成「被新会话替代，自动完成」，用户在页面上的回复交给最新那个会话，旧对话收不到
- 排队消息所有对话共用，A 排的消息可能被 B 的下一次调用取走
- 对话结束后，会话记录不清，网页端口也不关

### 原因

- `web/main.py` 的 `get_web_ui_manager()` 是进程级单例：一个 `WebUIManager`、一个端口（8765）、一个 `MessageQueue`
- `WebUIManager.create_session()` 是「单一活跃会话」：新会话成为 `current_session`，旧会话被强制完成
- 端口只在 8765 被别的进程占用时才会换；同一个进程里永远复用 8765
- MCP 工具调用里没有对话标识（只有 `_meta.progressToken`）

### 目标

每个对话一个「房间」：自己的端口（8765、8766……）、会话和排队消息。对话结束时关掉它的端口、清掉它的记录；MCP 进程不退出。

### 步骤

1. 先装一个只写日志的 Cursor 钩子（`~/.cursor/hooks.json`，全局生效，所有项目的对话都会经过），验证三件事：
   - 钩子拿到的字段里有没有稳定的 `conversation_id`
   - `preToolUse` 的 `updated_input` 对 MCP 工具参数能不能生效
   - 对话结束时 `stop` / `sessionEnd` 触不触发，带哪些字段
2. `interactive_feedback` 加一个可选参数 `conversation_id`，由钩子填，AI 不填。工具 schema 现在是 `additionalProperties: false`，`server.py` 里 docstring 的「Allowed parameters」同步改
3. 服务端按对话分房间：每个对话一个 `WebUIManager`（自己的端口、会话、`MessageQueue`），取代 `get_web_ui_manager()` 的单例；`create_session()` 的「单一活跃会话」限定在房间内
4. 新房间的端口从 8765 往上取空闲端口，不走 `PortManager.find_free_port_enhanced(auto_cleanup=True)`：它按进程命令行里的 `mcp-feedback-enhanced` / `mcp_feedback_enhanced` 判断能不能杀占用端口的进程，本项目经 `gl-mcp-feedback` 脚本启动，命令行不含这两个词，所以现在不会误杀；这段匹配逻辑不要改
5. 对话结束（`stop` / `sessionEnd` 钩子通知服务端）：关掉该对话的端口，清掉它的会话和排队消息
6. 备选：钩子改不了 MCP 参数时，改成首次调用返回一个 key，要求 AI 之后每次调用都带上；依赖 AI 记得，上下文被压缩后可能丢
7. 做完更新 `CLAUDE.md`「消息队列」里「对所有项目共用」「由 `WebUIManager.message_queue` 持有」等描述，以及 `/api/all-sessions` 的说明
