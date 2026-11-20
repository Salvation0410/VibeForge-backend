"""真实实验的发送、计费和选择边界使用Fake传输验证。"""
import json
from types import SimpleNamespace

import httpx
import pytest

from ai_service.orchestration import rag_qa_linked_experiment as subject
from ai_service.orchestration.rag_qa_linked_budget import Budget, fingerprint
from ai_service.models.reranker import RerankedChunk


def plan():
    return dict(schedule=[dict(kind='embedding', key=f'e{i}', inputs=28 if i < 7 else 29)
        for i in range(8)] + [dict(kind='bge', key=f'b{i}') for i in range(180)])


def test_budget_exclusive_and_reservation_is_persisted(tmp_path):
    path = tmp_path/'anchor'
    spec = plan()
    budget = Budget(path, spec)
    budget.reserve(spec['schedule'][0])
    assert json.loads(path.read_text().splitlines()[-1])['type'] == 'reserved'
    with pytest.raises(ValueError):
        budget.reserve(spec['schedule'][0])
    budget.close()
    with pytest.raises(FileExistsError):
        Budget(path, spec)


def test_full_budget_exhaustion(tmp_path):
    spec = plan()
    budget = Budget(tmp_path/'anchor', spec)
    try:
        for item in spec['schedule']:
            budget.reserve(item)
        assert budget.count == 188
        with pytest.raises(ValueError):
            budget.reserve(spec['schedule'][-1])
    finally:
        budget.close()


def test_disk_uncertainty_poisoned_budget(tmp_path, monkeypatch):
    from ai_service.orchestration import rag_qa_linked_budget as module
    spec = plan()
    budget = Budget(tmp_path/'anchor', spec)
    def broken(*args):
        raise OSError('synthetic')
    monkeypatch.setattr(module.os, 'fsync', broken)
    try:
        with pytest.raises(OSError):
            budget.reserve(spec['schedule'][0])
        with pytest.raises(ValueError, match='PERSISTENCE_UNKNOWN'):
            budget.reserve(spec['schedule'][0])
    finally:
        budget.close()


def chunk(index, doc):
    return dict(chunk_id=f'{doc}:1:{index}', document_id=doc, document_version=1,
        chunk_index=index, content=f'正文{index}', source_name=doc+'.md', source_locator='正文')


def test_cosine_stable_ties_and_cap1():
    chunks = [chunk(0,'a'), chunk(1,'a'), chunk(2,'b'), chunk(3,'c')]
    retrieved = subject.dense_top8(chunks, [[1,0]]*4, [1,0])
    assert [c.chunk_id for c in retrieved] == [c['chunk_id'] for c in chunks]
    ranked = [RerankedChunk(c, 10-i) for i,c in enumerate(retrieved)]
    stages, drops = subject.select_contexts('如何开始', retrieved, ranked)
    assert [c['documentId'] for c in stages['finalContext']] == ['a','b','c']
    assert drops['a:1:1'] == 'duplicate_document'


@pytest.mark.parametrize('vectors,query', [([[0,0]], [1,0]), ([[1,0]], [0,0]),
    ([[float('nan'),1]], [1,0]), ([[1,0]], [float('inf'),0]), ([[1,0]], [1])])
def test_bad_vectors_rejected(vectors, query):
    with pytest.raises(ValueError):
        subject.dense_top8([chunk(0,'a')], vectors, query)


class MemoryBudget:
    def __init__(self):
        self.reserved = []
    def reserve(self, item):
        self.reserved.append(item)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 500, 307])
async def test_http_pre_reserved_and_no_redirect_or_retry(status):
    budget = MemoryBudget()
    calls = []
    texts = ['说明']
    reservation = dict(kind='embedding', key='before:0', inputs=1, inputSha256=fingerprint(texts))
    def transport(request):
        assert budget.reserved == [reservation]
        calls.append(request)
        assert json.loads(request.content)['input'] == texts
        return httpx.Response(status, headers={'location':'https://other.invalid'},
            json=dict(data=[dict(index=0,embedding=[0.1]*3072)]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=False) as client:
        cfg = SimpleNamespace(closeai_base_url='https://provider.invalid/v1', closeai_api_key='fake')
        if status == 200:
            vectors, _ = await subject.embedding_batch(client,cfg,texts,budget,reservation)
            assert len(vectors[0]) == 3072
        else:
            with pytest.raises(ValueError):
                await subject.embedding_batch(client,cfg,texts,budget,reservation)
    assert len(calls) == len(budget.reserved) == 1


@pytest.mark.asyncio
async def test_changed_embedding_input_never_sent():
    budget = MemoryBudget()
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        await subject.embedding_batch(None, None, ['changed'], budget,
            dict(kind='embedding', inputs=1, inputSha256=fingerprint(['original'])))
    assert budget.reserved == []


@pytest.mark.asyncio
async def test_invalid_embedding_indices_consumes_reservation():
    budget = MemoryBudget()
    def transport(request):
        return httpx.Response(200, json=dict(data=[dict(index=1,embedding=[0.1]*3072)]))
    texts = ['说明']
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match='COUNT'):
            await subject.embedding_batch(client,
                SimpleNamespace(closeai_base_url='https://provider.invalid',closeai_api_key='fake'),
                texts,budget,dict(kind='embedding',inputs=1,inputSha256=fingerprint(texts)))
    assert len(budget.reserved) == 1
