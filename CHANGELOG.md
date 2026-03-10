# Changelog

## [Unreleased]

### 新增

- **图片粘贴/添加 Toast 提示**：粘贴或选择图片后，页面会弹出 Toast 提示"已添加 N 张图片"，改善用户体验
  - 在 `file-upload-manager.js` 的 `addFiles` 方法中添加成功提示
  - 补充 `showMessage` 方法对 `success` 类型的支持
  - 新增三种语言的 `fileUpload.imageAdded` 翻译条目

### 优化

- **移除图片 Base64 冗余传输**：图片不再通过 TextContent 中的 base64 编码传递，仅使用 MCPImage 原生方式传递，大幅减少 token 消耗
  - 移除 `create_feedback_text()` 中的图片附件概要文字生成（含 base64 预览和完整编码）
  - 调整 `interactive_feedback()` 中 TextContent 的创建条件，不再因图片存在而触发

### 移除

- **移除 Base64 兼容模式设置**：前端 UI 中的 "Base64 兼容模式" 开关已移除，因图片已完全通过 MCPImage 传递
  - 移除 `feedback.html` 中的 Base64 兼容模式设置项
  - 移除 `image-upload.html` 中的兼容性提示区域
  - 移除 `settings-manager.js` 中的 `enableBase64Detail` 默认设置、UI 同步及事件监听
  - 移除 `image-handler.js` 中的 `enableBase64Detail` 属性及相关 UI 元素绑定
  - 移除 `file-upload-manager.js` 中的 `enableBase64Detail` 配置
  - 移除 `app.js` 中的 `enableBase64Detail` 初始化和设置同步
  - 移除三个语言文件（zh-TW、zh-CN、en）中的相关翻译条目

### 影响的文件

- `src/mcp_feedback_enhanced/server.py`
- `src/mcp_feedback_enhanced/web/templates/feedback.html`
- `src/mcp_feedback_enhanced/web/templates/components/image-upload.html`
- `src/mcp_feedback_enhanced/web/static/js/app.js`
- `src/mcp_feedback_enhanced/web/static/js/modules/settings-manager.js`
- `src/mcp_feedback_enhanced/web/static/js/modules/image-handler.js`
- `src/mcp_feedback_enhanced/web/static/js/modules/file-upload-manager.js`
- `src/mcp_feedback_enhanced/web/locales/zh-TW/translation.json`
- `src/mcp_feedback_enhanced/web/locales/zh-CN/translation.json`
- `src/mcp_feedback_enhanced/web/locales/en/translation.json`

---

## [1.1.0] - 2025

### 修复

- 修复 MCP 图片传递序列化错误
