from __future__ import annotations

import asyncio
from typing import Any

from ai_service.models import EventError, GenerationEvent


class EventEmitter:
    def __init__(self, request_id: str, queue: asyncio.Queue[GenerationEvent | None] | None = None):
        self.request_id = request_id
        self.sequence = 0
        self.events: list[GenerationEvent] = []
        self.queue = queue

    async def emit(
        self,
        event_type: str,
        node: str,
        *,
        data: dict[str, Any] | None = None,
        error: EventError | None = None,
    ) -> GenerationEvent:
        self.sequence += 1
        event = GenerationEvent(
            type=event_type,  # type: ignore[arg-type]
            request_id=self.request_id,
            sequence=self.sequence,
            node=node,
            data=data or {},
            error=error,
        )
        self.events.append(event)
        if self.queue is not None:
            await self.queue.put(event)
        return event

    async def node_status(self, node: str, status: str, **data: Any) -> None:
        await self.emit("node_status", node, data={"status": status, **data})
