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
- `src/mcp_feedback_enhanced/web/models/queue_files.py` — 排队消息的本地文件（`QueueMarker` 标记文件、图片文件）
- `src/mcp_feedback_enhanced/web/utils/` — 工具函数（浏览器、网络、端口、压缩配置）

### 其他
- `hooks/gl-feedback.sh` — Cursor 钩子脚本（`pre` 填对话标识，`post` 送达排队消息，`log` 记录会话事件），见「Cursor 钩子」
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
- **工作区**：微信聊天式的对话流 + 底部固定输入框。AI 消息靠左、用户回复靠右，每条气泡最宽占 2/3（窄屏 90%），带头像、小尾巴，相邻消息间隔 ≥ 5 分钟时插入居中的时间分隔
    - 对话记录由 `loadHistory()` 从 `ROOM_BASE + '/api/all-sessions'` 拉取，`buildTimeline()` 把 AI 消息（按会话 `created_at`）、用户对提问的回复（按 `feedback_submitted_at`）、用户排队发送的消息合成一条时间线：随提问送达的排队消息（`delivered_via` 为 `question`）是那次 AI 调用带回来的结果，按发送顺序紧跟在 `delivered_session_id` 对应的 AI 消息（及其下方说明）之后，位置按那条 AI 消息的 `created_at` 算（对应会话已不在记录里时按 `delivered_at` 兜底）；排队中的消息和随工具调用送达的消息（`tool_call`）按 `submitted_at` 排位，所以被钩子取走的那一刻位置不变；其余按时间升序（同一时刻 AI 消息排前）；时间线条目的 `at` 是排序位置，`sentAt` 是排队消息的真实发送时间，悬停提示用它。`renderChat()` 整体重绘，只给新出现的消息加入场动画（`prefers-reduced-motion` 下关闭）
    - AI 气泡：Markdown 渲染（`.md` 样式 + 代码块复制按钮：`enhanceCodeBlocks()` 给每个 `<pre>` 包一层 `.code-block`，按钮挂在这层上，代码横向滚动时按钮不跟着跑）；AI 的调用仍在等回复（`round.is_current && round.awaiting_feedback`）的最新一条在气泡首行带 `NEW` 标记（`data-awaiting`）
    - 用户气泡：纯文本（`textContent`）+ 图片缩略图；没有收到回复的 AI 消息下方显示一行灰字说明：随这次调用送达了排队消息（「已随这次调用送达你排队的 N 条消息」，说明下面紧跟这 N 条消息）、超时、过期、出错、这次提问已结束、被新提问替代
    - 排队消息气泡（`role: 'queued'`）：用户气泡下方一行状态——排队中（琥珀色「排队中 · AI 下次调用工具或提问时送达」+「编辑」「撤回」）或灰色的「✓ 已随hook送达 AI · HH:MM 发送」（钩子取走）/「✓ 已随提问送达 AI · HH:MM 发送」（HH:MM 是真实发送时间，`formatChatTime()` 格式）。「编辑」就地展开文字编辑框（`startQueuedEdit()`，Ctrl/⌘+Enter 保存、Esc 取消，只改文字、图片不变），保存走 `PUT {ROOM_BASE}/api/queue/{id}`；「撤回」走 `DELETE {ROOM_BASE}/api/queue/{id}`；编辑期间其他消息不显示操作按钮，重绘时保持编辑框内容、焦点和光标。保存或撤回遇到 409（已被 AI 取走）时提示用户，编辑中的草稿放回主输入框；编辑中的消息被取走时（`releaseDeliveredEditor()`）同样放回
    - 本地先行显示：发出排队消息后立刻插入 `local: true` 的临时气泡（key 为 `local-N`，没有编辑/撤回），收到 `queue.messageQueued`（带 `message_id`）后去掉本地副本，`queuedKeyAlias` 让气泡的 key 沿用本地的，不重复播放入场动画；`loadHistory()` 重新拉取时保留尚未确认的本地气泡
    - 滚动：首次加载与新一轮到达时，若最新 AI 消息一屏放不下就对齐到它的开头，否则停在底部；自己发送后滚到底部；切到其他标签页再切回会恢复滚动位置
    - 输入框（`applyComposerState()`）：只有连接被其他窗口接管时才禁用（`getComposerLock()`）。AI 在等回复时按钮为「发送」、提示快捷键；AI 没在等时按钮为「排队发送」、提示「AI 正在处理，消息会排队（n/3），AI 下次调用工具或提问时一并读取」；排队满额（`isQueueFull()`，上限取自 `/api/all-sessions` 的 `queue_limit`）时按钮禁用并显示「队列已满」。拉取记录之前保持常规状态。临时提示（`setComposerNotice()`：队列已满、消息已被取走、编辑内容为空等）用琥珀色显示 6 秒并同步给读屏；「队列已满」的提示在队列不再满时（撤回、被取走）立即清除
