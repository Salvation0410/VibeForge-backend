from __future__ import annotations

import json
import os
import asyncio
import sys
from copy import deepcopy
from collections import defaultdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_service.app import create_app
from ai_service.config import Settings
from ai_service.models.base import ModelTurn, ToolCall
from ai_service.models.input_review import InputReviewResult
from ai_service.models.quality_review import ReviewerResult, ReviewerRole
from ai_service.models.reranker import DisabledReranker


if sys.platform == "win32":
    # Windows 下 psycopg 异步连接需要 SelectorEventLoop；让可选 PostgreSQL 集成测试
    # 在 pytest 中可运行，避免被 ProactorLoop 直接失败。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FakeModel:
    def __init__(
        self,
        *,
        reviews: list[bool] | None = None,
        role_reviews: dict[ReviewerRole, list[ReviewerResult]] | None = None,
        vue_tool_calls: int = 0,
    ):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reviews = list(reviews or [True])
        self.role_reviews = {
            role: list(results)
            for role, results in (role_reviews or {}).items()
        }
        self.vue_tool_calls = vue_tool_calls
        self._vue_turn = 0

    async def route(self, prompt: str) -> str:
        self.calls.append(("route", {"prompt": prompt}))
        lowered = prompt.lower()
        if "vue" in lowered:
            return "VUE_PROJECT"
        if "multiple" in lowered or "multi" in lowered:
            return "MULTI_FILE"
        return "HTML"

    async def review_input(self, context: dict[str, Any]) -> InputReviewResult:
        # 既有离线生成测试明确使用放行审核；专项测试通过覆写返回拒绝/澄清等结果。
        return InputReviewResult(decision="ALLOW", reason="NONE", message="", questions=[])

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        self.calls.append(("generate", {"branch": branch, "context": context}))
        if branch == "VUE_PROJECT" and self._vue_turn < self.vue_tool_calls:
            self._vue_turn += 1
            return ModelTurn(
                content=f"vue-step-{self._vue_turn}",
                tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
            )
        return ModelTurn(content=f"artifact:{branch}", finish_reason="STOP")

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        self.calls.append(("review", {"artifact": artifact, "context": context}))
        return self.reviews.pop(0) if self.reviews else False

    async def review_role(
        self,
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        self.calls.append((
            "review_role",
            {"role": role, "artifact": artifact, "context": context},
        ))
        results = self.role_reviews.get(role, [])
        if results:
            return results.pop(0)
        return ReviewerResult(reviewer=role, summary=f"{role.value} passed")

    async def repair(self, artifact: str, context: dict[str, Any]) -> ModelTurn:
        self.calls.append(("repair", {"artifact": artifact, "context": context}))
        if context.get("repairWork"):
            # 严格问题修复夹具也遵循真实协议；不能继续用无写入的“完成”绕过工作集。
            work = context["repairWork"]
            pending = next((i for i in range(len(work["issues"])) if i not in work["completedIssueIndexes"]), None)
            if pending is None:
                return ModelTurn(content="交回验证", finish_reason="STOP")
            if work["activeWriteSucceeded"]:
                return ModelTurn(content="本项待验证", completed_issue_index=pending, finish_reason="STOP")
            path = work["allowedFiles"][0]
            return ModelTurn(content="修复当前项", repair_plan={"issueIndex": pending, "files": [path]},
                             tool_calls=[ToolCall("file_write", {"relativeFilePath": path, "content": artifact + "|repaired"})],
                             finish_reason="STOP")
        return ModelTurn(content=artifact + "|repaired", finish_reason="STOP")


class FakeToolGateway:
    def __init__(
        self,
        *,
        artifact_context: dict[str, Any] | None = None,
        vue_source_snapshot: dict[str, Any] | None = None,
    ):
        self.calls: list[dict[str, Any]] = []
        self.artifact_context = artifact_context or {"exists": False}
        self.vue_source_snapshot = (
            vue_source_snapshot
            if vue_source_snapshot is not None
            else {
                "files": [
                    {
                        "path": "src/App.vue",
                        "content": "<template><main>fixed</main></template>",
                        "truncated": False,
                    }
                ],
                "eligibleFileCount": 1,
                "includedFileCount": 1,
                "omittedFileCount": 0,
                "truncated": False,
            }
        )

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        app_id: str,
        request_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        call = {
            "name": name,
            "arguments": arguments,
            "appId": app_id,
            "requestId": request_id,
            "toolCallId": tool_call_id,
        }
        self.calls.append(call)
        if name == "artifact_context":
            return dict(self.artifact_context)
        if name == "artifact_validate":
            return {"valid": True, "errors": []}
        if name == "artifact_publish":
            return {"published": True, "versionId": request_id, "hashes": {}}
        if name == "project_build":
            return {"built": True, "errorCode": "", "message": ""}
        if name == "vue_source_snapshot":
            return deepcopy(self.vue_source_snapshot)
        return {"ok": True, "echo": call}


class ChangingSnapshotGateway(FakeToolGateway):
    """为预算/恢复测试显式提供逐轮变化的源码；无进展测试使用固定快照。"""

    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        if name == "vue_source_snapshot":
            round_number = sum(call["name"] == name for call in self.calls)
            result["files"][0]["content"] += f"<!-- candidate {round_number} -->"
        return result


class MemoryCheckpoint:
    available = True

    def __init__(self):
        self.saved: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.cleaned_graph_threads: list[str] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        self.saved[thread_id].append(dict(state))

    async def cleanup_graph(self, thread_id: str) -> None:
        """只记录自动 checkpoint 清理，不删除测试需要检查的业务摘要。"""
        self.cleaned_graph_threads.append(thread_id)

    async def ping(self) -> bool:
        return self.available


@pytest.fixture(autouse=True)
def offline_service_environment(monkeypatch):
    # 离线测试不依赖未提交的私有 .env；仅补齐协议必需项，不配置真实模型密钥。
    # 显式真实资源验收沿用调用方配置，避免覆盖其 Spring 网关鉴权。
    if any(os.getenv(name, "").lower() == "true" for name in (
        "AI_SERVICE_POSTGRES_INTEGRATION", "AI_SERVICE_MILVUS_INTEGRATION",
    )):
        return
    defaults = {
        "AI_SERVICE_INTERNAL_BEARER_TOKEN": "test-internal-token",
        "AI_SERVICE_SPRING_GATEWAY_BASE_URL": "http://spring.test/api/internal/ai-tools",
        "AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN": "test-gateway-token",
    }
    for name, value in defaults.items():
        if name not in os.environ:
            monkeypatch.setenv(name, value)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        langsmith_tracing=False,
        internal_bearer_token="test-secret",
        spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
        spring_gateway_bearer_token="spring-secret",
        checkpoint_enabled=False,
        checkpoint_required=False,
        # 旧行为测试固定关闭新增增强；准备阶段专项测试另行覆盖默认开启路径。
        frontend_skills_enabled=False,
        prompt_rewrite_enabled=False,
        # 既有测试继续验证关闭准备阶段时的原始 checkpoint/追踪形状；诊断专项显式开启。
        preparation_diagnostics_enabled=False,
        # 运行器或开发环境可能加载本地客服开关；离线夹具显式隔离，专项测试自行开启。
        customer_service_rag_enabled=False,
        rag_publication_enabled=False,
        rag_embedding_dimension=2,
    )


