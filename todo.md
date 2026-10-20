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
- `WebUIManager.create_session()` 是「单一活跃会话」：新会话成为 `current_session`，旧会话被强制完成，页面的 WebSocket 转给新会话；`/ws` 收到的消息一律交给 `get_current_session()`。旧会话的等待事件没有置位，旧调用一直阻塞到超时
- 端口只在 8765 被别的进程占用时才会换；同一个进程里永远复用 8765
- 一台机器上不止一个 MCP 进程：Cursor 主程序一个，每个 `cursor-agent worker` 另带一个，各自一个 `WebUIManager`、各自抢端口。钩子要找的服务端不止一个，不能写死 8765
- `WebUIManager.stop()` 只清会话、不停服务器：`uvicorn.Server` 实例是 `run_server_with_retry()` 里的局部变量，没存下来，没法让它退出
- MCP 工具调用里没有对话标识（只有 `_meta.progressToken`），也没有「对话结束」的通知

### 目标

每个对话一个「房间」：自己的会话和排队消息，页面按房间分开展示。对话结束时清掉它的房间；MCP 进程不退出。

### Cursor 钩子的能力

全局钩子（`~/.cursor/hooks.json`）所有项目的对话都会经过。

- 每个钩子的输入都带 `conversation_id`，`session_id` 与它相同
- `preToolUse` 返回 `updated_input` 能改写 MCP 工具的入参（`tool_name` 是 `MCP:interactive_feedback`，入参是对象），实际执行的是改写后的值
- `postToolUse` 返回 `additional_context`，会作为 `<system_reminder>` 出现在该工具的结果之后（Shell、Read 已确认）；`postToolUseFailure` 文档里同样支持，未测
- `stop` 在每次 agent 运行（一个回合）结束时触发，不是对话结束；`sessionEnd` 对应对话结束，触发时机待验证

### 步骤

分四段，按顺序做。每做完一段，就把这段从本文件删掉，并更新 `CLAUDE.md` 里对应的描述。

#### 第一段：对话标识

1. 服务端已经接受可选参数 `conversation_id`，页面顶栏显示前 8 位；`~/.cursor/hooks/gl-probe.sh` 的 `pre` 已经往 `interactive_feedback` 的入参里填它（`updated_input`），目前只对本项目（脚本里的 `INJECT_ROOT`）的对话生效。还差：用顶栏的对话 ID 和日志里的 `conversation_id` 核对；核对无误后去掉 `INJECT_ROOT` 的限制，放开到所有项目。放开之前，旧代码的 MCP 进程（`cursor-agent worker` 带的几组）要一起重启或结束：它们不认这个参数，会拒绝调用
2. 把探针换成正式的钩子：现在装的是带日志的探针（`~/.cursor/hooks.json` + `~/.cursor/hooks/gl-probe.sh`，日志在 `~/.cursor/hooks/gl-probe.log`），`stop` / `sessionEnd` 的触发时机靠它被动记录；看清之后整理成正式的钩子脚本，探针和日志删掉

#### 第二段：房间

解决症状 1、2、3。页面按路径分房间：一个端口（8765），每个对话一个地址 `/c/<conversation_id>/`。

3. 服务端按对话分房间：房间持有自己的会话、`MessageQueue` 和页面连接，取代 `get_web_ui_manager()` 里单一的会话和队列；`create_session()` 的「单一活跃会话」限定在房间内，对话 B 的调用不再替换对话 A 的会话，排队消息也不会被别的对话取走。没有 `conversation_id` 的调用进共用的默认房间（`default`）
4. 路由和页面：`/c/{room_id}/`、`/c/{room_id}/api/*`、`/c/{room_id}/ws`，前端的 `/ws`、`/api/*` 加上房间路径前缀。`/` 是房间列表，没有房间时显示等待；房间还不存在时 `/c/{room_id}/` 显示等待页，会话到达后自动进入。对话第一次提问时为它新开一个标签页，房间页面已经开着就只刷新；标签页标题由页面生成：项目目录名加对话 ID 前 4 位
5. 做完更新 `CLAUDE.md`：「消息队列」里「对所有项目共用」「由 `WebUIManager.message_queue` 持有」等描述、`/api/*` 的路径、页面结构

#### 第三段：钩子送达排队消息

解决排队消息要等 AI 下次提问才送达的问题。

6. 钩子送达：AI 每次工具调用结束就能收到排队消息
    - `postToolUse` 和 `postToolUseFailure` 钩子：该对话的队列有消息就向服务端原子取走，用 `additional_context` 交给 AI，文字沿用 `MessageQueue.build_result()` 的说明加逐条列表。这个钩子对所有项目、所有工具调用都会触发，队列为空时要几毫秒返回：热路径只判断标记文件是否存在
    - 钩子找服务端：服务端处理某个对话的调用时，把这个 `conversation_id` 对应的地址写进本地状态文件，钩子读出后用 `curl` 调房间的取走接口（`/c/{room_id}/api/...`），不能写死 8765
    - `stop` 钩子：回合结束时队列里仍有消息，用 `followup_message` 自动提交为下一条用户消息；`loop_limit` 按需调大
    - `interactive_feedback` 的送达保留，和钩子共用 `MessageQueue` 的原子取走，同一条消息不会送达两次
    - 带图片的消息：`additional_context` 只能放文字，图片先落成本地临时文件，路径写进文字，AI 用 Read 读
    - 已送达记录：钩子送达没有 `delivered_session_id`，而 `visible_messages()` 现在只保留随记录里的会话送达的消息，钩子送达的要另定保留规则，对话流按 `delivered_at` 排位
    - 界面：「排队中 · AI 下次提问时送达」改成「下次调用工具或提问时送达」，钩子取走的那一刻页面把「排队中」翻成「✓ 已送达 AI」；已送达记录区分「随工具调用送达」和「随提问送达」
    - 待验证：模型会不会按注入的排队消息改变行动，用真实排队消息测；`postToolUseFailure` 和 `stop` 的 `followup_message` 未测
    - 做完更新 `CLAUDE.md`「消息队列」，以及 `server.py` 里 `_CHAT_PROMPT` 和 `interactive_feedback` docstring 中收到排队消息的处理说明

#### 第四段：对话结束清理

解决症状 4。

7. 以空闲超时为主：房间没有等待中的提问、没有排队消息、页面已断开，闲置一段时间就清掉；`sessionEnd` 钩子能用的话提前清，钩子通知服务端沿用第三段的状态文件和 `curl`。超时时长待定
8. 房间都清完之后，要不要闲置一段时间再关网页端口，待定：要关的话得保存 `uvicorn.Server` 实例才能让它退出，下次调用再启动
9. 做完更新 `CLAUDE.md`
