#!/usr/bin/env python3
"""
压缩配置管理器
==============

管理 Web UI 的 Gzip 压缩配置和静态文件缓存策略。
支援可配置的压缩参数和性能优化选项。
"""

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompressionConfig:
    """压缩配置类"""

    # Gzip 压缩设定
    minimum_size: int = 1000  # 最小压缩大小（bytes）
    compression_level: int = 6  # 压缩级别 (1-9, 6为平衡点)

    # 缓存设定
    static_cache_max_age: int = 3600  # 静态文件缓存时间（秒）
    api_cache_max_age: int = 0  # API 响应缓存时间（秒，0表示不缓存）

    # 支援的 MIME 类型
    compressible_types: list[str] = field(default_factory=list)

    # 排除的路径
    exclude_paths: list[str] = field(default_factory=list)

    def __post_init__(self):
        """初始化后处理"""
        if not self.compressible_types:
            self.compressible_types = [
                "text/html",
                "text/css",
                "text/javascript",
                "text/plain",
                "application/json",
                "application/javascript",
                "application/xml",
                "application/rss+xml",
                "application/atom+xml",
                "image/svg+xml",
            ]

        if not self.exclude_paths:
            self.exclude_paths = [
                "/ws",  # WebSocket 连接
                "/api/ws",  # WebSocket API
                "/health",  # 健康检查
            ]

    @classmethod
    def from_env(cls) -> "CompressionConfig":
        """从环境变数创建配置"""
        return cls(
            minimum_size=int(os.getenv("MCP_GZIP_MIN_SIZE", "1000")),
            compression_level=int(os.getenv("MCP_GZIP_LEVEL", "6")),
            static_cache_max_age=int(os.getenv("MCP_STATIC_CACHE_AGE", "3600")),
            api_cache_max_age=int(os.getenv("MCP_API_CACHE_AGE", "0")),
        )

    def should_compress(self, content_type: str, content_length: int) -> bool:
        """判断是否应该压缩"""
        if content_length < self.minimum_size:
            return False

        if not content_type:
            return False

        # 检查 MIME 类型
        for mime_type in self.compressible_types:
            if content_type.startswith(mime_type):
                return True

        return False

    def should_exclude_path(self, path: str) -> bool:
        """判断路径是否应该排除压缩"""
        for exclude_path in self.exclude_paths:
            if path.startswith(exclude_path):
                return True
        return False

    def get_cache_headers(self, path: str) -> dict[str, str]:
        """获取缓存头"""
        headers = {}

        if path.startswith("/static/"):
            # 静态文件缓存
            headers["Cache-Control"] = f"public, max-age={self.static_cache_max_age}"
            headers["Expires"] = self._get_expires_header(self.static_cache_max_age)
        elif path.startswith("/api/") and self.api_cache_max_age > 0:
            # API 缓存（如果启用）
            headers["Cache-Control"] = f"public, max-age={self.api_cache_max_age}"
            headers["Expires"] = self._get_expires_header(self.api_cache_max_age)
        else:
            # 其他路径不缓存
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            headers["Pragma"] = "no-cache"
            headers["Expires"] = "0"

        return headers

    def _get_expires_header(self, max_age: int) -> str:
        """生成 Expires 头"""
        from datetime import datetime, timedelta

        expires_time = datetime.utcnow() + timedelta(seconds=max_age)
        return expires_time.strftime("%a, %d %b %Y %H:%M:%S GMT")

    def get_compression_stats(self) -> dict[str, Any]:
        """获取压缩配置统计"""
        return {
            "minimum_size": self.minimum_size,
            "compression_level": self.compression_level,
            "static_cache_max_age": self.static_cache_max_age,
            "compressible_types_count": len(self.compressible_types),
            "exclude_paths_count": len(self.exclude_paths),
            "compressible_types": self.compressible_types,
            "exclude_paths": self.exclude_paths,
        }


class CompressionManager:
    """压缩管理器"""

    def __init__(self, config: CompressionConfig | None = None):
        self.config = config or CompressionConfig.from_env()
        self._stats = {
            "requests_total": 0,
            "requests_compressed": 0,
            "bytes_original": 0,
            "bytes_compressed": 0,
            "compression_ratio": 0.0,
        }

    def update_stats(
        self, original_size: int, compressed_size: int, was_compressed: bool
    ):
        """更新压缩统计"""
        self._stats["requests_total"] += 1
        self._stats["bytes_original"] += original_size

        if was_compressed:
            self._stats["requests_compressed"] += 1
            self._stats["bytes_compressed"] += compressed_size
        else:
            self._stats["bytes_compressed"] += original_size

        # 计算压缩比率
        if self._stats["bytes_original"] > 0:
            self._stats["compression_ratio"] = (
                1 - self._stats["bytes_compressed"] / self._stats["bytes_original"]
            ) * 100

    def get_stats(self) -> dict[str, Any]:
        """获取压缩统计"""
        stats = self._stats.copy()
        stats["compression_percentage"] = (
            self._stats["requests_compressed"]
            / max(self._stats["requests_total"], 1)
            * 100
        )
        return stats

    def reset_stats(self):
        """重置统计"""
        self._stats = {
            "requests_total": 0,
            "requests_compressed": 0,
            "bytes_original": 0,
            "bytes_compressed": 0,
            "compression_ratio": 0.0,
        }


# 全域压缩管理器实例
_compression_manager: CompressionManager | None = None


def get_compression_manager() -> CompressionManager:
    """获取全域压缩管理器实例"""
    global _compression_manager
    if _compression_manager is None:
        _compression_manager = CompressionManager()
    return _compression_manager