- **反馈提交**：WebSocket 发送 `submit_feedback` 消息，支持 Ctrl/⌘+Enter 快捷键。前端按 `isAwaitingReply()` 决定本地显示成回复还是排队气泡，实际分流以服务端为准（见「消息队列」）。每条消息服务端都回应一条 `notification`，前端按发送顺序对账（`inflightSends`，回复记空串、排队消息记本地气泡的 key，`settleSend()` 逐条销账），再重新拉取记录校正：`code` 为 `session.feedbackSubmitted`（作为回复）、`queue.messageQueued`（进了队列）、`queue.full`（队列已满）或 `queue.messageEmpty`（没有文字且图片都被过滤掉，例如过大），后两种都是被拒绝：撤掉本地气泡，发送的文字和图片放回输入框，不丢。WebSocket 断线重连后（`ws.onopen`），没收到回应的本地气泡一律撤掉（`dropUnconfirmedSends()`），以重新拉取的服务端记录为准
- **新反馈提醒**：收到 WebSocket `session_updated` 时调用 `notifyNewFeedback()`，用 Web Audio 合成「叮咚」提示音（`playAlertSound`）+ 桌面通知（`showDesktopNotification`，仅 `document.hidden` 时弹、点击聚焦窗口），同时重新拉取对话记录并滚到新消息。`silent: true` 的 `session_updated`（AI 的新一轮调用直接取走了排队消息，没有要用户回复的提问）不响不弹，只重新拉取并读屏提示「AI 已读取你排队的消息」；收到 `queue_delivered`（AI 调用完一次工具，钩子取走了排队的消息）同样不响不弹，重新拉取记录，把「排队中」翻成「已送达」。默认始终开启、无设置开关；首次用户手势由 `setupAlertOnFirstGesture()` 解锁音频播放并请求 `Notification` 授权
- **图片上传**：支持文件选择和 Ctrl+V 粘贴，base64 编码随反馈一起发送；客户端同时生成长边 ≤ 320px 的 JPEG 缩略图（`thumbnail`，data URL）随每张图片一起上传，服务端单独保存，仅用于对话记录展示

