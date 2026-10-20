# gl-mcp-feedback

## 项目概述

本项目是一个 MCP (Model Context Protocol) 交互式反馈收集工具，提供 Web UI 供用户与 AI 代理交互。前端采用 Tailwind CSS + 内联 JS（无框架、无模块化 CSS/JS），后端使用 Python (FastAPI + Jinja2)。

## 关键文件路径

### HTML 模板（后端 Jinja2）
- `src/mcp_feedback_enhanced/web/templates/feedback.html` — 主反馈页面（Tailwind CSS + 内联 JS，自包含）
- `src/mcp_feedback_enhanced/web/templates/index.html` — 房间列表页（每个对话一个入口，没有对话时显示等待提示，Tailwind CSS + 内联 JS）

### 静态资源
- `src/mcp_feedback_enhanced/web/static/js/vendor/tailwindcss.js` — Tailwind CSS JIT 编译器（本地）
- `src/mcp_feedback_enhanced/web/static/js/vendor/marked.min.js` — Markdown 渲染库
- `src/mcp_feedback_enhanced/web/static/icon.svg` — Favicon
- `src/mcp_feedback_enhanced/web/static/icon-192.png` — 桌面通知图标
- `src/mcp_feedback_enhanced/web/static/fonts/` — 本地字体（DM Sans、JetBrains Mono，woff2）

### Python 后端
- `src/mcp_feedback_enhanced/server.py` — MCP 服务器主入口
- `src/mcp_feedback_enhanced/web/main.py` — Web 应用主入口（`WebUIManager`：服务、房间表、打开浏览器、清理）
- `src/mcp_feedback_enhanced/web/routes/main_routes.py` — 路由定义
- `src/mcp_feedback_enhanced/web/models/room.py` — 对话房间（`Room`：一个对话自己的会话、排队消息和页面连接）
- `src/mcp_feedback_enhanced/web/models/feedback_session.py` — 反馈会话模型
- `src/mcp_feedback_enhanced/web/models/feedback_result.py` — 反馈结果模型
- `src/mcp_feedback_enhanced/web/models/message_queue.py` — 用户消息队列（`MessageQueue`，AI 没在等回复时用户发的消息先排队，两种送达途径）
- `src/mcp_feedback_enhanced/web/models/queue_files.py` — 钩子用的本地文件（`QueueMarker` 标记文件、`RoomRegistration` 登记文件、图片文件）
- `src/mcp_feedback_enhanced/web/utils/` — 工具函数（浏览器、网络、端口、压缩配置）
- `src/mcp_feedback_enhanced/hook_installer.py` — `gl-mcp-feedback hooks install / uninstall`（`HookInstaller`：复制钩子脚本、试跑、合并登记进 `~/.cursor/hooks.json`），见「Cursor 钩子」

### 其他
- `hooks/gl-feedback.py` — Cursor 钩子脚本（Python，只用标准库，由 `uv run --script` 运行；`pre` 填对话标识，`post` 送达排队消息，`stop` 报告对话回合结束，`log` 记录会话事件），见「Cursor 钩子」
- `todo.md` — 待办（只列没做的事，做完就删）
- `release.sh` + `.github/workflows/publish.yml` — 发布到 PyPI：脚本改版本号（`pyproject.toml`、`__init__.py`）、`uv lock`、提交、打 `v*` tag 并推送，工作流由 `v*` tag 触发

## 前端架构

### 设计原则
- **自包含**：每个 HTML 模板包含自己的所有样式和脚本，不依赖外部 CSS/JS 模块文件
- **Tailwind CSS**：使用本地 JIT 编译脚本，通过 `tailwind.config` 在 `<script>` 中配置
- **内联 JS**：WebSocket 连接、对话流渲染、表单提交、Tab 切换、图片上传等功能均写在模板内的 `<script>` 标签中
- **Markdown 渲染**：使用 `marked.min.js` 库，样式通过 `.md` CSS 类在 `<style>` 中定义
- **本地字体**：`static/fonts/` 下的 woff2，在模板 `<style>` 里用 `@font-face` 加载 DM Sans 和 JetBrains Mono

### Tailwind 配置
两个模板共享相同的 Tailwind 配置，定义在各自的 `<script>` 标签中：
- 深色模式：`darkMode: 'class'`
- 自定义颜色：`surface.base`、`surface.raised`、`surface.overlay`、`surface.muted`
- 自定义字体：`sans`（DM Sans）、`mono`（JetBrains Mono）

