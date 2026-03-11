# Makefile for mcp-feedback-enhanced development
# 適用於 mcp-feedback-enhanced 專案開發
# Compatible with Windows PowerShell and Unix systems
# 兼容 Windows PowerShell 和 Unix 系統

.PHONY: help install install-dev install-hooks lint format type-check test clean pre-commit-run pre-commit-all update-deps build-all test-all test-func test-web

# 預設目標 - 顯示幫助訊息
help: ## Show this help message
	@echo "Available commands:"
	@echo ""
	@echo "  dev-setup            Complete development setup"
	@echo "  install              Install the package"
	@echo "  install-dev          Install development dependencies"
	@echo "  install-hooks        Install pre-commit hooks"
	@echo "  lint                 Run linting with Ruff"
	@echo "  lint-fix             Run linting with auto-fix"
	@echo "  format               Format code with Ruff"
	@echo "  format-check         Check code formatting"
	@echo "  type-check           Run type checking with mypy"
	@echo "  check                Run all code quality checks"
	@echo "  check-fix            Run all checks with auto-fix"
	@echo "  pre-commit-run       Run pre-commit on staged files"
	@echo "  pre-commit-all       Run pre-commit on all files"
	@echo "  pre-commit-update    Update pre-commit hooks"
	@echo "  test                 Run tests"
	@echo "  test-cov             Run tests with coverage"
	@echo "  test-fast            Run tests without slow tests"
	@echo "  test-func            Run functional tests (standard)"
	@echo "  test-web             Run Web UI tests (continuous)"
	@echo "  clean                Clean up cache and temporary files"
	@echo "  ps-clean             PowerShell version of clean (Windows)"
	@echo "  update-deps          Update dependencies"
	@echo "  build                Build the package"
	@echo "  build-check          Check the built package"
	@echo "  bump-patch           Bump patch version"
	@echo "  bump-minor           Bump minor version"
	@echo "  bump-major           Bump major version"
	@echo "  ci                   Simulate CI pipeline locally"
	@echo "  quick-check          Quick check with auto-fix"

# 安裝相關命令
install: ## Install the package
	uv sync

install-dev: ## Install development dependencies
	uv sync --dev

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install
	@echo "✅ Pre-commit hooks installed successfully!"

# 程式碼品質檢查命令
lint: ## Run linting with Ruff
	uv run ruff check .

lint-fix: ## Run linting with auto-fix
	uv run ruff check . --fix

format: ## Format code with Ruff
	uv run ruff format .

format-check: ## Check code formatting
	uv run ruff format . --check

type-check: ## Run type checking with mypy
	uv run mypy

# 組合品質檢查命令
check: lint format-check type-check ## Run all code quality checks

check-fix: lint-fix format type-check ## Run all checks with auto-fix

# Pre-commit 相關命令
pre-commit-run: ## Run pre-commit on staged files
	uv run pre-commit run

pre-commit-all: ## Run pre-commit on all files
	uv run pre-commit run --all-files

pre-commit-update: ## Update pre-commit hooks
	uv run pre-commit autoupdate

# 測試相關命令
test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src/mcp_feedback_enhanced --cov-report=html --cov-report=term

test-fast: ## Run tests without slow tests
	uv run pytest -m "not slow"

# 功能測試命令
test-func: ## Run functional tests (standard)
	uv run python -m mcp_feedback_enhanced test

test-web: ## Run Web UI tests (continuous)
	uvx --no-cache --with-editable . mcp-feedback-enhanced test --web

# 維護相關命令
clean: ## Clean up cache and temporary files
	@echo "Cleaning up..."
	@if exist ".mypy_cache" rmdir /s /q ".mypy_cache" 2>nul || true
	@if exist ".ruff_cache" rmdir /s /q ".ruff_cache" 2>nul || true
	@if exist ".pytest_cache" rmdir /s /q ".pytest_cache" 2>nul || true
	@if exist "htmlcov" rmdir /s /q "htmlcov" 2>nul || true
	@if exist "dist" rmdir /s /q "dist" 2>nul || true
	@if exist "build" rmdir /s /q "build" 2>nul || true
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@find . -name "*.pyo" -delete 2>/dev/null || true
	@find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Cleanup completed!"

update-deps: ## Update dependencies
	uv sync --upgrade

# 建置相關命令
build: ## Build the package
	uv build

build-check: ## Check the built package
	uv run twine check dist/*

# 版本發布命令
bump-patch: ## Bump patch version
	uv run bump2version patch

bump-minor: ## Bump minor version
	uv run bump2version minor

bump-major: ## Bump major version
	uv run bump2version major

# 開發工作流程
dev-setup: install-dev install-hooks ## Complete development setup
	@echo "🎉 Development environment setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Run 'make check' to verify everything works"
	@echo "  2. Start coding! Pre-commit hooks will run automatically"
	@echo "  3. Use 'make help' to see all available commands"

# CI 流程模擬
ci: clean install-dev pre-commit-all test ## Simulate CI pipeline locally

# 快速開發命令
quick-check: lint-fix format type-check ## Quick check with auto-fix (recommended for development)

# Windows PowerShell 專用命令
ps-clean: ## PowerShell version of clean (Windows)
	powershell -Command "Get-ChildItem -Path . -Recurse -Name '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; Get-ChildItem -Path . -Recurse -Name '*.pyc' | Remove-Item -Force -ErrorAction SilentlyContinue; @('.mypy_cache', '.ruff_cache', '.pytest_cache', 'htmlcov', 'dist', 'build') | ForEach-Object { if (Test-Path $$_) { Remove-Item $$_ -Recurse -Force } }"

# 完整構建流程
build-all: clean build ## Build complete package
	@echo "🎉 Complete build finished!"

# 測試所有功能
test-all: test test-func ## Run all tests including functional tests
	@echo "✅ All tests completed!"
