from __future__ import annotations

import hmac
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from ai_service.cancellation import CancellationRegistry
from ai_service.checkpoint import CheckpointStore, DisabledCheckpoint, RedisCheckpoint
from ai_service.config import Settings, get_settings
from ai_service.llm import GenerationModel, OpenAICompatibleModel
from ai_service.models import (
    CancelRequest,
    CancelResponse,
    CodeGenType,
    GenerationRequest,
    RouteRequest,
    RouteResponse,
)
from ai_service.tools import SpringToolGateway
from ai_service.workflow import GenerationWorkflow


def create_app(
    *,
    settings: Settings | None = None,
    model: GenerationModel | None = None,
    tool_gateway: Any | None = None,
    checkpoint: CheckpointStore | None = None,
) -> FastAPI:
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

    async def require_internal_auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {config.internal_bearer_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/internal/v1/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/internal/v1/health/ready")
    async def ready():
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
        raw_type = await generation_model.route(body.prompt)
        try:
            code_gen_type = CodeGenType(raw_type)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"Model returned unsupported route: {raw_type}") from exc
        return RouteResponse(
            request_id=body.request_id or str(uuid.uuid4()),
            code_gen_type=code_gen_type,
        )

    @app.post(
        "/internal/v1/generations:stream",
        dependencies=[Depends(require_internal_auth)],
    )
    async def generate(body: GenerationRequest, request: Request) -> StreamingResponse:
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
        cancellations.cancel(f"{body.app_id}:{request_id}")
        return CancelResponse(request_id=request_id)

    return app
