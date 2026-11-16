"""权威 HTTP 边界不使用真实数据库或模型。"""
import gzip
import json

import httpx
import pytest

from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient


DISABLED = dict(enabled=False, epoch=0, publicationId=None, collectionName=None,
                snapshotHash=None, embeddingModelVersion=None, embeddingDimension=0, documents=[])


@pytest.mark.parametrize("base", ["http://spring/api", "http://spring/api/internal/ai-tools/invoke"])
async def test_snapshot_url_auth_and_disabled_control(base):
    def handler(request):
        assert str(request.url) == "http://spring/api/internal/customer-service/knowledge-publications/snapshot"
        assert request.headers['authorization'] == 'Bearer secret'
        return httpx.Response(200, json=dict(code=0, data=DISABLED, message='ok'))
    client = SpringKnowledgePublicationClient(gateway_base_url=base, bearer_token='secret', transport=httpx.MockTransport(handler))
    assert not (await client.snapshot()).enabled
    await client.close()


@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'location': 'http://evil'}),
    httpx.Response(200, json=dict(code=False, data=DISABLED)),
    httpx.Response(200, json=dict(code=1, data=DISABLED)),
    httpx.Response(200, json=dict(code='0', data=DISABLED)),
    httpx.Response(200, json=dict(code=0, data={**DISABLED, 'epoch': '0'})),
    httpx.Response(200, json=dict(code=0, data={**DISABLED, 'enabled': 0})),
    httpx.Response(200, json=dict(code=0, data={**DISABLED, 'embedding_dimension': 0})),
    httpx.Response(200, json=dict(code=0, data={**DISABLED, 'epoch': True})),
    httpx.Response(200, json=dict(code=0, data={**DISABLED, 'unknown': 1})),
    httpx.Response(200, headers={'content-length': '65537'}, content=b'{}'),
    httpx.Response(200, headers={'content-length': '-1'}, content=b'{}'),
    httpx.Response(200, headers={'content-length': '1.0'}, content=b'{}'),
    httpx.Response(200, content=b' ' * 65537),
    httpx.Response(200, headers={'content-encoding': 'gzip'}, content=gzip.compress(b' ' * 65537)),
])
async def test_invalid_boundary_fails_closed(response):
    client = SpringKnowledgePublicationClient(gateway_base_url='http://spring/api', bearer_token='secret', transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(Exception, match='KNOWLEDGE_PUBLICATION_UNAVAILABLE'):
        await client.snapshot()
    await client.close()


async def test_fact_binds_requested_id_and_strict_fingerprint():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/knowledge/publication-protocol.json').read_text(encoding='utf-8'))
    # 使用跨服务固定契约；未知字段及错配 ID 不得被宽松解析吞掉。
    receipt = fixture['candidateReceipt']
    payload = dict(code=0, data=dict(publicationId=receipt['publicationId'],
                                   epoch=receipt['baseEpoch'] + 1, receipt=receipt))
    client = SpringKnowledgePublicationClient(gateway_base_url='http://spring/api', bearer_token='secret',
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    assert (await client.fact(receipt['publicationId'])).receipt.snapshot_hash == receipt['snapshotHash']
    with pytest.raises(Exception):
        await client.fact('task_999999')
    payload['data']['receipt'] = {**receipt, 'snapshotHash': 'bad'}
    with pytest.raises(Exception):
        await client.fact(receipt['publicationId'])
    await client.close()