### 消息队列
AI 没在等回复时（正在干活，或上一次调用已经结束），用户发的消息先排队，有两条路送到 AI 手里，谁先到谁取走所有排队中的消息，同一条消息不会送达两次：AI 调用完任意一次工具时由 Cursor 钩子来取（随工具调用送达），或者 AI 下次调用 `interactive_feedback` 时一并返回（随提问送达）。不会因为 AI 再次提问而丢失。队列按房间分，每个对话一个，只被自己的调用取走。
- **分流规则**：WebSocket 收到 `submit_feedback` 时，`session.is_awaiting_feedback()`（状态为 `WAITING` 且 `feedback_completed` 未置位）为真，消息作为对当前提问的回复（`session.submit_feedback`）；否则交给 `Room.enqueue_user_message()` 进房间的队列。`/api/all-sessions` 的每个会话带 `awaiting_feedback`，前端用同一个口径决定输入框状态
- **`MessageQueue`**：只放内存，服务重启即清空；线程安全（Web 线程写入、修改、撤回，随提问送达在 MCP 线程取走，钩子的请求在 Web 线程里取走）。排队中的消息最多 3 条（`MAX_PENDING_MESSAGES`，已送达的记录不占名额），满额抛 `QueueFullError`，前端收到 `queue.full` 通知。排队消息可以改文字（`update_text`，纯文字消息不能改成空）和撤回（`cancel`），已被取走的抛 `MessageNotPendingError`。取走有两个方法：`deliver_with_question(session_id)`（随提问）和 `deliver_with_tool_call()`（随工具调用），各自在锁里一次取走全部排队中的消息并标上 `DeliveryChannel`。排队中的消息数可能变了（加入、撤回、取走）之后调用 `on_pending_changed`，在锁之外执行、出错只记日志，房间用它同步标记文件。队列由房间持有（`Room.message_queue`），每个对话一个；没有对话标识的调用共用 `default` 房间的队列，不区分项目目录
- **随提问送达（AI 再次调用时）**：`launch_web_feedback_ui()` 在 `Room.create_session()` 之后、启动服务和打开浏览器之前调用 `Room.deliver_queued_messages()`。房间的队列非空就取走所有排队消息，新会话 `close_with_queued_messages()`（状态 `WAITING` → `ACTIVE`、等待事件置位），已打开的页面收到 `silent: true` 的 `session_updated` 静默刷新，调用立刻返回，不等用户；队列为空则照常等用户回复
- **随工具调用送达（钩子）**：`hooks/gl-feedback.sh post` 挂在所有工具的 `postToolUse` 和 `postToolUseFailure` 上，每次工具调用结束都会触发，所以热路径只做一件事：从输入开头取 `conversation_id`，看标记文件 `$GL_FEEDBACK_HOME/queue/<对话标识>` 在不在，不在就直接返回（几毫秒）。
    - 标记文件（`QueueMarker`，`queue_files.py`）：房间的队列里有排队中的消息时存在，没有时不存在；两行内容，房间的地址（`WebUIManager.get_hook_room_url()`，监听 `0.0.0.0` 时用 `127.0.0.1`）和写它的服务进程号。写入是原子的（临时文件加 `replace`），`sync()` 在自己的锁里重新读排队数量，几个线程同时同步时文件最终和队列一致；写入失败只记日志，不影响队列。`default` 房间没有对话标识，钩子找不到它，不写标记；房间清理时（`cleanup_all_sessions()`）删掉标记。钩子发现进程号对应的进程已经不在，就把这个过期的标记删掉
    - 取走接口：钩子用 `curl --noproxy '*'` 调标记里的地址加 `/api/queue/take`（连接超时 1 秒、总超时 5 秒）。接口调 `Room.deliver_for_tool_call()` 取走全部排队消息，有消息返回 `{"additional_context": "..."}`，没有返回 `{}`；钩子只在收到 HTTP 成功且以 `{` 开头的回复时原样交给 Cursor，Cursor 把 `additional_context` 附在那次工具结果后面给 AI，其余情况输出 `{}`
    - 图片：钩子送达只能传文字，图片先存成 `$GL_FEEDBACK_HOME/images/<消息标识>/<序号>.<扩展名>`（按文件头判断格式），路径写进说明里让 AI 用 Read 查看；超过 24 小时的图片目录在下一次存图片时清掉（`IMAGE_RETENTION_SECONDS`）
    - 页面通知：消息已经标记送达之后，说明文字先备好，再通知页面（WebSocket `queue_delivered`，带 `count`），通知限时 2 秒（`Room.NOTIFY_TIMEOUT_SECONDS`），页面连接异常不会拖慢钩子的响应、也不会让取走的消息丢失
- **送达后的记录**：消息标记 `delivered_at` 和 `delivered_via`（`question` 或 `tool_call`），随提问送达的再带 `delivered_session_id`；原图释放、只留缩略图。随提问送达的记录对话记录里继续显示（排在送达它的那条 AI 消息之后），直到它随之送达的会话被淘汰出内存（`visible_messages()` 同时清理）；随工具调用送达的记录没有对应的会话，只保留最近 20 条（`MAX_TOOL_CALL_RECORDS`）
- **调用被取消**：`wait_for_feedback()` 捕获 `asyncio.CancelledError`，会话标记 `COMPLETED` 并放行等待事件，之后用户发的消息改走队列
- **AI 收到的内容**：随提问送达时是 `interactive_feedback` 的返回值（`MessageQueue.build_result()`）：先是一段说明（「你工作期间，用户追加了 N 条消息，按发送顺序列出。它们不是对你本次 summary 的回答，请按顺序当作新指令处理」），再按发送顺序列出每条 `[序号] HH:MM` 加文字（没有文字写「（无文字）」），带图片的消息标注图片张数和它们在返回图片里的位置；图片按消息顺序汇总，不含缩略图。随工具调用送达时是附在工具结果后面的文字（`MessageQueue.build_tool_call_context()`）：说明改成「请按顺序当作新指令处理，处理完再调用 interactive_feedback 汇报」，每条消息同样是 `[序号] HH:MM` 加文字，带图片的在文字下面逐行列出「图片：<本地路径>」
- **WebSocket 通知**：每条排队消息都有回应——`queue.messageQueued`（成功，带 `message_id`）、`queue.full`（`severity: warning`，队列已满）、`queue.messageEmpty`（`severity: warning`，没有文字且图片都被过滤掉）；`session_updated` 增加 `silent` 字段；钩子取走消息后发 `{"type": "queue_delivered", "count": N}`。通知码定义在 `MessageCodes`（`QUEUE_*`）

