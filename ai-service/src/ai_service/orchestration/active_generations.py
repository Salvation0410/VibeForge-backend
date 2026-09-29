from __future__ import annotations

import asyncio


class ActiveGenerationRegistry:
    """跟踪当前进程内正在执行的生成任务，并支持主动取消。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def register(self, thread_id: str, task: asyncio.Task[None]) -> None:
        previous = self._tasks.get(thread_id)
        if previous is not None and not previous.done() and previous is not task:
            raise RuntimeError(f"Generation task already active: {thread_id}")
        self._tasks[thread_id] = task

    def cancel(self, thread_id: str) -> bool:
        task = self._tasks.get(thread_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def clear(self, thread_id: str, task: asyncio.Task[None] | None = None) -> None:
        current = self._tasks.get(thread_id)
        if current is not None and (task is None or current is task):
            self._tasks.pop(thread_id, None)

    def is_active(self, thread_id: str) -> bool:
        task = self._tasks.get(thread_id)
        return task is not None and not task.done()
