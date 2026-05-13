# 并发会话设计说明

## 背景

Web UI 原先采用单一活跃会话模型：新的 `interactive_feedback` 调用会替换当前会话，并复用页面连接。这种模式适合串行反馈，但当多个 MCP 调用同时等待用户反馈时，旧调用容易等待到超时，用户也无法在页面中明确选择要回复的会话。

## 当前设计

- `WebUIManager.sessions` 是会话池，允许多个 `WebFeedbackSession` 同时处于等待状态。
- `WebUIManager.current_session` 仅保留为兼容字段，表示最近创建的默认会话。
- `interactive_feedback` 创建会话后，按创建出的 `session_id` 等待结果，不再依赖后续可能变化的 `current_session`。
- 新建会话不会自动完成、清理或替换旧会话。

## API 与 WebSocket

- `/api/status` 与 `/api/session-status` 返回兼容的当前会话状态，供等待页和旧调用方使用。
- `/api/all-sessions` 返回全部会话状态，用于渲染会话标签和会话管理页。
- `/api/session/{session_id}` 返回指定会话详情。
- `/ws/{session_id}` 是新的 session 级 WebSocket，提交反馈只作用于绑定会话。
- `/ws` 保留为兼容入口，默认连接最近的 `current_session`。

## 前端行为

- 主页面顶部显示会话标签，用户可以在多个等待会话间切换。
- 切换会话会更新摘要、标题、输入草稿和 WebSocket 连接。
- 提交反馈时，前端通过当前标签对应的 `/ws/{session_id}` 提交，避免反馈串到其他会话。
- 同一会话被另一个页面连接时，旧连接仍会收到 `connection_replaced`，但不同会话之间不会互相替换。

## 兼容性原则

- 保留旧 HTTP API 与旧 `/ws` 行为，降低已有客户端和前端入口的破坏面。
- 多会话能力优先作为增量能力提供，不要求调用方必须传入新的 MCP 参数。
- 单个会话完成或超时不应关闭全局 Web 服务，也不应影响其他等待会话。
