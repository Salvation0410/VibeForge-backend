from __future__ import annotations

import asyncio
import json
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
    RebuildFingerprint,
    RebuildPlan,
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
async def test_closeai_embedding_budget_stops_before_later_batches(settings):
    settings.rag_embedding_dimension = 3
    fake = FakeEmbeddings(document_responses=[[[1, 0, 0], [0, 1, 0]]])
    embeddings, _ = provider(settings, fake)
    with pytest.raises(
        EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED"
    ):
        await embeddings.embed_documents(["a", "b", "c"], max_elements=8)
    assert fake.document_calls == []
    await embeddings.close()


@pytest.mark.asyncio
async def test_closeai_rejects_actual_dimension_different_from_config(settings):
    settings.rag_embedding_dimension = 2
    fake = FakeEmbeddings(document_responses=[[[1, 0, 0], [0, 1, 0]]])
    embeddings, _ = provider(settings, fake)
    with pytest.raises(
        EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"
    ):
        await embeddings.embed_documents(["a", "b"])
    assert fake.document_calls == [["a", "b"]]
    await embeddings.close()


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


@pytest.mark.asyncio
async def test_closeai_sync_close_failure_still_closes_async_client(settings):
    class FailingSyncClient:
        attempted = False
        def close(self):
            self.attempted = True
            raise RuntimeError("sync close failed")

    async_client = httpx.AsyncClient()
    sync_client = FailingSyncClient()
    embeddings = CloseAIEmbeddingProvider(
        settings,
        embedding_factory=lambda *_args, **_kwargs: FakeEmbeddings(),
        http_client_factory=lambda **_kwargs: sync_client,
        http_async_client_factory=lambda **_kwargs: async_client,
    )
    with pytest.raises(RuntimeError, match="sync close failed"):
        await embeddings.close()
    assert sync_client.attempted
    assert async_client.is_closed


@pytest.mark.asyncio
async def test_closeai_async_close_failure_still_closes_sync_client(settings):
    class FailingAsyncClient:
        attempted = False
        async def aclose(self):
            self.attempted = True
            raise RuntimeError("async close failed")

    sync_client = httpx.Client()
    async_client = FailingAsyncClient()
    embeddings = CloseAIEmbeddingProvider(
        settings,
        embedding_factory=lambda *_args, **_kwargs: FakeEmbeddings(),
        http_client_factory=lambda **_kwargs: sync_client,
        http_async_client_factory=lambda **_kwargs: async_client,
    )
    with pytest.raises(RuntimeError, match="async close failed"):
        await embeddings.close()
    assert async_client.attempted
    assert sync_client.is_closed


def test_closeai_constructor_and_cleanup_failures_keep_stable_error(settings):
    class FailingSyncClient:
        attempted = False
        def close(self):
            self.attempted = True
            raise RuntimeError("sync cleanup vendor body")

    class FailingAsyncClient:
        attempted = False
        async def aclose(self):
            self.attempted = True
            raise RuntimeError("async cleanup vendor body")

    sync_client = FailingSyncClient()
    async_client = FailingAsyncClient()

    def fail(*_args, **_kwargs):
        raise RuntimeError("constructor vendor body")

    with pytest.raises(EmbeddingOutputError) as caught:
        CloseAIEmbeddingProvider(
            settings,
            embedding_factory=fail,
            http_client_factory=lambda **_kwargs: sync_client,
            http_async_client_factory=lambda **_kwargs: async_client,
        )
    assert str(caught.value) == "KNOWLEDGE_EMBEDDING_UNAVAILABLE"
    assert "vendor body" not in repr(caught.value)
    assert sync_client.attempted
    assert async_client.attempted


