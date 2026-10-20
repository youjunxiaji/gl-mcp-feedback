# 待办

只列没做的事，做完就删。

## 对话标识放开到所有项目与对话结束清理

每个对话有自己的房间（会话、排队消息、页面），排队消息在 AI 每次工具调用结束时由钩子送达。剩下的事：钩子只给本仓库的项目填对话标识；对话结束后房间不清；钩子送达的 `postToolUseFailure` 还没验证。

### 症状

- 钩子只给本项目（`GL_INJECT_ROOT`）的对话填对话标识，其他项目的对话仍进共用的 `default` 房间，没有标记文件，排队消息只能随提问送达
- 对话结束后，房间和会话记录不清，网页端口也不关

### 原因

- 端口只在 8765 被别的进程占用时才会换；同一个进程里永远复用 8765
- 一台机器上不止一个 MCP 进程：Cursor 主程序一个，每个 `cursor-agent worker` 另带一个，各自一个 `WebUIManager`、各自抢端口。旧代码的进程不认 `conversation_id`，会拒绝带这个参数的调用，所以放开之前要先把它们结束
- `WebUIManager.stop()` 只清会话、不停服务器：`uvicorn.Server` 实例是 `run_server_with_retry()` 里的局部变量，没存下来，没法让它退出
- `WebUIManager.rooms` 只增不减：没有任何地方回收房间
- MCP 协议里没有「对话结束」的通知

### 目标

所有项目的对话都有自己的房间和钩子送达；对话结束时清掉它的房间；MCP 进程不退出。

### Cursor 钩子的能力

全局钩子（`~/.cursor/hooks.json`）所有项目的对话都会经过。

- 每个钩子的输入都带 `conversation_id`，`session_id` 与它相同
- `preToolUse` 返回 `updated_input` 能改写 MCP 工具的入参，实际执行的是改写后的值
- `postToolUse` 返回 `additional_context`，会作为 `<system_reminder>` 出现在该工具的结果之后；`postToolUseFailure` 文档里同样支持，未测
- `stop` 在每次 agent 运行（一个回合）结束时触发，输入带 `status`（`completed` / `aborted` / `error`），不是对话结束；用户说结束、AI 收回合时是 `completed`
- `sessionEnd` 按官方论坛的说法在关闭或删除对话标签页、关窗口时触发，新开对话不触发，关窗口时命令起不来是已知 bug；实测里新开对话、对话结束都没有记录到过 `sessionEnd`
- `hooks/gl-feedback.sh log` 把 `sessionStart`、`sessionEnd`、`stop` 记在 `~/.cache/gl-mcp-feedback/hook-events.log`

### 步骤

每做完一步，就把它从本文件删掉，并更新 `CLAUDE.md` 里对应的描述。

1. 对话标识放开到所有项目：用顶栏的对话 ID 和钩子日志里的 `conversation_id` 核对；核对无误后去掉 `hooks/gl-feedback.sh` 里 `pre` 对 `GL_INJECT_ROOT` 的限制。放开之前，旧代码的 MCP 进程（`cursor-agent worker` 带的几组）要一起重启或结束
2. 钩子送达还有一处没验证：`postToolUseFailure` 的 `additional_context`
3. `stop` 钩子：回合结束时队列里仍有消息，用 `followup_message` 自动提交为下一条用户消息，`loop_limit` 按需调大。钩子送达用起来之后再决定做不做
4. 对话结束清理：`stop` 钩子通知服务端。`status` 为 `completed` 的房间 3 秒后销毁，3 秒内这个对话再次提问就撤销；`aborted` / `error` 只把房间标成「已结束」（房间列表显示「已结束」，不再是「AI 处理中」），不销毁；标了「已结束」的、以及从没收到过 `stop` 的房间，闲置一段时间后销毁（时长待定）。销毁时释放会话、排队消息、图片文件和标记文件，打开着的页面显示「对话已结束」并停止轮询。`stop` 钩子靠房间创建时写下的登记文件（地址 + 进程号）找到服务，沿用标记文件的 `curl` 方式。待确认：`stop` 时还有排队中的消息，是先留着等下次提问送达（推荐，不丢消息），还是照样销毁
5. 房间都清完之后，要不要闲置一段时间再关网页端口，待定：要关的话得保存 `uvicorn.Server` 实例才能让它退出，下次调用再启动
