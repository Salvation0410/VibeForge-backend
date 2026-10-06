"""职责分包的导入契约测试。"""

import sys

import pytest


def test_public_modules_can_be_imported_from_new_packages():
    """确保启动入口和四类职责模块均可独立加载。"""

    from ai_service.api import routes, schemas
    from ai_service.app import create_app
    from ai_service.infrastructure import checkpoint, postgres_checkpoint, spring_tools
    from ai_service.models import GenerationModel, OpenAICompatibleModel, quality_review
    from ai_service.orchestration import (
        active_generations,
        cancellation,
        events,
        multi_agent_review,
        workflow,
    )

    assert callable(create_app)
    assert routes.register_routes
    assert schemas.GenerationRequest
    assert checkpoint.DisabledCheckpoint
    assert postgres_checkpoint.PostgresCheckpoint
    assert spring_tools.SpringToolGateway
    assert GenerationModel
    assert OpenAICompatibleModel
    assert quality_review.ReviewerRole
    assert cancellation.CancellationRegistry
    assert active_generations.ActiveGenerationRegistry
    assert events.EventEmitter
    assert multi_agent_review.run_multi_agent_review
    assert workflow.GenerationWorkflow


def test_customer_service_rag_dependencies_can_be_imported():
    from docx import Document
    from FlagEmbedding import FlagReranker
    from langchain.embeddings import init_embeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from pymilvus import MilvusClient
    from pypdf import PdfReader

    assert callable(init_embeddings)
    assert RecursiveCharacterTextSplitter
    assert MilvusClient
    assert FlagReranker
    assert PdfReader
    assert Document


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CUDA wheel contract")
def test_windows_reranker_runtime_uses_cuda_torch():
    import torch

    assert torch.version.cuda == "12.4"