### feedback.html 功能
- **顶栏**：Logo 加名称（链接到 `/`，即所有对话的总览，`#homeLink`）、项目路径、对话 ID（最新一轮带了 `conversation_id` 才显示，取前 8 位，悬停看完整值）、连接状态
- **房间前缀与标签页标题**：页面地址是 `/c/{room_id}/`，服务端把前缀以 `room_base` 传给模板，写在 `<body>` 的 `data-room-base` 上，脚本里的 `ROOM_BASE` 从它读，WebSocket、`/api/all-sessions`、`/api/queue/{id}` 都拼在它后面；`<title>` 由服务端按房间生成（`Room.page_title()`）：项目目录名，对话的房间再加对话 ID 前 4 位，没有会话时是 `gl-mcp-feedback`
- **Tab 导航**：工作区、关于
- **工作区**：微信聊天式的对话流 + 底部固定输入框。AI 消息靠左、用户回复靠右，每条气泡最宽占 2/3（窄屏 90%，气泡本身 `max-w-full`，代码块的长行在块内横向滚动，不会把气泡撑宽），带头像、小尾巴，每条气泡下方一行浅灰小字显示时间（`createMessageMeta()`；当天只显示 `HH:MM:SS`，昨天加「昨天」，更早显示月日，跨年再加年份，格式见 `formatChatTime()`）
    - 对话记录由 `loadHistory()` 从 `ROOM_BASE + '/api/all-sessions'` 拉取，`buildTimeline()` 把 AI 消息（按会话 `created_at`）、用户对提问的回复（按 `feedback_submitted_at`）、用户排队发送的消息合成一条时间线：随提问送达的排队消息（`delivered_via` 为 `question`）是那次 AI 调用带回来的结果，按发送顺序紧跟在 `delivered_session_id` 对应的 AI 消息（及其下方说明）之后，位置按那条 AI 消息的 `created_at` 算（对应会话已不在记录里时按 `delivered_at` 兜底）；排队中的消息和随工具调用送达的消息（`tool_call`）按 `submitted_at` 排位，所以被钩子取走的那一刻位置不变；其余按时间升序（同一时刻 AI 消息排前）；时间线条目的 `at` 是排序位置，`sentAt` 是排队消息的真实发送时间，气泡下方显示的时间用它。`renderChat()` 整体重绘，只给新出现的消息加入场动画（`prefers-reduced-motion` 下关闭）
    - AI 气泡：Markdown 渲染（`.md` 样式 + 代码块复制按钮：`enhanceCodeBlocks()` 给每个 `<pre>` 包一层 `.code-block`，按钮挂在这层上，代码横向滚动时按钮不跟着跑）；AI 的调用仍在等回复（`round.is_current && round.awaiting_feedback`）的最新一条在气泡首行带 `NEW` 标记（`data-awaiting`）
    - 用户气泡：纯文本（`textContent`）+ 图片缩略图；没有收到回复的 AI 消息下方显示一行灰字说明：随这次调用送达了排队消息（「已随这次调用送达你排队的 N 条消息」，说明下面紧跟这 N 条消息）、超时、过期、出错、这次提问已结束、被新提问替代
    - 排队消息气泡（`role: 'queued'`）：用户气泡下方那一行，除了真实发送时间，还带状态（`appendQueuedStatus()`）——排队中（琥珀色「排队中 · AI 下次调用工具或提问时送达」+「编辑」「撤回」）或灰色的「✓ 已随hook送达 AI」（钩子取走）/「✓ 已随提问送达 AI」。「编辑」就地展开文字编辑框（`startQueuedEdit()`，Ctrl/⌘+Enter 保存、Esc 取消，只改文字、图片不变），保存走 `PUT {ROOM_BASE}/api/queue/{id}`；「撤回」走 `DELETE {ROOM_BASE}/api/queue/{id}`；编辑期间其他消息不显示操作按钮，重绘时保持编辑框内容、焦点和光标。保存或撤回遇到 409（已被 AI 取走）时提示用户，编辑中的草稿放回主输入框；编辑中的消息被取走时（`releaseDeliveredEditor()`）同样放回
    - 本地先行显示：发出排队消息后立刻插入 `local: true` 的临时气泡（key 为 `local-N`，没有编辑/撤回），收到 `queue.messageQueued`（带 `message_id`）后去掉本地副本，`queuedKeyAlias` 让气泡的 key 沿用本地的，不重复播放入场动画；`loadHistory()` 重新拉取时保留尚未确认的本地气泡
    - 滚动：首次加载与新一轮到达时，若最新 AI 消息一屏放不下就对齐到它的开头，否则停在底部；自己发送后立刻滚到底部（不做动画，免得被发送后马上到来的服务端确认重绘打断）；重绘前视图离底部不到 80px（`NEAR_BOTTOM_PX`）的话，重绘后继续贴着底部，服务端确认、状态行、图片让内容变高时最新的消息不会落到输入框后面；切到其他标签页再切回会恢复滚动位置
    - 输入框（`applyComposerState()`）：只有连接被其他窗口接管、或对话已结束（房间被销毁）时才禁用（`getComposerLock()`，`COMPOSER_LOCKS`）。附件图片和发送按钮高度一致（都是 `h-[34px]`）。AI 在等回复时按钮为「发送」、提示快捷键；AI 没在等时按钮为「排队发送」、提示「AI 正在处理，消息会排队（n/3），AI 下次调用工具或提问时一并读取」；排队满额（`isQueueFull()`，上限取自 `/api/all-sessions` 的 `queue_limit`）时按钮禁用并显示「队列已满」。拉取记录之前保持常规状态。临时提示（`setComposerNotice()`：队列已满、消息已被取走、编辑内容为空等）用琥珀色显示 6 秒并同步给读屏；「队列已满」的提示在队列不再满时（撤回、被取走）立即清除
