"""
測試輔助工具模組

包含測試中使用的輔助類和工具函數。
"""

import asyncio
from collections.abc import Callable


async def wait_until(condition: Callable[[], bool], timeout: float = 10) -> None:
    """等条件成立，超时就让测试失败"""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)
