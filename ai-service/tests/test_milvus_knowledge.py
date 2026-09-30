from __future__ import annotations

import asyncio
import math
import struct
import threading
import time
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import replace
from contextlib import asynccontextmanager

import pytest
import httpx

import ai_service.infrastructure.milvus_knowledge as milvus_module

from ai_service.config import Settings
from ai_service.app import _construct_in_thread
from ai_service.infrastructure.milvus_knowledge import (
    DenyAllKnowledgeMutationCoordinator,
    IndexedChunk,
    IndexedDocument,
    KnowledgeMutationLease,
    MilvusKnowledgeError,
    MilvusKnowledgeStore,
)
from ai_service.models.embeddings import CloseAIEmbeddingProvider, EmbeddingOutputError


class FakeEmbeddings:
    def __init__(self, document_responses=None, query_response=None, error=None):
        self.document_responses = list(document_responses or [])
        self.query_response = query_response
        self.error = error
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def aembed_documents(self, texts: list[str]):
        self.document_calls.append(list(texts))
        if self.error:
            raise self.error
        return self.document_responses.pop(0)

    async def aembed_query(self, text: str):
        self.query_calls.append(text)
        if self.error:
            raise self.error
        return self.query_response


def provider(settings, fake):
    settings.closeai_api_key = "closeai-test-secret"
    settings.closeai_base_url = "https://closeai.test/v1"
    settings.rag_embedding_batch_size = 2
    calls = []

    def factory(model, **kwargs):
        calls.append((model, kwargs))
        return fake

    return CloseAIEmbeddingProvider(settings, embedding_factory=factory), calls


@pytest.mark.asyncio
async def test_closeai_embeddings_initialize_once_and_batch_documents(settings):
    fake = FakeEmbeddings(document_responses=[[[1, 0], [0, 1]], [[0.5, 0.5]]])
    embeddings, factory_calls = provider(settings, fake)

    assert await embeddings.embed_documents(["a", "b", "c"]) == [
        [1.0, 0.0], [0.0, 1.0], [0.5, 0.5]
    ]
    assert fake.document_calls == [["a", "b"], ["c"]]
    model, kwargs = factory_calls[0]
    assert model == "openai:text-embedding-3-large"
    assert kwargs["api_key"] == "closeai-test-secret"
    assert kwargs["base_url"] == "https://closeai.test/v1"
    assert isinstance(kwargs["http_client"], httpx.Client)
    assert isinstance(kwargs["http_async_client"], httpx.AsyncClient)
    await embeddings.close()
    assert kwargs["http_client"].is_closed
    assert kwargs["http_async_client"].is_closed


@pytest.mark.asyncio
async def test_closeai_query_embedding_is_validated(settings):
    fake = FakeEmbeddings(query_response=[0.25, 0.75])
    embeddings, _ = provider(settings, fake)
    assert await embeddings.embed_query("refund") == [0.25, 0.75]
    assert fake.query_calls == ["refund"]
    await embeddings.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "code"),
    [
        ([[[1.0, 2.0]]], "KNOWLEDGE_EMBEDDING_COUNT_MISMATCH"),
        ([[[1.0, 2.0], [1.0]]], "KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"),
        ([[[1.0, float("nan")], [1.0, 2.0]]], "KNOWLEDGE_EMBEDDING_INVALID_VECTOR"),
        ([[[1.0, float("inf")], [1.0, 2.0]]], "KNOWLEDGE_EMBEDDING_INVALID_VECTOR"),
        ([[]], "KNOWLEDGE_EMBEDDING_COUNT_MISMATCH"),
        ([[None, None]], "KNOWLEDGE_EMBEDDING_INVALID_OUTPUT"),
    ],
)
async def test_closeai_embeddings_reject_invalid_outputs(settings, responses, code):
    fake = FakeEmbeddings(document_responses=responses)
    embeddings, _ = provider(settings, fake)
    with pytest.raises(EmbeddingOutputError, match=f"^{code}$"):
        await embeddings.embed_documents(["a", "b"])
    await embeddings.close()


@pytest.mark.asyncio
async def test_closeai_errors_are_stable_and_do_not_leak_provider_details(settings):
    secret = "closeai-test-secret"
    response_body = "provider response body"
    fake = FakeEmbeddings(error=RuntimeError(f"{secret}: {response_body}"))
    embeddings, _ = provider(settings, fake)
    with pytest.raises(EmbeddingOutputError) as caught:
        await embeddings.embed_query("question")
    assert str(caught.value) == "KNOWLEDGE_EMBEDDING_UNAVAILABLE"
    assert secret not in repr(caught.value)
    assert response_body not in repr(caught.value)
    await embeddings.close()


