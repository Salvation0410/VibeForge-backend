"""随机Milvus子进程半写退出，不访问MySQL或供应商。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_publication import value, Downloader, Embeddings
from test_knowledge_prepare_recovery import anchored, retry
from test_knowledge_publication_milvus_integration import publication_env

pytestmark = pytest.mark.skipif(os.getenv("AI_SERVICE_MILVUS_INTEGRATION", "").lower() != "true",
    reason="需要显式本地Milvus隔离验收")


async def test_half_written_child_exit_cannot_recover_or_overwrite(publication_env, tmp_path):
    settings, client, knowledge, publications = publication_env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf8")
    request = anchored(settings, value(settings))
    target = publications.candidate_name(request.context, request.mutation.lease.operation_id, request.mutation.lease.fence)
    # 父fixture登记确定性目标，即使子进程无finally也由归属校验清理。
    knowledge._client.created.add(target)
    payload = tmp_path / "child.json"
    payload.write_text(json.dumps({"alias": knowledge._alias, "path": str(path),
        "request": request.model_dump(by_alias=True)}), encoding="utf8")
    log = tmp_path / "child.log"
    with log.open("wb") as output:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(payload)],
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            code = await asyncio.to_thread(child.wait, 40)
            assert code == 79, "必须到达首批写完后的显式退出点"
        finally:
            if child.poll() is None:
                child.terminate()
                await asyncio.to_thread(child.wait, 10)
    rows = await publications.scan(target)
    assert rows and all(row["recordType"] != "publication_completion" for row in rows)
    before = rows
    embeddings = Embeddings()
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PUBLICATION_INCOMPLETE"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge).prepare(retry(request))
    assert embeddings.calls == 0
    assert await publications.scan(target) == before
    assert not client.has_collection(publications.candidate_name(request.context, "attempt_2", 2))


async def child_run(payload):
    from ai_service.config import Settings
    from ai_service.api.knowledge_publication_schemas import CandidateRequest
    from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore
    from test_knowledge_revision_milvus_integration import LocalCoordinator
    data = json.loads(Path(payload).read_text(encoding="utf8"))
    assert data["alias"].startswith("rag_pub_it_")
    settings = Settings(_env_file=None, milvus_uri="http://localhost:19530", milvus_token="",
        milvus_database="default", milvus_collection_alias=data["alias"],
        rag_embedding_model="publication-synthetic-v1", rag_embedding_dimension=2)
    store = MilvusKnowledgeStore(settings, mutation_coordinator=LocalCoordinator())
    write = store._write_exact
    async def exit_after_body(method, collection, rows):
        await write(method, collection, rows)
        if any(row.get("recordType") == "chunk" for row in rows):
            os._exit(79)
    store._write_exact = exit_after_body
    await KnowledgePublicationPreparer(settings, Downloader(Path(data["path"])), Embeddings(), store).prepare(
        CandidateRequest.model_validate(data["request"]))
    raise RuntimeError("未到达退出点")


if __name__ == "__main__":
    asyncio.run(child_run(sys.argv[1]))