### 房间（对话隔离）
一个服务（端口 8765），每个 Cursor 对话一个房间（`Room`，`web/models/room.py`）：房间持有这个对话自己的会话（最多 10 个）、排队消息（`MessageQueue`）和页面连接，对话之间互不影响。
- **房间标识**：钩子填入的 `conversation_id`（`Room.room_id_for()`）；没有或格式不对的调用共用 `default` 房间。`WebUIManager.rooms` 是房间表，`get_or_create_room()` 加锁创建（MCP 线程创建、Web 线程查询）
- **新提问只替换自己房间的上一轮**：`Room.create_session()` 把房间里还没结束的旧会话标成 `COMPLETED`（「被新会话替代，自动完成」），页面的 WebSocket 转给新会话；别的房间不受影响。`current_session` 是房间里最新一轮的会话，页面的回复和排队都交给它
- **地址**：`/c/{room_id}/` 是对话的页面，页面用到的 `/api/*` 和 `/ws` 都在这个前缀下；`/` 是房间列表，`/api/rooms` 是它的数据。房间不存在、或房间里已经没有会话时，`/c/{room_id}/` 跳回 `/`
- **打开浏览器**：`WebUIManager.smart_open_browser(room, url)`：房间的页面还连着（`Room.check_active_tab()` 发 ping 探测）就只发刷新通知，否则新开标签页；每个对话第一次提问会新开自己的页面。页面是新连上来的，补发一次 `session_updated`（`Room.pending_session_update`）
- **清理**：内存监控触发的清理（过期、内存压力）逐个房间执行（`Room.cleanup_expired_sessions()`、`Room.cleanup_sessions_by_memory_pressure()`）；房间表只增不减，清理只清房间里的会话

### Cursor 钩子
`hooks/gl-feedback.sh` 是本项目的 Cursor 钩子，一个脚本三种模式，在用户级 `~/.cursor/hooks.json` 里登记（写法见 README）。只依赖 bash、jq 和 curl；任何异常都放行：输出合法 JSON、退出码 0（`preToolUse` 输出非法 JSON 会拦住调用）。
- `pre`（`preToolUse`，matcher 限定 `interactive_feedback`）：删掉入参里的 `title` 和 AI 自己填的 `conversation_id`，再把 Cursor 给的对话标识写进 `conversation_id`。只给 `GL_INJECT_ROOT`（默认是脚本所在的仓库）项目里的对话填，其他项目只做删除
- `post`（`postToolUse` 和 `postToolUseFailure`，不加 matcher，所有工具）：这个对话有排队的消息就取走，见「消息队列」
- `log`（`sessionStart`、`sessionEnd`、`stop`）：把事件的几个字段（不含用户输入、AI 的摘要、命令和文件内容）追加到 `$GL_FEEDBACK_HOME/hook-events.log`，超过 1 MB 换新文件、旧的留一份
- 环境变量：`GL_FEEDBACK_HOME`（标记文件、图片和事件日志所在目录，默认 `~/.cache/gl-mcp-feedback`，服务端读的是同一个变量）、`GL_INJECT_ROOT`、`GL_JQ`（jq 的位置）。测试用 `GL_FEEDBACK_HOME` 指向临时目录（`tests/conftest.py` 的 `isolated_feedback_home` 对所有测试生效）

