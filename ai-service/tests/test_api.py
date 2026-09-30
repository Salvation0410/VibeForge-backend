import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai_service.api.schemas import CodeGenType, GenerationRequest
from ai_service.infrastructure.spring_tools import SpringToolGateway
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.orchestration.document_etl import KnowledgeEtlService
from ai_service.orchestration.customer_service_rag import (
    CustomerServiceAnswer,
    CustomerServiceRagError,
    CustomerServiceSource,
)
from ai_service.models.base import ModelTurn, ToolCall
from ai_service.models.quality_review import (
    IssueSeverity,
    QualityIssue,
    QualityReviewOutputError,
    ReviewerResult,
    ReviewerRole,
)
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.active_generations import ActiveGenerationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.workflow import GenerationWorkflow, _after_build
from ai_service.api.routes import _run_customer_service_answer
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint


def generation_payload(code_gen_type: str) -> dict:
    return {
        "requestId": "req-1",
        "appId": "42",
        "prompt": "build it",
        "codeGenType": code_gen_type,
        "conversation": [{"role": "user", "content": "previous detail"}],
    }


def knowledge_lease(
    operation_id="op-1", *, scope="document:doc-1", operation="INDEX",
) -> dict:
    return {
        "scope": scope,
        "operationId": operation_id,
        "operation": operation,
        "fence": 7,
        "expiresAt": time.time() + 300,
        "proof": "lease-proof-secret",
    }


def knowledge_etl_payload() -> dict:
    return {
        "operation": "INDEX",
        "documentId": "doc-1",
        "documentVersion": 2,
        "fileName": "manual.txt",
        "fileType": "TXT",
        "signedUrl": "https://oss.example.test/manual?Signature=signed-url-secret",
        "sha256": hashlib.sha256(b"knowledge").hexdigest(),
        "etlVersion": "etl-v1",
        "lease": knowledge_lease(),
    }


class FakeKnowledgeEtlService:
    def __init__(self, error=None):
        self.error = error
        self.index_calls = []
        self.delete_calls = []

    async def index(self, **kwargs):
        self.index_calls.append(kwargs)
        if self.error:
            raise self.error
        return type("Result", (), {
            "document_id": kwargs["document_id"],
            "document_version": kwargs["document_version"],
            "chunk_count": 3,
            "idempotent": False,
        })()

    async def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.error:
            raise self.error


class FakeLeaseValidator:
    def __init__(self, available=True):
        self.available = available
        self.pings = 0

    async def ping(self):
        self.pings += 1
        return self.available


class FakeCustomerServiceRag:
    def __init__(self, result=None, error=None):
        self.result = result or CustomerServiceAnswer(
            answered=True,
            answer="Click Deploy.",
            sources=(CustomerServiceSource(
                document_id="doc-1", document_name="guide.md", document_version=2,
                chunk_id="c1", locator="Deployment", excerpt="Click Deploy.",
            ),),
            degraded=False,
        )
        self.error = error
        self.questions = []

    async def answer(self, question):
        self.questions.append(question)
        if self.error:
            raise self.error
        return self.result


def reviewer_result(
    role: ReviewerRole,
    *,
    severity: IssueSeverity | None = None,
    code: str = "QUALITY_ISSUE",
    evidence: str = "visible evidence",
    repair_hint: str = "apply the repair",
) -> ReviewerResult:
    issues = []
    if severity is not None:
        issues.append(QualityIssue(
            code=code,
            category="quality",
            summary=f"{role.value} found a problem",
            evidence=evidence,
            repair_hint=repair_hint,
            severity=severity,
        ))
    return ReviewerResult(reviewer=role, summary=f"{role.value} review", issues=issues)


class GraphMemoryCheckpoint(MemoryCheckpoint):
    def __init__(self):
        super().__init__()
        self.graph_saver = InMemorySaver()

    def get_graph_saver(self):
        return self.graph_saver


async def graph_channel_values(
    checkpoint: GraphMemoryCheckpoint,
    thread_id: str,
) -> list[dict]:
    config = {"configurable": {"thread_id": thread_id}}
    return [
        item.checkpoint["channel_values"]
        async for item in checkpoint.graph_saver.alist(config)
    ]


