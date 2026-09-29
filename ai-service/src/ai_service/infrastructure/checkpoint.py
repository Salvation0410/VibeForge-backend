from __future__ import annotations

from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver


class CheckpointStore(Protocol):
    """工作流依赖的 checkpoint 生命周期与持久化协议。"""

    available: bool

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def save(self, thread_id: str, state: dict[str, Any]) -> None: ...

    async def cleanup_graph(self, thread_id: str) -> None: ...

    async def ping(self) -> bool: ...

    def get_graph_saver(self) -> BaseCheckpointSaver | None: ...


class DisabledCheckpoint:
    """用于测试或明确禁用场景，不声明具备状态恢复能力。"""

    available = True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        return None

    async def cleanup_graph(self, thread_id: str) -> None:
        return None

    async def ping(self) -> bool:
        return True

    def get_graph_saver(self) -> BaseCheckpointSaver | None:
        return None