- **反馈提交**：WebSocket 发送 `submit_feedback` 消息，支持 Ctrl/⌘+Enter 快捷键。前端按 `isAwaitingReply()` 决定本地显示成回复还是排队气泡，实际分流以服务端为准（见「消息队列」）。每条消息服务端都回应一条 `notification`，前端按发送顺序对账（`inflightSends`，回复记空串、排队消息记本地气泡的 key，`settleSend()` 逐条销账），再重新拉取记录校正：`code` 为 `session.feedbackSubmitted`（作为回复）、`queue.messageQueued`（进了队列）、`queue.full`（队列已满）或 `queue.messageEmpty`（没有文字且图片都被过滤掉，例如过大），后两种都是被拒绝：撤掉本地气泡，发送的文字和图片放回输入框，不丢。WebSocket 断线重连后（`ws.onopen`），没收到回应的本地气泡一律撤掉（`dropUnconfirmedSends()`），以重新拉取的服务端记录为准
- **新反馈提醒**：收到 WebSocket `session_updated` 时调用 `notifyNewFeedback()`，用 Web Audio 合成「叮咚」提示音（`playAlertSound`）+ 桌面通知（`showDesktopNotification`，仅 `document.hidden` 时弹、点击聚焦窗口），同时重新拉取对话记录并滚到新消息。`silent: true` 的 `session_updated`（AI 的新一轮调用直接取走了排队消息，没有要用户回复的提问）不响不弹，只重新拉取并读屏提示「AI 已读取你排队的消息」；收到 `queue_delivered`（AI 调用完一次工具，钩子取走了排队的消息）同样不响不弹，重新拉取记录，把「排队中」翻成「已送达」。默认始终开启、无设置开关；首次用户手势由 `setupAlertOnFirstGesture()` 解锁音频播放并请求 `Notification` 授权
- **图片上传**：支持文件选择和 Ctrl+V 粘贴，base64 编码随反馈一起发送；客户端同时生成长边 ≤ 320px 的 JPEG 缩略图（`thumbnail`，data URL）随每张图片一起上传，服务端单独保存，仅用于对话记录展示