class FakeMilvusClient:
    def __init__(self):
        self.collections: dict[str, list[dict]] = {}
        self.dimensions: dict[str, int] = {}
        self.descriptions: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.search_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.get_calls: list[list[str]] = []
        self.get_output_fields: list[list[str]] = []
        self.get_call_details: list[dict] = []
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
        self.block_next_create: tuple[threading.Event, threading.Event] | None = None
        self.block_next_alias_switch: tuple[threading.Event, threading.Event] | None = None
        self.coordinator = FakeMutationCoordinator()
        self.iterator_calls: list[dict] = []
        self.insert_sizes: list[int] = []
        self.describe_calls: list[str] = []

    def has_collection(self, collection_name, **_kwargs):
        return collection_name in self.collections

    def create_collection(self, collection_name, dimension, **kwargs):
        self.collections[collection_name] = []
        self.dimensions[collection_name] = dimension
        self.descriptions[collection_name] = kwargs.get("description", "")
        if self.block_next_create:
            started, release = self.block_next_create
            self.block_next_create = None
            started.set()
            assert release.wait(2)

    def describe_collection(self, collection_name, **_kwargs):
        self.describe_calls.append(collection_name)
        return {"collection_name": collection_name, "dimension": self.dimensions[collection_name],
                "description": self.descriptions[collection_name]}

    def drop_collection(self, collection_name, **_kwargs):
        self.collections.pop(collection_name, None)
        self.dimensions.pop(collection_name, None)
        self.descriptions.pop(collection_name, None)

    def delete(self, collection_name, ids, **_kwargs):
        wanted = set(ids)
        rows = self.collections[self._resolve(collection_name)]
        kept = [row for row in rows if row.get("id") not in wanted]
        deleted = len(rows) - len(kept)
        self.collections[self._resolve(collection_name)] = kept
        return {"delete_count": deleted}

    @staticmethod
    def _float32(value):
        return struct.unpack("!f", struct.pack("!f", float(value)))[0]

    @classmethod
    def _round_trip_vectors(cls, rows):
        for row in rows:
            if "embedding" in row:
                row["embedding"] = [cls._float32(value) for value in row["embedding"]]

    def insert(self, collection_name, data, **_kwargs):
        self.insert_sizes.append(len(data))
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

    def query_iterator(
        self, collection_name, batch_size, limit, filter="", output_fields=None,
        **_kwargs,
    ):
        self.iterator_calls.append({
            "collection_name": collection_name,
            "batch_size": batch_size,
            "limit": limit,
            "filter": filter,
            "output_fields": list(output_fields or []),
        })
        rows = [deepcopy(row) for row in self.collections[self._resolve(collection_name)]
                if self._matches(row, filter)]
        if 'recordType == "collection_retirement"' in filter:
            rows.sort(key=lambda row: str(row.get("id", "")))
        rows = rows[:limit]
        return FakeQueryIterator(rows, batch_size)

    def get(self, collection_name, ids, output_fields=None, **_kwargs):
        self.get_calls.append(list(ids))
        self.get_output_fields.append(list(output_fields or []))
        self.get_call_details.append({
            "collection_name": collection_name,
            "ids": list(ids),
            "output_fields": list(output_fields or []),
        })
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
        if self.coordinator.revoke_after_alias_switch:
            self.coordinator.revoked = True
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
            if " > " in clause:
                field, _, raw = clause.partition(" > ")
                if str(row.get(field, "")) <= raw.strip().strip('"'):
                    return False
                continue
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


