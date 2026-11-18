"""Java worker 隔离竞态的 Milvus 子进程：合成向量，不创建应用或调用模型。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

from pymilvus import MilvusClient

from ai_service.api.knowledge_publication_schemas import PublicationContext, ReadSnapshot
from ai_service.config import Settings
from ai_service.infrastructure.knowledge_publication_store import KIND, PublicationStore
from ai_service.infrastructure.milvus_knowledge import (
    IndexedChunk, IndexedDocument, KnowledgeMutationLease, MilvusKnowledgeStore,
)
from test_knowledge_revision_milvus_integration import LocalCoordinator, OwnedClient


async def run(action: str, directory: Path, input_path: Path | None) -> None:
    if os.getenv("AI_SERVICE_MILVUS_INTEGRATION", "").lower() != "true":
        raise RuntimeError("必须显式开启隔离Milvus验收")
    allowed = Path(__file__).resolve().parents[2] / "tmp"
    directory = directory.resolve()
    if not directory.is_relative_to(allowed) or not directory.name.startswith("rag-publication-worker-"):
        raise ValueError("只允许Java夹具创建的tmp证据目录")
    state_path = directory / "milvus-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "alias": "rag_worker_it_" + uuid4().hex, "collections": [], "reads": 0,
    }
    alias = state["alias"]
    if not re.fullmatch(r"rag_worker_it_[0-9a-f]{32}", alias):
        raise ValueError("随机命名空间无效")

    def save():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    # 不读取.env或外部凭据；本设施只允许固定回环Milvus与随机命名空间。
    settings = Settings(
        _env_file=None, internal_bearer_token="isolated-unused",
        spring_gateway_base_url="http://localhost:8123/api", spring_gateway_bearer_token="isolated-unused",
        milvus_uri="http://localhost:19530", milvus_token="", milvus_database="default",
        milvus_collection_alias=alias, rag_embedding_model="worker-synthetic-v1", rag_embedding_dimension=2,
        milvus_rpc_timeout_seconds=20,
    )
    client = MilvusClient(uri="http://localhost:19530", timeout=20)

    class PersistedOwner(OwnedClient):
        def create_collection(self, collection_name, *args, **kwargs):
            assert collection_name.startswith(alias + "_")
            # RPC前保存精确名称，即使创建成功后丢响应也能清理本例资源。
            if collection_name not in state["collections"]:
                state["collections"].append(collection_name)
                save()
            return super().create_collection(collection_name, *args, **kwargs)

    owned = PersistedOwner(client, alias)
    knowledge = MilvusKnowledgeStore(settings, client_factory=lambda **_kwargs: owned,
                                    mutation_coordinator=LocalCoordinator())
    publications = PublicationStore(settings, knowledge)
    try:
        if action == "cleanup":
            aliases = client.list_aliases(timeout=20)
            aliases = aliases.get("aliases", []) if isinstance(aliases, dict) else aliases
            if alias in aliases:
                assert client.describe_alias(alias, timeout=20)["collection_name"] in state["collections"]
                client.drop_alias(alias, timeout=20)
            for name in state["collections"]:
                assert name.startswith(alias + "_")
                if client.has_collection(name, timeout=20):
                    metadata = json.loads(client.describe_collection(name, timeout=20)["description"])
                    assert metadata["aliasHash"] == hashlib.sha256(alias.encode()).hexdigest()[:12]
                    assert metadata["kind"] == KIND
                    client.drop_collection(name, timeout=20)
            assert not any(client.has_collection(name, timeout=20) for name in state["collections"])
            state["cleaned"] = True
            save()
            print("MILVUS_NAMESPACE_CLEANED")
            return

        if action == "read":
            assert input_path is not None and input_path.resolve().is_relative_to(directory)
            snapshot = ReadSnapshot.model_validate_json(input_path.read_text(encoding="utf-8"))
            assert snapshot.collection_name in state["collections"]
            if state["reads"] == 0:
                client.create_alias(snapshot.collection_name, alias, timeout=20)
            assert client.describe_alias(alias, timeout=20)["collection_name"] == snapshot.collection_name
            receipt = await publications.validate_publication(snapshot, verify_rows=True)
            hits = await publications.search_publication([0.1, 0.9], 8, snapshot)
            assert len(hits) == 1 and hits[0].document_version == 2
            assert receipt.candidate_collection == state["candidateB"] == snapshot.collection_name
            assert snapshot.publication_id == state["publicationB"]
            assert snapshot.snapshot_hash == receipt.snapshot_hash
            state["reads"] += 1
            state["lastRead"] = {"collection": snapshot.collection_name, "publication": snapshot.publication_id,
                                 "snapshotHash": snapshot.snapshot_hash, "documentVersion": hits[0].document_version}
            save()
            print("B_READ_CONFIRMED")
            return

        if action == "bootstrap":
            context = PublicationContext(publication_id="bootstrap_" + "0" * 32, base_epoch=0,
                                         source_collection=None, documents=[])
            lease = KnowledgeMutationLease(scope="collection:" + alias, operation_id="bootstrap", operation="REBUILD",
                                           fence=1, expires_at=int(time.time()) + 600, proof="isolated-unused")
            documents = []
        elif action == "candidate":
            assert input_path is not None and input_path.resolve().is_relative_to(directory)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            context = PublicationContext.model_validate(payload["context"])
            lease = KnowledgeMutationLease(**{
                {"operationId": "operation_id", "expiresAt": "expires_at"}.get(key, key): value
                for key, value in payload["mutation"]["lease"].items()
            })
            documents = []
            for identity in context.documents:
                text = "同一worker时序的合成正文V" + str(identity.document_version)
                chunk = IndexedChunk("worker:" + str(identity.document_version), 0, text, "line 1",
                                     hashlib.sha256(text.encode()).hexdigest(), (0.1, 0.9))
                documents.append(IndexedDocument(
                    document_id=identity.document_id, document_version=identity.document_version,
                    etl_version=str(identity.index_revision), embedding_model_version=settings.rag_embedding_model,
                    file_name=identity.file_name, file_type=identity.file_type.lower(), content_hash=identity.sha256,
                    index_revision=identity.index_revision, activation_revision=identity.activation_revision, chunks=(chunk,),
                ))
        else:
            raise ValueError("未知隔离操作")
        # 候选服务使用生产不可变写入器；此进程许可只保证本例隔离，Spring租约验证由Java真实事务负责。
        async with knowledge._mutation(lease, scope=lease.scope, operation="rebuild" if action == "bootstrap" else "upsert") as permit:
            receipt = await publications.write(context, lease, documents, permit)
        if action == "candidate" and documents[0].document_version == 2:
            state["candidateB"], state["publicationB"] = receipt.candidate_collection, receipt.publication_id
            save()
        print(receipt.model_dump_json(by_alias=True))
    finally:
        await knowledge.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) == 4 else None))