### 消息队列
AI 没在等回复时（正在干活，或上一次调用已经结束），用户发的消息先排队，有两条路送到 AI 手里，谁先到谁取走所有排队中的消息，同一条消息不会送达两次：AI 调用完任意一次工具时由 Cursor 钩子来取（随工具调用送达），或者 AI 下次调用 `interactive_feedback` 时一并返回（随提问送达）。不会因为 AI 再次提问而丢失。队列按房间分，每个对话一个，只被自己的调用取走。
- **分流规则**：WebSocket 收到 `submit_feedback` 时，`session.is_awaiting_feedback()`（状态为 `WAITING` 且 `feedback_completed` 未置位）为真，消息作为对当前提问的回复（`session.submit_feedback`）；否则交给 `Room.enqueue_user_message()` 进房间的队列。`/api/all-sessions` 的每个会话带 `awaiting_feedback`，前端用同一个口径决定输入框状态
- **`MessageQueue`**：只放内存，服务重启即清空；线程安全（Web 线程写入、修改、撤回，随提问送达在 MCP 线程取走，钩子的请求在 Web 线程里取走）。排队中的消息最多 3 条（`MAX_PENDING_MESSAGES`，已送达的记录不占名额），满额抛 `QueueFullError`，前端收到 `queue.full` 通知。排队消息可以改文字（`update_text`，纯文字消息不能改成空）和撤回（`cancel`），已被取走的抛 `MessageNotPendingError`。取走有两个方法：`deliver_with_question(session_id)`（随提问）和 `deliver_with_tool_call()`（随工具调用），各自在锁里一次取走全部排队中的消息并标上 `DeliveryChannel`。排队中的消息数可能变了（加入、撤回、取走）之后调用 `on_pending_changed`，在锁之外执行、出错只记日志，房间用它同步标记文件。队列由房间持有（`Room.message_queue`），每个对话一个；没有对话标识的调用共用 `default` 房间的队列，不区分项目目录
- **随提问送达（AI 再次调用时）**：`launch_web_feedback_ui()` 在 `Room.create_session()` 之后、启动服务和打开浏览器之前调用 `Room.deliver_queued_messages()`。房间的队列非空就取走所有排队消息，新会话 `close_with_queued_messages()`（状态 `WAITING` → `ACTIVE`、等待事件置位），已打开的页面收到 `silent: true` 的 `session_updated` 静默刷新，调用立刻返回，不等用户；队列为空则照常等用户回复
- **随工具调用送达（钩子）**：`hooks/gl-feedback.py post` 挂在所有工具的 `postToolUse` 和 `postToolUseFailure` 上（工具调用失败时 `additional_context` 同样会作为 `<system_reminder>` 附在失败结果后面，已实测），每次工具调用结束都会触发，所以热路径只做一件事：从输入开头取 `conversation_id`，看标记文件 `$GL_FEEDBACK_HOME/queue/<对话标识>` 在不在，不在就直接返回（Cursor 的钩子日志里实测 `uv run` 加脚本 31 到 71 毫秒，大头是 uv 和 Python 的启动；真要联网时才导入 `http.client`）。
    - 标记文件（`QueueMarker`，`queue_files.py`）：房间的队列里有排队中的消息时存在，没有时不存在；两行内容，房间的地址（`WebUIManager.get_hook_room_url()`，监听 `0.0.0.0` 时用 `127.0.0.1`）和写它的服务进程号。写入是原子的（临时文件加 `replace`），`sync()` 在自己的锁里重新读排队数量，几个线程同时同步时文件最终和队列一致；写入失败只记日志，不影响队列。`default` 房间没有对话标识，钩子找不到它，不写标记；房间销毁或服务停止时（`Room.release_files()`）删掉标记。钩子发现进程号对应的进程已经不在，就把这个过期的标记删掉
    - 取走接口：钩子用 `http.client` 直连标记里的地址加 `/api/queue/take`（只认 `http` 地址，不走代理，连接超时 1 秒、总超时 5 秒）。接口调 `Room.deliver_for_tool_call()` 取走全部排队消息，有消息返回 `{"additional_context": "..."}`，没有返回 `{}`；钩子只在收到 HTTP 2xx 且内容是 JSON 对象时把它交给 Cursor，Cursor 把 `additional_context` 附在那次工具结果后面给 AI，其余情况输出 `{}`
    - 跳过内部轮询：AwaitShell 等后台任务（带 `shell_id`）时，Cursor 每秒几次对终端输出文件（`.../terminals/<编号>.txt`）发起内部 `Read`，每次都触发 `postToolUse`（同一个 `tool_use_id`），这些事件返回的 `additional_context` 会被丢弃，这时取走消息就丢了。钩子认出工具是 `Read`、读的是 `/terminals/*.txt` 的事件就不取（`is_terminal_poll()`，只看输入开头 4096 个字符），消息留给下一次真正的工具调用；不带 `shell_id` 的 AwaitShell 是纯等待，不产生钩子事件，等待期间发的消息在之后第一次工具调用结束时送达。这是按 Cursor 3.25.9 实测的行为做的判断，没有官方说明
    - 图片：钩子送达只能传文字，图片先存成 `$GL_FEEDBACK_HOME/images/<消息标识>/<序号>.<扩展名>`（按文件头判断格式），路径写进说明里让 AI 用 Read 查看。房间销毁时（`Room.close()`）删掉这个房间送达过的图片目录：房间自己记着存过哪些（`_image_directories`），`delete_message_images()` 只删 `images/` 下的一级目录；服务停止时不删，对话可能还在继续。其余的（服务异常退出留下的、别的运行留下的）超过 24 小时的在下一次存图片时清掉（`IMAGE_RETENTION_SECONDS`）
    - 页面通知：消息已经标记送达之后，说明文字先备好，再通知页面（WebSocket `queue_delivered`，带 `count`），通知限时 2 秒（`Room.NOTIFY_TIMEOUT_SECONDS`），页面连接异常不会拖慢钩子的响应、也不会让取走的消息丢失
- **送达后的记录**：消息标记 `delivered_at` 和 `delivered_via`（`question` 或 `tool_call`），随提问送达的再带 `delivered_session_id`；原图释放、只留缩略图。随提问送达的记录对话记录里继续显示（排在送达它的那条 AI 消息之后），直到它随之送达的会话被淘汰出内存（`visible_messages()` 同时清理）；随工具调用送达的记录没有对应的会话，只保留最近 20 条（`MAX_TOOL_CALL_RECORDS`）
- **调用被取消**：`wait_for_feedback()` 捕获 `asyncio.CancelledError`，会话标记 `COMPLETED` 并放行等待事件，之后用户发的消息改走队列
- **AI 收到的内容**：随提问送达时是 `interactive_feedback` 的返回值（`MessageQueue.build_result()`）：先是一段说明（「你工作期间，用户追加了 N 条消息，按发送顺序列出。它们不是对你本次 summary 的回答，请按顺序当作新指令处理」），再按发送顺序列出每条 `[序号] HH:MM` 加文字（没有文字写「（无文字）」），带图片的消息标注图片张数和它们在返回图片里的位置；图片按消息顺序汇总，不含缩略图。随工具调用送达时是附在工具结果后面的文字（`MessageQueue.build_tool_call_context()`）：说明改成「请按顺序当作新指令处理，处理完再调用 interactive_feedback 汇报」，每条消息同样是 `[序号] HH:MM` 加文字，带图片的在文字下面逐行列出「图片：<本地路径>」
- **WebSocket 通知**：每条排队消息都有回应——`queue.messageQueued`（成功，带 `message_id`）、`queue.full`（`severity: warning`，队列已满）、`queue.messageEmpty`（`severity: warning`，没有文字且图片都被过滤掉）；`session_updated` 增加 `silent` 字段；钩子取走消息后发 `{"type": "queue_delivered", "count": N}`。通知码定义在 `MessageCodes`（`QUEUE_*`）

