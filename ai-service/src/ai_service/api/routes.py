from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ai_service.api.schemas import (
    CancelRequest,
    CancelResponse,
    CodeGenType,
    GenerationRequest,
    RouteRequest,
    RouteResponse,
)
from ai_service.infrastructure.checkpoint import CheckpointStore
from ai_service.models.base import GenerationModel
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow


def register_routes(
    app: FastAPI,
    *,
    generation_model: GenerationModel,
    workflow: GenerationWorkflow,
    checkpoint_store: CheckpointStore,
    cancellations: CancellationRegistry,
    require_internal_auth: Any,
) -> None:
    """注册健康检查和内部生成接口，所有运行依赖由应用工厂显式传入。"""

    @app.get("/internal/v1/health/live")
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """报告进程存活状态，不检查外部依赖。"""

        return {"status": "live"}

    @app.get("/internal/v1/health/ready")
    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        """检查 checkpoint 存储是否可用，并据此返回就绪状态。"""

        ready_state = await checkpoint_store.ping()
        body = {"status": "ready" if ready_state else "not_ready", "checkpoint": ready_state}
        return JSONResponse(body, status_code=200 if ready_state else 503)

    @app.post(
        "/internal/v1/route",
        response_model=RouteResponse,
        response_model_by_alias=True,
        dependencies=[Depends(require_internal_auth)],
    )
    async def route(body: RouteRequest) -> RouteResponse:
        """调用模型确定代码生成类型，并拒绝模型返回的未知类型。"""

        raw_type = await generation_model.route(body.prompt)
        try:
            code_gen_type = CodeGenType(raw_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Model returned unsupported route: {raw_type}",
            ) from exc
        return RouteResponse(
            request_id=body.request_id or str(uuid.uuid4()),
            code_gen_type=code_gen_type,
        )

    @app.post(
        "/internal/v1/generations:stream",
        dependencies=[Depends(require_internal_auth)],
    )
    async def generate(body: GenerationRequest, request: Request) -> StreamingResponse:
        """以 NDJSON 流返回工作流事件，并在客户端断连时发出取消信号。"""

        thread_id = f"{body.app_id}:{body.request_id}"

        async def stream():
            completed = False
            try:
                async for event in workflow.stream(body):
                    if await request.is_disconnected():
                        cancellations.cancel(thread_id)
                        break
                    yield json.dumps(
                        event.model_dump(by_alias=True, mode="json", exclude_none=True),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ) + "\n"
                    if event.type in {"completed", "failed"}:
                        completed = True
            finally:
                if not completed:
                    cancellations.cancel(thread_id)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post(
        "/internal/v1/generations/{request_id}:cancel",
        response_model=CancelResponse,
        response_model_by_alias=True,
        status_code=202,
        dependencies=[Depends(require_internal_auth)],
    )
    async def cancel(request_id: str, body: CancelRequest) -> CancelResponse:
        """标记指定应用和请求对应的工作流为已取消。"""

        cancellations.cancel(f"{body.app_id}:{request_id}")
        return CancelResponse(request_id=request_id)
