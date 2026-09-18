from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ai_service.api.dependencies import create_internal_auth_dependency
from ai_service.api.routes import register_routes
from ai_service.config import Settings, get_settings
from ai_service.infrastructure.checkpoint import (
    CheckpointStore,
    DisabledCheckpoint,
    RedisCheckpoint,
)
from ai_service.infrastructure.spring_tools import SpringToolGateway
from ai_service.models.base import GenerationModel
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow


def create_app(
    *,
    settings: Settings | None = None,
    model: GenerationModel | None = None,
    tool_gateway: Any | None = None,
    checkpoint: CheckpointStore | None = None,
) -> FastAPI:
    """创建并组装 AI 服务。

    可注入模型、工具网关和 checkpoint，便于测试或替换基础设施；未注入时根据
    环境配置创建默认实现。函数保持为 Uvicorn 的稳定工厂入口。
    """

    config = settings or get_settings()
    generation_model = model or OpenAICompatibleModel(config)
    gateway = tool_gateway or SpringToolGateway(
        base_url=str(config.spring_gateway_base_url),
        bearer_token=config.spring_gateway_bearer_token,
    )
    checkpoint_store = checkpoint or (
        RedisCheckpoint(
            config.redis_url,
            required=config.redis_required,
            ttl_seconds=config.checkpoint_ttl_seconds,
        )
        if config.redis_enabled
        else DisabledCheckpoint()
    )
    cancellations = CancellationRegistry()
    workflow = GenerationWorkflow(
        model=generation_model,
        tool_gateway=gateway,
        checkpoint=checkpoint_store,
        cancellations=cancellations,
        settings=config,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """管理 checkpoint 和 HTTP 工具客户端的启动与释放。"""

        await checkpoint_store.start()
        try:
            yield
        finally:
            await checkpoint_store.close()
            close = getattr(gateway, "close", None)
            if close is not None:
                await close()

    app = FastAPI(title="yu-ai-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.state.model = generation_model
    app.state.tool_gateway = gateway
    app.state.checkpoint = checkpoint_store
    app.state.cancellations = cancellations
    app.state.workflow = workflow

    register_routes(
        app,
        generation_model=generation_model,
        workflow=workflow,
        checkpoint_store=checkpoint_store,
        cancellations=cancellations,
        require_internal_auth=create_internal_auth_dependency(config.internal_bearer_token),
    )
    return app