### 房间（对话隔离）
一个服务（端口 8765），每个 Cursor 对话一个房间（`Room`，`web/models/room.py`）：房间持有这个对话自己的会话（最多 10 个）、排队消息（`MessageQueue`）和页面连接，对话之间互不影响。
- **房间标识**：钩子填入的 `conversation_id`（`Room.room_id_for()`）；没有或格式不对的调用共用 `default` 房间。`WebUIManager.rooms` 是房间表，`get_or_create_room()` 加锁创建（MCP 线程创建、Web 线程查询）
- **进程**：一个 MCP 进程一个服务（`WebUIManager`）。端口只在 8765 被别的进程占用时才会换，同一个进程里永远复用 8765。一台机器上可以有多个 MCP 进程：Cursor 的 `mcp-process` 里一个，每个 `cursor-agent` worker 另带一个，各有各的房间表。房间都销毁后服务和端口留着，等下一次提问，MCP 进程退出时服务才停
- **新提问只替换自己房间的上一轮**：`Room.create_session()` 把房间里还没结束的旧会话标成 `COMPLETED`（「被新会话替代，自动完成」），页面的 WebSocket 转给新会话；别的房间不受影响。`current_session` 是房间里最新一轮的会话，页面的回复和排队都交给它
- **地址**：`/c/{room_id}/` 是对话的页面，页面用到的 `/api/*` 和 `/ws` 都在这个前缀下；`/` 是房间列表，`/api/rooms` 是它的数据。房间不存在、或房间里已经没有会话时，`/c/{room_id}/` 跳回 `/`
- **打开浏览器**：`WebUIManager.smart_open_browser(room, url)`：房间的页面还连着（`Room.check_active_tab()` 发 ping 探测）就只发刷新通知，否则新开标签页；每个对话第一次提问会新开自己的页面。页面是新连上来的，补发一次 `session_updated`（`Room.pending_session_update`）
- **清理**：内存监控触发的清理（过期、内存压力）逐个房间执行（`Room.cleanup_expired_sessions()`、`Room.cleanup_sessions_by_memory_pressure()`），只清房间里的会话，房间本身按下面的规则销毁
- **对话结束与销毁**：Cursor 的 `stop` 钩子在每个回合结束时触发，`hooks/gl-feedback.py stop` 把 `status`（`completed`、`aborted`、`error`）报告给房间（`POST /c/{room_id}/api/turn-ended`）。`Room.end_turn()` 记下结束标记（`ended_at`、`ended_status`），`Room.due_for_destroy()` 判断房间是否到期：
    - 登记文件：有对话标识的房间创建时写下 `$GL_FEEDBACK_HOME/rooms/<对话标识>`（`RoomRegistration`），格式同标记文件（房间的地址、服务进程号），房间存在期间一直在，`stop` 钩子靠它找到服务；服务启动完成后按实际端口重写一遍；`default` 房间没有对话标识，不登记
    - 报告被忽略：AI 的调用还在等回复（`is_awaiting_feedback()`）时对话明显还在继续，`end_turn()` 返回 `False`，接口响应 `{"ended": false}`；默认房间、已销毁的房间同样忽略，房间不存在返回 404
    - `completed`：房间标成已结束，`ENDED_DESTROY_DELAY_SECONDS`（3 秒）后销毁；这期间对话再提问（`Room.create_session()`），或者 AI 又调用了工具、钩子取走了排队的消息（`Room.deliver_for_tool_call()`），结束标记撤销，房间继续用。这时还有排队中的消息就不销毁，留着等对话下次提问或调用工具时送达
    - `aborted`、`error`（钩子读不出合法状态时是 `unknown`）：只标成已结束，不销毁，对话随时可能接着聊
    - 闲置：不论有没有收到过 `stop`，房间闲置超过 `IDLE_DESTROY_SECONDS`（2 小时）就销毁。`last_activity` 取房间和会话里最近的一次动静：会话有进展、排队消息变化、页面连上都算。AI 的调用还在等回复的房间、默认房间永远不销毁
    - 执行：服务运行期间一个后台任务（`WebUIManager._sweep_rooms_forever()`，在 FastAPI 的 lifespan 里启动）每 `ROOM_SWEEP_INTERVAL_SECONDS`（0.5 秒）调一次 `sweep_rooms()`。判断到期、从房间表摘掉、删标记和登记文件在 `_rooms_lock` 里一次完成（`_detach_if_due()`），和 AI 的新提问（`create_session_in_room()`）、结束报告（`WebUIManager.end_turn()`）互斥，刚被提问救回的房间不会被销毁。摘掉之后 `Room.close()` 给页面发 `{"type": "room_closed"}`、以 1000 / 「对话已结束」关闭连接（各限时 `NOTIFY_TIMEOUT_SECONDS`），再释放会话、排队消息，删掉送达过的图片文件。销毁后同一个对话再提问会建一个全新的房间
    - 图片：`Room.close()` 删掉这个房间钩子送达过的图片目录，别的房间的、不是它存的都不碰；服务停止（`cleanup_all_sessions()`）不删
    - 页面：收到 `room_closed` 后不再重连，连接状态显示「对话已结束」、输入框锁定，3 秒后 `window.close()`；浏览器只允许脚本关闭自己打开的标签页，关不掉的就留着这个状态。WebSocket 异常断开时页面等 3 秒重连一次，再失败同样 `window.close()`；房间已不存在时重连会被拒绝
    - 房间列表页：已结束的房间显示「已结束」，房间销毁后下一次轮询就看不到它

