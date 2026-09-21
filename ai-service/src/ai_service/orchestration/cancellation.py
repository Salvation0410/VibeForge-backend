from __future__ import annotations

import time


class CancellationRegistry:
    """在单进程内保存工作流的协作式取消信号。"""
    def __init__(self, ttl_seconds: int = 3600):
        self._cancelled: dict[str, float] = {}
        self._ttl_seconds = ttl_seconds

    def cancel(self, thread_id: str) -> None:
        """将指定 LangGraph thread 标记为已取消，并淘汰过期的孤立标记。"""
        now = time.monotonic()
        self._prune(now)
        self._cancelled[thread_id] = now + self._ttl_seconds

    def is_cancelled(self, thread_id: str) -> bool:
        """判断指定 LangGraph thread 是否已收到且尚未过期的取消信号。"""
        now = time.monotonic()
        self._prune(now)
        return self._cancelled.get(thread_id, 0) > now

    def clear(self, thread_id: str) -> None:
        """清除已结束请求的取消标记。"""
        self._cancelled.pop(thread_id, None)

    def _prune(self, now: float) -> None:
        """删除超过 TTL 且没有工作流消费的取消标记，限制长期内存占用。"""
        expired = [thread_id for thread_id, deadline in self._cancelled.items() if deadline <= now]
        for thread_id in expired:
            self._cancelled.pop(thread_id, None)


class GenerationCancelled(Exception):
    """工作流检测到协作式取消时抛出的内部控制异常。"""

