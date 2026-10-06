from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_service.infrastructure.milvus_knowledge import (
    KnowledgeMutationLease,
    MilvusKnowledgeError,
    RebuildPlan,
    RebuildResult,
)
from ai_service.models.embeddings import EmbeddingOutputError
from ai_service.orchestration.document_etl import DocumentETLError, KnowledgeEtlService


def rebuild_lease(*, alias: str = "customer_service_knowledge") -> dict:
    return {
        "scope": f"collection:{alias}",
        "operationId": "rebuild-op-1",
        "operation": "REBUILD",
        "fence": 19,
        "expiresAt": time.time() + 300,
        "proof": "rebuild-proof-secret",
    }


def rebuild_payload(*, alias: str = "customer_service_knowledge") -> dict:
    return {
        "operation": "REBUILD",
        "collectionAlias": alias,
        "documents": [
            {
                "documentId": "doc-1",
                "documentVersion": 2,
                "fileName": "one.txt",
                "fileType": "TXT",
                "signedUrl": "https://oss.example.test/one?Signature=secret-one",
                "sha256": hashlib.sha256(b"first").hexdigest(),
            },
            {
                "documentId": "doc-2",
                "documentVersion": 3,
                "fileName": "two.txt",
                "fileType": "TXT",
                "signedUrl": "https://oss.example.test/two?Signature=secret-two",
                "sha256": hashlib.sha256(b"second").hexdigest(),
            },
        ],
        "etlVersion": "etl-v1",
        "lease": rebuild_lease(alias=alias),
    }


class FakeRebuildService:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[dict] = []

    async def rebuild(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return RebuildResult("staging-v1", len(kwargs["documents"]), 2, False)


def test_rebuild_requires_auth_and_feature_enablement(app_factory, auth_headers):
    payload = rebuild_payload()
    client = TestClient(app_factory())
    assert client.post(
        "/internal/v1/customer-service/knowledge:rebuild", json=payload,
    ).status_code == 401
    response = client.post(
        "/internal/v1/customer-service/knowledge:rebuild",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CUSTOMER_SERVICE_RAG_DISABLED"


def test_rebuild_response_and_request_match_java_contract(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    service = FakeRebuildService()
    payload = rebuild_payload()
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=payload,
            headers=auth_headers,
        )
    assert response.status_code == 200
    assert response.json() == {
        "operation": "REBUILD",
        "status": "SUCCEEDED",
        "collectionAlias": "customer_service_knowledge",
        "etlVersion": "etl-v1",
        "documentCount": 2,
        "idempotent": False,
    }
    call = service.calls[0]
    assert call["documents"][0].document_id == "doc-1"
    assert call["documents"][1].signed_url.endswith("secret-two")
    assert (
        call["lease"].scope,
        call["lease"].operation_id,
        call["lease"].operation,
        call["lease"].fence,
        call["lease"].expires_at,
        call["lease"].proof,
    ) == (
        "collection:customer_service_knowledge",
        "rebuild-op-1",
        "REBUILD",
        19,
        payload["lease"]["expiresAt"],
        "rebuild-proof-secret",
    )


def test_empty_rebuild_matches_java_contract_and_propagates_idempotency(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True

    class Service(FakeRebuildService):
        async def rebuild(self, **kwargs):
            self.calls.append(kwargs)
            return RebuildResult("empty-staging", 0, 0, True)

    service = Service()
    payload = rebuild_payload()
    payload["documents"] = []
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=payload,
            headers=auth_headers,
        )
    assert response.status_code == 200
    assert response.json() == {
        "operation": "REBUILD", "status": "SUCCEEDED",
        "collectionAlias": "customer_service_knowledge", "etlVersion": "etl-v1",
        "documentCount": 0, "idempotent": True,
    }
    assert service.calls[0]["documents"] == []


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(operation="INDEX"),
    lambda value: value.update(collectionAlias="other_alias"),
    lambda value: value["lease"].update(scope="collection:other_alias"),
    lambda value: value["lease"].update(operation="DELETE"),
    lambda value: value["documents"][0].update(extra="value"),
    lambda value: value.update(extra="value"),
])
def test_rebuild_rejects_operation_scope_alias_and_extra_fields(
    app_factory, auth_headers, settings, mutate,
):
    settings.customer_service_rag_enabled = True
    payload = rebuild_payload()
    mutate(payload)
    with TestClient(app_factory(knowledge_etl_service=FakeRebuildService())) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=payload,
            headers=auth_headers,
        )
    assert response.status_code in {400, 422}