### Cursor 钩子
`hooks/gl-feedback.py` 是本项目的 Cursor 钩子，一个脚本四种模式。只用 Python 标准库，由 `uv run --script` 运行（脚本头是 PEP 723 内联元数据，`requires-python` 和项目一致）；任何异常都放行：输出合法 JSON、退出码 0（`preToolUse` 输出非法 JSON 会拦住调用）。输出只含 ASCII（非 ASCII 字符写成 `\uXXXX` 转义），输入按 `utf-8-sig` 解码：Windows 上 PowerShell 按控制台代码页转码钩子的输出，传进来的 JSON 开头可能带 UTF-8 BOM。
- 安装：`uvx gl-mcp-feedback@latest hooks install`（`hook_installer.py` 的 `HookInstaller`），README 只写这一种装法。`pyproject.toml` 的 `force-include` 把仓库的 `hooks/gl-feedback.py` 放进 wheel 的 `mcp_feedback_enhanced/hooks/`，安装时读这份（从源码运行时回退用仓库根目录的那份）复制到 `~/.cursor/hooks/gl-feedback.py`，再把五项登记（`HOOK_SPECS`）合并进用户级 `~/.cursor/hooks.json`。登记的命令是 `uv run --quiet --no-project --script ./hooks/gl-feedback.py <模式>`（`LAUNCHER`）：用户级钩子里的相对路径从 `~/.cursor/` 算起（带参数的相对路径在 Cursor 里实测可用），所以所有人登记的内容一样；命令只有 `uv` 加普通参数，没有引号和变量，bash、zsh、PowerShell 里都能执行
 - 试跑：登记之前先用同样的命令（`uv` 加登记的参数，工作目录是配置目录，输入 `{}`）跑一次脚本，退出码 0 且输出 `{}` 才算通过（`_trial_run()`，限时 60 秒，uv 第一次运行要准备环境，没有合适的 Python 时还要下载）。uv 自己出错时退出码是 2，Cursor 把 `preToolUse` 钩子的退出码 2 当成拒绝，会拦住 `interactive_feedback`，所以试跑不通就不改 hooks.json，刚复制的脚本（和随之空出来的 `hooks/` 目录）也撤掉，报告失败；PATH 里没有 `uv` 时跳过试跑，只警告并附 uv 的安装地址
 - 已经有 `gl-feedback.py <模式>` 登记的事件（`COMMAND_PATTERN`，脚本放哪、怎么启动都算，比如开发时指向仓库）默认不动，`--force` 才改成新位置；hooks.json 读不了或格式不对就什么都不改；改动前把原文件备份成 `hooks.json.bak`；重复执行不重复登记，已装的脚本和包里的不一样就更新
 - `uninstall` 删掉 hooks.json 里所有 `gl-feedback.py` 的登记（路径不限）、复制出来的脚本和空出来的 `hooks/` 目录，别的钩子原样保留；`--cursor-dir` 指定配置目录（测试用）
