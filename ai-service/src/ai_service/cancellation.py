from __future__ import annotations


class CancellationRegistry:
    def __init__(self):
        self._cancelled: set[str] = set()

    def cancel(self, thread_id: str) -> None:
        self._cancelled.add(thread_id)

    def is_cancelled(self, thread_id: str) -> bool:
        return thread_id in self._cancelled

    def clear(self, thread_id: str) -> None:
        self._cancelled.discard(thread_id)


class GenerationCancelled(Exception):
    pass

