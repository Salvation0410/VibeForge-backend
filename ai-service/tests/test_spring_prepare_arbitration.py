"""授权响应失联或协议异常不得重发BEGIN。"""
import httpx
import pytest
from ai_service.api.knowledge_publication_schemas import PrepareExecutionRequest
from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from test_knowledge_prepare_recovery import anchored
from test_knowledge_publication import value


@pytest.mark.parametrize("fault",["disconnect","503","oversize","bad-status","bad-grant"])
async def test_bad_grant_is_bounded_and_not_retried(settings,fault):
    body=anchored(settings,value(settings))
    request=PrepareExecutionRequest(context=body.context,lease=body.mutation.lease,executor_id="a"*32,status="BEGIN")
    calls=[]
    def handler(raw):
        calls.append(raw)
        assert raw.method=="POST" and raw.url.path.endswith("/prepare-execution")
        if fault=="disconnect":raise httpx.RemoteProtocolError("private")
        if fault=="503":return httpx.Response(503)
        if fault=="oversize":return httpx.Response(200,headers={"content-length":"999999"},content=b"{}")
        return httpx.Response(200,json={"code":0,"data":{"granted":True,"status":"WRONG" if fault=="bad-status" else "BUSY"}})
    client=SpringKnowledgePublicationClient(gateway_base_url="http://localhost/internal/ai-tools",bearer_token="test",transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(MilvusKnowledgeError):await client.arbitrate_prepare(request)
        assert len(calls)==1
    finally:await client.close()