- 平台：macOS、Linux 和 Windows。Cursor 的用户级配置目录三个平台都是主目录下的 `.cursor`（Windows 是 `%USERPROFILE%\.cursor`）。Windows 上 Cursor 通过 PowerShell 执行钩子命令（官方人员在论坛确认），脚本按这个环境写：命令不带引号和变量，输入去 BOM，输出只含 ASCII，进程存活检查用 `ctypes` 调 `OpenProcess`（Windows 上 `os.kill(pid, 0)` 会结束进程）。**Windows 没有在真机上验证过**，README 如实写了。论坛确认的 Cursor 问题：Git Bash 在启动进程的祖先里时（环境里有 `MSYSTEM`），Cursor 会把 PowerShell 写的命令交给 bash 执行，所有钩子失败，`preToolUse` 拦住 `interactive_feedback`（论坛里报告的是命令行版 cursor-agent，桌面版没有验证过）；README 的常见问题里写了卸载办法。没有钩子时服务端照常工作，所有对话共用 `default` 房间
- `pre`（`preToolUse`，matcher 限定 `interactive_feedback`）：删掉入参里 AI 自己填的 `conversation_id`，再把 Cursor 给的对话标识写进 `conversation_id`，所有项目的对话都填。`preToolUse` 的输入里没有 MCP 服务器名，只能按工具名匹配：别的 MCP 服务器如果也有叫 `interactive_feedback` 的工具，它的调用同样会被改写
- `post`（`postToolUse` 和 `postToolUseFailure`，不加 matcher，所有工具）：这个对话有排队的消息就取走（认出 AwaitShell 的内部轮询就跳过），见「消息队列」
- `stop`（`stop`，agent 每个回合结束时触发，输入带 `status`）：先和 `log` 一样写事件日志，再看登记文件 `$GL_FEEDBACK_HOME/rooms/<对话标识>` 在不在（进程号对应的进程已不在就删掉），在就用 `http.client`（不走代理，连接超时 1 秒、总超时 5 秒）向登记的地址加 `/api/turn-ended` POST `{"status": "<状态>"}`；状态只认 1 到 32 位的字母和下划线，其余写成 `unknown`。没有登记文件说明这个对话没有房间（没提过问、已销毁、服务不在），什么也不做。房间据此销毁或标记，见「房间」
- `log`（`sessionStart`）：把事件的几个字段（不含用户输入、AI 的摘要、命令和文件内容）追加到 `$GL_FEEDBACK_HOME/hook-events.log`，超过 1 MB 换新文件、旧的留一份
- 核对钩子：Cursor 自己的钩子日志在 `~/Library/Application Support/Cursor/logs/<启动时间>/window*/output_*/cursor.hooks.*.log`，每次钩子收到的完整输入和返回的输出都在里面，每个事件前有一行 `[时间戳] Hook step requested: <事件名>`；工具的输入输出也记在这份日志里，可能出现同样的文字，所以按行首的时间戳匹配（`rg '^\[[0-9T:.Z-]+\] Hook step requested: stop$'`）
- 环境变量：`GL_FEEDBACK_HOME`（标记文件、登记文件、图片和事件日志所在目录，默认 `~/.cache/gl-mcp-feedback`，服务端读的是同一个变量）。测试用 `GL_FEEDBACK_HOME` 指向临时目录（`tests/conftest.py` 的 `isolated_feedback_home` 对所有测试生效）
- 测试：`tests/unit/test_queue_hook.py`（钩子脚本，用 `sys.executable` 直接运行，不依赖 uv；`TestHookRuntime` 里有一组用真的 `uv run --script` 跑，没有 uv 时跳过）、`tests/unit/test_hook_installer.py`（安装器，PATH 里放一个占位的 `uv` 脚本）

### 对话标识（conversation_id）字段
- 数据流：Cursor 钩子（`hooks/gl-feedback.py pre`，`preToolUse` 的 `updated_input`）把对话标识填进 `interactive_feedback(conversation_id=...)` → `WebFeedbackSession.conversation_id`。AI 不填这个参数，schema 的描述和 docstring 都写明了
- 格式：只认字母、数字、下划线、连字符，最长 128 位（`WebFeedbackSession.normalize_conversation_id()`），其余一律当作没有；钩子没装或没生效时为空串
- 服务端按 pydantic 校验入参，没有 `conversation_id` 的旧版本会拒绝带这个参数的调用，所以要先让服务端升级、重启所有 MCP 进程（见「房间」的「进程」），再让钩子注入
- 钩子填入的 `conversation_id` 同时决定调用进哪个房间（见「房间」）
- API：`/c/{room_id}/api/session-status`、`/c/{room_id}/api/current-session`、`/c/{room_id}/api/all-sessions` 的会话信息都包含 `conversation_id`
- 前端渲染位置：顶栏的对话 ID 徽标（`updateConversationBadge()`，由 `renderChat()` 按最新一轮更新）、浏览器标签页标题的后缀（对话 ID 前 4 位）

### MCP 交互规则去哪儿了
`interactive_feedback` 工具的「何时调用 / `summary` 怎么写 / 何时该问『可以收尾了吗』选择题 / 收到排队消息怎么处理」全部在 `src/mcp_feedback_enhanced/server.py` 里 —— 由 `_CHAT_PROMPT`（`@mcp.prompt(name="chat")` 返回）和 `interactive_feedback` 函数自身的 docstring/Field description 承载。本文件不重复这些内容。

### index.html 功能
- 房间列表页（`/`）：每个对话一张卡片，显示标题（`Room.page_title()`）、项目目录、「等你回复」、「AI 处理中」或「已结束」（`stop` 钩子报告过回合结束，对话再动起来之前）、排队消息数、最近活动时间，点击进入 `/c/{room_id}/`；没有房间时显示「等待 MCP 服务调用」提示
- 每 3 秒轮询 `/api/rooms` 刷新；项目目录来自 AI 的调用参数，一律用 `textContent` 显示

