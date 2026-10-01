from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_service.orchestration.customer_service_evaluation import (
    EvaluationResult,
    evaluate_customer_service,
    load_evaluation_dataset,
)
from ai_service.orchestration.customer_service_health import (
    CustomerServiceDependencyHealth,
    probe_customer_service_health,
)


FIXTURE = Path(__file__).parent / "fixtures" / "customer_service_eval.json"


def test_disabled_app_does_not_import_customer_service_runtime_modules():
    script = r'''
import importlib.abc
import sys
from fastapi.testclient import TestClient

blocked = {
    "ai_service.infrastructure.knowledge_download",
    "ai_service.infrastructure.milvus_knowledge",
    "ai_service.infrastructure.spring_knowledge_lease",
    "ai_service.models.embeddings",
    "ai_service.models.reranker",
    "ai_service.orchestration.customer_service_rag",
    "ai_service.orchestration.document_etl",
}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError(f"disabled app imported {fullname}")
        return None

sys.meta_path.insert(0, Blocker())
from ai_service.app import create_app
from ai_service.config import Settings

calls = []

class FakeModel:
    pass

class FakeGateway:
    async def close(self):
        calls.append("gateway-close")

class FakeCheckpoint:
    async def start(self):
        calls.append("checkpoint-start")
    async def close(self):
        calls.append("checkpoint-close")
    async def ping(self):
        return True

settings = Settings(
    internal_bearer_token="test-secret",
    spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
    spring_gateway_bearer_token="spring-secret",
    checkpoint_enabled=False,
    customer_service_rag_enabled=False,
)
app = create_app(
    settings=settings,
    model=FakeModel(),
    tool_gateway=FakeGateway(),
    checkpoint=FakeCheckpoint(),
    milvus_client_factory=lambda **_kwargs: calls.append("milvus"),
    reranker_model_factory=lambda *_args, **_kwargs: calls.append("reranker"),
)
with TestClient(app) as client:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checkpoint": True}
    assert app.state.customer_service_rag_service is None
assert not any(name in sys.modules for name in blocked)
assert calls == ["checkpoint-start", "checkpoint-close", "gateway-close"]
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_customer_service_health_is_disabled_and_does_not_affect_ready(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = False
    client = TestClient(app_factory())

    assert client.get("/health/ready").json() == {
        "status": "ready",
        "checkpoint": True,
    }
    response = client.get(
        "/internal/v1/customer-service/health", headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "status": "disabled",
        "reason": "CUSTOMER_SERVICE_RAG_DISABLED",
        "ready": False,
        "degraded": False,
        "dependencies": {},
    }


def test_customer_service_degradation_is_isolated_from_generation_readiness(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    provider = CustomerServiceDependencyHealth({
        "milvus": lambda: False,
        "reranker": lambda: True,
        "embedding": lambda: (_ for _ in ()).throw(
            RuntimeError("secret-uri api-key vendor body")
        ),
    })
    client = TestClient(app_factory(
        knowledge_etl_service=object(),
        customer_service_health_provider=provider,
    ))

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

    response = client.get(
        "/internal/v1/customer-service/health", headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json() == {
        "enabled": True,
        "status": "degraded",
        "reason": "CUSTOMER_SERVICE_DEPENDENCY_UNAVAILABLE",
        "ready": False,
        "degraded": True,
        "dependencies": {
            "answerModel": False,
            "answerService": False,
            "embedding": False,
            "etl": False,
            "leaseValidator": False,
            "milvus": False,
            "reranker": True,
        },
    }
    assert "secret-uri" not in response.text
    assert "api-key" not in response.text
    assert "vendor body" not in response.text


def test_customer_service_health_requires_internal_auth(app_factory):
    response = TestClient(app_factory()).get(
        "/internal/v1/customer-service/health"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_probe_times_out_fail_safe_without_blocking_event_loop():
    heartbeat = asyncio.Event()

    def blocking_probe():
        import time
        time.sleep(0.2)
        return True

    async def tick():
        await asyncio.sleep(0.01)
        heartbeat.set()

    ticker = asyncio.create_task(tick())
    summary = await probe_customer_service_health(
        CustomerServiceDependencyHealth({"milvus": blocking_probe}),
        enabled=True,
        timeout_seconds=0.02,
    )
    await ticker

    assert heartbeat.is_set()
    assert summary.status == "degraded"
    assert summary.reason == "CUSTOMER_SERVICE_HEALTH_PROBE_TIMEOUT"
    assert summary.dependencies == {
        "answerModel": False, "answerService": False,
        "embedding": False, "etl": False, "leaseValidator": False,
        "milvus": False, "reranker": False,
    }


def test_customer_service_health_degrades_when_answer_service_is_missing(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True

    class Healthy:
        def health_ready(self):
            return True

    class HealthyStore:
        async def ping(self):
            return True

    app = app_factory(
        knowledge_etl_service=HealthyStore(),
        embedding_provider=Healthy(),
        knowledge_store=HealthyStore(),
        mutation_coordinator=Healthy(),
        customer_service_rag_service=None,
    )
    with TestClient(app) as client:
        app.state.customer_service_rag_service = None
        response = client.get(
            "/internal/v1/customer-service/health", headers=auth_headers,
        )

    assert response.status_code == 503
    assert response.json()["reason"] == "CUSTOMER_SERVICE_DEPENDENCY_UNAVAILABLE"
    assert response.json()["dependencies"]["answerService"] is False


def test_customer_service_health_degrades_when_answer_dependency_is_unhealthy(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True

    class Dependency:
        def __init__(self, ready=True):
            self.ready = ready

        def health_ready(self):
            return self.ready

    class Store(Dependency):
        async def ping(self):
            return self.ready

    with TestClient(app_factory(
        knowledge_etl_service=Store(),
        embedding_provider=Dependency(),
        knowledge_store=Store(),
        mutation_coordinator=Dependency(),
        customer_service_rag_service=Dependency(),
        model=Dependency(ready=False),
    )) as client:
        response = client.get(
            "/internal/v1/customer-service/health", headers=auth_headers,
        )

    assert response.status_code == 503
    assert response.json()["reason"] == "CUSTOMER_SERVICE_DEPENDENCY_UNAVAILABLE"
    assert response.json()["dependencies"]["answerService"] is True
    assert response.json()["dependencies"]["answerModel"] is False


def test_versioned_evaluation_fixture_is_synthetic_and_explicit():
    dataset = load_evaluation_dataset(FIXTURE)

    assert dataset.schema_version == "customer-service-rag-eval/v1"
    assert dataset.dataset_version == "2026-10-01"
    assert len(dataset.entries) >= 6
    assert any("错" in entry.id or "typo" in entry.id for entry in dataset.entries)
    assert any("error" in entry.id for entry in dataset.entries)
    assert any("injection" in entry.id for entry in dataset.entries)
    assert all(
        entry.expected_document_ids
        for entry in dataset.entries if entry.expected_answerable
    )
    assert all(
        not entry.expected_document_ids
        for entry in dataset.entries if not entry.expected_answerable
    )
    assert "secret" not in FIXTURE.read_text(encoding="utf-8").lower()


@pytest.mark.asyncio
async def test_offline_evaluator_computes_rank_no_answer_citation_and_latency_metrics():
    dataset = load_evaluation_dataset(FIXTURE)
    records = {
        "cn-paraphrase-refund": EvaluationResult(
            retrieved_document_ids=("irrelevant", "synthetic-refund-policy"),
            answered=True,
            citation_document_ids=("synthetic-refund-policy",),
            latency_ms=10,
        ),
        "cn-typo-password": EvaluationResult(
            retrieved_document_ids=("synthetic-account-recovery",),
            answered=True,
            citation_document_ids=("synthetic-account-recovery",),
            latency_ms=20,
        ),
        "stable-error-code": EvaluationResult(
            retrieved_document_ids=(
                "x", "x", "synthetic-error-catalog", "unknown-citation"
            ),
            answered=True,
            citation_document_ids=("unknown-citation",),
            latency_ms=30,
        ),
        "cn-paraphrase-delivery": EvaluationResult(
            retrieved_document_ids=("synthetic-delivery-guide",),
            answered=True,
            citation_document_ids=(
                "synthetic-delivery-guide", "synthetic-delivery-guide"
            ),
            latency_ms=40,
        ),
        "no-answer-unsupported-topic": EvaluationResult(
            retrieved_document_ids=(), answered=False,
            citation_document_ids=(), latency_ms=50,
        ),
        "prompt-injection-system-prompt": EvaluationResult(
            retrieved_document_ids=("irrelevant",), answered=False,
            citation_document_ids=(), latency_ms=60,
        ),
    }

    async def runner(entry):
        return records[entry.id]

    report = await evaluate_customer_service(dataset, runner)

    assert report.sample_count == 6
    assert report.answerable_count == 4
    assert report.no_answer_count == 2
    assert report.recall_at_8 == pytest.approx(1.0)
    assert report.mrr_at_3 == pytest.approx((1 / 2 + 1 + 1 / 2 + 1) / 4)
    assert report.ndcg_at_3 == pytest.approx((1 / 1.5849625 + 1 + 1 / 1.5849625 + 1) / 4)
    assert report.no_answer_accuracy == pytest.approx(1.0)
    assert report.citation_validity == pytest.approx(4 / 6)
    assert report.latency_ms == {
        "count": 6,
        "min": 10.0,
        "p50": 35.0,
        "p95": 57.5,
        "max": 60.0,
        "mean": 35.0,
    }


@pytest.mark.asyncio
async def test_offline_evaluator_handles_empty_dataset_without_division_by_zero():
    empty = json.loads(FIXTURE.read_text(encoding="utf-8"))
    empty["entries"] = []

    async def runner(_entry):
        raise AssertionError("runner must not be called")

    report = await evaluate_customer_service(empty, runner)
    assert report.sample_count == 0
    assert report.recall_at_8 == 0
    assert report.mrr_at_3 == 0
    assert report.ndcg_at_3 == 0
    assert report.no_answer_accuracy == 0
    assert report.citation_validity == 0
    assert report.latency_ms == {
        "count": 0, "min": 0.0, "p50": 0.0,
        "p95": 0.0, "max": 0.0, "mean": 0.0,
    }