@pytest.mark.asyncio
async def test_document_iterator_cancel_drains_next_before_close(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    started = threading.Event()
    release = threading.Event()
    next_finished = threading.Event()
    close_observed_finished: list[bool] = []

    class BlockingIterator:
        def next(self):
            started.set()
            assert release.wait(2)
            next_finished.set()
            return []

        def close(self):
            close_observed_finished.append(next_finished.is_set())

    client.query_iterator = lambda *_args, **_kwargs: BlockingIterator()
    task = asyncio.create_task(knowledge._document_rows("collection", "doc-1"))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    assert close_observed_finished == []

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert close_observed_finished == [True]


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
        self.revoke_after_alias_switch = False
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


def rebuild_fingerprint(items, *, alias="customer_service_knowledge", etl="etl-v1"):
    fingerprint = RebuildFingerprint(alias, etl)
    for item in items:
        fingerprint.add_document(
            document_id=item.document_id,
            document_version=item.document_version,
            file_name=item.file_name,
            file_type=item.file_type,
            content_hash=item.content_hash,
        )
    return fingerprint.hexdigest()


async def rebuild(
    knowledge, values, *, mutation_lease=None, fence=100, document_count=1,
    expected_documents=None,
):
    mutation_lease = mutation_lease or lease(
        "collection:customer_service_knowledge", fence, operation="REBUILD"
    )
    return await knowledge.rebuild_collection(
        values,
        lease=mutation_lease,
        plan=RebuildPlan(
            embedding_model_version="embedding-v1", embedding_dimension=2,
            etl_version="etl-v1", document_count=document_count,
            document_fingerprint=rebuild_fingerprint(
                expected_documents
                if expected_documents is not None
                else getattr(values, "items", ())
            ),
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


@pytest.mark.asyncio
async def test_search_proves_current_version_from_manifest_and_filters_old_active_hit(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document(version=1))
    await index_document(knowledge, document(version=2))
    collection = client.aliases[settings.milvus_collection_alias]
    for row in client.collections[collection]:
        if row.get("recordType") == "chunk" and row.get("documentVersion") == 1:
            row["isActive"] = True

    results = await knowledge.search([0.1, 0.2], 8)

    assert results
    assert {item.document_version for item in results} == {2}
    assert {item.current_document_version for item in results} == {2}


@pytest.mark.asyncio
async def test_search_fails_closed_when_active_hit_has_no_manifest(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    collection = client.aliases[settings.milvus_collection_alias]
    client.collections[collection] = [
        row for row in client.collections[collection]
        if row.get("recordType") != "manifest"
    ]

    assert await knowledge.search([0.1, 0.2], 8) == []


@pytest.mark.asyncio
async def test_search_returns_current_version_for_rebuild_manifest(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await rebuild(knowledge, documents(document(version=3)), document_count=1)

    results = await knowledge.search([0.1, 0.2], 8)

    assert results
    assert {item.current_document_version for item in results} == {3}


@pytest.mark.asyncio
async def test_search_filters_same_version_orphan_active_row_by_manifest_pk(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    collection = client.aliases[settings.milvus_collection_alias]
    original = next(
        row for row in client.collections[collection]
        if row.get("recordType") == "chunk"
    )
    orphan = deepcopy(original)
    orphan.update({"id": "f" * 64, "chunkId": "orphan-business-chunk"})
    client.collections[collection].append(orphan)

    results = await knowledge.search([0.1, 0.2], 8)

    assert results
    assert "orphan-business-chunk" not in {item.chunk_id for item in results}


@pytest.mark.asyncio
async def test_search_pins_physical_collection_across_alias_switch(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document(version=1))
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    rebuilt = await rebuild(
        knowledge, documents(document(version=2)), document_count=1,
    )
    client.aliases[alias] = old
    original_search = client.search

    def switch_after_vector_search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        client.aliases[alias] = rebuilt.collection_name
        return result

    client.search = switch_after_vector_search

    results = await knowledge.search([0.1, 0.2], 8)

    assert results
    assert {item.document_version for item in results} == {1}
    assert client.search_calls[-1]["collection_name"] == old
    assert all(
        call["collection_name"] == old
        for call in client.iterator_calls
        if call["filter"] == 'documentId == "doc-1"'
    )


@pytest.mark.asyncio
async def test_search_without_alias_fails_stably_before_vector_query(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    with pytest.raises(
        MilvusKnowledgeError, match="^CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE$"
    ):
        await knowledge.search([0.1, 0.2], 8)
    assert client.search_calls == []


@pytest.mark.asyncio
async def test_search_manifest_proof_reads_only_bounded_scalar_fields(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    for version in range(1, 20):
        await index_document(knowledge, document(version=version))

    results = await knowledge.search([0.1, 0.2], 8)

    assert results
    manifest_calls = [
        call for call in client.iterator_calls
        if 'recordType == "manifest"' in call["filter"]
    ]
    assert manifest_calls
    assert all(call["limit"] == 10_001 for call in manifest_calls)
    assert all("embedding" not in call["output_fields"] for call in manifest_calls)
    assert all("content" not in call["output_fields"] for call in manifest_calls)
    assert all(fields == [
        "id", "recordType", "documentId", "documentVersion", "isActive",
    ] for fields in client.get_output_fields[-1:])


class Documents:
    def __init__(self, items):
        self.items = items

    async def __aiter__(self):
        for item in self.items:
            yield item


def documents(*items: IndexedDocument) -> AsyncIterator[IndexedDocument]:
    return Documents(items)


@pytest.mark.asyncio
async def test_rebuild_validates_staging_then_atomically_switches_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    old_collection = client.aliases[settings.milvus_collection_alias]

    result = await rebuild(
        knowledge, documents(document(version=2), document(document_id="doc-2")),
        document_count=2,
    )
    assert result.chunk_count == 4 and result.document_count == 2
    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert result.collection_name != old_collection

    incremental = await index_document(knowledge, document(version=3))
    assert incremental.collection_name == result.collection_name
    active = [row for row in client.collections[result.collection_name]
              if row.get("recordType") == "chunk" and row["isActive"]]
    assert active and {row["documentVersion"] for row in active if row["documentId"] == "doc-1"} == {3}


@pytest.mark.asyncio
@pytest.mark.parametrize("retention", [2, 3])
async def test_rebuild_retains_configured_generations_after_grace(
    settings, monkeypatch, retention,
):
    clock = [1_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_retention_generations = retention
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    for version in range(1, retention + 2):
        value = document(version=version)
        await rebuild(
            knowledge, documents(value), document_count=1,
            mutation_lease=lease(
                f"collection:{settings.milvus_collection_alias}",
                100 + version, operation="REBUILD",
            ),
            expected_documents=(value,),
        )
        clock[0] += 70

    controlled = [
        name for name in client.collections
        if name.startswith(knowledge._collection_name("embedding-v1", 2))
    ]
    assert len(controlled) == retention
    assert client.aliases[settings.milvus_collection_alias] in controlled


@pytest.mark.asyncio
async def test_retention_state_rebinds_when_generation_config_changes(
    settings, monkeypatch,
):
    clock = [1_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_retention_generations = 2
    settings.rag_collection_cleanup_grace_seconds = 1
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    for version in range(1, 3):
        value = document(version=version)
        await rebuild(knowledge, documents(value), document_count=1,
                      expected_documents=(value,))
        clock[0] += 2

    settings.rag_collection_retention_generations = 4
    knowledge = store(settings, client)
    for version in range(3, 5):
        value = document(version=version)
        await rebuild(knowledge, documents(value), document_count=1,
                      expected_documents=(value,))
        clock[0] += 2
        assert len([
            name for name in client.collections
            if name != knowledge._control_collection_name()
        ]) == version

    fifth = document(version=5)
    await rebuild(knowledge, documents(fifth), document_count=1,
                  expected_documents=(fifth,))
    clock[0] += 2
    business = [
        name for name in client.collections
        if name != knowledge._control_collection_name()
    ]
    assert len(business) == 4
    state = next(
        row for row in client.collections[knowledge._control_collection_name()]
        if row.get("recordType") == "collection_retention_state"
    )
    assert state["retentionGenerations"] == 4
    assert state["retentionComplete"] is True
    assert len(state["protectedCollections"]) == 3

    settings.rag_collection_retention_generations = 2
    knowledge = store(settings, client)
    sixth = document(version=6)
    await rebuild(knowledge, documents(sixth), document_count=1,
                  expected_documents=(sixth,))
    clock[0] += 2
    seventh = document(version=7)
    await rebuild(knowledge, documents(seventh), document_count=1,
                  expected_documents=(seventh,))
    business = [
        name for name in client.collections
        if name != knowledge._control_collection_name()
    ]
    assert len(business) == 2
    state = next(
        row for row in client.collections[knowledge._control_collection_name()]
        if row.get("recordType") == "collection_retention_state"
    )
    assert state["retentionGenerations"] == 2
    assert state["retentionComplete"] is True
    assert len(state["protectedCollections"]) == 1


@pytest.mark.asyncio
async def test_rebuild_cleanup_preserves_generations_inside_grace(settings, monkeypatch):
    clock = [2_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    for version in range(1, 4):
        value = document(version=version)
        await rebuild(
            knowledge, documents(value), document_count=1,
            mutation_lease=lease(
                f"collection:{settings.milvus_collection_alias}",
                200 + version, operation="REBUILD",
            ),
            expected_documents=(value,),
        )
        clock[0] += 10

    controlled = [
        name for name in client.collections
        if name.startswith(knowledge._collection_name("embedding-v1", 2))
    ]
    assert len(controlled) == 3
    assert client.aliases[settings.milvus_collection_alias] in controlled


@pytest.mark.asyncio
async def test_retirement_grace_starts_when_alias_leaves_long_active_collection(
    settings, monkeypatch,
):
    clock = [1_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    first = document(version=1)
    await rebuild(knowledge, documents(first), document_count=1,
                  expected_documents=(first,))
    clock[0] = 100_000.0
    second = document(version=2)
    await rebuild(knowledge, documents(second), document_count=1,
                  expected_documents=(second,))
    clock[0] += 1
    third = document(version=3)
    await rebuild(knowledge, documents(third), document_count=1,
                  expected_documents=(third,))

    business = [
        name for name in client.collections
        if name.startswith(knowledge._collection_name("embedding-v1", 2))
    ]
    assert len(business) == 3


@pytest.mark.asyncio
async def test_legacy_collection_is_marked_then_deleted_only_after_grace(
    settings, monkeypatch,
):
    clock = [5_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    canonical = knowledge._collection_name("embedding-v1", 2)
    names = [f"{canonical}_staging_legacy_{index}" for index in range(3)]
    for index, name in enumerate(names):
        client.collections[name] = []
        client.dimensions[name] = 2
        client.descriptions[name] = knowledge._collection_metadata(
            "embedding-v1", 2, index + 1,
        )
    client.aliases[settings.milvus_collection_alias] = names[-1]

    await knowledge._cleanup_retired_collections(
        current=names[-1], just_replaced=names[-2],
        model="embedding-v1", dimension=2,
        permit=FakeMutationPermit(client.coordinator, lease(
            f"collection:{settings.milvus_collection_alias}", 10,
            operation="REBUILD",
        )),
    )
    assert names[0] in client.collections
    control = client.collections[knowledge._control_collection_name()]
    legacy_marker = next(
        row for row in control
        if row.get("id") == knowledge._retirement_id(names[0])
    )
    assert legacy_marker["retiredAt"] == 5_000.0

    clock[0] += 62
    await knowledge._cleanup_retired_collections(
        current=names[-1], just_replaced=names[-2],
        model="embedding-v1", dimension=2,
        permit=FakeMutationPermit(client.coordinator, lease(
            f"collection:{settings.milvus_collection_alias}", 11,
            operation="REBUILD",
        )),
    )
    assert names[0] not in client.collections


@pytest.mark.asyncio
async def test_model_dimension_migration_deletes_retired_old_namespace(
    settings, monkeypatch,
):
    clock = [7_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_retention_generations = 2
    settings.rag_collection_cleanup_grace_seconds = 1
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    async def rebuild_variant(version, model, dimension, fence):
        value = document(version=version, model=model, dimension=dimension)
        return await knowledge.rebuild_collection(
            documents(value),
            lease=lease(
                f"collection:{settings.milvus_collection_alias}", fence,
                operation="REBUILD",
            ),
            plan=RebuildPlan(
                embedding_model_version=model, embedding_dimension=dimension,
                etl_version="etl-v1", document_count=1,
                document_fingerprint=rebuild_fingerprint((value,)),
            ),
        )

    old = await rebuild_variant(1, "embedding-v1", 2, 701)
    clock[0] += 2
    await rebuild_variant(2, "embedding-v2", 3, 702)
    clock[0] += 2
    await rebuild_variant(3, "embedding-v2", 3, 703)

    assert old.collection_name not in client.collections
    control = client.collections[knowledge._control_collection_name()]
    assert not any(
        row.get("collectionName") == old.collection_name for row in control
    )


@pytest.mark.asyncio
async def test_legacy_collection_without_alias_hash_is_preserved(
    settings, monkeypatch,
):
    clock = [9_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_grace_seconds = 1
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    current = knowledge._collection_name("embedding-v1", 2)
    legacy = f"{current}_staging_legacy_without_alias_hash"
    for name in (current, legacy):
        client.collections[name] = []
        client.dimensions[name] = 2
        metadata = json.loads(knowledge._collection_metadata("embedding-v1", 2, 1))
        if name == legacy:
            metadata.pop("aliasHash", None)
        client.descriptions[name] = json.dumps(metadata)
    client.aliases[settings.milvus_collection_alias] = current

    for fence in range(801, 804):
        await knowledge._cleanup_retired_collections(
            current=current, just_replaced=None,
            model="embedding-v1", dimension=2,
            permit=FakeMutationPermit(client.coordinator, lease(
                f"collection:{settings.milvus_collection_alias}", fence,
                operation="REBUILD",
            )),
        )
        clock[0] += 2

    assert legacy in client.collections
    assert not any(
        row.get("collectionName") == legacy
        for row in client.collections[knowledge._control_collection_name()]
    )


@pytest.mark.asyncio
async def test_retirement_control_write_failure_does_not_fail_rebuild(
    settings, monkeypatch, caplog,
):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = document(version=1)
    await rebuild(knowledge, documents(first), document_count=1,
                  expected_documents=(first,))
    old = client.aliases[settings.milvus_collection_alias]
    original_upsert = client.upsert

    def fail_retirement(collection_name, data, **kwargs):
        if collection_name == knowledge._control_collection_name() and any(
            row.get("recordType") == "collection_retirement" for row in data
        ):
            raise RuntimeError("control unavailable with sensitive body")
        return original_upsert(collection_name, data, **kwargs)

    monkeypatch.setattr(client, "upsert", fail_retirement)
    second = document(version=2)
    result = await rebuild(knowledge, documents(second), document_count=1,
                           expected_documents=(second,))

    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert old in client.collections
    assert "cleanup deferred" in caplog.text
    assert "sensitive body" not in caplog.text


@pytest.mark.asyncio
async def test_rebuild_cleanup_list_failure_is_deferred_and_retried(
    settings, monkeypatch, caplog,
):
    clock = [3_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    original_list = client.list_collections

    first = document(version=1)
    await rebuild(knowledge, documents(first), document_count=1,
                  expected_documents=(first,))
    clock[0] += 70
    client.list_collections = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("list"))
    second = document(version=2)
    result = await rebuild(
        knowledge, documents(second), document_count=1,
        mutation_lease=lease(
            f"collection:{settings.milvus_collection_alias}", 302,
            operation="REBUILD",
        ), expected_documents=(second,),
    )
    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert "cleanup deferred" in caplog.text
    client.list_collections = original_list
    clock[0] += 70
    third = document(version=3)
    await rebuild(
        knowledge, documents(third), document_count=1,
        mutation_lease=lease(
            f"collection:{settings.milvus_collection_alias}", 303,
            operation="REBUILD",
        ), expected_documents=(third,),
    )
    assert len([
        name for name in client.collections
        if name.startswith(knowledge._collection_name("embedding-v1", 2))
    ]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_method", ["describe", "drop"])
async def test_rebuild_cleanup_timeout_preserves_success_current_and_rollback(
    settings, monkeypatch, slow_method,
):
    clock = [4_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_timeout_seconds = 0.02
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)

    for version in range(1, 3):
        value = document(version=version)
        await rebuild(
            knowledge, documents(value), document_count=1,
            mutation_lease=lease(
                f"collection:{settings.milvus_collection_alias}", 400 + version,
                operation="REBUILD",
            ), expected_documents=(value,),
        )
        clock[0] += 70

    previous = client.aliases[settings.milvus_collection_alias]
    retired_candidate = next(
        name for name in client.collections
        if name.startswith(knowledge._collection_name("embedding-v1", 2))
        and name != previous
    )
    if slow_method == "describe":
        original_describe = client.describe_collection

        def slow_current_describe(collection_name, timeout=None, **kwargs):
            if collection_name == retired_candidate:
                time.sleep(float(timeout or 0.02) + 0.01)
                raise TimeoutError
            return original_describe(collection_name, timeout=timeout, **kwargs)

        client.describe_collection = slow_current_describe
    else:
        def slow_drop(_collection_name, timeout=None, **_kwargs):
            time.sleep(float(timeout or 0.02) + 0.01)
            raise TimeoutError

        client.drop_collection = slow_drop
    current_document = document(version=3)
    started = time.perf_counter()
    result = await rebuild(
        knowledge, documents(current_document), document_count=1,
        mutation_lease=lease(
            f"collection:{settings.milvus_collection_alias}", 403,
            operation="REBUILD",
        ), expected_documents=(current_document,),
    )

    assert time.perf_counter() - started < 0.5
    assert result.collection_name == client.aliases[settings.milvus_collection_alias]
    assert result.collection_name in client.collections
    assert previous in client.collections


@pytest.mark.asyncio
async def test_rebuild_cleanup_scan_limit_advances_markers_and_deletes_in_batches(
    settings, monkeypatch,
):
    clock = [6_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_scan_limit = 10
    settings.rag_collection_cleanup_timeout_seconds = 30
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    canonical = knowledge._collection_name("embedding-v1", 2)
    names = [f"{canonical}_staging_scan_{index:03d}" for index in range(101)]
    for index, name in enumerate(names):
        client.collections[name] = []
        client.dimensions[name] = 2
        client.descriptions[name] = knowledge._collection_metadata(
            "embedding-v1", 2, index + 1,
        )
    client.aliases[settings.milvus_collection_alias] = names[-1]
    control = await knowledge._ensure_control_collection(99)
    client.collections[control].append({
        "id": knowledge._retention_state_id(),
        "recordType": "collection_retention_state",
        "protectedCollections": [names[-2]], "retentionComplete": True,
        "retentionGenerations": 2, "aliasHash": knowledge._alias_hash(),
        "mutationFence": 99, "schemaVersion": 1,
        "isActive": False, "embedding": [0.0],
    })
    client.collections[control].extend({
        "id": knowledge._retirement_id(name),
        "recordType": "collection_retirement", "collectionName": name,
        "retiredAt": clock[0] - 62, "aliasHash": knowledge._alias_hash(),
        "mutationFence": 99, "schemaVersion": 1,
        "isActive": False, "embedding": [0.0],
    } for name in names[:-2])

    remaining_counts = []
    for fence in range(100, 110):
        before = len(client.describe_calls)
        await knowledge._cleanup_retired_collections(
            current=names[-1], just_replaced=None,
            model="embedding-v1", dimension=2,
            permit=FakeMutationPermit(client.coordinator, lease(
                f"collection:{settings.milvus_collection_alias}", fence,
                operation="REBUILD",
            )),
        )
        described_business = [
            name for name in client.describe_calls[before:] if name in names
        ]
        assert len(described_business) <= 10
        remaining_counts.append(sum(
            row.get("recordType") == "collection_retirement"
            for row in client.collections[knowledge._control_collection_name()]
        ))
    assert remaining_counts == sorted(remaining_counts, reverse=True)
    assert remaining_counts[-1] == 0
    assert set(client.collections).intersection(names) == {names[-2], names[-1]}


@pytest.mark.asyncio
async def test_cleanup_large_namespace_reads_only_one_marker_batch_per_round(
    settings, monkeypatch,
):
    clock = [8_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    settings.rag_collection_cleanup_scan_limit = 100
    settings.rag_collection_cleanup_timeout_seconds = 60
    settings.rag_collection_cleanup_grace_seconds = 61
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    canonical = knowledge._collection_name("embedding-v1", 2)
    names = [f"{canonical}_staging_large_{index:05d}" for index in range(10_000)]
    for index, name in enumerate(names):
        client.collections[name] = []
        client.dimensions[name] = 2
        client.descriptions[name] = knowledge._collection_metadata(
            "embedding-v1", 2, index + 1,
        )
    client.aliases[settings.milvus_collection_alias] = names[-1]
    control = await knowledge._ensure_control_collection(299)
    client.collections[control].append({
        "id": knowledge._retention_state_id(),
        "recordType": "collection_retention_state",
        "protectedCollections": [names[-2]], "retentionComplete": True,
        "retentionGenerations": 2, "aliasHash": knowledge._alias_hash(),
        "mutationFence": 299, "schemaVersion": 1,
        "isActive": False, "embedding": [0.0],
    })
    client.collections[control].extend({
        "id": knowledge._retirement_id(name),
        "recordType": "collection_retirement", "collectionName": name,
        "retiredAt": clock[0] - 120, "aliasHash": knowledge._alias_hash(),
        "mutationFence": 299, "schemaVersion": 1,
        "isActive": False, "embedding": [0.0],
    } for name in names[:-2])

    remaining_counts = []
    for fence in range(300, 303):
        before_iterators = len(client.iterator_calls)
        before_describes = len(client.describe_calls)
        before_count = len(client.collections)
        permit = FakeMutationPermit(client.coordinator, lease(
            f"collection:{settings.milvus_collection_alias}", fence,
            operation="REBUILD",
        ))
        await knowledge._cleanup_retired_collections(
            current=names[-1], just_replaced=None,
            model="embedding-v1", dimension=2, permit=permit,
        )
        marker_queries = [
            call for call in client.iterator_calls[before_iterators:]
            if 'recordType == "collection_retirement"' in call["filter"]
        ]
        assert marker_queries
        assert all(call["limit"] <= 100 for call in marker_queries)
        assert len([
            name for name in client.describe_calls[before_describes:] if name in names
        ]) <= 100
        assert before_count - len(client.collections) <= 100
        remaining_counts.append(sum(
            row.get("recordType") == "collection_retirement"
            for row in client.collections[knowledge._control_collection_name()]
        ))
    assert remaining_counts == [9_898, 9_798, 9_698]


@pytest.mark.asyncio
async def test_revoked_permit_stops_post_publish_cleanup_mutations(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = document(version=1)
    await rebuild(knowledge, documents(first), document_count=1,
                  expected_documents=(first,))
    control = knowledge._control_collection_name()
    before_control = deepcopy(client.collections[control])
    before_business = set(client.collections)
    client.coordinator.revoke_after_alias_switch = True
    second = document(version=2)

    result = await rebuild(knowledge, documents(second), document_count=1,
                           expected_documents=(second,))

    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert client.collections[control] == before_control
    assert before_business.issubset(client.collections)


@pytest.mark.asyncio
async def test_publish_clears_stale_target_retirement_marker_before_alias_switch(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    value = document(version=1)
    mutation_lease = lease(
        f"collection:{settings.milvus_collection_alias}", 501,
        operation="REBUILD",
    )
    plan = RebuildPlan(
        embedding_model_version="embedding-v1", embedding_dimension=2,
        etl_version="etl-v1", document_count=1,
        document_fingerprint=rebuild_fingerprint((value,)),
    )
    target = knowledge._rebuild_collection_name(
        "embedding-v1", 2, mutation_lease, plan,
    )
    control = await knowledge._ensure_control_collection(mutation_lease.fence)
    client.collections[control].append({
        "id": knowledge._retirement_id(target),
        "recordType": "collection_retirement", "collectionName": target,
        "retiredAt": 1.0, "schemaVersion": 1, "embedding": [0.0],
    })

    result = await knowledge.rebuild_collection(
        documents(value), lease=mutation_lease, plan=plan,
    )

    assert result.collection_name == target
    assert client.aliases[settings.milvus_collection_alias] == target
    assert not any(
        row.get("id") == knowledge._retirement_id(target)
        for row in client.collections[control]
    )


@pytest.mark.asyncio
async def test_publish_does_not_switch_alias_when_target_marker_clear_is_uncertain(
    settings, monkeypatch,
):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    value = document(version=1)
    mutation_lease = lease(
        f"collection:{settings.milvus_collection_alias}", 502,
        operation="REBUILD",
    )
    plan = RebuildPlan(
        embedding_model_version="embedding-v1", embedding_dimension=2,
        etl_version="etl-v1", document_count=1,
        document_fingerprint=rebuild_fingerprint((value,)),
    )
    target = knowledge._rebuild_collection_name(
        "embedding-v1", 2, mutation_lease, plan,
    )
    control = await knowledge._ensure_control_collection(mutation_lease.fence)
    marker_id = knowledge._retirement_id(target)
    client.collections[control].append({
        "id": marker_id, "recordType": "collection_retirement",
        "collectionName": target, "retiredAt": 1.0,
        "schemaVersion": 1, "embedding": [0.0],
    })
    original_delete = client.delete

    def uncertain_delete(collection_name, ids, **kwargs):
        if collection_name == control and marker_id in ids:
            raise RuntimeError("uncertain delete")
        return original_delete(collection_name, ids, **kwargs)

    monkeypatch.setattr(client, "delete", uncertain_delete)

    with pytest.raises(
        MilvusKnowledgeError,
        match="^KNOWLEDGE_RETIREMENT_MARKER_CLEAR_FAILED$",
    ):
        await knowledge.rebuild_collection(
            documents(value), lease=mutation_lease, plan=plan,
        )
    assert settings.milvus_collection_alias not in client.aliases


@pytest.mark.asyncio
async def test_rebuild_inserts_bounded_batches_and_consumes_progressively(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = document(document_id="doc-1")
    first = replace(first, chunks=tuple(
        replace(
            first.chunks[0], chunk_id=f"doc-1:1:{index}", chunk_index=index,
            content=f"content-{index}", content_hash=f"hash-{index}",
        )
        for index in range(205)
    ))

    async def progressive_documents():
        yield first
        assert client.insert_sizes == [100, 100, 5, 1]
        yield document(document_id="doc-2")

    result = await rebuild(
        knowledge, progressive_documents(), document_count=2,
        expected_documents=(first, document(document_id="doc-2")),
    )

    assert result.chunk_count == 207
    assert max(client.insert_sizes) <= 100
    assert client.insert_sizes == [100, 100, 5, 1, 2, 1, 1]


@pytest.mark.asyncio
async def test_rebuild_second_batch_failure_preserves_old_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    value = document(version=2)
    value = replace(value, chunks=tuple(
        replace(
            value.chunks[0], chunk_id=f"doc-1:2:{index}", chunk_index=index,
            content=f"content-{index}", content_hash=f"hash-{index}",
        )
        for index in range(150)
    ))
    original_insert = client.insert
    calls = 0

    def fail_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second batch failed")
        return original_insert(*args, **kwargs)

    client.insert = fail_second_insert
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VECTOR_WRITE_FAILED$"):
        await rebuild(knowledge, documents(value))

    assert calls == 2
    assert client.aliases[alias] == old
    assert all("_staging_" not in name for name in client.collections)


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
async def test_empty_rebuild_clears_old_alias_with_inherited_schema(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]

    result = await rebuild(knowledge, documents(), document_count=0)

    assert result.document_count == 0
    assert result.chunk_count == 0
    assert not result.idempotent
    assert client.aliases[alias] == result.collection_name
    assert result.collection_name != old
    assert client.dimensions[result.collection_name] == 2
    assert await knowledge.search([0.1, 0.2], 8) == []


@pytest.mark.asyncio
async def test_empty_rebuild_without_alias_uses_configured_model_and_dimension(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    mutation_lease = lease(
        "collection:customer_service_knowledge", 101, operation="REBUILD"
    )
    plan = RebuildPlan(
        embedding_model_version=settings.rag_embedding_model,
        embedding_dimension=3072,
        etl_version="etl-v1", document_count=0,
        document_fingerprint=rebuild_fingerprint(()),
    )

    result = await knowledge.rebuild_collection(
        documents(), lease=mutation_lease, plan=plan,
    )

    assert result.document_count == 0
    assert client.dimensions[result.collection_name] == 3072
    metadata = json.loads(client.descriptions[result.collection_name])
    assert metadata["embeddingModelVersion"] == settings.rag_embedding_model
    assert metadata["embeddingDimension"] == 3072


@pytest.mark.asyncio
async def test_rebuild_replay_is_idempotent_without_consuming_documents(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    mutation_lease = lease(
        "collection:customer_service_knowledge", 102, operation="REBUILD"
    )
    plan = RebuildPlan(
        embedding_model_version="embedding-v1", embedding_dimension=2,
        etl_version="etl-v1", document_count=1,
        document_fingerprint=rebuild_fingerprint((document(),)),
    )
    first = await knowledge.rebuild_collection(
        documents(document()), lease=mutation_lease, plan=plan,
    )

    async def must_not_consume():
        raise AssertionError("idempotent replay consumed documents")
        yield document()

    replay = await knowledge.rebuild_collection(
        must_not_consume(), lease=mutation_lease, plan=plan,
    )

    assert replay.collection_name == first.collection_name
    assert replay.document_count == 1
    assert replay.chunk_count == 2
    assert replay.idempotent
    rows = client.collections[first.collection_name]
    assert len([row for row in rows if row.get("recordType") == "rebuild_complete"]) == 1


@pytest.mark.asyncio
async def test_rebuild_rejects_polluted_deterministic_staging(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    mutation_lease = lease(
        "collection:customer_service_knowledge", 103, operation="REBUILD"
    )
    plan = RebuildPlan(
        embedding_model_version="embedding-v1", embedding_dimension=2,
        etl_version="etl-v1", document_count=1,
        document_fingerprint="2" * 64,
    )
    staging = knowledge._rebuild_collection_name(
        "embedding-v1", 2, mutation_lease, plan
    )
    client.create_collection(
        staging, 2,
        description=knowledge._collection_metadata(
            "embedding-v1", 2, mutation_lease.fence
        ),
    )

    async def must_not_consume():
        raise AssertionError("polluted staging consumed documents")
        yield document()

    with pytest.raises(
        MilvusKnowledgeError, match="^KNOWLEDGE_REBUILD_STAGING_CONFLICT$"
    ):
        await knowledge.rebuild_collection(
            must_not_consume(), lease=mutation_lease, plan=plan,
        )


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
    await rebuild(
        knowledge, documents(document(), document(document_id="doc-2")),
        document_count=2,
    )
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
async def test_retirement_marker_and_tombstone_use_isolated_control_rows(
    settings, monkeypatch,
):
    clock = [7_000.0]
    monkeypatch.setattr(milvus_module.time, "time", lambda: clock[0])
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await delete_version(knowledge, "doc-1", 1, mutation_lease=lease(
        "document:doc-1", 7, operation="DELETE"
    ))
    first = document(version=2)
    await rebuild(knowledge, documents(first), document_count=1,
                  expected_documents=(first,))
    second = document(version=3)
    await rebuild(knowledge, documents(second), document_count=1,
                  expected_documents=(second,))

    control = client.collections[knowledge._control_collection_name()]
    record_types = {row.get("recordType") for row in control}
    assert "tombstone" in record_types
    assert "collection_retirement" in record_types
    ids = [row["id"] for row in control]
    assert len(ids) == len(set(ids))


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


@pytest.mark.asyncio
async def test_cancellation_during_rebuild_insert_keeps_old_alias_and_cleans_staging(
    settings,
):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    value = document(version=2)
    value = replace(value, chunks=tuple(
        replace(
            value.chunks[0], chunk_id=f"doc-1:2:{index}", chunk_index=index,
            content=f"content-{index}", content_hash=f"hash-{index}",
        )
        for index in range(150)
    ))
    started, release = threading.Event(), threading.Event()
    original_insert = client.insert
    calls = 0

    def block_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            assert release.wait(2)
        return original_insert(*args, **kwargs)

    client.insert = block_second_insert
    task = asyncio.create_task(rebuild(knowledge, documents(value)))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)

    assert client.aliases[alias] == old
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.aliases[alias] == old
    assert all("_staging_" not in name for name in client.collections)


@pytest.mark.asyncio
async def test_cancellation_after_rebuild_create_cleans_new_staging(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    started, release = threading.Event(), threading.Event()
    client.block_next_create = (started, release)

    task = asyncio.create_task(rebuild(
        knowledge, documents(document(version=2)), fence=104,
    ))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)

    assert client.aliases[alias] == old
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.aliases[alias] == old
    assert all("_staging_" not in name for name in client.collections)


@pytest.mark.asyncio
async def test_rebuild_validation_failure_after_create_cleans_new_staging(
    settings, monkeypatch,
):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await index_document(knowledge, document())
    alias = settings.milvus_collection_alias
    old = client.aliases[alias]
    original = knowledge._validate_rebuild_collection
    calls = 0

    async def fail_first_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_STAGING_CONFLICT")
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        knowledge, "_validate_rebuild_collection", fail_first_validation
    )
    with pytest.raises(
        MilvusKnowledgeError, match="^KNOWLEDGE_REBUILD_STAGING_CONFLICT$"
    ):
        await rebuild(
            knowledge, documents(document(version=2)), fence=105,
        )

    assert client.aliases[alias] == old
    assert all("_staging_" not in name for name in client.collections)


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

    value = document()
    result = await knowledge.rebuild_collection(
        documents(value),
        lease=lease(f"collection:{alias}", 100, operation="REBUILD"),
        plan=RebuildPlan(
            "embedding-v1", 2, "etl-v1", 1,
            rebuild_fingerprint((value,), alias=alias),
        ),
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
        value = document(document_id=document_id)
        result = await knowledge.rebuild_collection(
            documents(value),
            lease=lease(
                f"collection:{alias}", 100 + index, operation="REBUILD"
            ),
            plan=RebuildPlan(
                "embedding-v1", 2, "etl-v1", 1,
                rebuild_fingerprint((value,), alias=alias),
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
    assert {
        row["documentId"] for row in client.collections[first_control]
        if row.get("recordType") == "tombstone"
    } == {"doc-1"}
    assert {
        row["documentId"] for row in client.collections[second_control]
        if row.get("recordType") == "tombstone"
    } == {"doc-2"}


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