def test_authentication_is_required(app_factory, auth_headers):
    client = TestClient(app_factory())
    assert client.get("/internal/v1/health/live").status_code == 200
    assert client.post("/internal/v1/route", json={"prompt": "site"}).status_code == 401
    assert client.post(
        "/internal/v1/route",
        json={"prompt": "site"},
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
    assert client.post(
        "/internal/v1/route", json={"prompt": "site"}, headers=auth_headers
    ).status_code == 200


def test_customer_service_answer_auth_disabled_and_question_bounds(
    app_factory, auth_headers, settings,
):
    path = "/internal/v1/customer-service/answers"
    with TestClient(app_factory(customer_service_rag_service=FakeCustomerServiceRag())) as client:
        assert client.post(path, json={"question": "hello"}).status_code == 401
        disabled = client.post(path, json={"question": "hello"}, headers=auth_headers)
        assert disabled.status_code == 503
        assert disabled.json()["error"]["code"] == "CUSTOMER_SERVICE_RAG_DISABLED"

    settings.customer_service_rag_enabled = True
    service = FakeCustomerServiceRag()
    with TestClient(app_factory(
        customer_service_rag_service=service,
        knowledge_etl_service=FakeKnowledgeEtlService(),
    )) as client:
        for question in ("   ", "x" * 4001):
            response = client.post(path, json={"question": question}, headers=auth_headers)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert service.questions == []


def test_customer_service_answer_response_is_bounded_and_has_no_raw_fields(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    service = FakeCustomerServiceRag()
    checkpoint = MemoryCheckpoint()
    with TestClient(app_factory(
        customer_service_rag_service=service, checkpoint=checkpoint,
        knowledge_etl_service=FakeKnowledgeEtlService(),
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/answers",
            json={"question": "How do I deploy?"}, headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json() == {
        "answered": True,
        "answer": "Click Deploy.",
        "sources": [{
            "documentId": "doc-1", "documentName": "guide.md", "documentVersion": 2,
            "chunkId": "c1", "locator": "Deployment", "excerpt": "Click Deploy.",
        }],
        "degraded": False,
    }
    assert checkpoint.saved == {}
    assert "score" not in response.text.lower()
    assert "prompt" not in response.text.lower()
    assert "reasoning" not in response.text.lower()


@pytest.mark.parametrize("code", [
    "CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE",
    "CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE",
])
def test_customer_service_answer_unavailable_is_stable_and_redacted(
    app_factory, auth_headers, settings, code,
):
    settings.customer_service_rag_enabled = True
    service = FakeCustomerServiceRag(
        error=CustomerServiceRagError(code)
    )
    with TestClient(app_factory(
        customer_service_rag_service=service,
        knowledge_etl_service=FakeKnowledgeEtlService(),
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/answers",
            json={"question": "secret query"}, headers=auth_headers,
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code
    assert "secret query" not in response.text


def test_customer_service_no_answer_has_no_sources(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    service = FakeCustomerServiceRag(result=CustomerServiceAnswer(
        answered=False, answer="暂未找到足够依据回答该问题。", sources=(), degraded=True,
    ))
    with TestClient(app_factory(
        customer_service_rag_service=service,
        knowledge_etl_service=FakeKnowledgeEtlService(),
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/answers",
            json={"question": "unknown"}, headers=auth_headers,
        )
    assert response.json() == {
        "answered": False,
        "answer": "暂未找到足够依据回答该问题。",
        "sources": [],
        "degraded": True,
    }


@pytest.mark.asyncio
async def test_customer_service_disconnect_cancels_and_drains_service_task():
    cancelled = asyncio.Event()

    class BlockingService:
        async def answer(self, _question):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    class DisconnectedRequest:
        async def receive(self):
            return {"type": "http.disconnect"}

    with pytest.raises(asyncio.CancelledError):
        await _run_customer_service_answer(
            DisconnectedRequest(), BlockingService(), "question", timeout_seconds=1,
        )
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_customer_service_total_timeout_cancels_hanging_model_work():
    cancelled = asyncio.Event()

    class BlockingService:
        async def answer(self, _question):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    class ConnectedRequest:
        async def receive(self):
            await asyncio.Event().wait()

    with pytest.raises(CustomerServiceRagError, match="^CUSTOMER_SERVICE_TIMEOUT$"):
        await _run_customer_service_answer(
            ConnectedRequest(), BlockingService(), "question", timeout_seconds=0.02,
        )
    assert cancelled.is_set()


def test_customer_service_etl_requires_auth_and_disabled_is_stable(
    app_factory, auth_headers,
):
    payload = knowledge_etl_payload()
    client = TestClient(app_factory())
    assert client.post(
        "/internal/v1/customer-service/knowledge:etl", json=payload,
    ).status_code == 401
    assert client.post(
        "/internal/v1/customer-service/knowledge:delete", json={
            "operation": "DELETE", "documentId": "doc-1", "documentVersion": 2,
            "lease": knowledge_lease("op-2", operation="DELETE"),
        },
    ).status_code == 401
    assert client.get("/internal/v1/customer-service/health").status_code == 401
    response = client.post(
        "/internal/v1/customer-service/knowledge:etl", json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CUSTOMER_SERVICE_RAG_DISABLED"


def test_customer_service_index_delete_and_health(app_factory, auth_headers, settings):
    settings.customer_service_rag_enabled = True
    service = FakeKnowledgeEtlService()
    validator = FakeLeaseValidator()
    with TestClient(app_factory(
        knowledge_etl_service=service, mutation_coordinator=validator,
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=knowledge_etl_payload(), headers=auth_headers,
        )
        deleted = client.post(
            "/internal/v1/customer-service/knowledge:delete",
            json={
                "operation": "DELETE", "documentId": "doc-1",
                "documentVersion": 2,
                "lease": knowledge_lease("op-2", operation="DELETE"),
            },
            headers=auth_headers,
        )
        health = client.get(
            "/internal/v1/customer-service/health", headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json() == {
        "operation": "INDEX", "status": "SUCCEEDED", "documentId": "doc-1",
        "documentVersion": 2, "chunkCount": 3, "idempotent": False,
    }
    assert service.index_calls[0]["lease"].proof == "lease-proof-secret"
    assert service.index_calls[0]["lease"].operation == "INDEX"
    assert deleted.status_code == 200
    assert deleted.json()["operation"] == "DELETE"
    assert service.delete_calls[0]["lease"].operation_id == "op-2"
    assert service.delete_calls[0]["lease"].operation == "DELETE"
    assert health.json() == {
        "enabled": True, "ready": True,
        "dependencies": {"etl": True, "milvus": True, "leaseValidator": True},
    }
    assert validator.pings == 1


def test_customer_service_health_degrades_when_lease_validator_is_unavailable(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    validator = FakeLeaseValidator(available=False)
    with TestClient(app_factory(
        knowledge_etl_service=FakeKnowledgeEtlService(),
        mutation_coordinator=validator,
    )) as client:
        response = client.get(
            "/internal/v1/customer-service/health", headers=auth_headers,
        )
    assert response.status_code == 503
    assert response.json() == {
        "enabled": True, "ready": False,
        "dependencies": {"etl": True, "milvus": True, "leaseValidator": False},
    }
    assert validator.pings == 1


@pytest.mark.parametrize("failure", ["404", "timeout"])
def test_customer_service_health_degrades_on_spring_validator_failure(
    app_factory, auth_headers, settings, failure,
):
    settings.customer_service_rag_enabled = True

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("vendor health body", request=request)
        return httpx.Response(404, content=b"vendor health body")

    from ai_service.infrastructure.spring_knowledge_lease import (
        SpringKnowledgeMutationCoordinator,
    )
    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(handler),
    )
    with TestClient(app_factory(
        knowledge_etl_service=FakeKnowledgeEtlService(),
        mutation_coordinator=coordinator,
    )) as client:
        response = client.get(
            "/internal/v1/customer-service/health", headers=auth_headers,
        )
    assert response.status_code == 503
    assert response.json()["dependencies"] == {
        "etl": True, "milvus": True, "leaseValidator": False,
    }


def test_customer_service_repeated_index_reports_store_idempotency(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True

    class IdempotentService(FakeKnowledgeEtlService):
        async def index(self, **kwargs):
            result = await super().index(**kwargs)
            result.idempotent = len(self.index_calls) > 1
            return result

    service = IdempotentService()
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        first = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=knowledge_etl_payload(), headers=auth_headers,
        )
        second = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=knowledge_etl_payload(), headers=auth_headers,
        )
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True


def test_http_etl_preserves_all_lease_fields_through_service_store_and_coordinator(
    app_factory, auth_headers, settings, tmp_path,
):
    settings.customer_service_rag_enabled = True
    path = tmp_path / "lease.txt"
    path.write_text("knowledge", encoding="utf-8")

    class Downloaded:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            path.unlink(missing_ok=True)
        @property
        def path(self):
            return path

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return Downloaded()

    class Embeddings:
        async def embed_documents(self, texts, **_kwargs):
            assert texts == ["knowledge"]
            return [[0.1, 0.2]]

    class Permit:
        fence = 17
        async def assert_current(self):
            return None

    class Coordinator(FakeLeaseValidator):
        @asynccontextmanager
        async def hold(self, lease, *, scope, operation):
            self.held = (lease, scope, operation)
            yield Permit()

    coordinator = Coordinator()

    class Store:
        async def upsert_document_version(self, document, *, lease):
            self.lease = lease
            async with coordinator.hold(
                lease, scope=f"document:{document.document_id}", operation="upsert"
            ):
                pass
            return type("Result", (), {
                "document_id": document.document_id,
                "document_version": document.document_version,
                "chunk_count": len(document.chunks),
                "idempotent": False,
            })()
        async def ping(self):
            return True

    store = Store()
    service = KnowledgeEtlService(
        settings, Downloader(), Embeddings(), store
    )
    expires_at = time.time() + 300
    payload = knowledge_etl_payload()
    payload["lease"] = {
        "scope": "document:doc-1",
        "operationId": "operation-raw-1",
        "operation": "INDEX",
        "fence": 17,
        "expiresAt": expires_at,
        "proof": "proof-raw-value",
    }
    with TestClient(app_factory(
        knowledge_etl_service=service, mutation_coordinator=coordinator,
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=payload, headers=auth_headers,
        )

    assert response.status_code == 200
    lease = store.lease
    assert (
        lease.scope, lease.operation_id, lease.operation, lease.fence,
        lease.expires_at, lease.proof,
    ) == (
        "document:doc-1", "operation-raw-1", "INDEX", 17,
        expires_at, "proof-raw-value",
    )
    held_lease, held_scope, held_operation = coordinator.held
    assert held_lease is lease
    assert held_scope == "document:doc-1"
    assert held_operation == "upsert"


def test_customer_service_errors_are_stable_and_redacted(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    secret_url = knowledge_etl_payload()["signedUrl"]
    service = FakeKnowledgeEtlService(
        MilvusKnowledgeError("KNOWLEDGE_STALE_VERSION")
    )
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=knowledge_etl_payload(), headers=auth_headers,
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KNOWLEDGE_STALE_VERSION"
    assert secret_url not in response.text
    assert "lease-proof-secret" not in response.text


def test_customer_service_unexpected_vendor_error_is_bounded_and_redacted(
    app_factory, auth_headers, settings, caplog,
):
    settings.customer_service_rag_enabled = True
    service = FakeKnowledgeEtlService(
        RuntimeError("vendor body signed-url-secret lease-proof-secret api-key")
    )
    with caplog.at_level(logging.DEBUG):
        with TestClient(app_factory(knowledge_etl_service=service)) as client:
            response = client.post(
                "/internal/v1/customer-service/knowledge:etl",
                json=knowledge_etl_payload(), headers=auth_headers,
            )
    assert response.status_code == 500
    assert response.json() == {"error": {
        "code": "KNOWLEDGE_ETL_FAILED",
        "message": "Knowledge operation failed",
    }}
    assert len(response.text) < 256
    assert "vendor body" not in response.text
    assert "api-key" not in response.text
    for secret in (
        "signed-url-secret", "lease-proof-secret", "test-secret",
        "vendor body", "api-key",
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize("field,value", [
    ("documentId", "x" * 129),
    ("fileName", "x" * 256),
    ("fileType", "HTML"),
    ("signedUrl", "http://oss.example.test/file"),
    ("sha256", "not-a-sha"),
    ("lease", {**knowledge_lease(), "expiresAt": 1}),
    ("lease", {**knowledge_lease(), "scope": "document:other"}),
    ("lease", {**knowledge_lease(), "operationId": "x" * 129}),
])
def test_customer_service_etl_schema_boundaries(
    app_factory, auth_headers, settings, field, value,
):
    settings.customer_service_rag_enabled = True
    payload = knowledge_etl_payload()
    payload[field] = value
    with TestClient(app_factory(knowledge_etl_service=FakeKnowledgeEtlService())) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=payload, headers=auth_headers,
        )
    assert response.status_code == 422
    assert "signed-url-secret" not in response.text
    assert "lease-proof-secret" not in response.text


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["lease"].update(operation="DELETE"),
    lambda payload: payload["lease"].update(operation="INVALID"),
    lambda payload: payload["lease"].update(unexpected="value"),
    lambda payload: payload.update(unexpected="value"),
])
def test_customer_service_rejects_lease_operation_mismatch_and_extra_fields(
    app_factory, auth_headers, settings, mutate,
):
    settings.customer_service_rag_enabled = True
    payload = knowledge_etl_payload()
    mutate(payload)
    with TestClient(app_factory(
        knowledge_etl_service=FakeKnowledgeEtlService(),
        mutation_coordinator=FakeLeaseValidator(),
    )) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:etl",
            json=payload, headers=auth_headers,
        )
    assert response.status_code == 422


def test_customer_service_disabled_does_not_initialize_dependencies(
    app_factory, settings,
):
    settings.customer_service_rag_enabled = False
    calls = []
    with TestClient(app_factory(
        milvus_client_factory=lambda **_kwargs: calls.append("milvus"),
    )):
        pass
    assert calls == []


@pytest.mark.asyncio
async def test_milvus_constructor_is_offloaded_and_lifespan_closes_resources(
    app_factory, settings,
):
    settings.customer_service_rag_enabled = True
    started = threading.Event()
    release = threading.Event()
    heartbeat = asyncio.Event()

    class Client:
        closed = False
        def close(self):
            self.closed = True

    client = Client()

    def factory(**_kwargs):
        started.set()
        release.wait(1)
        return client

    class Coordinator:
        closed = False
        async def close(self):
            self.closed = True

    coordinator = Coordinator()
    app = app_factory(
        knowledge_downloader=object(), embedding_provider=object(),
        mutation_coordinator=coordinator, milvus_client_factory=factory,
    )
    context = app.router.lifespan_context(app)

    async def mark_heartbeat():
        await asyncio.sleep(0.02)
        heartbeat.set()

    timer = threading.Timer(0.5, release.set)
    timer.start()
    enter = asyncio.create_task(context.__aenter__())
    ticker = asyncio.create_task(mark_heartbeat())
    try:
        assert await asyncio.to_thread(started.wait, 0.3)
        await asyncio.wait_for(heartbeat.wait(), 0.2)
        assert not enter.done()
        release.set()
        await enter
    finally:
        release.set()
        timer.cancel()
        await ticker
    await context.__aexit__(None, None, None)
    assert client.closed
    assert coordinator.closed


@pytest.mark.asyncio
async def test_startup_cancellation_drains_constructor_and_closes_created_client(
    app_factory, settings,
):
    settings.customer_service_rag_enabled = True
    started = threading.Event()
    release = threading.Event()

    class Client:
        closed = False
        def close(self):
            self.closed = True

    client = Client()

    def factory(**_kwargs):
        started.set()
        release.wait(1)
        return client

    class Coordinator:
        closed = False
        async def close(self):
            self.closed = True

    coordinator = Coordinator()
    app = app_factory(
        knowledge_downloader=object(), embedding_provider=object(),
        mutation_coordinator=coordinator, milvus_client_factory=factory,
    )
    context = app.router.lifespan_context(app)
    enter = asyncio.create_task(context.__aenter__())
    assert await asyncio.to_thread(started.wait, 0.3)
    enter.cancel()
    await asyncio.sleep(0.02)
    assert not enter.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await enter
    assert client.closed
    assert coordinator.closed


@pytest.mark.asyncio
async def test_partial_startup_failure_closes_initialized_dependencies(
    app_factory, settings,
):
    settings.customer_service_rag_enabled = True

    class Closeable:
        closed = False
        async def close(self):
            self.closed = True

    coordinator = Closeable()
    embeddings = Closeable()
    downloader = Closeable()

    def failing_factory(**_kwargs):
        raise RuntimeError("milvus constructor vendor body")

    app = app_factory(
        knowledge_downloader=downloader, embedding_provider=embeddings,
        mutation_coordinator=coordinator, milvus_client_factory=failing_factory,
    )
    context = app.router.lifespan_context(app)
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_VECTOR_STORE_UNAVAILABLE"):
        await context.__aenter__()
    assert coordinator.closed
    assert embeddings.closed
    assert downloader.closed


def test_route_returns_supported_generation_type(app_factory, auth_headers):
    client = TestClient(app_factory(model=FakeModel()))
    response = client.post(
        "/internal/v1/route",
        json={"prompt": "Create a Vue dashboard", "appId": "42", "requestId": "route-1"},
        headers=auth_headers,
    )
    assert response.json() == {
        "requestId": "route-1",
        "codeGenType": "VUE_PROJECT",
    }


def test_all_generation_branches_complete_in_order(app_factory, auth_headers, ndjson_parser):
    for branch in ("HTML", "MULTI_FILE", "VUE_PROJECT"):
        model = FakeModel()
        gateway = FakeToolGateway()
        checkpoint = MemoryCheckpoint()
        client = TestClient(app_factory(model=model, gateway=gateway, checkpoint=checkpoint))
        response = client.post(
            "/internal/v1/generations:stream",
            json=generation_payload(branch),
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = ndjson_parser(response)
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert events[-1]["type"] == "completed"
        completed = events[-1]["data"]
        assert "artifact" not in completed
        assert completed["threadId"] == "42:req-1"
        assert completed["codeGenType"] == branch
        assert completed["qualityPassed"] is True
        assert completed["repairCount"] == 0
        assert completed["toolCallCount"] >= 0
        if branch in {"HTML", "MULTI_FILE"}:
            assert completed["published"] is True
            assert completed["versionId"] == "req-1"
            assert completed["artifactHashes"] == {}
        else:
            assert completed["built"] is True
        assert all(event["requestId"] == "req-1" for event in events)
        generation_calls = [payload for name, payload in model.calls if name == "generate"]
        assert generation_calls[0]["branch"] == branch
        assert generation_calls[0]["context"]["currentArtifact"] == {"exists": False}
        assert "context_prepare" in [event["node"] for event in events]
        assert "quality_review" in [event["node"] for event in events]
        assert "42:req-1" in checkpoint.saved
        saved_states = checkpoint.saved["42:req-1"]
        assert saved_states
        assert all("artifact" not in state for state in saved_states)
        assert all("prompt" not in state for state in saved_states)
        assert all("conversation" not in state for state in saved_states)
        assert all("context" not in state for state in saved_states)
        assert checkpoint.cleaned_graph_threads == ["42:req-1"]
        build_calls = [call for call in gateway.calls if call["name"] == "project_build"]
        assert bool(build_calls) is (branch == "VUE_PROJECT")
        assert not [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]


def test_generation_failure_cleans_graph_checkpoint(app_factory, auth_headers, ndjson_parser):
    class FailingModel(FakeModel):
        async def generate(self, branch, context):
            raise RuntimeError("model generation failed")

    checkpoint = MemoryCheckpoint()
    client = TestClient(app_factory(model=FailingModel(), checkpoint=checkpoint))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert not [event for event in events if event["type"] == "completed"]
    assert checkpoint.cleaned_graph_threads == ["42:req-1"]


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_override_completed_terminal_event(settings, caplog):
    class CleanupFailCheckpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id: str) -> None:
            raise RuntimeError("cleanup failed")

    workflow = GenerationWorkflow(
        model=FakeModel(),
        tool_gateway=FakeToolGateway(),
        checkpoint=CleanupFailCheckpoint(),
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-cleanup-fail",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )

    with caplog.at_level(logging.WARNING):
        events = await workflow.run(request)

    assert events[-1].type == "completed"
    assert not [event for event in events if event.type == "failed"]
    assert "42:req-cleanup-fail" in caplog.text


@pytest.mark.asyncio
async def test_task_cancellation_cleans_graph_checkpoint(settings):
    class BlockingModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def generate(self, branch, context):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("阻塞模型不应正常返回")

    model = BlockingModel()
    checkpoint = MemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-task-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )
    task = asyncio.create_task(workflow.run(request))
    await asyncio.wait_for(model.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert checkpoint.cleaned_graph_threads == ["42:req-task-cancel"]


@pytest.mark.asyncio
async def test_stream_consumer_cancellation_waits_for_checkpoint_cleanup(settings):
    class BlockingModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def generate(self, branch, context):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("阻塞模型不应正常返回")

    class DelayedCheckpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id: str) -> None:
            await asyncio.sleep(0)
            await super().cleanup_graph(thread_id)

    model = BlockingModel()
    checkpoint = DelayedCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-stream-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )
    stream = workflow.stream(request)

    async def consume() -> None:
        async for _event in stream:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(model.started.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await stream.aclose()

    assert checkpoint.cleaned_graph_threads == ["42:req-stream-cancel"]


@pytest.mark.asyncio
async def test_active_registry_cancels_blocking_model_and_emits_cancelled_terminal(settings):
    class BlockingModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def generate(self, branch, context):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("阻塞模型不应正常返回")

    model = BlockingModel()
    checkpoint = MemoryCheckpoint()
    active = ActiveGenerationRegistry()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        active_generations=active,
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-active-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )
    events = []

    async def consume() -> None:
        async for event in workflow.stream(request):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(model.started.wait(), timeout=1)
    assert active.is_active("42:req-active-cancel")

    assert active.cancel("42:req-active-cancel") is True
    await asyncio.wait_for(consumer, timeout=1)

    assert events[-1].type == "failed"
    assert events[-1].error is not None
    assert events[-1].error.code == "cancelled"
    assert not [event for event in events if event.type == "completed"]
    assert checkpoint.cleaned_graph_threads == ["42:req-active-cancel"]
    assert not active.is_active("42:req-active-cancel")


@pytest.mark.asyncio
async def test_active_registry_cancels_all_blocking_multi_agent_reviewers(settings):
    settings.multi_agent_review_enabled = True
    settings.multi_agent_review_timeout_seconds = 10

    class BlockingReviewModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.all_started = asyncio.Event()
            self.started: set[ReviewerRole] = set()
            self.cancelled: set[ReviewerRole] = set()
            self.reviewer_tasks: dict[ReviewerRole, asyncio.Task] = {}

        async def review_role(self, role, artifact, context):
            current_task = asyncio.current_task()
            assert current_task is not None
            self.reviewer_tasks[role] = current_task
            self.calls.append((
                "review_role",
                {"role": role, "artifact": artifact, "context": context},
            ))
            self.started.add(role)
            if self.started == set(ReviewerRole):
                self.all_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.add(role)
                raise

    thread_id = "42:req-review-cancel"
    model = BlockingReviewModel()
    checkpoint = MemoryCheckpoint()
    active = ActiveGenerationRegistry()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        active_generations=active,
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-review-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.VUE_PROJECT,
    )
    events = []

    async def consume() -> None:
        async for event in workflow.stream(request):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(model.all_started.wait(), timeout=1)
    assert model.started == set(ReviewerRole)
    assert active.is_active(thread_id)

    assert active.cancel(thread_id) is True
    await asyncio.wait_for(consumer, timeout=1)

    reviewer_tasks = set(model.reviewer_tasks.values())
    assert model.cancelled == set(ReviewerRole)
    assert set(model.reviewer_tasks) == set(ReviewerRole)
    assert all(task.done() for task in reviewer_tasks)
    assert reviewer_tasks.isdisjoint(asyncio.all_tasks())
    assert events[-1].type == "failed"
    assert events[-1].error is not None
    assert events[-1].error.code == "cancelled"
    assert not [event for event in events if event.type == "completed"]
    assert not [call for call in model.calls if call[0] == "repair"]
    assert checkpoint.saved[thread_id]
    assert checkpoint.cleaned_graph_threads == [thread_id]
    assert not active.is_active(thread_id)


def test_existing_artifact_context_is_loaded_before_generation(app_factory, auth_headers, ndjson_parser):
    current_artifact = {
        "exists": True,
        "codeGenType": "HTML",
        "entry": "index.html",
        "artifact": "```html\n<html><body>old</body></html>\n```",
    }
    model = FakeModel()
    gateway = FakeToolGateway(artifact_context=current_artifact)
    client = TestClient(app_factory(model=model, gateway=gateway))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "completed"
    context_calls = [call for call in gateway.calls if call["name"] == "artifact_context"]
    assert len(context_calls) == 1
    assert context_calls[0]["toolCallId"] == "req-1:artifact_context"
    assert context_calls[0]["arguments"] == {"codeGenType": "HTML"}
    generate_context = next(data for name, data in model.calls if name == "generate")["context"]
    assert generate_context["currentArtifact"] == current_artifact


def test_artifact_context_failure_stops_before_model_generation(app_factory, auth_headers, ndjson_parser):
    class FailingContextGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "artifact_context":
                raise RuntimeError("ARTIFACT_CONTEXT_READ_FAILED: failed to read active artifact")
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=FailingContextGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "ARTIFACT_CONTEXT_READ_FAILED"
    assert not [call for call in model.calls if call[0] == "generate"]


@pytest.mark.parametrize("code_gen_type", ["HTML", "MULTI_FILE"])
def test_non_vue_repair_is_capped_without_source_snapshot(
    app_factory, auth_headers, ndjson_parser, code_gen_type
):
    model = FakeModel(reviews=[False, False, False, False])
    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=model, gateway=gateway))
    response = client.post(
        "/internal/v1/generations:stream",
        json=generation_payload(code_gen_type),
        headers=auth_headers,
    )
    events = ndjson_parser(response)
    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert events[-1]["type"] == "failed"
    assert not [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]


def test_vue_multi_agent_switch_off_keeps_first_review_without_snapshot(
    app_factory, auth_headers, ndjson_parser
):
    model = FakeModel()
    gateway = FakeToolGateway()

    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "completed"
    assert [data["artifact"] for name, data in model.calls if name == "review"] == [
        "artifact:VUE_PROJECT"
    ]
    assert not [call for call in model.calls if call[0] == "review_role"]
    assert not [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]


def test_vue_multi_agent_first_review_uses_snapshot_for_all_roles(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    source = "<template>REAL_SNAPSHOT_SOURCE</template>"
    gateway = FakeToolGateway(vue_source_snapshot={
        "files": [{"path": "src/App.vue", "content": source, "truncated": False}],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    })
    model = FakeModel()

    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert [call["toolCallId"] for call in snapshot_calls] == [
        "req-1:vue-source-snapshot:0"
    ]
    role_calls = [data for name, data in model.calls if name == "review_role"]
    assert {call["role"] for call in role_calls} == set(ReviewerRole)
    assert all(source in call["artifact"] for call in role_calls)
    assert all(call["artifact"] != "artifact:VUE_PROJECT" for call in role_calls)
    assert not [call for call in model.calls if call[0] == "review"]
    assert events[-1]["type"] == "completed"
    assert events[-1]["data"]["qualityPassed"] is True


def test_vue_multi_agent_minor_issue_passes_without_repair(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    model = FakeModel(role_reviews={
        ReviewerRole.REQUIREMENT: [
            reviewer_result(ReviewerRole.REQUIREMENT, severity=IssueSeverity.MINOR)
        ]
    })

    events = ndjson_parser(TestClient(app_factory(model=model)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "completed"
    assert events[-1]["data"]["repairCount"] == 0
    assert not [call for call in model.calls if call[0] == "repair"]


def test_vue_multi_agent_major_issue_repairs_with_feedback_and_reviews_fresh_snapshot(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    issue_code = "REQ_MAJOR_CODE"
    evidence = "REQ_MAJOR_EVIDENCE"
    repair_hint = "REQ_MAJOR_REPAIR_HINT"
    model = FakeModel(
        vue_tool_calls=1,
        role_reviews={
            ReviewerRole.REQUIREMENT: [
                reviewer_result(
                    ReviewerRole.REQUIREMENT,
                    severity=IssueSeverity.MAJOR,
                    code=issue_code,
                    evidence=evidence,
                    repair_hint=repair_hint,
                ),
                reviewer_result(ReviewerRole.REQUIREMENT),
            ],
            ReviewerRole.FUNCTION: [
                reviewer_result(ReviewerRole.FUNCTION),
                reviewer_result(ReviewerRole.FUNCTION),
            ],
            ReviewerRole.TECHNICAL: [
                reviewer_result(ReviewerRole.TECHNICAL),
                reviewer_result(ReviewerRole.TECHNICAL),
            ],
        },
    )
    gateway = FakeToolGateway()

    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_call = next(data for name, data in model.calls if name == "repair")
    feedback = repair_call["context"]["qualityReview"]["repairFeedback"]
    assert issue_code in feedback
    assert evidence in feedback
    assert repair_hint in feedback
    assert repair_call["context"]["toolResults"]
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert [call["toolCallId"] for call in snapshot_calls] == [
        "req-1:vue-source-snapshot:0",
        "req-1:vue-source-snapshot:1",
    ]
    second_round = [
        data for name, data in model.calls if name == "review_role"
    ][3:]
    assert len(second_round) == 3
    assert all("qualityReview" not in call["context"] for call in second_round)
    assert all("toolResults" not in call["context"] for call in second_round)
    assert all(call["context"]["codeGenType"] == "VUE_PROJECT" for call in second_round)
    assert all(set(call["context"]) == {
        "prompt",
        "codeGenType",
        "validation",
        "build",
    } for call in second_round)
    assert events[-1]["type"] == "completed"
    assert events[-1]["data"]["repairCount"] == 1


def test_vue_multi_agent_review_context_excludes_unbounded_generation_state(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    tool_source_marker = "UNIQUE_TOOL_SOURCE_2101"
    tool_source = tool_source_marker + ("x" * 100_001)
    conversation_marker = "UNIQUE_CONVERSATION_2102"
    metadata_marker = "UNIQUE_METADATA_2103"
    current_artifact_marker = "UNIQUE_CURRENT_ARTIFACT_2104"
    snapshot_marker = "UNIQUE_BOUNDED_SNAPSHOT_2105"

    class ToolingRepairModel(FakeModel):
        def __init__(self):
            super().__init__(role_reviews={
                ReviewerRole.REQUIREMENT: [
                    reviewer_result(
                        ReviewerRole.REQUIREMENT,
                        severity=IssueSeverity.MAJOR,
                        code="REPAIR_REQUIRED",
                    ),
                    reviewer_result(ReviewerRole.REQUIREMENT),
                ]
            })
            self.generate_turn = 0
            self.repair_turn = 0

        async def generate(self, branch, context):
            self.calls.append(("generate", {"branch": branch, "context": context}))
            self.generate_turn += 1
            if self.generate_turn == 1:
                return ModelTurn(
                    content="generate tool request",
                    tool_calls=[ToolCall(
                        name="file_read",
                        arguments={"relativeFilePath": "src/App.vue"},
                    )],
                )
            return ModelTurn(content="generated", finish_reason="STOP")

        async def repair(self, artifact, context):
            self.calls.append(("repair", {"artifact": artifact, "context": context}))
            self.repair_turn += 1
            if self.repair_turn == 1:
                return ModelTurn(
                    content="repair tool request",
                    tool_calls=[ToolCall(
                        name="file_read",
                        arguments={"relativeFilePath": "src/App.vue"},
                    )],
                )
            return ModelTurn(content="repaired", finish_reason="STOP")

    class SensitiveGateway(FakeToolGateway):
        def __init__(self):
            super().__init__(
                artifact_context={
                    "exists": True,
                    "content": current_artifact_marker,
                },
                vue_source_snapshot={
                    "files": [{
                        "path": "src/App.vue",
                        "content": snapshot_marker,
                        "truncated": False,
                    }],
                    "eligibleFileCount": 1,
                    "includedFileCount": 1,
                    "omittedFileCount": 0,
                    "truncated": False,
                },
            )

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            if name == "file_read":
                return {"content": tool_source}
            return result

    payload = generation_payload("VUE_PROJECT")
    payload["conversation"] = [{"role": "user", "content": conversation_marker}]
    payload["metadata"] = {"privateMarker": metadata_marker}
    model = ToolingRepairModel()

    events = ndjson_parser(TestClient(app_factory(
        model=model,
        gateway=SensitiveGateway(),
    )).post(
        "/internal/v1/generations:stream",
        json=payload,
        headers=auth_headers,
    ))

    role_calls = [data for name, data in model.calls if name == "review_role"]
    assert len(role_calls) == 6
    expected_context_keys = {"prompt", "codeGenType", "validation", "build"}
    forbidden_keys = {
        "conversation",
        "metadata",
        "currentArtifact",
        "toolResults",
        "appId",
        "requestId",
        "qualityReview",
    }
    forbidden_markers = {
        tool_source_marker,
        conversation_marker,
        metadata_marker,
        current_artifact_marker,
    }
    for call in role_calls:
        assert set(call["context"]) == expected_context_keys
        assert call["context"]["codeGenType"] == "VUE_PROJECT"
        assert forbidden_keys.isdisjoint(call["context"])
        serialized_context = json.dumps(call["context"], ensure_ascii=False)
        assert all(marker not in serialized_context for marker in forbidden_markers)
        assert snapshot_marker in call["artifact"]
        assert tool_source_marker not in call["artifact"]
        assert conversation_marker not in call["artifact"]
        assert metadata_marker not in call["artifact"]
        assert current_artifact_marker not in call["artifact"]

    repair_calls = [data for name, data in model.calls if name == "repair"]
    assert len(repair_calls) == 2
    assert all(call["context"]["toolResults"] for call in repair_calls)
    assert tool_source_marker in json.dumps(repair_calls, ensure_ascii=False)
    assert events[-1]["type"] == "completed"


def test_vue_multi_agent_two_blocking_rounds_exhaust_repairs_without_leaking_review_text(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    source_secret = "UNIQUE_SNAPSHOT_SOURCE_1049"
    evidence_secret = "UNIQUE_EVIDENCE_2857"
    repair_secret = "UNIQUE_REPAIR_HINT_3981"
    blocking = [
        reviewer_result(
            ReviewerRole.REQUIREMENT,
            severity=IssueSeverity.MAJOR,
            code="BLOCKING_REQUIREMENT",
            evidence=evidence_secret,
            repair_hint=repair_secret,
        )
        for _ in range(3)
    ]
    model = FakeModel(role_reviews={ReviewerRole.REQUIREMENT: blocking})
    gateway = FakeToolGateway(vue_source_snapshot={
        "files": [{"path": "src/App.vue", "content": source_secret, "truncated": False}],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    })
    checkpoint = MemoryCheckpoint()

    events = ndjson_parser(TestClient(app_factory(
        model=model,
        gateway=gateway,
        checkpoint=checkpoint,
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    repair_calls = [data for name, data in model.calls if name == "repair"]
    assert [call["context"]["repairCount"] for call in repair_calls] == [1, 2]
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert [call["toolCallId"] for call in snapshot_calls] == [
        "req-1:vue-source-snapshot:0",
        "req-1:vue-source-snapshot:1",
        "req-1:vue-source-snapshot:2",
    ]
    assert max(state["repairCount"] for state in checkpoint.saved["42:req-1"]) == 2
    serialized = json.dumps(events, ensure_ascii=False)
    assert source_secret not in serialized
    assert "BLOCKING_REQUIREMENT" not in serialized
    assert evidence_secret not in serialized
    assert repair_secret not in serialized
    assert all(
        call["context"]["qualityReview"]["repairFeedback"] not in serialized
        for call in repair_calls
    )
    assert "repair_feedback" not in serialized


def test_vue_multi_agent_snapshot_failure_is_stable_and_stops_before_review_or_repair(
    settings, app_factory, auth_headers, ndjson_parser
):
    settings.multi_agent_review_enabled = True
    secret = "UNIQUE_SNAPSHOT_PROVIDER_SECRET_7741"

    class SnapshotFailureGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "vue_source_snapshot":
                raise RuntimeError(f"snapshot provider failed: {secret}")
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(
        model=model,
        gateway=SnapshotFailureGateway(),
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"] == {
        "code": "MULTI_AGENT_REVIEW_SNAPSHOT_ERROR",
        "message": "MULTI_AGENT_REVIEW_SNAPSHOT_ERROR: Vue source snapshot is unavailable",
    }
    assert secret not in json.dumps(events, ensure_ascii=False)
    assert not [call for call in model.calls if call[0] == "review_role"]
    assert not [call for call in model.calls if call[0] == "repair"]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("timeout", "MULTI_AGENT_REVIEW_TIMEOUT"),
        ("invalid", "MULTI_AGENT_REVIEW_INVALID_OUTPUT"),
        ("model", "MULTI_AGENT_REVIEW_MODEL_ERROR"),
    ],
)
def test_vue_multi_agent_reviewer_system_errors_fail_without_repair(
    settings, app_factory, auth_headers, ndjson_parser, failure, expected_code
):
    settings.multi_agent_review_enabled = True
    settings.multi_agent_review_timeout_seconds = 0.01 if failure == "timeout" else 1

    class FailingRoleModel(FakeModel):
        async def review_role(self, role, artifact, context):
            self.calls.append((
                "review_role",
                {"role": role, "artifact": artifact, "context": context},
            ))
            if failure == "timeout":
                await asyncio.Event().wait()
            if role is ReviewerRole.REQUIREMENT:
                if failure == "invalid":
                    raise QualityReviewOutputError("invalid_json")
                raise RuntimeError("unique downstream provider detail")
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    model = FailingRoleModel()
    events = ndjson_parser(TestClient(app_factory(model=model)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == expected_code
    assert not [call for call in model.calls if call[0] == "repair"]


def test_multi_agent_review_feedback_is_excluded_from_business_checkpoint(
    settings, app_factory, auth_headers, ndjson_parser
):
    state = {
        "request_id": "req-1",
        "app_id": "42",
        "code_gen_type": "VUE_PROJECT",
        "quality_passed": False,
        "repair_count": 1,
        "tool_call_count": 2,
        "repair_feedback": "UNIQUE_CHECKPOINT_FEEDBACK",
    }
    payload = GenerationWorkflow._checkpoint_payload(state, "quality_review")
    assert set(payload) == {
        "node",
        "requestId",
        "appId",
        "codeGenType",
        "qualityPassed",
        "repairCount",
        "toolCallCount",
    }

    settings.multi_agent_review_enabled = True
    checkpoint = MemoryCheckpoint()
    events = ndjson_parser(TestClient(app_factory(checkpoint=checkpoint)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "completed"
    assert all(
        "repair_feedback" not in saved
        for saved in checkpoint.saved["42:req-1"]
    )


@pytest.mark.asyncio
async def test_vue_minor_review_details_never_enter_real_graph_checkpoint(settings):
    settings.multi_agent_review_enabled = True
    summary_secret = "UNIQUE_MINOR_SUMMARY_1501"
    evidence_secret = "UNIQUE_MINOR_EVIDENCE_1502"
    repair_secret = "UNIQUE_MINOR_REPAIR_HINT_1503"
    model = FakeModel(role_reviews={
        ReviewerRole.REQUIREMENT: [ReviewerResult(
            reviewer=ReviewerRole.REQUIREMENT,
            summary=summary_secret,
            issues=[QualityIssue(
                code="MINOR_ONLY",
                category="quality",
                summary="minor issue",
                evidence=evidence_secret,
                repair_hint=repair_secret,
                severity=IssueSeverity.MINOR,
            )],
        )]
    })
    checkpoint = GraphMemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )

    events = await workflow.run(GenerationRequest.model_validate(
        generation_payload("VUE_PROJECT")
    ))
    channel_values = await graph_channel_values(checkpoint, "42:req-1")
    serialized = json.dumps(channel_values, ensure_ascii=False)

    assert events[-1].type == "completed"
    assert all("reviewer_results" not in values for values in channel_values)
    assert all("quality_issues" not in values for values in channel_values)
    assert summary_secret not in serialized
    assert evidence_secret not in serialized
    assert repair_secret not in serialized


@pytest.mark.asyncio
async def test_vue_blocking_review_real_graph_checkpoint_keeps_only_bounded_feedback(settings):
    settings.multi_agent_review_enabled = True
    evidence = "UNIQUE_BLOCKING_EVIDENCE_1601"
    repair_hint = "UNIQUE_BLOCKING_REPAIR_HINT_1602"
    model = FakeModel(role_reviews={
        ReviewerRole.REQUIREMENT: [
            reviewer_result(
                ReviewerRole.REQUIREMENT,
                severity=IssueSeverity.MAJOR,
                code="BLOCKING_CHECKPOINT",
                evidence=evidence,
                repair_hint=repair_hint,
            ),
            reviewer_result(ReviewerRole.REQUIREMENT),
        ]
    })
    checkpoint = GraphMemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )

    events = await workflow.run(GenerationRequest.model_validate(
        generation_payload("VUE_PROJECT")
    ))
    channel_values = await graph_channel_values(checkpoint, "42:req-1")
    feedback_values = [
        values["repair_feedback"]
        for values in channel_values
        if values.get("repair_feedback")
    ]

    assert events[-1].type == "completed"
    assert all("reviewer_results" not in values for values in channel_values)
    assert all("quality_issues" not in values for values in channel_values)
    assert feedback_values
    assert all(len(feedback) <= 4000 for feedback in feedback_values)
    assert any(evidence in feedback and repair_hint in feedback for feedback in feedback_values)


@pytest.mark.asyncio
async def test_vue_blocking_review_resumes_from_feedback_without_repeating_reviewers(settings):
    settings.multi_agent_review_enabled = True
    evidence = "UNIQUE_RESUME_EVIDENCE_1701"
    repair_hint = "UNIQUE_RESUME_REPAIR_HINT_1702"

    class ResumeModel(FakeModel):
        def __init__(self):
            super().__init__(role_reviews={
                ReviewerRole.REQUIREMENT: [
                    reviewer_result(
                        ReviewerRole.REQUIREMENT,
                        severity=IssueSeverity.MAJOR,
                        code="BLOCKING_RESUME",
                        evidence=evidence,
                        repair_hint=repair_hint,
                    ),
                    reviewer_result(ReviewerRole.REQUIREMENT),
                ]
            })
            self.review_count_when_repair_called: int | None = None

        async def repair(self, artifact, context):
            self.review_count_when_repair_called = len([
                call for call in self.calls if call[0] == "review_role"
            ])
            return await super().repair(artifact, context)

    model = ResumeModel()
    checkpoint = GraphMemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    emitter = EventEmitter("req-1")
    graph = workflow._build_graph(
        emitter,
        "42:req-1",
        {"published": False, "completed": False},
    )
    config = {"configurable": {"thread_id": "42:req-1"}}
    request = GenerationRequest.model_validate(generation_payload("VUE_PROJECT"))
    initial = {
        "app_id": request.app_id,
        "request_id": request.request_id,
        "thread_id": "42:req-1",
        "prompt": request.prompt,
        "code_gen_type": request.code_gen_type.value,
        "conversation": [item.model_dump() for item in request.conversation],
        "metadata": request.metadata,
        "repair_count": 0,
        "tool_call_count": 0,
    }

    await graph.ainvoke(initial, config=config, interrupt_after=["quality_review"])
    first_checkpoint = await checkpoint.graph_saver.aget_tuple(config)
    assert first_checkpoint is not None
    feedback = first_checkpoint.checkpoint["channel_values"]["repair_feedback"]
    assert evidence in feedback
    assert repair_hint in feedback
    assert len([call for call in model.calls if call[0] == "review_role"]) == 3

    await graph.ainvoke(None, config=config, interrupt_after=["repair"])
    assert model.review_count_when_repair_called == 3
    repair_call = next(data for name, data in model.calls if name == "repair")
    assert repair_call["context"]["qualityReview"]["repairFeedback"] == feedback
    assert len([call for call in model.calls if call[0] == "review_role"]) == 3

    final_state = await graph.ainvoke(None, config=config)
    assert final_state["quality_passed"] is True
    assert final_state["repair_count"] == 1
    assert len([call for call in model.calls if call[0] == "review_role"]) == 6


def test_vue_build_failure_is_repaired_and_rebuilt_before_review(
    app_factory, auth_headers, ndjson_parser
):
    class BuildSequenceGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.build_results = [
                {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "vite failed"},
                {"built": True, "errorCode": "", "message": ""},
            ]

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                call = {
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                }
                self.calls.append(call)
                return self.build_results.pop(0)
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = BuildSequenceGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 2
    repair_calls = [data for name, data in model.calls if name == "repair"]
    assert len(repair_calls) == 1
    assert repair_calls[0]["context"]["build"] == {
        "built": False,
        "errorCode": "VUE_NPM_BUILD_FAILED",
        "message": "vite failed",
    }
    assert len([call for call in model.calls if call[0] == "review"]) == 1
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0]["toolCallId"] == "req-1:vue-source-snapshot:1"
    assert snapshot_calls[0]["arguments"] == {"codeGenType": "VUE_PROJECT"}
    review = next(data for name, data in model.calls if name == "review")
    assert review["artifact"] == (
        '{"eligibleFileCount":1,"files":[{"content":"<template><main>fixed</main></template>",'
        '"path":"src/App.vue","truncated":false}],"includedFileCount":1,'
        '"omittedFileCount":0,"truncated":false}'
    )
    assert "vueSourceSnapshot" not in review["context"]
    finished = next(
        event for event in events
        if event["type"] == "tool_finished" and event["data"]["tool"] == "vue_source_snapshot"
    )
    assert finished["data"]["result"] == {
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }
    assert "fixed" not in json.dumps(finished, ensure_ascii=False)
    assert events[-1]["type"] == "completed"


def test_vue_snapshot_failure_stops_before_review_without_fallback(
    app_factory, auth_headers, ndjson_parser
):
    class SnapshotFailureGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.build_count = 0

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                self.build_count += 1
                if self.build_count == 1:
                    return {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "broken"}
            if name == "vue_source_snapshot":
                raise RuntimeError("VUE_SOURCE_SNAPSHOT_FAILED: snapshot unavailable")
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=SnapshotFailureGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "VUE_SOURCE_SNAPSHOT_FAILED"
    assert not [call for call in model.calls if call[0] == "review"]
    assert not [event for event in events if event["type"] == "completed"]


def test_truncated_vue_snapshot_is_reviewed_after_repair(
    app_factory, auth_headers, ndjson_parser
):
    snapshot = {
        "files": [{"path": "src/App.vue", "content": "fixed partial", "truncated": True}],
        "eligibleFileCount": 2,
        "includedFileCount": 1,
        "omittedFileCount": 1,
        "truncated": True,
    }

    class RepairingGateway(FakeToolGateway):
        def __init__(self):
            super().__init__(vue_source_snapshot=snapshot)
            self.build_count = 0

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                self.build_count += 1
                if self.build_count == 1:
                    return {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "broken"}
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=RepairingGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    review = next(data for name, data in model.calls if name == "review")
    assert json.loads(review["artifact"])["truncated"] is True
    assert json.loads(review["artifact"])["files"][0]["truncated"] is True
    assert events[-1]["type"] == "completed"


def test_failed_repaired_vue_snapshot_review_triggers_second_repair(
    app_factory, auth_headers, ndjson_parser
):
    snapshots = [
        {
            "files": [{"path": "src/App.vue", "content": "snapshot-v1", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": False,
        },
        {
            "files": [{"path": "src/App.vue", "content": "snapshot-v2", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": False,
        },
    ]

    class SequencedSnapshotGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.snapshots = list(snapshots)

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            if name == "vue_source_snapshot":
                return self.snapshots.pop(0)
            return result

    model = FakeModel(reviews=[False, False, True])
    gateway = SequencedSnapshotGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert [call["toolCallId"] for call in snapshot_calls] == [
        "req-1:vue-source-snapshot:1",
        "req-1:vue-source-snapshot:2",
    ]
    review_calls = [data for name, data in model.calls if name == "review"]
    assert review_calls[0]["artifact"] == "artifact:VUE_PROJECT"
    assert "snapshot-v1" in review_calls[1]["artifact"]
    assert "snapshot-v2" not in review_calls[1]["artifact"]
    assert "snapshot-v2" in review_calls[2]["artifact"]
    assert "snapshot-v1" not in review_calls[2]["artifact"]
    assert events[-1]["type"] == "completed"
    assert events[-1]["data"]["repairCount"] == 2


def test_vue_snapshot_review_failure_does_not_leak_source_to_events(
    app_factory, auth_headers, ndjson_parser
):
    secret = "TOP_SECRET_SOURCE"
    snapshot = {
        "files": [{"path": "src/App.vue", "content": secret, "truncated": False}],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }

    class LeakingReviewModel(FakeModel):
        async def review(self, artifact, context):
            self.calls.append(("review", {"artifact": artifact, "context": context}))
            if artifact == "artifact:VUE_PROJECT":
                return False
            raise RuntimeError(f"provider rejected artifact: {artifact}")

    model = LeakingReviewModel()
    events = ndjson_parser(TestClient(app_factory(
        model=model,
        gateway=FakeToolGateway(vue_source_snapshot=snapshot),
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["message"] == "Vue source snapshot review failed"
    serialized_events = json.dumps(events, ensure_ascii=False)
    assert secret not in serialized_events
    assert "src/App.vue" not in serialized_events
    assert len([call for call in model.calls if call[0] == "review"]) == 2


def test_vue_build_failure_exhausts_two_repairs_without_review_or_completion(
    app_factory, auth_headers, ndjson_parser
):
    class AlwaysFailingBuildGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                call = {
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                }
                self.calls.append(call)
                return {
                    "built": False,
                    "errorCode": "VUE_NPM_BUILD_FAILED",
                    "message": "vite failed repeatedly",
                }
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = AlwaysFailingBuildGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 3
    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert not [call for call in model.calls if call[0] == "review"]
    assert events[-1]["type"] == "failed"
    assert not [event for event in events if event["type"] == "completed"]


def test_after_build_requires_literal_boolean_true():
    assert _after_build(
        {"build": {"built": "false"}, "repair_count": 0},
        max_attempts=2,
    ) == "repair"


def test_validation_failure_after_build_repair_does_not_reuse_old_build_error(
    app_factory, auth_headers, ndjson_parser
):
    class BuildThenValidationFailureGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.validation_results = [
                {"valid": True, "errors": []},
                {"valid": False, "errors": [{"code": "VALIDATION_AFTER_BUILD_REPAIR"}]},
                {"valid": False, "errors": [{"code": "VALIDATION_AFTER_BUILD_REPAIR"}]},
            ]

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name in {"artifact_validate", "project_build"}:
                self.calls.append({
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                })
                if name == "artifact_validate":
                    return self.validation_results.pop(0)
                return {
                    "built": False,
                    "errorCode": "VUE_NPM_BUILD_FAILED",
                    "message": "old build failure",
                }
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = BuildThenValidationFailureGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert not [call for call in model.calls if call[0] == "review"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] != "VUE_NPM_BUILD_FAILED"
    assert "VALIDATION_AFTER_BUILD_REPAIR" in events[-1]["error"]["message"]
    assert not [event for event in events if event["type"] == "completed"]


def test_truncated_multi_file_response_fails_before_publication(app_factory, auth_headers, ndjson_parser):
    class TruncatedModel(FakeModel):
        async def generate(self, branch: str, context: dict) -> ModelTurn:
            return ModelTurn(content="partial", finish_reason="LENGTH")

    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=TruncatedModel(), gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "MODEL_OUTPUT_TRUNCATED"
    assert "MODEL_OUTPUT_TRUNCATED" in events[-1]["error"]["message"]
    assert not [call for call in gateway.calls if call["name"] == "artifact_publish"]


def test_multi_file_completes_only_after_publication(app_factory, auth_headers, ndjson_parser):
    gateway = FakeToolGateway()
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "completed"
    calls = [call["name"] for call in gateway.calls]
    assert "artifact_publish" in calls
    assert "project_build" not in calls


def test_html_completes_only_after_successful_publication(app_factory, auth_headers, ndjson_parser):
    """HTML 必须在 Spring 确认发布后才产生 completed 终态。"""
    gateway = FakeToolGateway()
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    publish_calls = [call for call in gateway.calls if call["name"] == "artifact_publish"]
    assert len(publish_calls) == 1
    assert publish_calls[0]["arguments"]["codeGenType"] == "HTML"
    publish_finished = next(i for i, event in enumerate(events)
                            if event["type"] == "tool_finished" and event["node"] == "artifact_publish")
    completed = next(i for i, event in enumerate(events) if event["type"] == "completed")
    assert publish_finished < completed


def test_html_publication_rejection_fails_without_completed(app_factory, auth_headers, ndjson_parser):
    """Spring 拒绝 HTML 发布时工作流只能发送 failed，不得伪造成功。"""
    class RejectingGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            return {"published": False} if name == "artifact_publish" else result

    gateway = RejectingGateway()
    events = ndjson_parser(TestClient(app_factory(gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"] == {
        "code": "GENERATION_FAILED",
        "message": "Spring rejected artifact publication",
    }
    assert len([call for call in gateway.calls if call["name"] == "artifact_publish"]) == 1
    assert not [event for event in events if event["type"] == "completed"]


def test_spring_business_error_code_reaches_failed_event(app_factory, auth_headers, ndjson_parser):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        requests.append(body)
        if body["toolName"] == "artifact_context":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"exists": False, "codeGenType": "HTML"},
                    "message": "ok",
                },
            )
        if body["toolName"] == "artifact_validate":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"valid": True, "errors": []}, "message": "ok"},
            )
        assert body["toolName"] == "artifact_publish"
        return httpx.Response(
            200,
            json={
                "code": 50001,
                "data": None,
                "message": "TOOL_EXECUTION_INDETERMINATE",
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    with TestClient(app_factory(gateway=gateway)) as client:
        events = ndjson_parser(client.post(
            "/internal/v1/generations:stream",
            json=generation_payload("HTML"),
            headers=auth_headers,
        ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"] == {
        "code": "TOOL_EXECUTION_INDETERMINATE",
        "message": "TOOL_EXECUTION_INDETERMINATE: Spring tool request failed (code=50001)",
    }
    assert len([request for request in requests if request["toolName"] == "artifact_publish"]) == 1
    assert not [event for event in events if event["type"] == "completed"]


def test_html_remains_completed_when_graph_checkpoint_fails_after_publication(
    app_factory, auth_headers, ndjson_parser
):
    """HTML 已提交后即使图 checkpoint 失败，也只补发一次 completed。"""
    gateway = FakeToolGateway()

    class FailAfterPublishSaver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            if any(call["name"] == "artifact_publish" for call in gateway.calls):
                raise RuntimeError("checkpoint failed after html publication")
            return await super().aput(config, checkpoint, metadata, new_versions)

    class GraphCheckpoint(MemoryCheckpoint):
        def __init__(self):
            super().__init__()
            self.graph_saver = FailAfterPublishSaver()

        def get_graph_saver(self):
            return self.graph_saver

    events = ndjson_parser(TestClient(app_factory(gateway=gateway, checkpoint=GraphCheckpoint())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))
    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]


def test_multi_file_remains_completed_when_graph_checkpoint_fails_after_publication(
    app_factory, auth_headers, ndjson_parser
):
    gateway = FakeToolGateway()

    class FailAfterPublishSaver(InMemorySaver):
        """模拟 Spring 已发布后 LangGraph 自动 checkpoint 写入失败。"""

        async def aput(self, config, checkpoint, metadata, new_versions):
            if any(call["name"] == "artifact_publish" for call in gateway.calls):
                raise RuntimeError("checkpoint failed after artifact publication")
            return await super().aput(config, checkpoint, metadata, new_versions)

    class GraphCheckpoint(MemoryCheckpoint):
        """同时提供业务 checkpoint 与故障注入用 LangGraph saver。"""

        def __init__(self):
            super().__init__()
            self.graph_saver = FailAfterPublishSaver()

        def get_graph_saver(self):
            return self.graph_saver

    client = TestClient(app_factory(gateway=gateway, checkpoint=GraphCheckpoint()))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))

    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]
    assert any(call["name"] == "artifact_publish" for call in gateway.calls)


def test_multi_file_records_publication_before_tool_finished_event(
    app_factory, auth_headers, ndjson_parser, monkeypatch
):
    gateway = FakeToolGateway()
    original_emit = EventEmitter.emit
    failed_once = False

    async def fail_publish_finished_once(self, event_type, node, *, data=None, error=None):
        nonlocal failed_once
        if (
            not failed_once
            and event_type == "tool_finished"
            and node == "artifact_publish"
        ):
            failed_once = True
            raise RuntimeError("tool_finished delivery failed")
        return await original_emit(self, event_type, node, data=data, error=error)

    monkeypatch.setattr(EventEmitter, "emit", fail_publish_finished_once)
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))

    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]
    assert any(call["name"] == "artifact_publish" for call in gateway.calls)


def test_vue_agent_tool_calls_are_bounded_and_identified(app_factory, auth_headers, ndjson_parser):
    model = FakeModel(vue_tool_calls=10)
    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=model, gateway=gateway))
    events = ndjson_parser(
        client.post(
            "/internal/v1/generations:stream",
            json=generation_payload("VUE_PROJECT"),
            headers=auth_headers,
        )
    )
    vue_calls = [call for call in gateway.calls if call["name"] == "file_read"]
    assert len(vue_calls) == 4
    assert all(call["toolCallId"].startswith("req-1:vue-generate:") for call in vue_calls)
    assert len({call["toolCallId"] for call in vue_calls}) == 4
    assert all(call["appId"] == "42" for call in vue_calls)
    assert all(call["requestId"] == "req-1" for call in vue_calls)
    assert all("appId" not in call["arguments"] for call in vue_calls)
    assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in vue_calls)
    assert len([event for event in events if event["type"] == "tool_started" and event["node"] == "vue_agent"]) == 4
    assert len([event for event in events if event["type"] == "tool_finished" and event["node"] == "vue_agent"]) == 4


@pytest.mark.parametrize(
    ("finish_reason", "error_code"),
    [
        ("LENGTH", "MODEL_OUTPUT_TRUNCATED"),
        ("CONTENT_FILTER", "MODEL_OUTPUT_BLOCKED"),
        ("CONTENT_FILTERED", "MODEL_OUTPUT_BLOCKED"),
    ],
)
def test_incomplete_first_vue_turn_fails_before_executing_model_tool(
    app_factory, auth_headers, ndjson_parser, finish_reason, error_code
):
    class IncompleteVueModel(FakeModel):
        async def generate(self, branch, context):
            return ModelTurn(
                content="partial",
                tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                finish_reason=finish_reason,
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=IncompleteVueModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert not [call for call in gateway.calls if call["name"] == "file_read"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == error_code


@pytest.mark.parametrize(
    ("finish_reason", "error_code"),
    [
        ("LENGTH", "MODEL_OUTPUT_TRUNCATED"),
        ("CONTENT_FILTER", "MODEL_OUTPUT_BLOCKED"),
    ],
)
def test_incomplete_later_vue_turn_emits_no_content_or_tool_call(
    app_factory, auth_headers, ndjson_parser, finish_reason, error_code
):
    class IncompleteSecondTurnModel(FakeModel):
        async def generate(self, branch, context):
            if not context.get("toolResults"):
                return ModelTurn(
                    content="first turn",
                    tool_calls=[ToolCall(
                        name="file_read",
                        arguments={"relativeFilePath": "src/App.vue"},
                    )],
                )
            return ModelTurn(
                content="must-not-be-emitted",
                tool_calls=[ToolCall(
                    name="file_modify",
                    arguments={
                        "relativeFilePath": "src/App.vue",
                        "oldContent": "broken",
                        "newContent": "fixed",
                    },
                )],
                finish_reason=finish_reason,
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(
        model=IncompleteSecondTurnModel(),
        gateway=gateway,
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    vue_calls = [call for call in gateway.calls if ":vue-generate:" in call["toolCallId"]]
    assert [call["name"] for call in vue_calls] == ["file_read"]
    emitted_content = [
        event["data"]["content"]
        for event in events
        if event["type"] == "content_delta" and event["node"] == "vue_agent"
    ]
    assert emitted_content == ["first turn"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == error_code


@pytest.mark.asyncio
async def test_vue_tool_loop_sums_token_usage_across_model_turns(app_factory):
    class TokenUsageModel(FakeModel):
        async def generate(self, branch, context):
            self.calls.append(("generate", {"branch": branch, "context": context}))
            if len(context.get("toolResults", [])) == 0:
                return ModelTurn(
                    content="inspect",
                    tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                    token_usage={"input_tokens": 2, "output_tokens": 3},
                )
            return ModelTurn(
                content="done",
                finish_reason="STOP",
                token_usage={"input_tokens": 5, "output_tokens": 7},
            )

    app = app_factory(model=TokenUsageModel(), gateway=FakeToolGateway())
    result = await app.state.workflow._run_vue_tool_loop(
        state={
            "app_id": "42",
            "request_id": "req-1",
            "code_gen_type": "VUE_PROJECT",
            "context": {},
            "tool_call_count": 0,
        },
        emitter=EventEmitter("req-1"),
        thread_id="42:req-1",
        node="vue_agent",
        call_id_prefix="req-1:vue-generate",
        invoke_model=lambda context: app.state.model.generate("VUE_PROJECT", context),
    )

    assert result["token_usage"] == {"input_tokens": 7, "output_tokens": 10}


def test_vue_repair_executes_file_tools_through_spring(app_factory, auth_headers, ndjson_parser):
    class VueRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False, True])
            self.repair_turn = 0

        async def repair(self, artifact, context):
            self.calls.append(("repair", {"artifact": artifact, "context": context}))
            self.repair_turn += 1
            if self.repair_turn == 1:
                return ModelTurn(
                    content="read current component",
                    tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                )
            if self.repair_turn == 2:
                return ModelTurn(
                    content="apply targeted fix",
                    tool_calls=[ToolCall(
                        name="file_modify",
                        arguments={
                            "relativeFilePath": "src/App.vue",
                            "oldContent": "broken",
                            "newContent": "fixed",
                        },
                    )],
                )
            return ModelTurn(content="repair completed", finish_reason="STOP")

    model = VueRepairModel()
    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:1:")
    ]
    assert [call["name"] for call in repair_calls] == ["file_read", "file_modify"]
    assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in repair_calls)
    repair_model_calls = [data for name, data in model.calls if name == "repair"]
    assert repair_model_calls[1]["context"]["toolResults"][0]["tool"] == "file_read"
    assert repair_model_calls[2]["context"]["toolResults"][1]["tool"] == "file_modify"
    assert len([call for call in gateway.calls if call["name"] == "artifact_validate"]) == 2
    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 2
    assert events[-1]["type"] == "completed"
    assert "artifact" not in events[-1]["data"]
    assert events[-1]["data"]["built"] is True
    assert events[-1]["data"]["toolCallCount"] == 2


def test_invalid_vue_repair_tool_is_rejected_before_spring_gateway(
    app_factory, auth_headers, ndjson_parser
):
    class InvalidRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[ToolCall(name="search_reference", arguments={"q": "layout"})],
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=InvalidRepairModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert not [call for call in gateway.calls if call["name"] == "search_reference"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"


@pytest.mark.parametrize(
    ("controlled_name", "controlled_value"),
    [("appId", "other-app"), ("codeGenType", "HTML")],
)
def test_vue_repair_rejects_model_controlled_arguments_before_spring(
    app_factory, auth_headers, ndjson_parser, controlled_name, controlled_value
):
    class ControlledArgumentModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[ToolCall(
                    name="file_read",
                    arguments={
                        "relativeFilePath": "src/App.vue",
                        controlled_name: controlled_value,
                    },
                )],
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=ControlledArgumentModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:")
    ]
    assert repair_calls == []
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"
    assert controlled_name in events[-1]["error"]["message"]


def test_vue_generation_and_repair_share_total_tool_budget(
    app_factory, auth_headers, ndjson_parser
):
    class BudgetedRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False, True], vue_tool_calls=3)

        async def repair(self, artifact, context):
            self.calls.append(("repair", {"artifact": artifact, "context": context}))
            if len(context.get("toolResults", [])) == 4:
                return ModelTurn(content="repair completed", finish_reason="STOP")
            return ModelTurn(
                content="continue repair",
                tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
            )

    model = BudgetedRepairModel()
    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    vue_file_calls = [call for call in gateway.calls if call["name"] == "file_read"]
    assert len(vue_file_calls) == 4
    assert len([call for call in vue_file_calls if ":vue-generate:" in call["toolCallId"]]) == 3
    assert len([call for call in vue_file_calls if ":vue-repair:1:" in call["toolCallId"]]) == 1
    repair_model_calls = [data for name, data in model.calls if name == "repair"]
    assert len(repair_model_calls) == 2
    assert len(repair_model_calls[1]["context"]["toolResults"]) == 4
    assert repair_model_calls[1]["context"]["toolResults"][-1]["toolCallId"] == (
        "req-1:vue-repair:1:1"
    )
    assert events[-1]["type"] == "completed"


def test_vue_repair_checks_cancellation_before_each_tool_call(
    app_factory, auth_headers, ndjson_parser
):
    class TwoToolRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[
                    ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"}),
                    ToolCall(
                        name="file_modify",
                        arguments={
                            "relativeFilePath": "src/App.vue",
                            "oldContent": "broken",
                            "newContent": "fixed",
                        },
                    ),
                ],
            )

    class CancellingGateway(FakeToolGateway):
        cancellations = None

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            if tool_call_id.startswith("req-1:vue-repair:1:1"):
                self.cancellations.cancel("42:req-1")
            return result

    gateway = CancellingGateway()
    app = app_factory(model=TwoToolRepairModel(), gateway=gateway)
    gateway.cancellations = app.state.cancellations
    events = ndjson_parser(TestClient(app).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:1:")
    ]
    assert [call["name"] for call in repair_calls] == ["file_read"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "cancelled"


def test_invalid_vue_tool_is_rejected_before_spring_gateway(app_factory, auth_headers, ndjson_parser):
    class InvalidToolModel(FakeModel):
        async def generate(self, branch, context):
            if branch == "VUE_PROJECT":
                return ModelTurn(tool_calls=[ToolCall(name="search_reference", arguments={"q": "layout"})], content="")
            return await super().generate(branch, context)

    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=InvalidToolModel(), gateway=gateway))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert [call["name"] for call in gateway.calls] == ["artifact_context"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"
    assert "search_reference" in events[-1]["error"]["message"]


def test_cancel_endpoint_marks_generation_cancelled(app_factory, auth_headers):
    app = app_factory()
    client = TestClient(app)
    response = client.post(
        "/internal/v1/generations/req-cancel:cancel",
        json={"appId": "42"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    assert response.json() == {"requestId": "req-cancel", "status": "cancelled"}
    assert app.state.cancellations.is_cancelled("42:req-cancel")


def test_cancelled_generation_has_explicit_terminal_event(app_factory, auth_headers, ndjson_parser):
    checkpoint = MemoryCheckpoint()
    app = app_factory(checkpoint=checkpoint)
    client = TestClient(app)
    client.post(
        "/internal/v1/generations/req-cancel:cancel",
        json={"appId": "42"},
        headers=auth_headers,
    )
    payload = generation_payload("HTML")
    payload["requestId"] = "req-cancel"
    events = ndjson_parser(
        client.post("/internal/v1/generations:stream", json=payload, headers=auth_headers)
    )
    assert events[-1]["type"] == "failed"
    assert events[-1]["data"]["status"] == "cancelled"
    assert events[-1]["error"]["code"] == "cancelled"
    assert not app.state.cancellations.is_cancelled("42:req-cancel")
    assert checkpoint.cleaned_graph_threads == ["42:req-cancel"]


def test_health_ready_reports_checkpoint_failure(app_factory):
    checkpoint = MemoryCheckpoint()
    checkpoint.available = False
    client = TestClient(app_factory(checkpoint=checkpoint))
    assert client.get("/internal/v1/health/live").json() == {"status": "live"}
    response = client.get("/internal/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