## 编码规范

### 样式修改
1. 所有样式通过 Tailwind utility classes 或模板内 `<style>` 标签的 `.md` 类定义
2. **无外部 CSS 文件**
3. Markdown 渲染样式在 `feedback.html` 的 `<style>` 中通过 `.md` 前缀选择器定义

### JS 修改
1. 所有 JS 逻辑内联在模板的 `<script>` 标签中
2. **无外部 JS 模块**（除了 vendor 目录的第三方库）
3. WebSocket 连接、提交、图片处理等均在 `feedback.html` 内实现

### Jinja 变量
- `feedback.html` 使用：`{{ page_title }}`（浏览器标签页标题）、`{{ project_directory }}`、`{{ room_base }}`（写在 `<body>` 的 `data-room-base` 上，脚本里的 `ROOM_BASE` 从它读）、`{{ version }}`（AI 摘要等对话内容不走 Jinja，由前端从 `ROOM_BASE + '/api/all-sessions'` 渲染）
- `index.html` 使用：`{{ version }}`
- Jinja 表达式只写在 HTML 里，不写进 `<script>`：编辑器把 `<script>` 当 JS 解析，`{{ ... }}` 会报语法错误；脚本要用的服务端值写在元素的 `data-*` 属性上，再从 `dataset` 读

### 后端 API
- `GET /` — 房间列表页（index.html）
- `GET /api/rooms` — 返回 `{ rooms }`，最近有动静的在前；每项含 `room_id`、`title`（同 `Room.page_title()`）、`conversation_id`、`project_directory`、`awaiting_feedback`（房间最新一轮的调用是否还在等回复）、`ended`（`stop` 钩子报告过回合结束，对话再动起来之前为 `true`）、`pending_count`（排队中的消息数）、`session_count`、`last_activity`（毫秒时间戳，会话有进展、排队消息变化、页面连上都算）、`has_websocket`
- `GET /c/{room_id}/` — 对话的页面（feedback.html）；房间不存在或房间里没有会话时重定向到 `/`
- 以下接口都挂在 `/c/{room_id}` 前缀下，房间不存在返回 404（`messageCode` 为 `room.notFound`）：
    - `GET /api/session-status` — 返回房间当前会话状态（含 `has_session`、`session_info`）
    - `GET /api/all-sessions` — 返回 `{ sessions, queued_messages, queue_limit }`。`sessions` 是房间里的所有会话（最多 10 个），每项含 AI 侧的 `summary`、`conversation_id`、`created_at`、`awaiting_feedback`（AI 的调用是否还在等这一轮的回复），以及用户回复的 `feedback`、`feedback_submitted_at`（毫秒时间戳，未回复为 `null`）、`feedback_images`（缩略图列表，每项含 `name` 和 `thumbnail`）；`queued_messages` 是用户排队发送的消息，每项含 `id`、`feedback`、`feedback_images`（缩略图）、`submitted_at`、`delivered_at`（毫秒时间戳，排队中为 `null`）、`delivered_via`（`question`、`tool_call`，排队中为 `null`）、`delivered_session_id`（随提问送达时才有）；`queue_limit` 是排队上限。对话流用的就是这个接口
    - `POST /api/turn-ended` — `stop` 钩子报告对话的一个回合结束了，请求体 `{ "status": "completed" }`（请求体读不出来或没有 `status` 时状态为空串，只标记、不销毁）：返回 `{ "ended": true }` 表示房间记下了结束标记，`{ "ended": false }` 表示被忽略（AI 的调用还在等回复）
    - `POST /api/queue/take` — 钩子在 AI 调用完一次工具后来取排队的消息：有消息返回 `{ "additional_context": "..." }`，没有返回 `{}`；只取这个房间的队列
    - `PUT /api/queue/{id}` — 修改排队中消息的文字，请求体 `{ "feedback": "..." }`；消息不在这个房间的队列里或已被取走返回 409，内容为空且没有图片返回 400
    - `DELETE /api/queue/{id}` — 撤回排队中的消息；消息不在这个房间的队列里或已被取走返回 409
    - `GET /api/current-session` — 返回房间当前会话详情
    - `WebSocket /ws` — 实时通信（反馈提交、会话更新、排队结果通知、排队消息被取走的通知、房间销毁的通知 `room_closed`）；房间不存在或没有会话时拒绝连接

## MCP Prompt 设计规范

MCP prompt 定义（`@mcp.prompt`）**不应包含任何参数**。

Cursor 会对 prompt 中的每个参数弹出输入框，导致用户体验差且延迟启动时间。所有上下文信息应由 AI 在调用工具时自行填充，而非要求用户手动输入。

```python
# 正确：无参数 prompt
@mcp.prompt(name="chat")
def gl_mcp_feedback() -> str:
    return "请立即调用 interactive_feedback 工具..."

# 错误：带参数会导致 Cursor 弹出输入框
@mcp.prompt(name="chat")
def gl_mcp_feedback(topic: str = "") -> str:
    ...
```
