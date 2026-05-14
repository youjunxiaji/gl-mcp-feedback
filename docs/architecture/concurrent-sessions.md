# 并发会话设计说明

## 背景

Web UI 原先采用单一活跃会话模型：新的 `interactive_feedback` 调用会替换当前会话，并复用页面连接。这种模式适合串行反馈，但当多个 MCP 调用同时等待用户反馈时，旧调用容易等待到超时，用户也无法在页面中明确选择要回复的会话。

## 当前设计

- `WebUIManager.sessions` 是会话池，允许多个 `WebFeedbackSession` 同时处于等待状态。
- `WebUIManager.current_session` 仅保留为兼容字段，表示最近创建的默认会话。
- `interactive_feedback` 创建会话后，按创建出的 `session_id` 等待结果，不再依赖后续可能变化的 `current_session`。
- 新建会话不会自动完成、清理或替换旧会话。
- `continue_feedback_session(session_id, timeout=600)` 用于 MCP 保活续等，不重新发送摘要内容。
- 等待窗口使用比 `timeout` 更短的实际时长，默认以 `600` 秒设计并提前返回保活请求，避免触发 MCP 客户端外层超时。
- `MCP_FEEDBACK_AUTO_EXTEND_MAX` 控制自动保活次数，默认 `3` 次；用户在页面点击“延长等待”产生的手动额度不计入自动次数。
- `MCP_WEB_ALWAYS_ON=true` 可让 MCP server 启动时直接启动 Web 等待页；默认仍为懒启动。
- `MCP_WEB_SHARE_MODE=shared` 可启用由 MCP client 自动维护的 managed Web Hub，让多个项目级 MCP 进程复用同一个 Web 页面。

## 多项目共享 Web Hub

默认 `MCP_WEB_SHARE_MODE=isolated`，保持上游兼容：每个 MCP server 进程使用自己的 Web 服务。启用 `shared` 后，MCP server 进程不再直接持有 Web UI，而是作为 client：

1. 检查本地 Hub 状态文件和 `/api/hub/health`。
2. 如果 Hub 不存在，通过当前 Python 环境自动拉起 `python -m mcp_feedback_enhanced web-hub` 子进程。
3. 使用 `POST /api/hub/sessions` 把当前项目的反馈会话注册到 Hub。
4. 使用 `POST /api/hub/sessions/{session_id}/wait` long-poll 等待用户提交或保活续等。
5. 如果 Hub 在等待期间中断，client 最多内定重建 3 次，并用同一个 `session_id` 和原始摘要重新注册会话。

Hub 不是用户需要单独部署的服务，而是 MCP client 按需维护的子进程。它会写入 `~/.cache/gl-mcp-feedback/hub.json`，其中包含 URL、PID、版本和本地访问 token。多个 client 通过文件锁避免同时拉起多个 Hub。

相关环境变量：

- `MCP_WEB_SHARE_MODE=isolated|shared`：默认 `isolated`。
- `MCP_WEB_PORT_AUTO_CLEANUP=false`：默认不清理占用端口的进程，避免多个项目互相终止。
- `MCP_WEB_HUB_IDLE_TIMEOUT=1800`：Hub 没有待处理 session 后的空闲退出时间，`0` 表示不自动退出。
- `MCP_WEB_HOST` / `MCP_WEB_PORT`：shared 模式下建议固定为同一组本地地址，例如 `127.0.0.1:9790`。

推荐多项目配置：

```json
"env": {
  "MCP_WEB_SHARE_MODE": "shared",
  "MCP_WEB_HOST": "127.0.0.1",
  "MCP_WEB_PORT": "9790",
  "MCP_WEB_PORT_AUTO_CLEANUP": "false",
  "MCP_WEB_HUB_IDLE_TIMEOUT": "1800",
  "MCP_FEEDBACK_KEEPALIVE_MARGIN": "30"
}
```

## API 与 WebSocket

- `/api/status` 与 `/api/session-status` 返回兼容的当前会话状态，供等待页和旧调用方使用。
- `/api/all-sessions` 返回全部会话状态，用于渲染会话标签和会话管理页。
- `/api/session/{session_id}` 返回指定会话详情。
- `/api/hub/health` 用于 MCP client 识别和复用 managed Web Hub。
- `/api/hub/sessions` 用于跨进程注册反馈会话。
- `/api/hub/sessions/{session_id}/wait` 用于跨进程 long-poll 等待反馈结果。
- `/ws/{session_id}` 是新的 session 级 WebSocket，提交反馈只作用于绑定会话。
- `/ws` 保留为兼容入口，默认连接最近的 `current_session`。
- WebSocket 支持 `extend_wait` 消息，用于为当前 session 增加一次手动保活额度。

## 前端行为

- 主页面顶部显示会话标签，用户可以在多个等待会话间切换。
- shared 模式下，会话标签会带上来源项目名，避免不同项目的反馈会话混淆。
- 新会话到达时只新增并标红标签，不自动打断当前编辑会话。
- 切换会话会更新摘要、标题、输入草稿、图片草稿和 WebSocket 连接。
- 文字与图片草稿按 `session_id` 缓存，提交后清理，未提交草稿 24 小时后过期。
- 反馈输入区显示当前 MCP 等待窗口的真实倒计时，并提供“延长等待”按钮。
- 设置页支持默认快捷键模式、Cursor 快捷键模式和自定义规则入口。
- 页面支持自动/明/暗三态主题，浅色主题参考本地覆盖脚本配色。
- 提交反馈时，前端通过当前标签对应的 `/ws/{session_id}` 提交，避免反馈串到其他会话。
- 同一会话被另一个页面连接时，旧连接仍会收到 `connection_replaced`，但不同会话之间不会互相替换。

## 兼容性原则

- 保留旧 HTTP API 与旧 `/ws` 行为，降低已有客户端和前端入口的破坏面。
- 多会话能力优先作为增量能力提供，不要求调用方必须传入新的 MCP 参数。
- 单个会话完成或超时不应关闭全局 Web 服务，也不应影响其他等待会话。
