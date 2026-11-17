"""有界只读观测的失败边界；Fake只实现读方法，意外写入会直接失败。"""
import json
import pytest
import httpx
from ai_service.orchestration.knowledge_capacity import age_seconds, observe
from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
from ai_service.api.knowledge_publication_schemas import InventoryEntry
from test_authoritative_knowledge import Control
from test_knowledge_publication import env, seed, snapshot


async def test_inventory_strict_pages_and_change_rejected():
    data=dict(enabled=True,epoch=2,publicationId='task_2',nextEpoch=1,hasMore=True,
              entries=[dict(publicationId='task_1',epoch=1,collectionName='candidate_1',createTime='2030-01-01 00:00:00')])
    def handler(request):
        if 'afterEpoch=1' in str(request.url):
            return httpx.Response(200,json=dict(code=0,data={**data,'epoch':3,'nextEpoch':2,'hasMore':False,
                'entries':[dict(publicationId='task_2',epoch=2,collectionName='candidate_2',createTime='2030-01-01 00:00:00')]}))
        return httpx.Response(200,json=dict(code=0,data=data))
    client=SpringKnowledgePublicationClient(gateway_base_url='http://spring/api',bearer_token='secret',transport=httpx.MockTransport(handler))
    with pytest.raises(Exception,match='KNOWLEDGE_INVENTORY_CHANGED'): await client.inventory()
    await client.close()


async def test_inventory_valid_pagination_and_budget_stops_before_extra_request():
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        cursor=int(request.url.params['afterEpoch'])
        item=dict(publicationId=f'task_{cursor+1}',epoch=cursor+1,collectionName=f'candidate_{cursor+1}',createTime='2030-01-01 00:00:00')
        return httpx.Response(200,json=dict(code=0,data=dict(enabled=True,epoch=2,publicationId='task_2',nextEpoch=cursor+1,hasMore=cursor==0,entries=[item])))
    client=SpringKnowledgePublicationClient(gateway_base_url='http://spring/api',bearer_token='secret',transport=httpx.MockTransport(handler))
    control,entries=await client.inventory()
    assert len(entries)==2 and control==(True,2,'task_2') and len(calls)==2
    calls.clear()
    with pytest.raises(Exception,match='BUDGET'): await client.inventory(max_entries=1)
    assert len(calls)==1
    await client.close()


@pytest.mark.parametrize('bad',['cursor','empty_more','duplicate','bool'])
async def test_inventory_invalid_pages_fail_closed(bad):
    entries=[dict(publicationId='task_1',epoch=1,collectionName='candidate_1',createTime='2030-01-01 00:00:00')]
    data=dict(enabled=True,epoch=2,publicationId='task_2',nextEpoch=1,hasMore=False,entries=entries)
    if bad=='cursor': data['nextEpoch']=0
    if bad=='empty_more': data.update(entries=[],hasMore=True,nextEpoch=0)
    if bad=='duplicate': data['entries']=entries*2
    if bad=='bool': data['epoch']=True
    client=SpringKnowledgePublicationClient(gateway_base_url='http://spring/api',bearer_token='secret',transport=httpx.MockTransport(lambda _:httpx.Response(200,json=dict(code=0,data=data))))
    with pytest.raises(Exception): await client.inventory()
    await client.close()


def test_age_unknown_future_and_hybrid_timestamp():
    now=1_800_000_000
    assert age_seconds(int((now-12)*1000)<<18,now)==12
    for value in [None,True,0,'bad',int((now+1)*1000)<<18]: assert age_seconds(value,now) is None


async def capacity_env(env):
    settings, fake, store, publications=env
    receipt=await seed(env);control=Control(snapshot(receipt))
    async def inventory():
        return (True,control.current.epoch,control.current.publication_id),[InventoryEntry(publication_id=receipt.publication_id,
            epoch=control.current.epoch,collection_name=receipt.candidate_collection,create_time='2030-01-01 00:00:00')]
    control.inventory=inventory
    old=store._call
    async def call(method,*args,**kwargs):
        if method=='list_collections': return list(fake.collections)
        if method=='get_collection_stats': return {'row_count':len(fake.collections[args[0]])}
        return await old(method,*args,**kwargs)
    store._call=call
    return settings,fake,store,control,receipt


async def test_capacity_json_is_size_only_and_never_delete_proof(env):
    settings,fake,store,control,receipt=await capacity_env(env)
    result=await observe(settings,control,store,measure_json=True)
    assert result['complete'] and result['measuredCollections']==1
    assert result['entries'][0]['reference']=='CURRENT'
    encoded=json.dumps(result)
    assert 'embedding' not in encoded and 'sha256' not in encoded and '私有知识正文' not in encoded
    assert result['diskBytes'] is None and not result['deletionSafetyEstablished']


async def test_capacity_missing_target_and_budget_are_unknown(env):
    settings,fake,store,control,receipt=await capacity_env(env)
    result=await observe(settings,control,store,measure_json=True,total_json_budget=1)
    assert not result['complete'] and result['entries'][0]['serializedJsonBytes'] is None
    fake.collections.pop(receipt.candidate_collection)
    result=await observe(settings,control,store)
    assert result['entries'][0]['status']=='MISSING_TARGET' and not result['complete']


async def test_capacity_changed_epoch_does_not_claim_complete(env):
    settings,fake,store,control,receipt=await capacity_env(env)
    original=store._call
    async def call(method,*args,**kwargs):
        result=await original(method,*args,**kwargs)
        if method=='get_collection_stats': control.current.epoch+=1
        return result
    store._call=call
    result=await observe(settings,control,store)
    assert not result['complete'] and not result['controlAndCollectionListStable']


async def test_unobserved_candidate_and_historical_fact_remain_distinct(env):
    from test_knowledge_publication import request
    from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
    settings,fake,store,control,first=await capacity_env(env)
    second=await KnowledgePublicationPreparer(settings,None,None,store).prepare(request(first))
    result=await observe(settings,control,store)
    assert {r['reference'] for r in result['entries']}=={'CURRENT','NO_OBSERVED_FACT'}
    control.current=snapshot(second)
    async def inventory():
        return (True,control.current.epoch,control.current.publication_id),[
            InventoryEntry(publication_id=r.publication_id,epoch=r.base_epoch+1,collection_name=r.candidate_collection,create_time='2030-01-01 00:00:00') for r in (first,second)]
    control.inventory=inventory
    result=await observe(settings,control,store)
    assert {r['reference'] for r in result['entries']}=={'CURRENT','HISTORICAL_FACT'}
    assert not result['deletionSafetyEstablished']
