from __future__ import annotations

import asyncio


class ActiveGenerationRegistry:
    """跟踪当前进程内正在执行的生成任务，并支持主动取消。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._draining: set[str] = set()

    def register(self, thread_id: str, task: asyncio.Task[None]) -> None:
        previous = self._tasks.get(thread_id)
        if previous is not None and not previous.done() and previous is not task:
            raise RuntimeError(f"Generation task already active: {thread_id}")
        self._tasks[thread_id] = task
        self._draining.discard(thread_id)

    def begin_draining(self, thread_id: str, task: asyncio.Task[None] | None) -> None:
        """仅执行所有者可以进入收尾；迟到回调不能保护另一次执行。"""
        if task is not None and self._tasks.get(thread_id) is task:
            self._draining.add(thread_id)

    def cancel(self, thread_id: str, owner: asyncio.Task[None] | None = None) -> bool:
        task = self._tasks.get(thread_id)
        if task is None or task.done() or (owner is not None and task is not owner):
            return False
        # 首次取消仍中断模型/图；重复停止不能打断其异步退出和数据库收尾。
        if thread_id not in self._draining and not task.cancelling():
            task.cancel()
        return True

    def clear(self, thread_id: str, task: asyncio.Task[None] | None = None) -> None:
        current = self._tasks.get(thread_id)
        if current is not None and (task is None or current is task):
            self._tasks.pop(thread_id, None)
            self._draining.discard(thread_id)

    def is_active(self, thread_id: str) -> bool:
        task = self._tasks.get(thread_id)
        return task is not None and not task.done()
