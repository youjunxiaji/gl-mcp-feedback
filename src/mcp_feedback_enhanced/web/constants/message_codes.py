"""
统一的消息代码定义

这个模组定义了所有后端使用的消息代码常量。
前端根据这些代码识别通知类型。

使用方式：
    from ..constants import MessageCodes, get_message_code

    # 使用常量
    code = MessageCodes.SESSION_FEEDBACK_SUBMITTED

    # 或使用辅助函数
    code = get_message_code("SESSION_FEEDBACK_SUBMITTED")
"""


class MessageCodes:
    """消息代码常量类"""

    # ========== 系统相关 ==========
    SYSTEM_CONNECTION_ESTABLISHED = "system.connectionEstablished"
    SYSTEM_CONNECTION_LOST = "system.connectionLost"
    SYSTEM_CONNECTION_RECONNECTING = "system.connectionReconnecting"
    SYSTEM_CONNECTION_RECONNECTED = "system.connectionReconnected"
    SYSTEM_CONNECTION_FAILED = "system.connectionFailed"
    SYSTEM_WEBSOCKET_ERROR = "system.websocketError"
    SYSTEM_WEBSOCKET_READY = "system.websocketReady"
    SYSTEM_MEMORY_PRESSURE = "system.memoryPressure"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_PROCESS_KILLED = "system.processKilled"
    SYSTEM_CONNECTION_REPLACED = "system.connectionReplaced"

    # ========== 会话相关 ==========
    SESSION_NO_ACTIVE = "session.noActiveSession"
    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    SESSION_EXPIRED = "session.expired"
    SESSION_TIMEOUT = "session.timeout"
    SESSION_CLEANED = "session.cleaned"
    SESSION_FEEDBACK_SUBMITTED = "session.feedbackSubmitted"
    SESSION_MANUAL_CLEANUP = "session.manualCleanup"
    SESSION_ERROR_CLEANUP = "session.errorCleanup"

    # ========== 消息队列相关 ==========
    QUEUE_MESSAGE_QUEUED = "queue.messageQueued"
    QUEUE_FULL = "queue.full"
    QUEUE_MESSAGE_UPDATED = "queue.messageUpdated"
    QUEUE_MESSAGE_CANCELLED = "queue.messageCancelled"
    QUEUE_MESSAGE_NOT_PENDING = "queue.messageNotPending"
    QUEUE_MESSAGE_EMPTY = "queue.messageEmpty"

    # ========== 错误相关 ==========
    ERROR_GENERIC = "error.generic"
    ERROR_NETWORK = "error.network"
    ERROR_SERVER = "error.server"
    ERROR_TIMEOUT = "error.timeout"
    ERROR_INVALID_INPUT = "error.invalidInput"
    ERROR_OPERATION_FAILED = "error.operationFailed"
    ERROR_GET_SESSIONS_FAILED = "error.getSessionsFailed"
    ERROR_RESOURCE_CLEANUP = "error.resourceCleanup"
    ERROR_PROCESSING = "error.processing"

    # ========== 档案相关 ==========
    FILE_UPLOAD_SUCCESS = "file.uploadSuccess"
    FILE_UPLOAD_FAILED = "file.uploadFailed"
    FILE_SIZE_TOO_LARGE = "file.sizeTooLarge"
    FILE_TYPE_NOT_SUPPORTED = "file.typeNotSupported"
    FILE_PROCESSING = "file.processing"
    FILE_REMOVED = "file.removed"

    # ========== 提示词相关 ==========
    PROMPT_SAVED = "prompt.saved"
    PROMPT_DELETED = "prompt.deleted"
    PROMPT_APPLIED = "prompt.applied"
    PROMPT_IMPORT_SUCCESS = "prompt.importSuccess"
    PROMPT_IMPORT_FAILED = "prompt.importFailed"
    PROMPT_EXPORT_SUCCESS = "prompt.exportSuccess"
    PROMPT_VALIDATION_FAILED = "prompt.validationFailed"


# 向后兼容的映射表（从旧的 key 到新的常量名称）
LEGACY_KEY_MAPPING = {
    # feedback_session.py 的旧 key
    "FEEDBACK_SUBMITTED": "SESSION_FEEDBACK_SUBMITTED",
    "SESSION_CLEANUP": "SESSION_CLEANED",
    "TIMEOUT_CLEANUP": "SESSION_TIMEOUT",
    "EXPIRED_CLEANUP": "SESSION_EXPIRED",
    "MEMORY_PRESSURE_CLEANUP": "SYSTEM_MEMORY_PRESSURE",
    "MANUAL_CLEANUP": "SESSION_MANUAL_CLEANUP",
    "ERROR_CLEANUP": "SESSION_ERROR_CLEANUP",
    "SHUTDOWN_CLEANUP": "SYSTEM_SHUTDOWN",
    "PROCESS_KILLED": "SYSTEM_PROCESS_KILLED",
    "RESOURCE_CLEANUP_ERROR": "ERROR_RESOURCE_CLEANUP",
    "PROCESSING_ERROR": "ERROR_PROCESSING",
    "WEBSOCKET_READY": "SYSTEM_WEBSOCKET_READY",
    # main_routes.py 的旧 key
    "no_active_session": "SESSION_NO_ACTIVE",
    "websocket_connected": "SYSTEM_CONNECTION_ESTABLISHED",
    "new_session_created": "SESSION_CREATED",
    "get_sessions_failed": "ERROR_GET_SESSIONS_FAILED",
    "connection_replaced": "SYSTEM_CONNECTION_REPLACED",
}


def get_message_code(key: str) -> str:
    """
    获取消息代码

    支援三种输入方式：
    1. 直接使用常量名称：get_message_code("SESSION_FEEDBACK_SUBMITTED")
    2. 使用旧的 key（向后兼容）：get_message_code("FEEDBACK_SUBMITTED")
    3. 使用小写的旧 key（向后兼容）：get_message_code("feedback_submitted")

    Args:
        key: 消息 key 或常量名称

    Returns:
        消息代码字串（例如："session.feedbackSubmitted"）
    """
    # 尝试直接从 MessageCodes 获取
    if hasattr(MessageCodes, key):
        return str(getattr(MessageCodes, key))

    # 尝试从映射表获取（支援大写和小写）
    upper_key = key.upper()
    if upper_key in LEGACY_KEY_MAPPING:
        constant_name = LEGACY_KEY_MAPPING[upper_key]
        if hasattr(MessageCodes, constant_name):
            return str(getattr(MessageCodes, constant_name))

    # 如果是小写的 key，也尝试映射
    if key in LEGACY_KEY_MAPPING:
        constant_name = LEGACY_KEY_MAPPING[key]
        if hasattr(MessageCodes, constant_name):
            return str(getattr(MessageCodes, constant_name))

    # 如果都找不到，返回一个默认格式
    return f"unknown.{key}"
