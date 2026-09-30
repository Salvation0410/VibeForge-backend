from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from ai_service.api.schemas import (
    CancelRequest,
    CancelResponse,
    CodeGenType,
    CustomerServiceAnswerRequest,
    CustomerServiceAnswerResponse,
    CustomerServiceSourceResponse,
    GenerationRequest,
    KnowledgeDeleteRequest,
    KnowledgeEtlRequest,
    KnowledgeEtlResponse,
    KnowledgeRebuildRequest,
    KnowledgeRebuildResponse,
    RouteRequest,
    RouteResponse,
)
from ai_service.infrastructure.checkpoint import CheckpointStore
from ai_service.infrastructure.knowledge_download import KnowledgeDownloadError
from ai_service.infrastructure.milvus_knowledge import (
    KnowledgeMutationLease,
    MilvusKnowledgeError,
)
from ai_service.models.base import GenerationModel
from ai_service.models.embeddings import EmbeddingOutputError
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.active_generations import ActiveGenerationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow
from ai_service.orchestration.document_etl import DocumentETLError
from ai_service.orchestration.customer_service_rag import CustomerServiceRagError


_CUSTOMER_SERVICE_ERROR_STATUS = {
    "CUSTOMER_SERVICE_RAG_DISABLED": 503,
    "KNOWLEDGE_STALE_VERSION": 409,
    "KNOWLEDGE_VERSION_CONFLICT": 409,
    "KNOWLEDGE_DOCUMENT_DISABLED": 409,
    "KNOWLEDGE_MUTATION_LEASE_REQUIRED": 409,
    "KNOWLEDGE_MUTATION_LEASE_INVALID": 409,
    "KNOWLEDGE_DOWNLOAD_INVALID_REQUEST": 400,
    "KNOWLEDGE_DOWNLOAD_TARGET_REJECTED": 400,
    "KNOWLEDGE_DOWNLOAD_HASH_MISMATCH": 422,
    "KNOWLEDGE_DOCUMENT_INVALID": 422,
    "KNOWLEDGE_DOCUMENT_EMPTY": 422,
    "KNOWLEDGE_DOCUMENT_ENCRYPTED": 422,
    "KNOWLEDGE_DOCUMENT_UNSUPPORTED": 422,
    "KNOWLEDGE_DOCUMENT_TOO_LARGE": 422,
    "KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED": 422,
    "KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS": 422,
    "KNOWLEDGE_REBUILD_TOO_MANY_CHUNKS": 422,
    "KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED": 422,
}


def _stable_error(code: str, *, status_code: int | None = None) -> JSONResponse:
    bounded_code = code if code.isascii() and 0 < len(code) <= 128 else "KNOWLEDGE_ETL_FAILED"
    status = status_code or _CUSTOMER_SERVICE_ERROR_STATUS.get(bounded_code, 503)
    messages = {
        "CUSTOMER_SERVICE_RAG_DISABLED": "Customer service knowledge is disabled",
        "KNOWLEDGE_STALE_VERSION": "Document version is stale",
        "INVALID_REQUEST": "Request validation failed",
    }
    return JSONResponse(
        {"error": {"code": bounded_code, "message": messages.get(
            bounded_code, "Knowledge operation failed"
        )}},
        status_code=status,
    )


def _lease(value) -> KnowledgeMutationLease:
    return KnowledgeMutationLease(
        scope=value.scope,
        operation_id=value.operation_id,
        operation=value.operation,
        fence=value.fence,
        expires_at=value.expires_at,
        proof=value.proof,
    )


