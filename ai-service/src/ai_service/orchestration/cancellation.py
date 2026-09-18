from __future__ import annotations


class CancellationRegistry:
    """在单进程内保存工作流的协作式取消信号。"""
    def __init__(self):
        self._cancelled: set[str] = set()

    def cancel(self, thread_id: str) -> None:
        """将指定 LangGraph thread 标记为已取消。"""
        self._cancelled.add(thread_id)

    def is_cancelled(self, thread_id: str) -> bool:
        """判断指定 LangGraph thread 是否已收到取消信号。"""
        return thread_id in self._cancelled

    def clear(self, thread_id: str) -> None:
        """清除已结束请求的取消标记。"""
        self._cancelled.discard(thread_id)


class GenerationCancelled(Exception):
    """工作流检测到协作式取消时抛出的内部控制异常。"""

