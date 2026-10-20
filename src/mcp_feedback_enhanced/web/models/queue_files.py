#!/usr/bin/env python3
"""
钩子用到的本地文件
==================

Cursor 钩子和服务端靠 ``feedback_home()`` 下的几类本地文件配合：

- ``queue/<房间标识>``：标记文件。房间的队列里有排队中的消息时存在，没有时不存在。
  钩子每次工具调用结束都会触发，靠它不用连服务端就能判断「这个对话有没有消息要取」，并读到房间的地址
- ``rooms/<房间标识>``：登记文件。房间存在期间一直在，房间销毁时删除。
  对话的一个回合结束时（stop 钩子）靠它找到房间的地址，向服务端报告
- ``images/<消息标识>/``：消息带的图片。钩子送达只能传文字，图片先存成文件，把路径写进文字交给 AI。
  房间销毁时删掉它送达过的图片目录，其余的超过保留时间后清掉

标记文件和登记文件的内容一样：两行，房间的地址，以及写入这个文件的服务进程号
（进程已经不在时，钩子把文件当作过期的删掉）。
"""

import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from ...debug import web_debug_log as debug_log
from .message_queue import QueuedMessage


# 图片文件保留的时间，过期的在下一次存图片时清掉
IMAGE_RETENTION_SECONDS = 24 * 60 * 60

# 常见图片格式的文件头，用来决定存成什么扩展名
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def feedback_home() -> Path:
    """钩子和服务端共用的本地目录

    环境变量 ``GL_FEEDBACK_HOME`` 可以改位置，钩子脚本读的是同一个变量。
    """
    configured = os.environ.get("GL_FEEDBACK_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "gl-mcp-feedback"


class RoomHookFile:
    """一个房间的钩子文件：文件名是房间标识，内容两行——房间的地址、写文件的服务进程号"""

    # 文件放在 feedback_home() 下的哪个子目录，由子类指定
    directory = ""

    def __init__(self, room_id: str, room_url: Callable[[], str]):
        """
        Args:
            room_id: 房间标识，也是文件名；调用方保证只含字母、数字、下划线和连字符。
            room_url: 返回房间地址的函数（不带结尾的斜杠），写文件时才调用，
                这样服务端的端口在房间创建之后才确定也没问题。
        """
        self.room_id = room_id
        self._room_url = room_url
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """文件的位置"""
        return feedback_home() / self.directory / self.room_id

    def clear(self) -> None:
        """删除文件"""
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as e:
                debug_log(f"删除钩子文件失败: {e}")

    def _write(self) -> None:
        """原子地写入文件，钩子不会读到写了一半的内容"""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        temp_path.write_text(f"{self._room_url()}\n{os.getpid()}\n", encoding="utf-8")
        temp_path.replace(path)


class QueueMarker(RoomHookFile):
    """一个房间的标记文件：队列里有排队中的消息时存在，没有时不存在

    钩子往房间地址下面的 ``/api/queue/take`` 取消息。
    """

    directory = "queue"

    def sync(self, pending_count: Callable[[], int]) -> None:
        """按队列此刻的情况写入或删除标记文件

        Args:
            pending_count: 返回排队中消息数量的函数。它在锁里调用：几个线程同时同步时，
                最后拿到锁的那次读到的一定是最新数量，文件最终和队列一致。
        """
        with self._lock:
            try:
                if pending_count() > 0:
                    self._write()
                else:
                    self.path.unlink(missing_ok=True)
            except OSError as e:
                debug_log(f"同步排队标记文件失败: {e}")


class RoomRegistration(RoomHookFile):
    """一个房间的登记文件：房间存在期间一直在

    stop 钩子往房间地址下面的 ``/api/turn-ended`` 报告对话的一个回合结束了。
    """

    directory = "rooms"

    def register(self) -> None:
        """写入登记文件，服务的地址变了之后可以再调用一次来更新"""
        with self._lock:
            try:
                self._write()
            except OSError as e:
                debug_log(f"写入房间登记文件失败: {e}")


def save_message_images(messages: list[QueuedMessage]) -> dict[str, list[Path]]:
    """把消息附带的图片存成本地文件

    Args:
        messages: 钩子取走的消息，图片原始数据还在。

    Returns:
        消息标识到图片文件路径（按图片顺序）的对应；没有图片、或图片一张都没存成的消息不在其中。
    """
    prune_message_images()
    root = feedback_home() / "images"
    saved_paths: dict[str, list[Path]] = {}
    for message in messages:
        if not message.images:
            continue
        directory = root / message.message_id
        paths: list[Path] = []
        for index, image in enumerate(message.images, 1):
            data = image["data"]
            path = directory / f"{index}{_image_extension(data)}"
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError as e:
                debug_log(f"保存排队消息的图片失败: {e}")
                continue
            paths.append(path)
        if paths:
            saved_paths[message.message_id] = paths
    return saved_paths


def delete_message_images(directories: Iterable[Path]) -> None:
    """删掉存过图片的目录

    只删 ``images/`` 下的一级目录，其他路径一律不碰。

    Args:
        directories: ``save_message_images`` 存图片的目录，也就是图片文件所在的目录。
    """
    root = feedback_home() / "images"
    for directory in directories:
        if directory.parent != root:
            debug_log(f"不在图片目录下，不删除: {directory}")
            continue
        shutil.rmtree(directory, ignore_errors=True)


def prune_message_images(max_age_seconds: float = IMAGE_RETENTION_SECONDS) -> None:
    """清掉超过保留时间的图片目录

    Args:
        max_age_seconds: 目录的最后修改时间距现在超过这么多秒就删除。
    """
    root = feedback_home() / "images"
    if not root.is_dir():
        return
    deadline = time.time() - max_age_seconds
    for directory in root.iterdir():
        try:
            if directory.is_dir() and directory.stat().st_mtime < deadline:
                shutil.rmtree(directory, ignore_errors=True)
        except OSError as e:
            debug_log(f"清理过期的排队图片失败: {e}")


def _image_extension(data: bytes) -> str:
    """按文件头判断图片格式，认不出来时用 .png"""
    for signature, extension in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"
