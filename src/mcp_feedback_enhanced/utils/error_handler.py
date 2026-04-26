"""
统一错误处理框架
================

提供统一的错误处理机制，包括：
- 错误类型分类
- 用户友好错误信息
- 错误上下文记录
- 解决方案建议
- 国际化支持

注意：此模组不会影响 JSON RPC 通信，所有错误处理都在应用层进行。
"""

import os
import time
import traceback
from enum import Enum
from typing import Any

from ..debug import debug_log


class ErrorType(Enum):
    """错误类型枚举"""

    NETWORK = "network"  # 网络相关错误
    FILE_IO = "file_io"  # 文件 I/O 错误
    PROCESS = "process"  # 进程相关错误
    TIMEOUT = "timeout"  # 超时错误
    USER_CANCEL = "user_cancel"  # 用户取消操作
    SYSTEM = "system"  # 系统错误
    PERMISSION = "permission"  # 权限错误
    VALIDATION = "validation"  # 数据验证错误
    DEPENDENCY = "dependency"  # 依赖错误
    CONFIGURATION = "config"  # 配置错误


class ErrorSeverity(Enum):
    """错误严重程度"""

    LOW = "low"  # 低：不影响核心功能
    MEDIUM = "medium"  # 中：影响部分功能
    HIGH = "high"  # 高：影响核心功能
    CRITICAL = "critical"  # 严重：系统无法正常运行