def test_rebuild_enforces_configured_document_count(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    settings.rag_rebuild_max_documents = 1
    payload = rebuild_payload()
    service = FakeRebuildService()
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=payload,
            headers=auth_headers,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS"
    assert service.calls == []


def test_rebuild_errors_are_stable_and_do_not_leak(
    app_factory, auth_headers, settings, caplog,
):
    settings.customer_service_rag_enabled = True
    service = FakeRebuildService(RuntimeError(
        "secret-one rebuild-proof-secret vendor-body"
    ))
    with caplog.at_level(logging.DEBUG):
        with TestClient(app_factory(knowledge_etl_service=service)) as client:
            response = client.post(
                "/internal/v1/customer-service/knowledge:rebuild",
                json=rebuild_payload(),
                headers=auth_headers,
            )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "KNOWLEDGE_REBUILD_FAILED"
    for secret in ("secret-one", "rebuild-proof-secret", "vendor-body"):
        assert secret not in response.text
        assert secret not in caplog.text


def test_rebuild_fails_closed_when_spring_validator_is_unavailable(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    service = FakeRebuildService(
        MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
    )
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=rebuild_payload(),
            headers=auth_headers,
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KNOWLEDGE_MUTATION_LEASE_INVALID"


def test_rebuild_text_budget_error_is_a_stable_validation_response(
    app_factory, auth_headers, settings,
):
    settings.customer_service_rag_enabled = True
    service = FakeRebuildService(
        DocumentETLError("KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED")
    )
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        response = client.post(
            "/internal/v1/customer-service/knowledge:rebuild",
            json=rebuild_payload(), headers=auth_headers,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED"
    )


class Downloaded:
    def __init__(self, path: Path, cleanups: list[str], on_exit=None):
        self.path = path
        self._cleanups = cleanups
        self._on_exit = on_exit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self._cleanups.append(self.path.name)
        self.path.unlink(missing_ok=True)
        if self._on_exit is not None:
            self._on_exit()


@pytest.mark.asyncio
async def test_rebuild_processes_two_documents_sequentially_and_passes_lease(
    settings, tmp_path,
):
    cleanups: list[str] = []
    active_downloads = 0
    max_active_downloads = 0

    class Downloader:
        async def download(self, url, **_kwargs):
            nonlocal active_downloads, max_active_downloads
            active_downloads += 1
            max_active_downloads = max(max_active_downloads, active_downloads)
            name = "one.txt" if "/one?" in url else "two.txt"
            path = tmp_path / name
            path.write_text("first" if name == "one.txt" else "second", encoding="utf-8")
            def exit_download():
                nonlocal active_downloads
                active_downloads -= 1
            return Downloaded(path, cleanups, exit_download)

    class Embeddings:
        async def embed_documents(self, texts, *, max_elements):
            assert max_elements <= settings.rag_max_embedding_elements
            return [[0.1, 0.2] for _ in texts]

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            self.lease = lease
            self.plan = plan
            self.documents = [document async for document in documents]
            return RebuildResult("staging-v1", len(self.documents), 2, False)

    store = Store()
    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), store)
    documents = rebuild_payload()["documents"]
    lease = KnowledgeMutationLease(
        scope="collection:customer_service_knowledge",
        operation_id="rebuild-op-1",
        operation="REBUILD",
        fence=19,
        expires_at=time.time() + 300,
        proof="proof",
    )
    result = await service.rebuild(documents=documents, etl_version="etl-v1", lease=lease)
    assert result.document_count == 2
    assert [document.document_id for document in store.documents] == ["doc-1", "doc-2"]
    assert store.lease is lease
    assert store.plan == RebuildPlan(
        embedding_model_version=settings.rag_embedding_model,
        embedding_dimension=settings.rag_embedding_dimension,
        etl_version="etl-v1", document_count=2,
        document_fingerprint=store.plan.document_fingerprint,
    )
    assert cleanups == ["one.txt", "two.txt"]
    assert max_active_downloads == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_rebuild_idempotent_result_skips_download_and_embedding(settings):
    class Downloader:
        calls = 0
        async def download(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("idempotent replay downloaded a document")

    class Embeddings:
        calls = 0
        async def embed_documents(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("idempotent replay requested embeddings")

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            self.plan = plan
            return RebuildResult("existing-staging", 2, 4, True)

    downloader, embeddings, store = Downloader(), Embeddings(), Store()
    service = KnowledgeEtlService(settings, downloader, embeddings, store)
    result = await service.rebuild(
        documents=rebuild_payload()["documents"], etl_version="etl-v1",
        lease=KnowledgeMutationLease(
            "collection:customer_service_knowledge", "op", "REBUILD", 1,
            time.time() + 300, "proof",
        ),
    )
    assert result.idempotent
    assert downloader.calls == 0
    assert embeddings.calls == 0
    assert store.plan.document_count == 2


@pytest.mark.asyncio
async def test_rebuild_document_failure_prevents_store_completion_and_cleans_temp(
    settings, tmp_path,
):
    cleanups: list[str] = []

    class Downloader:
        async def download(self, url, **_kwargs):
            path = tmp_path / ("one.txt" if "/one?" in url else "two.txt")
            path.write_text("first" if path.name == "one.txt" else "", encoding="utf-8")
            return Downloaded(path, cleanups)

    class Embeddings:
        async def embed_documents(self, texts, **_kwargs):
            return [[0.1, 0.2] for _ in texts]

    class Store:
        completed = False
        async def rebuild_collection(self, documents, *, lease, plan):
            [document async for document in documents]
            self.completed = True
            return RebuildResult("should-not-switch", 2, 2, False)

    store = Store()
    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), store)
    with pytest.raises(RuntimeError, match="KNOWLEDGE_DOCUMENT_EMPTY"):
        await service.rebuild(
            documents=rebuild_payload()["documents"],
            etl_version="etl-v1",
            lease=KnowledgeMutationLease(
                "collection:customer_service_knowledge", "op", "REBUILD", 1,
                time.time() + 300, "proof",
            ),
        )
    assert store.completed is False
    assert cleanups == ["one.txt", "two.txt"]
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_rebuild_total_embedding_budget_is_fail_closed(settings, tmp_path):
    settings.rag_max_embedding_elements = 3
    cleanups: list[str] = []

    class Downloader:
        calls = 0
        async def download(self, url, **_kwargs):
            self.calls += 1
            path = tmp_path / ("one.txt" if "/one?" in url else "two.txt")
            path.write_text(path.stem, encoding="utf-8")
            return Downloaded(path, cleanups)

    class Embeddings:
        calls = 0
        async def embed_documents(self, texts, **_kwargs):
            self.calls += 1
            return [[0.1, 0.2] for _ in texts]

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            [document async for document in documents]
            raise AssertionError("budget failure should originate from iterator")

    downloader, embeddings = Downloader(), Embeddings()
    service = KnowledgeEtlService(settings, downloader, embeddings, Store())
    payload_documents = rebuild_payload()["documents"]
    payload_documents.append({
        "documentId": "doc-3",
        "documentVersion": 1,
        "fileName": "three.txt",
        "fileType": "TXT",
        "signedUrl": "https://oss.example.test/three?Signature=secret-three",
        "sha256": hashlib.sha256(b"third").hexdigest(),
    })
    with pytest.raises(
        EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED"
    ):
        await service.rebuild(
            documents=payload_documents,
            etl_version="etl-v1",
            lease=KnowledgeMutationLease(
                "collection:customer_service_knowledge", "op", "REBUILD", 1,
                time.time() + 300, "proof",
            ),
        )
    assert embeddings.calls == 0
    assert downloader.calls == 2
    assert cleanups == ["one.txt", "two.txt"]


