from __future__ import annotations

import math
from collections.abc import AsyncIterator
from copy import deepcopy

import pytest

from ai_service.infrastructure.milvus_knowledge import (
    IndexedChunk,
    IndexedDocument,
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
    assert factory_calls == [("openai:text-embedding-3-large", {
        "api_key": "closeai-test-secret", "base_url": "https://closeai.test/v1"
    })]


@pytest.mark.asyncio
async def test_closeai_query_embedding_is_validated(settings):
    fake = FakeEmbeddings(query_response=[0.25, 0.75])
    embeddings, _ = provider(settings, fake)
    assert await embeddings.embed_query("refund") == [0.25, 0.75]
    assert fake.query_calls == ["refund"]


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


class FakeMilvusClient:
    def __init__(self):
        self.collections: dict[str, list[dict]] = {}
        self.dimensions: dict[str, int] = {}
        self.aliases: dict[str, str] = {}
        self.search_calls: list[dict] = []
        self.fail_alias_switch = False
        self.corrupt_next_insert = False
        self.corrupt_next_content = False

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, dimension, **_kwargs):
        self.collections[collection_name] = []
        self.dimensions[collection_name] = dimension

    def drop_collection(self, collection_name):
        self.collections.pop(collection_name, None)
        self.dimensions.pop(collection_name, None)

    def insert(self, collection_name, data):
        rows = deepcopy(data)
        if self.corrupt_next_insert and rows:
            rows.pop()
            self.corrupt_next_insert = False
        if self.corrupt_next_content and rows:
            rows[0]["content"] = "corrupt"
            self.corrupt_next_content = False
        self.collections[collection_name].extend(rows)
        return {"insert_count": len(rows)}

    def upsert(self, collection_name, data):
        rows = self.collections[self._resolve(collection_name)]
        by_id = {row["id"]: row for row in rows}
        items = deepcopy(data)
        if self.corrupt_next_insert and items:
            items.pop()
            self.corrupt_next_insert = False
        for item in items:
            by_id[item["id"]] = item
        self.collections[self._resolve(collection_name)] = list(by_id.values())
        return {"upsert_count": len(items)}

    def query(self, collection_name, filter="", output_fields=None, **_kwargs):
        rows = deepcopy(self.collections[self._resolve(collection_name)])
        return [row for row in rows if self._matches(row, filter)]

    def search(self, collection_name, data, filter, limit, output_fields, **_kwargs):
        self.search_calls.append({"collection_name": collection_name, "filter": filter,
                                  "output_fields": output_fields, "data": data})
        rows = [row for row in self.collections[self._resolve(collection_name)]
                if self._matches(row, filter)][:limit]
        return [[{"id": row["id"], "distance": 0.2, "score": 0.8,
                  "entity": {key: deepcopy(row[key]) for key in output_fields}}
                 for row in rows]]

    def describe_alias(self, alias):
        if alias not in self.aliases:
            raise RuntimeError("alias missing")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def create_alias(self, collection_name, alias):
        self.aliases[alias] = collection_name

    def alter_alias(self, collection_name, alias):
        self.aliases[alias] = collection_name
        if self.fail_alias_switch:
            self.fail_alias_switch = False
            raise RuntimeError("switch failed with sensitive provider response")

    def list_collections(self):
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
    return MilvusKnowledgeStore(settings, client_factory=lambda **_kwargs: client)


@pytest.mark.asyncio
async def test_version_write_is_complete_idempotent_and_activates_newest(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = await knowledge.upsert_document_version(document())
    replay = await knowledge.upsert_document_version(document())
    second = await knowledge.upsert_document_version(document(version=2))

    assert first.chunk_count == 2 and not first.idempotent
    assert replay.idempotent
    assert second.document_version == 2
    rows = next(iter(client.collections.values()))
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
    await knowledge.upsert_document_version(document(version=2))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STALE_VERSION$"):
        await knowledge.upsert_document_version(document(version=1))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VERSION_CONFLICT$"):
        await knowledge.upsert_document_version(document(version=2, etl="etl-other"))
    assert all(row["documentVersion"] == 2 and row["isActive"]
               for row in next(iter(client.collections.values())))


@pytest.mark.asyncio
async def test_dimension_mismatch_is_rejected_without_mixing_collection(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await knowledge.upsert_document_version(document())
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH$"):
        await knowledge.upsert_document_version(document(version=2, dimension=3))
    assert list(client.dimensions.values()) == [2]


@pytest.mark.asyncio
async def test_delete_disables_version_and_search_only_returns_active_metadata(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await knowledge.upsert_document_version(document())
    await knowledge.upsert_document_version(document(document_id="doc-2"))
    await knowledge.delete_document("doc-1", 1)

    results = await knowledge.search([0.1, 0.2], 8)
    assert results and {item.document_id for item in results} == {"doc-2"}
    assert "isActive == true" in client.search_calls[-1]["filter"]
    assert "embedding" not in client.search_calls[-1]["output_fields"]
    assert all(not hasattr(item, "embedding") and math.isfinite(item.distance)
               for item in results)


async def documents(*items: IndexedDocument) -> AsyncIterator[IndexedDocument]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_rebuild_validates_staging_then_atomically_switches_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await knowledge.upsert_document_version(document())
    old_collection = client.aliases[settings.milvus_collection_alias]

    result = await knowledge.rebuild_collection(documents(document(version=2), document(document_id="doc-2")))
    assert result.chunk_count == 4 and result.document_count == 2
    assert client.aliases[settings.milvus_collection_alias] == result.collection_name
    assert result.collection_name != old_collection


@pytest.mark.asyncio
async def test_rebuild_integrity_or_alias_failure_keeps_old_alias(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    await knowledge.upsert_document_version(document())
    alias = settings.milvus_collection_alias
    old_collection = client.aliases[alias]

    client.corrupt_next_insert = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await knowledge.rebuild_collection(documents(document(version=2)))
    assert client.aliases[alias] == old_collection

    client.corrupt_next_content = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await knowledge.rebuild_collection(documents(document(version=2)))
    assert client.aliases[alias] == old_collection

    client.fail_alias_switch = True
    with pytest.raises(MilvusKnowledgeError) as caught:
        await knowledge.rebuild_collection(documents(document(version=2)))
    assert str(caught.value) == "KNOWLEDGE_ALIAS_SWITCH_FAILED"
    assert "provider response" not in repr(caught.value)
    assert client.aliases[alias] == old_collection


@pytest.mark.asyncio
async def test_incomplete_document_staging_can_be_retried_idempotently(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    client.corrupt_next_insert = True
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await knowledge.upsert_document_version(document())

    result = await knowledge.upsert_document_version(document())
    assert not result.idempotent
    replay = await knowledge.upsert_document_version(document())
    assert replay.idempotent
    assert len(next(iter(client.collections.values()))) == 2


@pytest.mark.asyncio
async def test_ping_uses_injected_client_and_maps_failures(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    assert await knowledge.ping()
    client.list_collections = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    assert not await knowledge.ping()