class ErrorHandler:
    """统一错误处理器"""

    # 错误类型到用户友好信息的映射
    _ERROR_MESSAGES = {
        ErrorType.NETWORK: {
            "zh-TW": "网络连接出现问题",
            "zh-CN": "网络连接出现问题",
            "en": "Network connection issue",
        },
        ErrorType.FILE_IO: {
            "zh-TW": "文件读写出现问题",
            "zh-CN": "文件读写出现问题",
            "en": "File read/write issue",
        },
        ErrorType.PROCESS: {
            "zh-TW": "进程执行出现问题",
            "zh-CN": "进程执行出现问题",
            "en": "Process execution issue",
        },
        ErrorType.TIMEOUT: {
            "zh-TW": "操作超时",
            "zh-CN": "操作超时",
            "en": "Operation timeout",
        },
        ErrorType.USER_CANCEL: {
            "zh-TW": "用户取消了操作",
            "zh-CN": "用户取消了操作",
            "en": "User cancelled the operation",
        },
        ErrorType.SYSTEM: {
            "zh-TW": "系统出现问题",
            "zh-CN": "系统出现问题",
            "en": "System issue",
        },
        ErrorType.PERMISSION: {
            "zh-TW": "权限不足",
            "zh-CN": "权限不足",
            "en": "Insufficient permissions",
        },
        ErrorType.VALIDATION: {
            "zh-TW": "数据验证失败",
            "zh-CN": "数据验证失败",
            "en": "Data validation failed",
        },
        ErrorType.DEPENDENCY: {
            "zh-TW": "依赖组件出现问题",
            "zh-CN": "依赖组件出现问题",
            "en": "Dependency issue",
        },
        ErrorType.CONFIGURATION: {
            "zh-TW": "配置出现问题",
            "zh-CN": "配置出现问题",
            "en": "Configuration issue",
        },
    }

    # 错误解决建议
    _ERROR_SOLUTIONS = {
        ErrorType.NETWORK: {
            "zh-TW": ["检查网络连接是否正常", "确认防火墙设置", "尝试重新启动应用程序"],
            "zh-CN": ["检查网络连接是否正常", "确认防火墙设置", "尝试重新启动应用程序"],
            "en": [
                "Check network connection",
                "Verify firewall settings",
                "Try restarting the application",
            ],
        },
        ErrorType.FILE_IO: {
            "zh-TW": ["检查文件是否存在", "确认文件权限", "检查磁盘空间是否足够"],
            "zh-CN": ["检查文件是否存在", "确认文件权限", "检查磁盘空间是否足够"],
            "en": [
                "Check if file exists",
                "Verify file permissions",
                "Check available disk space",
            ],
        },
        ErrorType.PROCESS: {
            "zh-TW": [
                "检查进程是否正在运行",
                "确认系统资源是否足够",
                "尝试重新启动相关服务",
            ],
            "zh-CN": [
                "检查进程是否正在运行",
                "确认系统资源是否足够",
                "尝试重新启动相关服务",
            ],
            "en": [
                "Check if process is running",
                "Verify system resources",
                "Try restarting related services",
            ],
        },
        ErrorType.TIMEOUT: {
            "zh-TW": ["增加超时时间设置", "检查网络延迟", "稍后重试操作"],
            "zh-CN": ["增加超时时间设置", "检查网络延迟", "稍后重试操作"],
            "en": [
                "Increase timeout settings",
                "Check network latency",
                "Retry the operation later",
            ],
        },
        ErrorType.PERMISSION: {
            "zh-TW": ["以管理员身份运行", "检查文件/目录权限", "联系系统管理员"],
            "zh-CN": ["以管理员身份运行", "检查文件/目录权限", "联系系统管理员"],
            "en": [
                "Run as administrator",
                "Check file/directory permissions",
                "Contact system administrator",
            ],
        },
    }

    @staticmethod
    def get_current_language() -> str:
        """获取当前语言设置"""
        try:
            # 尝试从 i18n 模组获取当前语言
            from ..i18n import get_i18n_manager

            return get_i18n_manager().get_current_language()
        except Exception:
            # 回退到环境变数或默认语言
            return os.getenv("MCP_LANGUAGE", "zh-TW")

    @staticmethod
    def get_i18n_error_message(error_type: ErrorType) -> str:
        """从国际化系统获取错误信息"""
        try:
            from ..i18n import get_i18n_manager

            i18n = get_i18n_manager()
            key = f"errors.types.{error_type.value}"
            message = i18n.t(key)
            # 如果返回的是键本身，说明没有找到翻译，使用回退
            if message == key:
                raise Exception("Translation not found")
            return message
        except Exception:
            # 回退到内建映射
            language = ErrorHandler.get_current_language()
            error_messages = ErrorHandler._ERROR_MESSAGES.get(error_type, {})
            return error_messages.get(
                language, error_messages.get("zh-TW", "发生未知错误")
            )

    @staticmethod
    def get_i18n_error_solutions(error_type: ErrorType) -> list[str]:
        """从国际化系统获取错误解决方案"""
        try:
            from ..i18n import get_i18n_manager

            i18n = get_i18n_manager()
            key = f"errors.solutions.{error_type.value}"
            i18n_result = i18n.t(key)

            # 修复类型推断问题 - 使用 Any 类型并明确检查
            from typing import Any

            result: Any = i18n_result

            # 检查是否为列表类型且非空
            if isinstance(result, list) and len(result) > 0:
                return result

            # 如果不是列表或为空，使用回退
            raise Exception("Solutions not found or invalid format")
        except Exception:
            # 回退到内建映射
            language = ErrorHandler.get_current_language()
            solutions_dict = ErrorHandler._ERROR_SOLUTIONS.get(error_type, {})
            return solutions_dict.get(language, solutions_dict.get("zh-TW", []))

    @staticmethod
    def classify_error(error: Exception) -> ErrorType:
        """
        根据异常类型自动分类错误

        Args:
            error: Python 异常对象

        Returns:
            ErrorType: 错误类型
        """
        error_name = type(error).__name__
        error_message = str(error).lower()

        # 超时错误（优先检查，避免被网络错误覆盖）
        if "timeout" in error_name.lower() or "timeout" in error_message:
            return ErrorType.TIMEOUT

        # 权限错误（优先检查，避免被文件错误覆盖）
        if "permission" in error_name.lower():
            return ErrorType.PERMISSION
        if any(
            keyword in error_message
            for keyword in ["permission denied", "access denied", "forbidden"]
        ):
            return ErrorType.PERMISSION

        # 网络相关错误
        if any(
            keyword in error_name.lower()
            for keyword in ["connection", "network", "socket"]
        ):
            return ErrorType.NETWORK
        if any(
            keyword in error_message for keyword in ["connection", "network", "socket"]
        ):
            return ErrorType.NETWORK

        # 文件 I/O 错误
        if any(
            keyword in error_name.lower() for keyword in ["file", "ioerror"]
        ):  # 使用更精确的匹配
            return ErrorType.FILE_IO
        if any(
            keyword in error_message
            for keyword in ["file", "directory", "no such file"]
        ):
            return ErrorType.FILE_IO

        # 进程相关错误
        if any(keyword in error_name.lower() for keyword in ["process", "subprocess"]):
            return ErrorType.PROCESS
        if any(
            keyword in error_message for keyword in ["process", "command", "executable"]
        ):
            return ErrorType.PROCESS

        # 验证错误
        if any(
            keyword in error_name.lower() for keyword in ["validation", "value", "type"]
        ):
            return ErrorType.VALIDATION

        # 配置错误
        if any(
            keyword in error_message for keyword in ["config", "setting", "environment"]
        ):
            return ErrorType.CONFIGURATION

        # 默认为系统错误
        return ErrorType.SYSTEM

    @staticmethod
    def format_user_error(
        error: Exception,
        error_type: ErrorType | None = None,
        context: dict[str, Any] | None = None,
        include_technical: bool = False,
    ) -> str:
        """
        将技术错误转换为用户友好的错误信息

        Args:
            error: Python 异常对象
            error_type: 错误类型（可选，会自动分类）
            context: 错误上下文信息
            include_technical: 是否包含技术细节

        Returns:
            str: 用户友好的错误信息
        """
        # 自动分类错误类型
        if error_type is None:
            error_type = ErrorHandler.classify_error(error)

        # 获取当前语言
        language = ErrorHandler.get_current_language()

        # 获取用户友好的错误信息（优先使用国际化系统）
        user_message = ErrorHandler.get_i18n_error_message(error_type)

        # 构建完整的错误信息
        parts = [f"❌ {user_message}"]

        # 添加上下文信息
        if context:
            if context.get("operation"):
                if language == "en":
                    parts.append(f"Operation: {context['operation']}")
                else:
                    parts.append(f"操作：{context['operation']}")

            if context.get("file_path"):
                if language == "en":
                    parts.append(f"File: {context['file_path']}")
                else:
                    parts.append(f"文件：{context['file_path']}")

        # 添加技术细节（如果需要）
        if include_technical:
            if language == "en":
                parts.append(f"Technical details: {type(error).__name__}: {error!s}")
            else:
                parts.append(f"技术细节：{type(error).__name__}: {error!s}")

        return "\n".join(parts)

    @staticmethod
    def get_error_solutions(error_type: ErrorType) -> list[str]:
        """
        获取错误解决建议

        Args:
            error_type: 错误类型

        Returns:
            List[str]: 解决建议列表
        """
        return ErrorHandler.get_i18n_error_solutions(error_type)

    @staticmethod
    def log_error_with_context(
        error: Exception,
        context: dict[str, Any] | None = None,
        error_type: ErrorType | None = None,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    ) -> str:
        """
        记录带上下文的错误信息（不影响 JSON RPC）

        Args:
            error: Python 异常对象
            context: 错误上下文信息
            error_type: 错误类型
            severity: 错误严重程度

        Returns:
            str: 错误 ID，用于追踪
        """
        # 生成错误 ID
        error_id = f"ERR_{int(time.time())}_{id(error) % 10000}"

        # 自动分类错误
        if error_type is None:
            error_type = ErrorHandler.classify_error(error)

        # 错误记录已通过 debug_log 输出，无需额外存储

        # 记录到调试日志（不影响 JSON RPC）
        debug_log(f"错误记录 [{error_id}]: {error_type.value} - {error!s}")

        if context:
            debug_log(f"错误上下文 [{error_id}]: {context}")

        # 对于严重错误，记录完整堆栈跟踪
        if severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            debug_log(f"错误堆栈 [{error_id}]:\n{traceback.format_exc()}")

        return error_id

    @staticmethod
    def create_error_response(
        error: Exception,
        context: dict[str, Any] | None = None,
        error_type: ErrorType | None = None,
        include_solutions: bool = True,
        for_user: bool = True,
    ) -> dict[str, Any]:
        """
        创建标准化的错误响应

        Args:
            error: Python 异常对象
            context: 错误上下文
            error_type: 错误类型
            include_solutions: 是否包含解决建议
            for_user: 是否为用户界面使用

        Returns:
            Dict[str, Any]: 标准化错误响应
        """
        # 自动分类错误
        if error_type is None:
            error_type = ErrorHandler.classify_error(error)

        # 记录错误
        error_id = ErrorHandler.log_error_with_context(error, context, error_type)

        # 构建响应
        response = {
            "success": False,
            "error_id": error_id,
            "error_type": error_type.value,
            "message": ErrorHandler.format_user_error(
                error, error_type, context, include_technical=not for_user
            ),
        }

        # 添加解决建议
        if include_solutions:
            solutions = ErrorHandler.get_error_solutions(error_type)
            response["solutions"] = solutions  # 即使为空列表也添加

        # 添加上下文（仅用于调试）
        if context and not for_user:
            response["context"] = context

        return response
