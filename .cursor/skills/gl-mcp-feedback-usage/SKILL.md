---
name: gl-mcp-feedback-usage
description: Guide AI agents on how to use the gl-mcp-feedback MCP tool for interactive feedback collection. Use when working on projects that have gl-mcp-feedback configured, when the user mentions feedback, or when completing coding tasks that need user confirmation.
---

# gl-mcp-feedback Usage Guide

## What is gl-mcp-feedback

An MCP server providing a Web UI for interactive feedback between AI agents and users. The agent calls the `interactive_feedback` tool, which opens a browser-based interface where users can review AI's work summary, type text feedback, and upload images.

## When to Call interactive_feedback

Call the tool in these situations (unless the user explicitly says "end" or "no more interaction"):

1. **After completing code changes** — summarize what was done, wait for user testing/feedback
2. **Before implementing new requirements** — explain your understanding and approach, get confirmation first
3. **When a tool call is interrupted/cancelled** — ask the user what to do next
4. **On MCP timeout** — re-call immediately, do not assume user is unresponsive
5. **During multi-step tasks** — check in after each phase

## Tool Parameters

```
interactive_feedback(
    project_directory: str = ".",     # project path for context
    summary: str = "...",             # markdown summary of work done
    timeout: int = 600                # wait time in seconds (keep ≥ 600)
)
```

## Writing Good Summaries

The `summary` parameter is rendered as Markdown in the Web UI. Write it for the user to quickly understand what happened.

**Do:**
- Use headers (`##`), bullet points, and tables for structure
- Include file paths that were changed
- Highlight key decisions or trade-offs
- Keep it concise but complete

**Don't:**
- Write a wall of unformatted text
- Include raw code dumps in the summary
- Leave the summary as the default string

**Example:**

```markdown
## 完成登录功能重构

- 重构了 `src/auth/login.py`，将验证逻辑抽取为独立函数
- 新增 `src/auth/validators.py`
- 修复了密码哈希使用已废弃算法的问题

请测试登录流程是否正常，特别是：
1. 正常登录
2. 错误密码提示
3. 账户锁定逻辑
```

## Feedback Loop Pattern

```
Start task
  └─> Do work
       └─> Call interactive_feedback (summarize work)
            └─> Read user feedback
                 ├─> Feedback has new instructions → adjust & loop back
                 ├─> Feedback confirms completion → finish
                 └─> Empty feedback → ask for clarification via another call
```

## Common Mistakes to Avoid

| Mistake | Fix |
|---------|-----|
| Not calling the tool after code changes | Always call — it's the primary communication channel |
| Setting timeout too low (< 300) | Use ≥ 600; users need time to test |
| Treating timeout as "user is gone" | Re-call immediately on timeout |
| Writing summary in English when user speaks Chinese | Match the user's language |
| Continuing work without feedback | Stop and call the tool first |