@pytest.mark.asyncio
async def test_closeai_provider_closes_sync_and_async_http_clients(settings):
    fake = FakeEmbeddings()
    embeddings, calls = provider(settings, fake)
    sync_client = calls[0][1]["http_client"]
    async_client = calls[0][1]["http_async_client"]
    assert not sync_client.is_closed
    assert not async_client.is_closed
    await embeddings.close()
    assert sync_client.is_closed
    assert async_client.is_closed


@pytest.mark.asyncio
async def test_closeai_real_langchain_shape_owns_and_closes_http_clients(settings):
    settings.closeai_api_key = "closeai-test-secret"
    settings.closeai_base_url = "https://closeai.test/v1"
    embeddings = CloseAIEmbeddingProvider(settings)
    sync_client = embeddings._http_client
    async_client = embeddings._http_async_client
    assert isinstance(sync_client, httpx.Client) and not sync_client.is_closed
    assert isinstance(async_client, httpx.AsyncClient) and not async_client.is_closed
    await embeddings.close()
    assert sync_client.is_closed
    assert async_client.is_closed


def test_closeai_provider_constructor_failure_closes_owned_http_clients(settings):
    sync_client = httpx.Client()
    async_client = httpx.AsyncClient()

    def fail(*_args, **_kwargs):
        raise RuntimeError("constructor failed")

    with pytest.raises(EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_UNAVAILABLE"):
        CloseAIEmbeddingProvider(
            settings,
            embedding_factory=fail,
            http_client_factory=lambda **_kwargs: sync_client,
            http_async_client_factory=lambda **_kwargs: async_client,
        )
    assert sync_client.is_closed
    assert async_client.is_closed


def test_closeai_async_http_client_factory_failure_closes_sync_client(settings):
    sync_client = httpx.Client()

    def fail(**_kwargs):
        raise RuntimeError("async client constructor failed")

    with pytest.raises(EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_UNAVAILABLE"):
        CloseAIEmbeddingProvider(
            settings,
            embedding_factory=lambda *_args, **_kwargs: FakeEmbeddings(),
            http_client_factory=lambda **_kwargs: sync_client,
            http_async_client_factory=fail,
        )
    assert sync_client.is_closed


@pytest.mark.asyncio
async def test_cancelled_closeai_construction_closes_owned_http_clients(settings):
    sync_client = httpx.Client()
    async_client = httpx.AsyncClient()
    started = threading.Event()
    release = threading.Event()

    def factory(*_args, **_kwargs):
        started.set()
        release.wait(1)
        return FakeEmbeddings()

    task = asyncio.create_task(_construct_in_thread(
        CloseAIEmbeddingProvider,
        settings,
        embedding_factory=factory,
        http_client_factory=lambda **_kwargs: sync_client,
        http_async_client_factory=lambda **_kwargs: async_client,
    ))
    assert await asyncio.to_thread(started.wait, 0.3)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sync_client.is_closed
    assert async_client.is_closed


class FakeMilvusClient:
    def __init__(self):
        self.collections: dict[str, list[dict]] = {}
        self.dimensions: dict[str, int] = {}
        self.descriptions: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.search_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.get_calls: list[list[str]] = []
        self.fail_alias_switch = False
        self.corrupt_next_insert = False
        self.corrupt_next_content = False
        self.corrupt_next_embedding = False
        self.fail_alias_lookup = False
        self.alias_switch_failures: list[str] = []
        self.partial_upsert_count: int | None = None
        self.partial_active_upsert_count: int | None = None
        self.before_old_activation: tuple[threading.Event, threading.Event] | None = None
        self.block_next_upsert: tuple[threading.Event, threading.Event] | None = None
        self.block_next_alias_switch: tuple[threading.Event, threading.Event] | None = None
        self.coordinator = FakeMutationCoordinator()
        self.iterator_calls: list[dict] = []

    def has_collection(self, collection_name, **_kwargs):
        return collection_name in self.collections

    def create_collection(self, collection_name, dimension, **kwargs):
        self.collections[collection_name] = []
        self.dimensions[collection_name] = dimension
        self.descriptions[collection_name] = kwargs.get("description", "")

    def describe_collection(self, collection_name, **_kwargs):
        return {"collection_name": collection_name, "dimension": self.dimensions[collection_name],
                "description": self.descriptions[collection_name]}

    def drop_collection(self, collection_name, **_kwargs):
        self.collections.pop(collection_name, None)
        self.dimensions.pop(collection_name, None)
        self.descriptions.pop(collection_name, None)

    @staticmethod
    def _float32(value):
        return struct.unpack("!f", struct.pack("!f", float(value)))[0]

    @classmethod
    def _round_trip_vectors(cls, rows):
        for row in rows:
            if "embedding" in row:
                row["embedding"] = [cls._float32(value) for value in row["embedding"]]

    def insert(self, collection_name, data, **_kwargs):
        rows = deepcopy(data)
        self._round_trip_vectors(rows)
        if self.corrupt_next_insert and rows:
            rows.pop()
            self.corrupt_next_insert = False
        if self.corrupt_next_content and rows:
            rows[0]["content"] = "corrupt"
            self.corrupt_next_content = False
        if self.corrupt_next_embedding and rows:
            rows[0]["embedding"][0] += 99
            self.corrupt_next_embedding = False
        self.collections[collection_name].extend(rows)
        return {"insert_count": len(rows)}

    def upsert(self, collection_name, data, **_kwargs):
        items = deepcopy(data)
        self._round_trip_vectors(items)
        if self.block_next_upsert:
            started, release = self.block_next_upsert
            self.block_next_upsert = None
            started.set()
            assert release.wait(2)
        if self.corrupt_next_insert and items:
            items.pop()
            self.corrupt_next_insert = False
        if self.before_old_activation and any(
            item.get("documentVersion") == 1 and item.get("isActive") for item in items
        ):
            started, release = self.before_old_activation
            self.before_old_activation = None
            started.set()
            assert release.wait(2)
        if self.partial_upsert_count is not None:
            items = items[:self.partial_upsert_count]
            self.partial_upsert_count = None
        if self.partial_active_upsert_count is not None and any(item.get("isActive") for item in items):
            items = items[:self.partial_active_upsert_count]
            self.partial_active_upsert_count = None
        rows = self.collections[self._resolve(collection_name)]
        by_id = {row["id"]: row for row in rows}
        for item in items:
            by_id[item["id"]] = item
        self.collections[self._resolve(collection_name)] = list(by_id.values())
        return {"upsert_count": len(items)}

    def query(self, collection_name, filter="", output_fields=None, **_kwargs):
        self.query_calls.append({"collection_name": collection_name, "filter": filter})
        rows = deepcopy(self.collections[self._resolve(collection_name)])
        return [row for row in rows if self._matches(row, filter)]

    def query_iterator(self, collection_name, batch_size, limit, filter="", **_kwargs):
        self.iterator_calls.append({"batch_size": batch_size, "limit": limit, "filter": filter})
        rows = [deepcopy(row) for row in self.collections[self._resolve(collection_name)]
                if self._matches(row, filter)][:limit]
        return FakeQueryIterator(rows, batch_size)

    def get(self, collection_name, ids, output_fields=None, **_kwargs):
        self.get_calls.append(list(ids))
        wanted = set(ids)
        return [deepcopy(row) for row in self.collections[self._resolve(collection_name)]
                if row["id"] in wanted]

    def search(self, collection_name, data, filter, limit, output_fields, **_kwargs):
        self.search_calls.append({"collection_name": collection_name, "filter": filter,
                                  "output_fields": output_fields, "data": data})
        rows = [row for row in self.collections[self._resolve(collection_name)]
                if self._matches(row, filter)][:limit]
        return [[{"id": row["id"], "distance": 0.99,
                  "entity": {key: deepcopy(row[key]) for key in output_fields}}
                 for row in rows]]

    def describe_alias(self, alias, **_kwargs):
        if self.fail_alias_lookup:
            raise RuntimeError("milvus unavailable with sensitive response")
        if alias not in self.aliases:
            raise RuntimeError("alias missing")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def list_aliases(self, collection_name="", **_kwargs):
        if self.fail_alias_lookup:
            raise RuntimeError("milvus unavailable with sensitive response")
        return {"aliases": list(self.aliases)}

    def create_alias(self, collection_name, alias, **_kwargs):
        if alias in self.aliases:
            raise RuntimeError("alias already exists")
        self.aliases[alias] = collection_name

    def alter_alias(self, collection_name, alias, **_kwargs):
        if self.block_next_alias_switch:
            started, release = self.block_next_alias_switch
            self.block_next_alias_switch = None
            started.set()
            assert release.wait(2)
        if self.alias_switch_failures:
            mode = self.alias_switch_failures.pop(0)
            if mode == "before":
                raise RuntimeError("switch failed before commit")
            if mode == "after":
                self.aliases[alias] = collection_name
                raise RuntimeError("switch response lost after commit")
        self.aliases[alias] = collection_name
        if self.fail_alias_switch:
            self.fail_alias_switch = False
            raise RuntimeError("switch failed with sensitive provider response")

    def list_collections(self, **_kwargs):
        return list(self.collections)

    def close(self):
        return None

    def _resolve(self, name):
        return self.aliases.get(name, name)

    @staticmethod
    def _matches(row, expression):
        if not expression:
            return True
        clauses = [clause.strip() for clause in expression.split(" and ")]
        for clause in clauses:
            field, _, raw = clause.partition(" == ")
            expected = raw.strip().strip('"')
            actual = row.get(field)
            if expected.lower() in {"true", "false"}:
                expected = expected.lower() == "true"
            elif expected.isdigit():
                expected = int(expected)
            if actual != expected:
                return False
        return True


class FakeQueryIterator:
    def __init__(self, rows, batch_size):
        self.rows = rows
        self.batch_size = batch_size
        self.offset = 0
        self.closed = False

    def next(self):
        batch = self.rows[self.offset:self.offset + self.batch_size]
        self.offset += len(batch)
        return batch

    def close(self):
        self.closed = True


class FakeMutationPermit:
    def __init__(self, coordinator, lease):
        self.coordinator = coordinator
        self.fence = lease.fence

    async def assert_current(self):
        self.coordinator.assertions += 1
        if self.coordinator.revoked:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
        if (self.coordinator.revoke_after is not None
                and self.coordinator.assertions > self.coordinator.revoke_after):
            self.coordinator.revoked = True
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")


class FakeMutationCoordinator:
    def __init__(self):
        self.active: set[str] = set()
        self.condition = asyncio.Condition()
        self.max_active = 0
        self.entries: list[str] = []
        self.assertions = 0
        self.revoked = False
        self.revoke_after: int | None = None
        self.block_next: tuple[threading.Event, threading.Event] | None = None

    @staticmethod
    def _conflicts(left, right):
        return left == right or left.startswith("collection:") or right.startswith("collection:")

    @asynccontextmanager
    async def hold(self, lease, *, scope, operation):
        if (lease.scope != scope or lease.proof != "valid-proof"
                or lease.expires_at <= time.time() or self.revoked):
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
        async with self.condition:
            await self.condition.wait_for(
                lambda: not any(self._conflicts(scope, active) for active in self.active)
            )
            self.active.add(scope)
            self.entries.append(f"{operation}:{scope}")
            self.max_active = max(self.max_active, len(self.active))
        try:
            if self.block_next:
                entered, release = self.block_next
                self.block_next = None
                entered.set()
                await asyncio.to_thread(release.wait, 2)
            yield FakeMutationPermit(self, lease)
        finally:
            async with self.condition:
                self.active.remove(scope)
                self.condition.notify_all()


def document(version=1, *, etl="etl-v1", model="embedding-v1", dimension=2,
             document_id="doc-1"):
    chunks = tuple(
        IndexedChunk(
            chunk_id=f"{document_id}:{version}:{index}", chunk_index=index,
            content=f"content-{index}", source_locator=f"section-{index}",
            content_hash=f"chunk-hash-{version}-{index}",
            embedding=tuple(float(index + offset) for offset in range(dimension)),
        )
        for index in range(2)
    )
    return IndexedDocument(
        document_id=document_id, document_version=version, etl_version=etl,
        embedding_model_version=model, file_name="manual.md", file_type="md",
        content_hash=f"document-hash-{version}", chunks=chunks,
    )


def store(settings, client):
    settings.milvus_collection_alias = "customer_service_knowledge"
    return MilvusKnowledgeStore(
        settings, client_factory=lambda **_kwargs: client,
        mutation_coordinator=client.coordinator,
    )


def lease(
    scope, fence=1, *, proof="valid-proof", expires_at=None, operation="INDEX",
):
    return KnowledgeMutationLease(
        scope=scope, operation_id=f"operation-{fence}", operation=operation, fence=fence,
        expires_at=expires_at or time.time() + 60, proof=proof,
    )


async def index_document(knowledge, value, *, mutation_lease=None):
    return await knowledge.upsert_document_version(
        value,
        lease=mutation_lease or lease(f"document:{value.document_id}", value.document_version),
    )


async def delete_version(knowledge, document_id, version, *, mutation_lease=None):
    return await knowledge.delete_document(
        document_id, version,
        lease=mutation_lease or lease(
            f"document:{document_id}", version, operation="DELETE"
        ),
    )


async def rebuild(knowledge, values, *, mutation_lease=None, fence=100):
    return await knowledge.rebuild_collection(
        values,
        lease=mutation_lease or lease(
            "collection:customer_service_knowledge", fence, operation="REBUILD"
        ),
    )


@pytest.mark.asyncio
async def test_version_write_is_complete_idempotent_and_activates_newest(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = await index_document(knowledge, document())
    replay = await index_document(knowledge, document())
    second = await index_document(knowledge, document(version=2))

    assert first.chunk_count == 2 and not first.idempotent
    assert replay.idempotent
    assert second.document_version == 2
    rows = [row for row in next(iter(client.collections.values()))
            if row.get("recordType") == "chunk"]
    assert len(rows) == 4
    assert all(row["isActive"] == (row["documentVersion"] == 2) for row in rows)
    required = {"chunkId", "documentId", "documentVersion", "etlVersion",
                "embeddingModelVersion", "fileName", "fileType", "sourceLocator",
                "content", "contentHash", "embeddingDimension", "schemaVersion",
                "isActive", "embedding"}
    assert required <= rows[0].keys()


@pytest.mark.asyncio
async def test_stale_or_conflicting_version_cannot_replace_active_version(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document(version=2))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STALE_VERSION$"):
        await index_document(knowledge, document(version=1))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VERSION_CONFLICT$"):
        await index_document(knowledge, document(version=2, etl="etl-other"))
    assert all(row["documentVersion"] == 2 and row["isActive"]
               for row in next(iter(client.collections.values()))
               if row.get("recordType") == "chunk")


@pytest.mark.asyncio
async def test_dimension_mismatch_is_rejected_without_mixing_collection(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH$"):
        await index_document(knowledge, document(version=2, dimension=3))
    assert list(client.dimensions.values()) == [2]


@pytest.mark.asyncio
async def test_delete_disables_version_and_search_only_returns_active_metadata(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    await index_document(knowledge, document(document_id="doc-2"))
    await delete_version(knowledge, "doc-1", 1)

    results = await knowledge.search([0.1, 0.2], 8)
    assert results and {item.document_id for item in results} == {"doc-2"}
    assert "isActive == true" in client.search_calls[-1]["filter"]
    assert "embedding" not in client.search_calls[-1]["output_fields"]
    assert all(not hasattr(item, "embedding") and math.isfinite(item.distance)
               for item in results)
    assert all(item.score == pytest.approx(0.99) for item in results)
    assert all(item.distance == pytest.approx(0.01) for item in results)
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_DISABLED$"):
        await index_document(knowledge, document())


async def documents(*items: IndexedDocument) -> AsyncIterator[IndexedDocument]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_rebuild_validates_staging_then_atomically_switches_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    old_collection = client.aliases[settings.milvus_collection_alias]

    result = await rebuild(knowledge, documents(document(version=2), document(document_id="doc-2")))
    assert result.chunk_count == 4 and result.document_count == 2
    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert result.collection_name != old_collection

    incremental = await index_document(knowledge, document(version=3))
    assert incremental.collection_name == result.collection_name
    active = [row for row in client.collections[result.collection_name]
              if row.get("recordType") == "chunk" and row["isActive"]]
    assert active and {row["documentVersion"] for row in active if row["documentId"] == "doc-1"} == {3}


@pytest.mark.asyncio
async def test_rebuild_integrity_or_alias_failure_keeps_old_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old_collection = client.aliases[alias]

    client.corrupt_next_insert = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VECTOR_WRITE_INCOMPLETE$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert client.aliases[alias] == old_collection

    client.corrupt_next_content = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert client.aliases[alias] == old_collection

    client.corrupt_next_embedding = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert client.aliases[alias] == old_collection

    client.fail_alias_switch = True
    with pytest.raises(MilvusKnowledgeError) as caught:
        await rebuild(knowledge, documents(document(version=2)))
    assert str(caught.value) == "KNOWLEDGE_ALIAS_SWITCH_FAILED"
    assert "provider response" not in repr(caught.value)
    assert client.aliases[alias] == old_collection


@pytest.mark.asyncio
async def test_incomplete_document_staging_can_be_retried_idempotently(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    client.corrupt_next_insert = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VECTOR_WRITE_INCOMPLETE$"):
        await index_document(knowledge, document())

    result = await index_document(knowledge, document())
    assert not result.idempotent
    replay = await index_document(knowledge, document())
    assert replay.idempotent
    assert len([row for row in next(iter(client.collections.values()))
                if row.get("recordType") == "chunk"]) == 2


@pytest.mark.asyncio
async def test_float_vectors_are_canonicalized_before_write_verification(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    value = document()
    chunks = tuple(replace(chunk, embedding=(0.1, 0.2)) for chunk in value.chunks)

    await index_document(knowledge, replace(value, chunks=chunks))

    stored = next(row for row in next(iter(client.collections.values()))
                  if row.get("recordType") == "chunk")
    assert stored["embedding"] != [0.1, 0.2]
    assert stored["embedding"] == [FakeMilvusClient._float32(0.1),
                                    FakeMilvusClient._float32(0.2)]


@pytest.mark.asyncio
async def test_two_store_instances_are_strictly_serialized_by_shared_coordinator(settings):
    client = FakeMilvusClient()
    old_store = store(settings, client)
    new_store = store(settings, client)
    old_started = threading.Event()
    release_old = threading.Event()
    client.coordinator.block_next = (old_started, release_old)

    old_task = asyncio.create_task(index_document(old_store, document(version=1)))
    assert await asyncio.to_thread(old_started.wait, 1)
    new_task = asyncio.create_task(index_document(new_store, document(version=2)))
    await asyncio.sleep(0.02)
    assert not new_task.done()
    assert client.coordinator.max_active == 1
    release_old.set()
    await asyncio.gather(old_task, new_task)

    rows = next(iter(client.collections.values()))
    active = [row for row in rows if row.get("recordType") == "chunk" and row["isActive"]]
    assert {row["documentVersion"] for row in active} == {2}
    assert client.coordinator.entries[:2] == [
        "upsert:document:doc-1", "upsert:document:doc-1"
    ]


@pytest.mark.asyncio
async def test_partial_activation_retry_converges_before_idempotent_success(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document(version=1))
    client.partial_active_upsert_count = 1
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VECTOR_WRITE_INCOMPLETE$"):
        await index_document(knowledge, document(version=2))

    result = await index_document(knowledge, document(version=2))
    replay = await index_document(knowledge, document(version=2))
    rows = next(iter(client.collections.values()))
    active = [row for row in rows if row.get("recordType") == "chunk" and row["isActive"]]
    assert {row["documentVersion"] for row in active} == {2}
    assert not result.idempotent and replay.idempotent


@pytest.mark.asyncio
async def test_alias_lookup_failure_is_not_treated_as_missing_on_delete(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    client.fail_alias_lookup = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VECTOR_STORE_UNAVAILABLE$"):
        await delete_version(knowledge, "doc-1", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_id", ['bad"id', "bad\\id", "bad\nid", "bad\0id"])
async def test_document_and_chunk_ids_reject_filter_metacharacters(settings, invalid_id):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_INVALID$"):
        await index_document(knowledge, document(document_id=invalid_id))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_INVALID$"):
        await delete_version(knowledge, invalid_id, 1)
    valid = document()
    invalid_chunk = replace(valid.chunks[0], chunk_id=invalid_id)
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_INVALID$"):
        await index_document(
            knowledge, replace(valid, chunks=(invalid_chunk, *valid.chunks[1:]))
        )


@pytest.mark.asyncio
async def test_rebuild_verifies_known_primary_keys_without_unbounded_query(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await rebuild(knowledge, documents(document(), document(document_id="doc-2")))
    assert len(client.get_calls) >= 1
    assert not any(call["filter"] == "" for call in client.query_calls)


@pytest.mark.asyncio
async def test_alias_switch_response_loss_rolls_back_only_after_readback(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    client.alias_switch_failures = ["after"]
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_ALIAS_SWITCH_FAILED$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert client.aliases[alias] == old
    assert all("_staging_" not in name for name in client.collections)


@pytest.mark.asyncio
async def test_alias_rollback_failure_keeps_possibly_active_staging(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    client.alias_switch_failures = ["after", "before"]
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert client.aliases[alias] != old
    assert client.aliases[alias] in client.collections


@pytest.mark.asyncio
async def test_alias_readback_failure_preserves_both_collections(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    client.alias_switch_failures = ["after"]
    original_alter = client.alter_alias

    def switch_then_break_lookup(collection_name, alias, **kwargs):
        try:
            return original_alter(collection_name, alias, **kwargs)
        finally:
            client.fail_alias_lookup = True

    client.alter_alias = switch_then_break_lookup
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN$"):
        await rebuild(knowledge, documents(document(version=2)))
    assert len(client.collections) == 2


@pytest.mark.asyncio
async def test_missing_invalid_expired_and_default_deny_leases_write_nothing(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    value = document()
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_MUTATION_LEASE_REQUIRED$"):
        await knowledge.upsert_document_version(value)
    for invalid in (
        lease("document:wrong", 1),
        lease("document:doc-1", 1, operation="DELETE"),
        lease("document:doc-1", 1, proof="forged"),
        lease("document:doc-1", 1, expires_at=time.time() - 1),
    ):
        with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_MUTATION_LEASE_INVALID$"):
            await knowledge.upsert_document_version(value, lease=invalid)
    denied = MilvusKnowledgeStore(settings, client_factory=lambda **_kwargs: client)
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_MUTATION_LEASE_REQUIRED$"):
        await denied.upsert_document_version(value, lease=lease("document:doc-1"))
    assert client.collections == {}


@pytest.mark.asyncio
async def test_revoked_permit_stops_before_manifest_and_alias_publication(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    client.coordinator.revoke_after = 3
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_MUTATION_LEASE_INVALID$"):
        await index_document(knowledge, document())
    business_rows = [rows for name, rows in client.collections.items()
                     if "mutation_control" not in name]
    assert business_rows
    assert all(not row["isActive"] for row in business_rows[0])
    assert not any(row.get("recordType") == "manifest" for row in business_rows[0])
    assert client.aliases == {}


@pytest.mark.asyncio
async def test_delete_before_manifest_writes_deterministic_tombstone(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await delete_version(knowledge, "doc-1", 1, mutation_lease=lease(
        "document:doc-1", 7, operation="DELETE"
    ))
    control = client.collections[knowledge._control_collection_name()]
    assert len(control) == 1
    assert control[0]["id"] == knowledge._tombstone_id("doc-1", 1)
    assert control[0]["mutationFence"] == 7
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_DISABLED$"):
        await index_document(knowledge, document(), mutation_lease=lease("document:doc-1", 8))
    assert len(client.collections[knowledge._control_collection_name()]) == 1


@pytest.mark.asyncio
async def test_delete_and_converge_are_serialized_and_tombstone_wins(settings):
    client = FakeMilvusClient()
    first = store(settings, client)
    second = store(settings, client)
    entered, release = threading.Event(), threading.Event()
    client.coordinator.block_next = (entered, release)
    index_task = asyncio.create_task(index_document(first, document()))
    assert await asyncio.to_thread(entered.wait, 1)
    delete_task = asyncio.create_task(
        delete_version(second, "doc-1", 1, mutation_lease=lease(
            "document:doc-1", 2, operation="DELETE"
        ))
    )
    await asyncio.sleep(0.02)
    assert not delete_task.done()
    release.set()
    await asyncio.gather(index_task, delete_task)
    target = client.aliases["customer_service_knowledge"]
    assert not any(row.get("isActive") for row in client.collections[target])
    assert await first._tombstoned_versions("doc-1", {1}) == {1}


@pytest.mark.asyncio
async def test_rebuild_scope_serializes_incremental_mutation(settings):
    client = FakeMilvusClient()
    rebuild_store = store(settings, client)
    incremental_store = store(settings, client)
    entered, release = threading.Event(), threading.Event()
    client.coordinator.block_next = (entered, release)
    rebuild_task = asyncio.create_task(rebuild(
        rebuild_store, documents(document()), mutation_lease=lease(
            "collection:customer_service_knowledge", 10, operation="REBUILD"
        )
    ))
    assert await asyncio.to_thread(entered.wait, 1)
    incremental_task = asyncio.create_task(index_document(
        incremental_store, document(version=2),
        mutation_lease=lease("document:doc-1", 11),
    ))
    await asyncio.sleep(0.02)
    assert not incremental_task.done()
    release.set()
    await asyncio.gather(rebuild_task, incremental_task)
    assert client.coordinator.max_active == 1
    target = client.aliases["customer_service_knowledge"]
    active = [row for row in client.collections[target]
              if row.get("recordType") == "chunk" and row.get("isActive")]
    assert {row["documentVersion"] for row in active} == {2}


@pytest.mark.asyncio
async def test_document_history_uses_iterator_pages(settings, monkeypatch):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    monkeypatch.setattr(milvus_module, "_VERIFY_BATCH_SIZE", 2)
    await index_document(knowledge, document())
    assert any(call["batch_size"] == 2 for call in client.iterator_calls)
    assert not client.query_calls


@pytest.mark.asyncio
async def test_document_history_hard_limit_fails_closed(settings, monkeypatch):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    target = client.aliases["customer_service_knowledge"]
    extra = deepcopy(client.collections[target][0])
    extra["id"] = "f" * 64
    extra["chunkId"] = "doc-1:0:extra"
    client.collections[target].append(extra)
    monkeypatch.setattr(milvus_module, "_MAX_DOCUMENT_HISTORY_RECORDS", 3)
    with pytest.raises(
        MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_HISTORY_LIMIT_EXCEEDED$"
    ):
        await index_document(knowledge, document(version=2))


@pytest.mark.asyncio
async def test_fence_is_recorded_in_manifest_and_collection_metadata(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(
        knowledge, document(), mutation_lease=lease("document:doc-1", 42)
    )
    target = client.aliases["customer_service_knowledge"]
    manifest = next(row for row in client.collections[target]
                    if row.get("recordType") == "manifest")
    assert manifest["mutationFence"] == 42
    assert '"mutationFence":42' in client.descriptions[target]


@pytest.mark.asyncio
async def test_cancellation_during_upsert_keeps_permit_until_rpc_finishes(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    started, release = threading.Event(), threading.Event()
    client.block_next_upsert = (started, release)

    task = asyncio.create_task(index_document(knowledge, document()))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)

    assert "document:doc-1" in client.coordinator.active
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.coordinator.active == set()


@pytest.mark.asyncio
async def test_cancellation_during_alias_switch_preserves_active_staging(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    started, release = threading.Event(), threading.Event()
    client.block_next_alias_switch = (started, release)

    task = asyncio.create_task(rebuild(knowledge, documents(document(version=2))))
    assert await asyncio.to_thread(started.wait, 1)
    staging = next(name for name in client.collections if "_staging_" in name)
    task.cancel()
    await asyncio.sleep(0.02)

    assert f"collection:{alias}" in client.coordinator.active
    assert not task.done()
    assert client.aliases[alias] == old
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.coordinator.active == set()
    assert client.aliases[alias] == staging
    assert staging in client.collections


@pytest.mark.parametrize(
    "alias",
    ["1knowledge", "-knowledge", "knowledge-name", "知识库", "a" * 256],
)
def test_settings_reject_invalid_milvus_aliases(alias):
    with pytest.raises(ValueError, match="milvus_collection_alias"):
        Settings(
            internal_bearer_token="test-secret",
            spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
            spring_gateway_bearer_token="spring-secret",
            checkpoint_enabled=False,
            checkpoint_required=False,
            milvus_collection_alias=alias,
        )


def test_store_revalidates_alias_and_derives_legal_bounded_names(settings):
    settings.milvus_collection_alias = "a" * 255
    knowledge = MilvusKnowledgeStore(
        settings, client_factory=lambda **_kwargs: FakeMilvusClient(),
        mutation_coordinator=FakeMutationCoordinator(),
    )
    names = [
        knowledge._collection_name("embedding-v1", 1536),
        knowledge._collection_name(
            "embedding-v1", 1536, suffix="_staging_" + "f" * 12
        ),
        knowledge._control_collection_name(),
    ]
    assert all(len(name) <= 255 and name[0].isalpha() for name in names)
    assert all(all(character.isalnum() or character == "_" for character in name)
               for name in names)

    settings.milvus_collection_alias = "bad-alias"
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_COLLECTION_ALIAS_INVALID$"):
        MilvusKnowledgeStore(settings, client_factory=lambda **_kwargs: FakeMilvusClient())


@pytest.mark.asyncio
async def test_max_length_alias_can_rebuild_and_publish_staging(settings):
    alias = "a" * 255
    settings.milvus_collection_alias = alias
    client = FakeMilvusClient()
    knowledge = MilvusKnowledgeStore(
        settings, client_factory=lambda **_kwargs: client,
        mutation_coordinator=client.coordinator,
    )

    result = await knowledge.rebuild_collection(
        documents(document()),
        lease=lease(f"collection:{alias}", 100, operation="REBUILD"),
    )

    assert client.aliases[alias] == result.collection_name
    assert result.collection_name in client.collections
    assert len(result.collection_name) <= 255


@pytest.mark.asyncio
async def test_long_alias_fingerprints_keep_business_and_control_data_isolated(settings):
    aliases = ("a" * 254 + "x", "a" * 254 + "y")
    client = FakeMilvusClient()
    stores = []
    derived_names = []

    for alias in aliases:
        settings.milvus_collection_alias = alias
        knowledge = MilvusKnowledgeStore(
            settings, client_factory=lambda **_kwargs: client,
            mutation_coordinator=client.coordinator,
        )
        stores.append(knowledge)
        derived_names.append((
            knowledge._collection_name("embedding-v1", 2),
            knowledge._collection_name(
                "embedding-v1", 2, suffix="_staging_000000000000"
            ),
            knowledge._control_collection_name(),
        ))

    assert all(left != right for left, right in zip(*derived_names))

    for index, (alias, knowledge) in enumerate(zip(aliases, stores), start=1):
        document_id = f"doc-{index}"
        result = await knowledge.rebuild_collection(
            documents(document(document_id=document_id)),
            lease=lease(
                f"collection:{alias}", 100 + index, operation="REBUILD"
            ),
        )
        assert client.aliases[alias] == result.collection_name
        results = await knowledge.search([0.1, 0.2], 8)
        assert {item.document_id for item in results} == {document_id}
        await knowledge.delete_document(
            document_id, 99,
            lease=lease(
                f"document:{document_id}", 200 + index, operation="DELETE"
            ),
        )

    first_control, second_control = (
        derived_names[0][2], derived_names[1][2]
    )
    assert {row["documentId"] for row in client.collections[first_control]} == {"doc-1"}
    assert {row["documentId"] for row in client.collections[second_control]} == {"doc-2"}


@pytest.mark.asyncio
async def test_configured_timeout_is_used_for_client_and_rpc(settings):
    settings.milvus_rpc_timeout_seconds = 1.25
    client = FakeMilvusClient()
    client_kwargs = {}
    rpc_kwargs = {}

    def factory(**kwargs):
        client_kwargs.update(kwargs)
        return client

    def list_collections(**kwargs):
        rpc_kwargs.update(kwargs)
        return []

    client.list_collections = list_collections
    knowledge = MilvusKnowledgeStore(
        settings, client_factory=factory,
        mutation_coordinator=client.coordinator,
    )

    assert await knowledge.ping()
    assert client_kwargs["timeout"] == 1.25
    assert rpc_kwargs == {"timeout": 1.25}


@pytest.mark.asyncio
async def test_ping_uses_injected_client_and_maps_failures(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    assert await knowledge.ping()
    client.list_collections = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    assert not await knowledge.ping()


def test_default_mutation_coordinator_is_fail_closed_contract():
    assert DenyAllKnowledgeMutationCoordinator
    assert KnowledgeMutationLease