@pytest.mark.asyncio
async def test_rebuild_text_byte_budget_stops_before_next_download(settings, tmp_path):
    settings.rag_rebuild_max_text_bytes = 4
    cleanups: list[str] = []

    class Downloader:
        calls = 0
        async def download(self, url, **_kwargs):
            self.calls += 1
            path = tmp_path / ("one.txt" if "/one?" in url else "two.txt")
            path.write_text("你好", encoding="utf-8")
            return Downloaded(path, cleanups)

    class Embeddings:
        calls = 0
        async def embed_documents(self, texts, **_kwargs):
            self.calls += 1
            return [[0.1, 0.2] for _ in texts]

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            [document async for document in documents]

    downloader, embeddings = Downloader(), Embeddings()
    service = KnowledgeEtlService(settings, downloader, embeddings, Store())
    with pytest.raises(
        DocumentETLError, match="^KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED$"
    ):
        await service.rebuild(
            documents=rebuild_payload()["documents"],
            etl_version="etl-v1",
            lease=KnowledgeMutationLease(
                "collection:customer_service_knowledge", "op", "REBUILD", 1,
                time.time() + 300, "proof",
            ),
        )
    assert downloader.calls == 1
    assert embeddings.calls == 0
    assert cleanups == ["one.txt"]


