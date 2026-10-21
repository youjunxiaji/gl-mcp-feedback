"""
统一错误处理框架
================

提供统一的错误处理机制，包括：
- 错误类型分类
- 用户友好错误信息
- 错误上下文记录
- 解决方案建议

注意：此模组不会影响 JSON RPC 通信，所有错误处理都在应用层进行。
"""

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


class FeedbackTimeoutError(TimeoutError):
    """用户在等待时限内没有回复。

    不是故障，和网络、套接字这类系统超时区分开：AI 收到的是一条让它再次提问的提示。

    Args:
        waited_seconds: 实际等待的秒数。
    """

    def __init__(self, waited_seconds: float) -> None:
        super().__init__(f"等待用户反馈超时（{waited_seconds:g}秒）")
        self.waited_seconds = waited_seconds


class ErrorHandler:
    """统一错误处理器"""

    # 错误类型到用户友好信息的映射
    _ERROR_MESSAGES: dict[ErrorType, str] = {
        ErrorType.NETWORK: "网络连接出现问题",
        ErrorType.FILE_IO: "文件读写出现问题",
        ErrorType.PROCESS: "进程执行出现问题",
        ErrorType.TIMEOUT: "操作超时",
        ErrorType.USER_CANCEL: "用户取消了操作",
        ErrorType.SYSTEM: "系统出现问题",
        ErrorType.PERMISSION: "权限不足",
        ErrorType.VALIDATION: "数据验证失败",
        ErrorType.DEPENDENCY: "依赖组件出现问题",
        ErrorType.CONFIGURATION: "配置出现问题",
    }

    # 错误解决建议
    _ERROR_SOLUTIONS: dict[ErrorType, list[str]] = {
        ErrorType.NETWORK: [
            "检查网络连接是否正常",
            "确认防火墙设置",
            "尝试重新启动应用程序",
        ],
        ErrorType.FILE_IO: ["检查文件是否存在", "确认文件权限", "检查磁盘空间是否足够"],
        ErrorType.PROCESS: [
            "检查进程是否正在运行",
            "确认系统资源是否足够",
            "尝试重新启动相关服务",
        ],
        ErrorType.TIMEOUT: ["增加超时时间设置", "检查网络延迟", "稍后重试操作"],
        ErrorType.PERMISSION: [
            "以管理员身份运行",
            "检查文件/目录权限",
            "联系系统管理员",
        ],
    }

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

        # 获取用户友好的错误信息
        user_message = ErrorHandler._ERROR_MESSAGES.get(error_type, "发生未知错误")

        # 构建完整的错误信息
        parts = [f"❌ {user_message}"]

        # 添加上下文信息
        if context:
            if context.get("operation"):
                parts.append(f"操作：{context['operation']}")

            if context.get("file_path"):
                parts.append(f"文件：{context['file_path']}")

        # 添加技术细节（如果需要）
        if include_technical:
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
        return list(ErrorHandler._ERROR_SOLUTIONS.get(error_type, []))

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
