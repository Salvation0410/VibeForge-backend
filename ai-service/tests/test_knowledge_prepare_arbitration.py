"""持久仲裁注入契约：未知授权不付费，同一身份只有一请求执行。"""
import asyncio
import pytest
from ai_service.api.knowledge_publication_schemas import PrepareExecutionResult
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, MilvusKnowledgeStore
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_publication import env, value, Downloader, Embeddings
from test_knowledge_prepare_recovery import anchored
from test_knowledge_recovery_fault_matrix import CurrentPermit


@pytest.mark.parametrize("failure", ["transport", "BUSY", "UNKNOWN", "COMPLETE"])
async def test_ungranted_request_never_embeds(env, tmp_path, failure):
    settings, _, store, _ = env
    path=tmp_path/"manual.txt";path.write_text("私有知识正文",encoding="utf8")
    embeddings=Embeddings()
    class Arbitration:
        async def snapshot(self):
            from ai_service.api.knowledge_publication_schemas import ReadSnapshot
            return ReadSnapshot(enabled=False,epoch=0,publication_id=None,collection_name=None,snapshot_hash=None,
                embedding_model_version=None,embedding_dimension=0,documents=[])
        async def arbitrate_prepare(self, request):
            if failure=="transport":raise MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_UNAVAILABLE")
            return PrepareExecutionResult(granted=False,status=failure)
    with pytest.raises(MilvusKnowledgeError):
        await KnowledgePublicationPreparer(settings,Downloader(path),embeddings,store,arbitration=Arbitration()).prepare(anchored(settings,value(settings)))
    assert embeddings.calls==0


async def test_independent_preparers_share_one_execution_grant(env, tmp_path):
    settings,client,_,_=env
    path=tmp_path/"manual.txt";path.write_text("私有知识正文",encoding="utf8")
    calls=0;entered=asyncio.Event();release=asyncio.Event();granted=False
    class Arbitration:
        async def arbitrate_prepare(self, request):
            nonlocal granted
            if request.status=="COMPLETE":return PrepareExecutionResult(granted=False,status="COMPLETE")
            if granted:return PrepareExecutionResult(granted=False,status="BUSY")
            granted=True;return PrepareExecutionResult(granted=True,status="STARTED")
    class BlockingEmbeddings:
        async def embed_documents(self,texts,**kwargs):
            nonlocal calls
            calls+=1;entered.set();await asyncio.wait_for(release.wait(),5)
            return [[.1,.9] for _ in texts]
    stores=[MilvusKnowledgeStore(settings,client_factory=lambda **_:client,mutation_coordinator=CurrentPermit()) for _ in range(2)]
    preparers=[KnowledgePublicationPreparer(settings,Downloader(path),BlockingEmbeddings(),store,arbitration=Arbitration()) for store in stores]
    request=anchored(settings,value(settings))
    # 单元夹具context=0仅用于本地无head候选，权威前置校验由HTTP资源场景覆盖。
    from unittest.mock import AsyncMock, patch
    validation=patch('ai_service.orchestration.knowledge_publication_boundary.validate_candidate_request',new=AsyncMock())
    validation.start()
    first=asyncio.create_task(preparers[0].prepare(request))
    try:
        await asyncio.wait_for(entered.wait(),5)
        with pytest.raises(MilvusKnowledgeError,match="KNOWLEDGE_PREPARE_BUSY"):await preparers[1].prepare(request)
        release.set();receipt=await first
        assert await preparers[1].prepare(request)==receipt
        assert calls==1
    finally:
        release.set();await asyncio.gather(first,return_exceptions=True)
        for store in stores:await store.close()
        validation.stop()
