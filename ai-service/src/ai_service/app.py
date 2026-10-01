from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import inspect
from typing import Any

from fastapi import FastAPI

from ai_service.api.dependencies import create_internal_auth_dependency
from ai_service.api.routes import register_routes
from ai_service.config import Settings, get_settings
from ai_service.infrastructure.checkpoint import (
    CheckpointStore,
    DisabledCheckpoint,
)
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from ai_service.infrastructure.spring_tools import SpringToolGateway
from ai_service.models.base import GenerationModel
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.active_generations import ActiveGenerationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow
from ai_service.orchestration.customer_service_health import (
    CustomerServiceDependencyHealth,
    CustomerServiceDependencyReference,
    dispose_customer_service_health_provider,
)


async def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    if inspect.iscoroutinefunction(close):
        await close()
        return
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        await result


async def _construct_in_thread(factory: Any, *args: Any, **kwargs: Any) -> Any:
    """Finish an uncancellable constructor and close its result before propagating cancel."""

    task = asyncio.create_task(asyncio.to_thread(factory, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            try:
                resource = task.result()
            except Exception:
                pass
            else:
                try:
                    await _close_resource(resource)
                except Exception:
                    pass
        raise


def create_app(
    *,
    settings: Settings | None = None,
    model: GenerationModel | None = None,
    tool_gateway: Any | None = None,
    checkpoint: CheckpointStore | None = None,
    knowledge_etl_service: Any | None = None,
    knowledge_downloader: Any | None = None,
    embedding_provider: Any | None = None,
    knowledge_store: Any | None = None,
    mutation_coordinator: Any | None = None,
    milvus_client_factory: Any | None = None,
    lease_validation_transport: Any | None = None,
    reranker: Any | None = None,
    reranker_model_factory: Any | None = None,
    customer_service_rag_service: Any | None = None,
    customer_service_health_provider: Any | None = None,
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
        PostgresCheckpoint(
            config.checkpoint_postgres_url,
            required=config.checkpoint_required,
            auto_setup=config.checkpoint_auto_setup,
            ttl_seconds=config.checkpoint_ttl_seconds,
            pool_min_size=config.checkpoint_pool_min_size,
            pool_max_size=config.checkpoint_pool_max_size,
        )
        if config.checkpoint_enabled
        else DisabledCheckpoint()
    )
    cancellations = CancellationRegistry()
    active_generations = ActiveGenerationRegistry()
    initial_etl_service = knowledge_etl_service
    initial_mutation_coordinator = mutation_coordinator
    initial_reranker = reranker
    initial_rag_service = customer_service_rag_service
    workflow = GenerationWorkflow(
        model=generation_model,
        tool_gateway=gateway,
        checkpoint=checkpoint_store,
        cancellations=cancellations,
        active_generations=active_generations,
        settings=config,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """管理 checkpoint 和 HTTP 工具客户端的启动与释放。"""

        await checkpoint_store.start()
        resources: list[Any] = []
        try:
            try:
                if config.customer_service_rag_enabled:
                    # Import and initialize the optional stack behind the feature gate.
                    from ai_service.infrastructure.knowledge_download import KnowledgeDownloader
                    from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore
                    from ai_service.infrastructure.spring_knowledge_lease import (
                        SpringKnowledgeMutationCoordinator,
                    )
                    from ai_service.models.embeddings import CloseAIEmbeddingProvider
                    from ai_service.models.reranker import (
                        DisabledReranker,
                        LocalCrossEncoderReranker,
                    )
                    from ai_service.orchestration.customer_service_rag import (
                        CustomerServiceRagService,
                    )
                    from ai_service.orchestration.document_etl import KnowledgeEtlService

                    if app.state.reranker is None:
                        app.state.reranker = DisabledReranker()
                    resources.extend(
                        resource for resource in (
                            initial_etl_service,
                            initial_mutation_coordinator,
                            initial_reranker,
                        ) if resource is not None
                    )
                if (
                    config.customer_service_rag_enabled
                    and config.rag_reranker_provider == "local_cross_encoder"
                    and initial_reranker is None
                ):
                    factory_kwargs = {}
                    if reranker_model_factory is not None:
                        factory_kwargs["model_factory"] = reranker_model_factory
                    local_reranker = await LocalCrossEncoderReranker.create(
                        config, **factory_kwargs,
                    )
                    resources.append(local_reranker)
                    app.state.reranker = local_reranker
                if (
                    config.customer_service_rag_enabled
                    and app.state.knowledge_etl_service is None
                ):
                    coordinator = mutation_coordinator or SpringKnowledgeMutationCoordinator(
                        gateway_base_url=str(config.spring_gateway_base_url),
                        bearer_token=config.spring_gateway_bearer_token,
                        transport=lease_validation_transport,
                    )
                    resources.append(coordinator)
                    app.state.knowledge_mutation_coordinator = coordinator
                    downloader = knowledge_downloader or KnowledgeDownloader(config)
                    resources.append(downloader)
                    embeddings = embedding_provider or await _construct_in_thread(
                        CloseAIEmbeddingProvider, config
                    )
                    resources.append(embeddings)
                    app.state.customer_service_embedding_provider = embeddings
                    store_kwargs = {"mutation_coordinator": coordinator}
                    if milvus_client_factory is not None:
                        store_kwargs["client_factory"] = milvus_client_factory
                    store = knowledge_store or await _construct_in_thread(
                        MilvusKnowledgeStore, config, **store_kwargs
                    )
                    resources.append(store)
                    app.state.customer_service_knowledge_store = store
                    app.state.knowledge_etl_service = KnowledgeEtlService(
                        config,
                        downloader,
                        embeddings,
                        store,
                        semaphore=asyncio.Semaphore(config.rag_etl_max_concurrency),
                    )
                    if app.state.customer_service_rag_service is None:
                        app.state.customer_service_rag_service = CustomerServiceRagService(
                            config, embeddings, store, app.state.reranker, generation_model,
                        )
                elif (
                    config.customer_service_rag_enabled
                    and app.state.customer_service_rag_service is None
                    and embedding_provider is not None
                    and knowledge_store is not None
                ):
                    app.state.customer_service_rag_service = CustomerServiceRagService(
                        config, embedding_provider, knowledge_store,
                        app.state.reranker, generation_model,
                    )
                if (
                    config.customer_service_rag_enabled
                    and app.state.customer_service_health_provider is None
                ):
                    app.state.customer_service_health_provider = (
                        CustomerServiceDependencyHealth({
                            "answerModel": CustomerServiceDependencyReference(
                                lambda: app.state.model
                            ),
                            "answerService": CustomerServiceDependencyReference(
                                lambda: app.state.customer_service_rag_service
                            ),
                            "embedding": CustomerServiceDependencyReference(
                                lambda: app.state.customer_service_embedding_provider
                            ),
                            "etl": lambda: app.state.knowledge_etl_service is not None,
                            "leaseValidator": CustomerServiceDependencyReference(
                                lambda: app.state.knowledge_mutation_coordinator
                            ),
                            "milvus": CustomerServiceDependencyReference(
                                lambda: app.state.customer_service_knowledge_store
                                or app.state.knowledge_etl_service
                            ),
                            "reranker": CustomerServiceDependencyReference(
                                lambda: app.state.reranker
                            ),
                        })
                    )
            except Exception:
                # Optional customer-service dependencies never take code generation down.
                seen: set[int] = set()
                for resource in reversed(resources):
                    if id(resource) in seen:
                        continue
                    seen.add(id(resource))
                    try:
                        await _close_resource(resource)
                    except Exception:
                        pass
                resources.clear()
                app.state.knowledge_etl_service = None
                app.state.knowledge_mutation_coordinator = None
                app.state.customer_service_embedding_provider = None
                app.state.customer_service_knowledge_store = None
                app.state.reranker = None
                app.state.customer_service_rag_service = None
                app.state.customer_service_health_provider = (
                    CustomerServiceDependencyHealth({
                        "answerModel": lambda: False,
                        "answerService": lambda: False,
                        "embedding": lambda: False,
                        "etl": lambda: False,
                        "leaseValidator": lambda: False,
                        "milvus": lambda: False,
                        "reranker": lambda: False,
                    })
                )
            yield
        finally:
            try:
                seen: set[int] = set()
                resource_error: BaseException | None = None
                health_provider = getattr(
                    app.state, "customer_service_health_provider", None,
                )
                if health_provider is not None:
                    try:
                        await dispose_customer_service_health_provider(
                            health_provider
                        )
                    except BaseException as error:
                        resource_error = error
                for resource in reversed(resources):
                    if id(resource) in seen:
                        continue
                    seen.add(id(resource))
                    close = getattr(resource, "close", None)
                    if close is not None:
                        try:
                            await _close_resource(resource)
                        except BaseException as error:
                            if resource_error is None:
                                resource_error = error
                if resource_error is not None:
                    raise resource_error
            finally:
                try:
                    await checkpoint_store.close()
                finally:
                    close = getattr(gateway, "close", None)
                    if close is not None:
                        await close()

    app = FastAPI(title="yu-ai-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.state.model = generation_model
    app.state.tool_gateway = gateway
    app.state.checkpoint = checkpoint_store
    app.state.cancellations = cancellations
    app.state.active_generations = active_generations
    app.state.workflow = workflow
    app.state.knowledge_etl_service = initial_etl_service
    app.state.knowledge_mutation_coordinator = initial_mutation_coordinator
    app.state.customer_service_embedding_provider = embedding_provider
    app.state.customer_service_knowledge_store = knowledge_store
    app.state.reranker = initial_reranker
    app.state.customer_service_rag_service = initial_rag_service
    app.state.customer_service_health_provider = customer_service_health_provider

    register_routes(
        app,
        generation_model=generation_model,
        workflow=workflow,
        checkpoint_store=checkpoint_store,
        cancellations=cancellations,
        active_generations=active_generations,
        require_internal_auth=create_internal_auth_dependency(config.internal_bearer_token),
        customer_service_rag_enabled=config.customer_service_rag_enabled,
    )
    return app