@pytest.fixture(autouse=True)
def disable_implicit_tracing(monkeypatch):
    # 离线测试不能通过进程环境中的回调导出合成提示词。
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    from langsmith import tracing_context

    with tracing_context(enabled=False):
        yield


@pytest.fixture
def app_factory(settings: Settings):
    def factory(*, model=None, gateway=None, checkpoint=None, **kwargs):
        kwargs.setdefault("reranker", DisabledReranker())
        # 明确注入控制面；既有离线测试不连接 Spring，也不关闭生产入口守卫。
        if 'knowledge_publication_client' not in kwargs and 'publication_transport' not in kwargs:
            from ai_service.api.knowledge_publication_schemas import ReadSnapshot
            from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
            class FakePublicationClient:
                async def snapshot(self):
                    return ReadSnapshot(enabled=False, epoch=0, publication_id=None,
                        collection_name=None, snapshot_hash=None, embedding_model_version=None,
                        embedding_dimension=0, documents=[])
                require_legacy = SpringKnowledgePublicationClient.require_legacy
                async def close(self):
                    pass
            kwargs['knowledge_publication_client'] = FakePublicationClient()
        injected_answer = kwargs.get('customer_service_rag_service')
        if injected_answer is not None and callable(getattr(injected_answer, 'answer', None)):
            from ai_service.orchestration.customer_service_rag import CustomerServiceRagService
            from ai_service.infrastructure.authoritative_knowledge import LegacyAnswerServiceGuard
            if not isinstance(injected_answer, (CustomerServiceRagService, LegacyAnswerServiceGuard)):
                # 旧 HTTP fake 明确选择受持久模式保护的兼容适配器，生产不自动包装未知实现。
                kwargs['customer_service_rag_service'] = LegacyAnswerServiceGuard(
                    injected_answer, settings, kwargs['knowledge_publication_client'])
        return create_app(
            settings=settings,
            model=model or FakeModel(),
            tool_gateway=gateway or FakeToolGateway(),
            checkpoint=checkpoint or MemoryCheckpoint(),
            **kwargs,
        )

    return factory


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-secret"}


def parse_ndjson(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line]


@pytest.fixture
def ndjson_parser():
    return parse_ndjson