### 对话标识（conversation_id）字段
- 数据流：Cursor 钩子（`hooks/gl-feedback.sh pre`，`preToolUse` 的 `updated_input`）把对话标识填进 `interactive_feedback(conversation_id=...)` → `WebFeedbackSession.conversation_id`。AI 不填这个参数，schema 的描述和 docstring 都写明了
- 格式：只认字母、数字、下划线、连字符，最长 128 位（`WebFeedbackSession.normalize_conversation_id()`），其余一律当作没有；钩子没装或没生效时为空串
- 服务端按 pydantic 校验入参，没有 `conversation_id` 的旧版本会拒绝带这个参数的调用，所以要先让服务端升级、重启 MCP，再让钩子注入
- 钩子填入的 `conversation_id` 同时决定调用进哪个房间（见「房间」）
- API：`/c/{room_id}/api/session-status`、`/c/{room_id}/api/current-session`、`/c/{room_id}/api/all-sessions` 的会话信息都包含 `conversation_id`
- 前端渲染位置：顶栏的对话 ID 徽标（`updateConversationBadge()`，由 `renderChat()` 按最新一轮更新）、浏览器标签页标题的后缀（对话 ID 前 4 位）

### MCP 交互规则去哪儿了
`interactive_feedback` 工具的「何时调用 / `summary` 怎么写 / 何时该问『可以收尾了吗』选择题 / 收到排队消息怎么处理」全部在 `src/mcp_feedback_enhanced/server.py` 里 —— 由 `_CHAT_PROMPT`（`@mcp.prompt(name="chat")` 返回）和 `interactive_feedback` 函数自身的 docstring/Field description 承载。本文件不重复这些内容。

### index.html 功能
- 房间列表页（`/`）：每个对话一张卡片，显示标题（`Room.page_title()`）、项目目录、「等你回复」或「AI 处理中」、排队消息数、最近活动时间，点击进入 `/c/{room_id}/`；没有房间时显示「等待 MCP 服务调用」提示
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
- `GET /api/rooms` — 返回 `{ rooms }`，最近有动静的在前；每项含 `room_id`、`title`（同 `Room.page_title()`）、`conversation_id`、`project_directory`、`awaiting_feedback`（房间最新一轮的调用是否还在等回复）、`pending_count`（排队中的消息数）、`session_count`、`last_activity`（毫秒时间戳）、`has_websocket`
- `GET /c/{room_id}/` — 对话的页面（feedback.html）；房间不存在或房间里没有会话时重定向到 `/`
- 以下接口都挂在 `/c/{room_id}` 前缀下，房间不存在返回 404（`messageCode` 为 `room.notFound`）：
    - `GET /api/session-status` — 返回房间当前会话状态（含 `has_session`、`session_info`）
    - `GET /api/all-sessions` — 返回 `{ sessions, queued_messages, queue_limit }`。`sessions` 是房间里的所有会话（最多 10 个），每项含 AI 侧的 `summary`、`conversation_id`、`created_at`、`awaiting_feedback`（AI 的调用是否还在等这一轮的回复），以及用户回复的 `feedback`、`feedback_submitted_at`（毫秒时间戳，未回复为 `null`）、`feedback_images`（缩略图列表，每项含 `name` 和 `thumbnail`）；`queued_messages` 是用户排队发送的消息，每项含 `id`、`feedback`、`feedback_images`（缩略图）、`submitted_at`、`delivered_at`（毫秒时间戳，排队中为 `null`）、`delivered_via`（`question`、`tool_call`，排队中为 `null`）、`delivered_session_id`（随提问送达时才有）；`queue_limit` 是排队上限。对话流用的就是这个接口
    - `POST /api/queue/take` — 钩子在 AI 调用完一次工具后来取排队的消息：有消息返回 `{ "additional_context": "..." }`，没有返回 `{}`；只取这个房间的队列
    - `PUT /api/queue/{id}` — 修改排队中消息的文字，请求体 `{ "feedback": "..." }`；消息不在这个房间的队列里或已被取走返回 409，内容为空且没有图片返回 400
    - `DELETE /api/queue/{id}` — 撤回排队中的消息；消息不在这个房间的队列里或已被取走返回 409
    - `GET /api/current-session` — 返回房间当前会话详情
    - `WebSocket /ws` — 实时通信（反馈提交、会话更新、排队结果通知、排队消息被取走的通知）；房间不存在或没有会话时拒绝连接

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
