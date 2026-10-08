"""为现有代码生成工作流提供 LangGraph Studio 图适配器。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import DisabledCheckpoint
from ai_service.infrastructure.spring_tools import SpringToolGateway
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.orchestration.active_generations import ActiveGenerationRegistry
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.workflow import GenerationWorkflow


@asynccontextmanager
async def graph(config: RunnableConfig) -> AsyncIterator[object]:
    """每次调用创建独立事件与终态；Agent Server 管理自己的 checkpoint。"""
    settings = Settings()
    model = await asyncio.to_thread(OpenAICompatibleModel, settings)
    gateway = SpringToolGateway(
        base_url=str(settings.spring_gateway_base_url),
        bearer_token=settings.spring_gateway_bearer_token,
    )
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=gateway,
        checkpoint=DisabledCheckpoint(),
        cancellations=CancellationRegistry(),
        active_generations=ActiveGenerationRegistry(),
        settings=settings,
    )
    run_id = str(config.get("configurable", {}).get("run_id") or uuid4())
    thread_id = str(config.get("configurable", {}).get("thread_id") or run_id)
    try:
        yield workflow._build_graph(
            EventEmitter(run_id), thread_id, {"published": False, "completed": False}
        )
    finally:
        await gateway.close()