@pytest.mark.asyncio
async def test_rebuild_total_chunk_budget_is_fail_closed(settings, tmp_path):
    settings.rag_rebuild_max_chunks = 1
    cleanups: list[str] = []

    class Downloader:
        async def download(self, url, **_kwargs):
            path = tmp_path / ("one.txt" if "/one?" in url else "two.txt")
            path.write_text(path.stem, encoding="utf-8")
            return Downloaded(path, cleanups)

    class Embeddings:
        async def embed_documents(self, texts, **_kwargs):
            return [[0.1, 0.2] for _ in texts]

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            [document async for document in documents]

    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), Store())
    with pytest.raises(
        RuntimeError, match="KNOWLEDGE_REBUILD_TOO_MANY_CHUNKS"
    ):
        await service.rebuild(
            documents=rebuild_payload()["documents"],
            etl_version="etl-v1",
            lease=KnowledgeMutationLease(
                "collection:customer_service_knowledge", "op", "REBUILD", 1,
                time.time() + 300, "proof",
            ),
        )
    assert cleanups == ["one.txt", "two.txt"]


@pytest.mark.asyncio
async def test_rebuild_cancellation_cleans_current_temp_file(settings, tmp_path):
    started = asyncio.Event()
    cleanups: list[str] = []

    class Downloader:
        async def download(self, _url, **_kwargs):
            path = tmp_path / "one.txt"
            path.write_text("first", encoding="utf-8")
            return Downloaded(path, cleanups)

    class Embeddings:
        async def embed_documents(self, _texts, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    class Store:
        async def rebuild_collection(self, documents, *, lease, plan):
            [document async for document in documents]

    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), Store())
    task = asyncio.create_task(service.rebuild(
        documents=rebuild_payload()["documents"],
        etl_version="etl-v1",
        lease=KnowledgeMutationLease(
            "collection:customer_service_knowledge", "op", "REBUILD", 1,
            time.time() + 300, "proof",
        ),
    ))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanups == ["one.txt", "one.txt"]
    assert not list(tmp_path.iterdir())
