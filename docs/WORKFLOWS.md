# GitHub Actions 工作流程說明

本項目使用 GitHub Actions 來自動化發佈流程。

## 🏗️ 工作流程架構

### 發佈工作流程 (publish.yml)

**用途**: 負責版本管理和 PyPI 發佈

**觸發條件**:
- 手動觸發 (workflow_dispatch)

**功能**:
- 自動或手動版本號管理
- 發佈到 PyPI
- 創建 GitHub Release

## 🚀 使用方式

### 發佈新版本時

1. **手動觸發發佈** - 在 GitHub Actions 頁面運行 "Auto Release to PyPI"
2. **選擇發佈選項**:
   - `version_type`: patch/minor/major (或使用 custom_version)

## 📋 最佳實踐

### 發佈流程

1. **準備發佈**:
   - 更新 CHANGELOG 文件
   - 測試本地功能

2. **執行發佈**:
   - 手動觸發 "Auto Release to PyPI" 工作流程
   - 選擇適當的版本類型

3. **發佈後驗證**:
   - 檢查 PyPI 上的新版本
   - 測試安裝: `uvx mcp-feedback-enhanced@latest`

## 🔧 故障排除

### 發佈流程問題

1. **版本衝突**:
   - 檢查 PyPI 上是否已存在相同版本
   - 確認版本號格式正確 (X.Y.Z)

2. **權限問題**:
   - 確認 PYPI_API_TOKEN 密鑰已正確設置
   - 檢查 GitHub Token 權限
