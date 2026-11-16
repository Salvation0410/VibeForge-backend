"""复现两端版本约定对应的已知存储行为；通过不表示目标生命周期已修好。"""
from dataclasses import replace
import pytest

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, RebuildPlan
from test_milvus_knowledge import (
    FakeMilvusClient, document, documents, lease, rebuild_fingerprint, store,
)


@pytest.mark.asyncio
async def test_identical_retry_is_idempotent_but_new_etl_same_content_conflicts(settings):
    knowledge=store(settings,FakeMilvusClient())
    value=document(etl='1')
    try:
        await knowledge.upsert_document_version(value,lease=lease('document:doc-1',1))
        retry=await knowledge.upsert_document_version(value,lease=lease('document:doc-1',2))
        assert retry.idempotent
        with pytest.raises(MilvusKnowledgeError,match='KNOWLEDGE_VERSION_CONFLICT'):
            await knowledge.upsert_document_version(replace(value,etl_version='2'),
                                                     lease=lease('document:doc-1',3))
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_enable_using_same_content_version_is_blocked_by_version_tombstone(settings):
    knowledge=store(settings,FakeMilvusClient())
    try:
        await knowledge.upsert_document_version(document(etl='1'),lease=lease('document:doc-1',1))
        await knowledge.delete_document('doc-1',1,lease=lease('document:doc-1',2,operation='DELETE'))
        with pytest.raises(MilvusKnowledgeError,match='KNOWLEDGE_DOCUMENT_DISABLED'):
            await knowledge.upsert_document_version(document(etl='3'),lease=lease('document:doc-1',3))
        await knowledge.upsert_document_version(document(version=2,etl='4'),lease=lease('document:doc-1',4))
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_rebuild_epoch_conflicts_with_later_document_etl_and_empty_rebuild_switches(settings):
    client=FakeMilvusClient()
    knowledge=store(settings,client)
    value=document(etl='rebuild-epoch')
    try:
        plan=RebuildPlan('embedding-v1',2,'rebuild-epoch',1,
                         rebuild_fingerprint([value],etl='rebuild-epoch'))
        await knowledge.rebuild_collection(documents(value),
            lease=lease('collection:customer_service_knowledge',10,operation='REBUILD'),plan=plan)
        with pytest.raises(MilvusKnowledgeError,match='KNOWLEDGE_VERSION_CONFLICT'):
            await knowledge.upsert_document_version(document(etl='2'),lease=lease('document:doc-1',11))
        before=client.aliases['customer_service_knowledge']
        empty=RebuildPlan('embedding-v1',2,'empty-epoch',0,rebuild_fingerprint([],etl='empty-epoch'))
        result=await knowledge.rebuild_collection(documents(),
            lease=lease('collection:customer_service_knowledge',12,operation='REBUILD'),plan=empty)
        assert result.document_count==0
        assert client.aliases['customer_service_knowledge'] != before
    finally:
        await knowledge.close()