async def _run_customer_service_answer(
    request: Request, service: Any, question: str, *, timeout_seconds: float,
) -> Any:
    """Bound the whole RAG request and cancel it when the caller disconnects."""

    answer_task = asyncio.create_task(service.answer(question))

    async def wait_for_disconnect() -> None:
        while True:
            message = await request.receive()
            if message.get("type") == "http.disconnect":
                return

    disconnect_task = asyncio.create_task(wait_for_disconnect())

    async def cancel_and_drain() -> None:
        for task in (answer_task, disconnect_task):
            if not task.done():
                task.cancel()
        gathering = asyncio.gather(
            answer_task, disconnect_task, return_exceptions=True,
        )
        interrupted: asyncio.CancelledError | None = None
        while not gathering.done():
            try:
                await asyncio.shield(gathering)
            except asyncio.CancelledError as error:
                interrupted = error
        if interrupted is not None:
            raise interrupted

    try:
        async with asyncio.timeout(timeout_seconds):
            done, _ = await asyncio.wait(
                (answer_task, disconnect_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                answer_task.cancel()
                await asyncio.gather(answer_task, return_exceptions=True)
                raise asyncio.CancelledError
            return await answer_task
    except TimeoutError:
        raise CustomerServiceRagError("CUSTOMER_SERVICE_TIMEOUT") from None
    finally:
        await cancel_and_drain()


def register_routes(
    app: FastAPI,
    *,
    generation_model: GenerationModel,
    workflow: GenerationWorkflow,
    checkpoint_store: CheckpointStore,
    cancellations: CancellationRegistry,
    active_generations: ActiveGenerationRegistry,
    require_internal_auth: Any,
    customer_service_rag_enabled: bool = False,
) -> None:
    """注册健康检查和内部生成接口，所有运行依赖由应用工厂显式传入。"""

    async def validation_error_handler(
        request: Request, error: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path.startswith("/internal/v1/customer-service/"):
            return _stable_error("INVALID_REQUEST", status_code=422)
        return await request_validation_exception_handler(request, error)

    app.add_exception_handler(RequestValidationError, validation_error_handler)

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
        "/internal/v1/customer-service/knowledge:etl",
        response_model=KnowledgeEtlResponse,
        response_model_by_alias=True,
        dependencies=[Depends(require_internal_auth)],
        responses={400: {"description": "Invalid request"}, 409: {"description": "Conflict"}},
    )
    async def knowledge_etl(body: KnowledgeEtlRequest, request: Request):
        service = getattr(request.app.state, "knowledge_etl_service", None)
        if not customer_service_rag_enabled or service is None:
            return _stable_error("CUSTOMER_SERVICE_RAG_DISABLED")
        try:
            result = await service.index(
                document_id=body.document_id,
                document_version=body.document_version,
                file_name=body.file_name,
                file_type=body.file_type,
                signed_url=str(body.signed_url),
                sha256=body.sha256,
                etl_version=body.etl_version,
                lease=_lease(body.lease),
            )
            return KnowledgeEtlResponse(
                operation="INDEX",
                document_id=result.document_id,
                document_version=result.document_version,
                chunk_count=result.chunk_count,
                idempotent=result.idempotent,
            )
        except (
            KnowledgeDownloadError, DocumentETLError,
            EmbeddingOutputError, MilvusKnowledgeError,
        ) as error:
            return _stable_error(error.code)
        except Exception:
            return _stable_error("KNOWLEDGE_ETL_FAILED", status_code=500)

    @app.post(
        "/internal/v1/customer-service/knowledge:delete",
        response_model=KnowledgeEtlResponse,
        response_model_by_alias=True,
        dependencies=[Depends(require_internal_auth)],
    )
    async def knowledge_delete(body: KnowledgeDeleteRequest, request: Request):
        service = getattr(request.app.state, "knowledge_etl_service", None)
        if not customer_service_rag_enabled or service is None:
            return _stable_error("CUSTOMER_SERVICE_RAG_DISABLED")
        try:
            await service.delete(
                document_id=body.document_id,
                document_version=body.document_version,
                lease=_lease(body.lease),
            )
            return KnowledgeEtlResponse(
                operation="DELETE",
                document_id=body.document_id,
                document_version=body.document_version,
                chunk_count=0,
            )
        except MilvusKnowledgeError as error:
            return _stable_error(error.code)
        except Exception:
            return _stable_error("KNOWLEDGE_ETL_FAILED", status_code=500)

    @app.post(
        "/internal/v1/customer-service/knowledge:rebuild",
        response_model=KnowledgeRebuildResponse,
        response_model_by_alias=True,
        dependencies=[Depends(require_internal_auth)],
    )
    async def knowledge_rebuild(body: KnowledgeRebuildRequest, request: Request):
        service = getattr(request.app.state, "knowledge_etl_service", None)
        if not customer_service_rag_enabled or service is None:
            return _stable_error("CUSTOMER_SERVICE_RAG_DISABLED")
        settings = request.app.state.settings
        if body.collection_alias != settings.milvus_collection_alias:
            return _stable_error("INVALID_REQUEST", status_code=422)
        if len(body.documents) > settings.rag_rebuild_max_documents:
            return _stable_error("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS")
        try:
            result = await service.rebuild(
                documents=body.documents,
                etl_version=body.etl_version,
                lease=_lease(body.lease),
            )
            return KnowledgeRebuildResponse(
                operation="REBUILD",
                collection_alias=body.collection_alias,
                etl_version=body.etl_version,
                document_count=result.document_count,
                idempotent=result.idempotent,
            )
        except (
            KnowledgeDownloadError, DocumentETLError,
            EmbeddingOutputError, MilvusKnowledgeError,
        ) as error:
            return _stable_error(error.code)
        except Exception:
            return _stable_error("KNOWLEDGE_REBUILD_FAILED", status_code=500)

    @app.get(
        "/internal/v1/customer-service/health",
        dependencies=[Depends(require_internal_auth)],
    )
    async def customer_service_health(request: Request) -> JSONResponse:
        service = getattr(request.app.state, "knowledge_etl_service", None)
        coordinator = getattr(
            request.app.state, "knowledge_mutation_coordinator", None
        )
        milvus_ready = False
        validator_ready = False
        if customer_service_rag_enabled and service is not None:
            ping = getattr(service, "ping", None)
            try:
                milvus_ready = True if ping is None else bool(await ping())
            except Exception:
                milvus_ready = False
        if customer_service_rag_enabled and coordinator is not None:
            ping = getattr(coordinator, "ping", None)
            if ping is not None:
                try:
                    validator_ready = bool(await ping())
                except Exception:
                    validator_ready = False
        ready_state = bool(service is not None and milvus_ready and validator_ready)
        dependencies = {
            "etl": service is not None,
            "milvus": milvus_ready,
            "leaseValidator": validator_ready,
        }
        return JSONResponse({
            "enabled": customer_service_rag_enabled,
            "ready": ready_state,
            "dependencies": dependencies,
        }, status_code=200 if ready_state or not customer_service_rag_enabled else 503)

    @app.post(
        "/internal/v1/customer-service/answers",
        response_model=CustomerServiceAnswerResponse,
        response_model_by_alias=True,
        dependencies=[Depends(require_internal_auth)],
    )
    async def customer_service_answer(
        body: CustomerServiceAnswerRequest, request: Request,
    ):
        service = getattr(request.app.state, "customer_service_rag_service", None)
        if not customer_service_rag_enabled or service is None:
            return _stable_error("CUSTOMER_SERVICE_RAG_DISABLED")
        try:
            result = await _run_customer_service_answer(
                request, service, body.question,
                timeout_seconds=request.app.state.settings.rag_answer_timeout_seconds,
            )
            return CustomerServiceAnswerResponse(
                answered=result.answered,
                answer=result.answer,
                sources=[CustomerServiceSourceResponse(
                    document_id=item.document_id,
                    document_name=item.document_name,
                    document_version=item.document_version,
                    chunk_id=item.chunk_id,
                    locator=item.locator,
                    excerpt=item.excerpt,
                ) for item in result.sources[:3]],
                degraded=result.degraded,
            )
        except CustomerServiceRagError as error:
            return _stable_error(error.code)
        except Exception:
            return _stable_error("CUSTOMER_SERVICE_UNAVAILABLE")

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

        def cancel_generation() -> None:
            cancellations.cancel(thread_id)
            active_generations.cancel(thread_id)

        async def stream():
            completed = False
            try:
                async for event in workflow.stream(body):
                    if await request.is_disconnected():
                        cancel_generation()
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
                    cancel_generation()

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

        thread_id = f"{body.app_id}:{request_id}"
        cancellations.cancel(thread_id)
        active_generations.cancel(thread_id)
        return CancelResponse(request_id=request_id)
