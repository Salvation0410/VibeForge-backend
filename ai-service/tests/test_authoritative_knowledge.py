"""真实发布适配器与 FakeMilvus 的请求级边界，不调用供应商。"""
import asyncio

import httpx
import pytest

from ai_service.api.knowledge_publication_schemas import ReconcileRequest
from ai_service.infrastructure.authoritative_knowledge import AuthoritativeKnowledgeStore
from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
from ai_service.models.base import CustomerServiceModelAnswer
from ai_service.orchestration.customer_service_rag import CustomerServiceRagService, SYSTEM_FALLBACK_TEXT
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from ai_service.orchestration.knowledge_publication_boundary import reconcile_alias, validate_candidate_request
from test_knowledge_publication import env, seed, snapshot, request, rebuild_request, value
from test_customer_service_rag import FakeEmbeddings, FakeAnswerModel, FakeReranker, FakeStore
from test_spring_knowledge_publication import DISABLED


class Control:
    require_legacy = SpringKnowledgePublicationClient.require_legacy
    def __init__(self, current):
        self.current = current
        self.calls = 0
        self.error = False

    async def snapshot(self):
        self.calls += 1
        if self.error:
            from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE')
        return self.current.model_copy(deep=True)

    async def close(self):
        pass


async def setup(env):
    settings, _, knowledge, publications = env
    settings.rag_publication_enabled = True
    receipt = await seed(env)
    control = Control(snapshot(receipt))
    legacy = FakeStore()
    wrapper = AuthoritativeKnowledgeStore(settings, legacy, publications, control)
    embeddings = FakeEmbeddings()
    model = FakeAnswerModel(CustomerServiceModelAnswer(True, '有据回答', ('doc-1:1:0',)))
    service = CustomerServiceRagService(settings, embeddings, wrapper, FakeReranker(), model)
    return receipt, control, wrapper, embeddings, model, service


@pytest.mark.parametrize('change', ['before', 'after', 'db_before', 'db_after', 'documents', 'head'])
async def test_epoch_or_control_failure_discards_answer_without_retry(env, change):
    _, control, wrapper, embeddings, model, service = await setup(env)
    original = wrapper.search
    async def search(*args):
        result = await original(*args)
        if change == 'before':
            control.current.epoch += 1
        if change == 'db_before':
            control.error = True
        if change == 'documents':
            control.current.documents = []
        if change == 'head':
            control.current.collection_name = 'replacement'
        return result
    wrapper.search = search
    original_model = model.answer_customer_service
    async def answer(*args):
        result = await original_model(*args)
        if change == 'after':
            control.current.epoch += 1
        if change == 'db_after':
            control.error = True
        return result
    model.answer_customer_service = answer
    result = await service.answer('如何操作')
    assert not result.answered and result.sources == () and result.answer == SYSTEM_FALLBACK_TEXT
    assert len(model.calls) == (1 if change in {'after', 'db_after'} else 0)
    assert wrapper._snapshot.get() is None and not wrapper.legacy.calls


async def test_empty_whitelist_validates_head_but_avoids_all_paid_calls(env):
    _, control, wrapper, embeddings, model, service = await setup(env)
    control.current.documents = []
    result = await service.answer('如何操作')
    assert not result.answered and not embeddings.questions and not model.calls
    assert control.calls == 2 and wrapper._snapshot.get() is None


@pytest.mark.parametrize('bad', ['completion', 'model', 'head', 'db'])
async def test_invalid_publication_never_embeds_or_answers(env, bad):
    receipt, control, wrapper, embeddings, model, service = await setup(env)
    if bad == 'completion':
        env[1].collections[receipt.candidate_collection].pop()
    elif bad == 'model':
        control.current.embedding_model_version = 'wrong'
    elif bad == 'head':
        control.current.collection_name = 'missing'
    else:
        control.error = True
    result = await service.answer('如何操作')
    assert not result.answered and not embeddings.questions and not model.calls
    assert not wrapper.legacy.calls and wrapper._snapshot.get() is None


async def test_scope_concurrent_isolation_and_repeated_cancel_cleanup(env):
    _, control, wrapper, _, _, _ = await setup(env)
    entered = asyncio.Event()
    release = asyncio.Event()
    async def first():
        async with wrapper.read_scope():
            original_epoch = wrapper._snapshot.get().epoch
            entered.set()
            await release.wait()
            assert wrapper._snapshot.get().epoch == original_epoch
    task = asyncio.create_task(first())
    await entered.wait()
    control.current.epoch += 1
    async with wrapper.read_scope():
        assert wrapper._snapshot.get().epoch == control.current.epoch
    assert wrapper._snapshot.get() is None
    release.set()
    await task
    # 成功退出和后续取消均不得污染发起任务或下一次请求。
    release.clear()
    entered.clear()
    task = asyncio.create_task(first())
    await entered.wait()
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wrapper._snapshot.get() is None
    async with wrapper.read_scope():
        await wrapper.assert_current()


async def test_legacy_inflight_bootstrap_never_returns_old_answer(env):
    settings, _, _, publications = env
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot
    from test_customer_service_rag import chunk
    control = Control(ReadSnapshot.model_validate(DISABLED))
    settings.rag_publication_enabled = False
    legacy = FakeStore([chunk('c1', 'doc')])
    wrapper = AuthoritativeKnowledgeStore(settings, legacy, publications, control)
    model = FakeAnswerModel()
    original = model.answer_customer_service
    async def answer(*args):
        result = await original(*args)
        receipt = await seed(env)
        control.current = snapshot(receipt)
        return result
    model.answer_customer_service = answer
    service = CustomerServiceRagService(settings, FakeEmbeddings(), wrapper, FakeReranker(), model)
    assert not (await service.answer('question')).answered
    assert len(model.calls) == 1
    assert not (await service.answer('question')).answered
    assert len(model.calls) == 1


@pytest.mark.parametrize('failure', ['none', 'pre_changed', 'post_changed', 'write', 'post_db'])
async def test_alias_reconciliation_never_restores_old_mirror(env, failure):
    receipt, control, _, _, _, _ = await setup(env)
    settings, client, knowledge, publications = env
    client.aliases[knowledge._alias] = 'old_mirror'
    body = ReconcileRequest(collection_alias=knowledge._alias, collection_name=receipt.candidate_collection,
                            publication_id=receipt.publication_id, epoch=control.current.epoch)
    original = knowledge._call
    async def call(method, *args, **kwargs):
        if method == 'alter_alias':
            if failure == 'write':
                raise RuntimeError('RPC failed')
            result = await original(method, *args, **kwargs)
            if failure == 'post_changed':
                control.current.epoch += 1
            if failure == 'post_db':
                control.error = True
            return result
        return await original(method, *args, **kwargs)
    knowledge._call = call
    if failure == 'pre_changed':
        control.current.epoch += 1
    if failure in {'write', 'post_db'}:
        with pytest.raises(Exception):
            await reconcile_alias(body, control, publications, settings)
    else:
        result = await reconcile_alias(body, control, publications, settings)
        assert result.reconciled is (failure == 'none')
    expected = 'old_mirror' if failure in {'pre_changed', 'write'} else receipt.candidate_collection
    assert client.aliases[knowledge._alias] == expected
    # 迟到镜像写不会触及权威集合；定期对账可在写入静止后重新收敛。
    control.error = False
    control.current = snapshot(receipt)
    knowledge._call = original
    client.aliases[knowledge._alias] = 'late_old_mirror'
    assert (await reconcile_alias(body, control, publications, settings)).reconciled
    assert client.aliases[knowledge._alias] == receipt.candidate_collection


async def test_http_candidate_raw_receipt_and_validation_auth(env, app_factory, auth_headers):
    receipt, control, _, _, _, _ = await setup(env)
    settings, _, knowledge, _ = env
    settings.customer_service_rag_enabled = True
    preparer = KnowledgePublicationPreparer(settings, None, None, knowledge)
    control.current.documents = []
    app = app_factory(knowledge_publication_client=control, knowledge_publication_preparer=preparer)
    transport = httpx.ASGITransport(app=app)
    body = request(receipt).model_dump(by_alias=True, mode='json')
    async with httpx.AsyncClient(transport=transport, base_url='http://python') as client:
        assert (await client.post('/internal/v1/customer-service/knowledge:prepare-publication', json=body)).status_code == 401
        result = await client.post('/internal/v1/customer-service/knowledge:prepare-publication', json=body, headers=auth_headers)
        assert result.status_code == 200, result.text
        from ai_service.api.knowledge_publication_schemas import CandidateReceipt
        parsed = CandidateReceipt.model_validate(result.json())
        assert parsed.publication_id == 'task_1' and parsed.documents == []
        assert 'status' not in result.json() and 'operation' not in result.json()
        body['context']['baseEpoch'] += 1
        failed = await client.post('/internal/v1/customer-service/knowledge:prepare-publication', json=body, headers=auth_headers)
        assert failed.status_code == 503
        assert 'proof' not in failed.text and 'valid-proof' not in failed.text


async def test_legacy_endpoint_rechecks_durable_mode_on_live_instance(env, app_factory, auth_headers):
    receipt, control, _, _, _, _ = await setup(env)
    settings = env[0]
    settings.customer_service_rag_enabled = True
    settings.rag_publication_enabled = False
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot
    from test_knowledge_rebuild import FakeRebuildService, rebuild_payload
    control.current = ReadSnapshot.model_validate(DISABLED)
    service = FakeRebuildService()
    app = app_factory(knowledge_etl_service=service, knowledge_publication_client=control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://python') as client:
        body = rebuild_payload(alias=settings.milvus_collection_alias)
        first = await client.post('/internal/v1/customer-service/knowledge:rebuild', json=body, headers=auth_headers)
        assert first.status_code == 200
        control.current = snapshot(receipt)
        second = await client.post('/internal/v1/customer-service/knowledge:rebuild', json=body, headers=auth_headers)
        assert second.status_code == 503 and len(service.calls) == 1
        control.error = True
        assert (await client.post('/internal/v1/customer-service/knowledge:rebuild', json=body, headers=auth_headers)).status_code == 503
        assert len(service.calls) == 1


@pytest.mark.parametrize('local_enabled', [True, False])
async def test_http_injected_legacy_answer_cannot_bypass_publication_guard(env, app_factory, auth_headers, local_enabled):
    receipt, control, _, _, _, _ = await setup(env)
    settings = env[0]
    settings.customer_service_rag_enabled = True
    settings.rag_publication_enabled = local_enabled
    old_model = FakeAnswerModel()
    from test_customer_service_rag import chunk
    old_service = CustomerServiceRagService(settings, FakeEmbeddings(), FakeStore([chunk('c1', 'doc')]), FakeReranker(), old_model)
    app = app_factory(customer_service_rag_service=old_service, knowledge_publication_client=control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://python') as client:
        if not local_enabled:
            from ai_service.api.knowledge_publication_schemas import ReadSnapshot
            control.current = ReadSnapshot.model_validate(DISABLED)
            first = await client.post('/internal/v1/customer-service/answers', json={'requestId': 'old-mode', 'question': '如何操作'}, headers=auth_headers)
            assert first.status_code == 503
            assert len(old_model.calls) == 0
            control.current = snapshot(receipt)
        before = len(old_model.calls)
        result = await client.post('/internal/v1/customer-service/answers', json={'requestId': 'publication-mode', 'question': '如何操作'}, headers=auth_headers)
        assert result.status_code == 503 and len(old_model.calls) == before


async def test_official_legacy_retrieval_bootstrap_stops_before_model(env):
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot
    from test_customer_service_rag import chunk
    settings, _, _, publications = env
    settings.rag_publication_enabled = False
    receipt = await seed(env)
    control = Control(ReadSnapshot.model_validate(DISABLED))
    class BootstrappingStore(FakeStore):
        async def search(self, *args):
            result = await super().search(*args)
            control.current = snapshot(receipt)
            return result
    legacy = BootstrappingStore([chunk('c1', 'doc')])
    wrapper = AuthoritativeKnowledgeStore(settings, legacy, publications, control)
    model = FakeAnswerModel()
    service = CustomerServiceRagService(settings, FakeEmbeddings(), wrapper, FakeReranker(), model)
    result = await service.answer('question')
    assert not result.answered and not result.sources
    assert len(legacy.calls) == 1 and not model.calls


async def test_http_rag_uses_real_control_client_and_concrete_collection(env, app_factory, auth_headers):
    receipt, control, wrapper, embeddings, model, service = await setup(env)
    settings, milvus, knowledge, _ = env
    settings.customer_service_rag_enabled = True
    urls = []
    def spring(request):
        urls.append(str(request.url))
        return httpx.Response(200, json={'code': 0, 'data': control.current.model_dump(by_alias=True), 'message': 'ok'})
    client = SpringKnowledgePublicationClient(gateway_base_url='http://spring/api/internal/ai-tools',
        bearer_token='secret', transport=httpx.MockTransport(spring))
    wrapper.client = client
    milvus.aliases[knowledge._alias] = 'stale_mirror'
    app = app_factory(customer_service_rag_service=service, knowledge_publication_client=client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://python') as http:
        result = await http.post('/internal/v1/customer-service/answers', headers=auth_headers,
                                 json={'requestId': 'real-control', 'question': 'question'})
        assert result.status_code == 200 and result.json()['answered']
        assert len(model.calls) == 1 and len(embeddings.questions) == 1 and not wrapper.legacy.calls
        assert len(urls) == 4
        assert all(url.endswith('/api/internal/customer-service/knowledge-publications/snapshot') for url in urls)
        control.error = True
        def broken(_):
            return httpx.Response(503)
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(broken))
        failed = await http.post('/internal/v1/customer-service/answers', headers=auth_headers,
                                 json={'requestId': 'db-down', 'question': 'question'})
        assert failed.status_code == 503 and len(model.calls) == 1
    await client.close()


async def test_http_reconcile_exact_raw_contract(env, app_factory, auth_headers):
    receipt, control, _, _, _, _ = await setup(env)
    settings, milvus, knowledge, _ = env
    settings.customer_service_rag_enabled = True
    preparer = KnowledgePublicationPreparer(settings, None, None, knowledge)
    app = app_factory(knowledge_publication_client=control, knowledge_publication_preparer=preparer)
    body = dict(collectionAlias=knowledge._alias, collectionName=receipt.candidate_collection,
                publicationId=receipt.publication_id, epoch=control.current.epoch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://python') as client:
        assert (await client.post('/internal/v1/customer-service/knowledge:reconcile-alias', json=body)).status_code == 401
        result = await client.post('/internal/v1/customer-service/knowledge:reconcile-alias', json=body, headers=auth_headers)
        assert result.json() == {key: body[key] for key in ('publicationId', 'epoch', 'collectionName')} | {'reconciled': True}
        assert type(result.json()['epoch']) is int


async def test_bootstrap_control_keeps_preparer_and_shares_adapter_semaphore(env, app_factory, tmp_path):
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot
    from test_knowledge_publication import Downloader, Embeddings
    settings, _, knowledge, _ = env
    settings.customer_service_rag_enabled = True
    settings.rag_publication_enabled = True
    control = Control(ReadSnapshot.model_validate(DISABLED))
    path = tmp_path / 'manual.txt'
    path.write_text('私有知识正文', encoding='utf-8')
    app = app_factory(knowledge_store=knowledge, embedding_provider=Embeddings(),
                      knowledge_downloader=Downloader(path), knowledge_publication_client=control,
                      mutation_coordinator=knowledge._mutation_coordinator)
    async with app.router.lifespan_context(app):
        preparer = app.state.knowledge_publication_preparer
        assert preparer is not None
        assert preparer.semaphore is app.state.knowledge_etl_service._semaphore
        assert app.state.customer_service_rag_service._store.publications is preparer.publications
        result = await app.state.customer_service_rag_service.answer('question')
        assert not result.answered
        summary = await app.state.customer_service_health_provider.probe()
        assert summary['etl'] is False


@pytest.mark.parametrize('bad', ['epoch', 'source', 'whitelist', 'delete_still_enabled', 'bootstrap_after_head'])
async def test_candidate_request_exact_authoritative_binding(env, bad):
    receipt, control, _, _, _, _ = await setup(env)
    settings = env[0]
    candidate = request(receipt)
    control.current.documents = []
    if bad == 'epoch':
        candidate.context.base_epoch += 1
    elif bad == 'source':
        candidate.context.source_collection = 'other_source'
    elif bad == 'whitelist':
        from ai_service.infrastructure.knowledge_publication_store import document_identity
        candidate.context.documents = [document_identity(value(settings, 'doc-2'))]
    elif bad == 'delete_still_enabled':
        control.current = snapshot(receipt)
    else:
        candidate = rebuild_request(settings, receipt, value(settings), bootstrap=True)
        candidate.context.base_epoch = control.current.epoch
    with pytest.raises(Exception, match='KNOWLEDGE_PUBLICATION_INVALID'):
        await validate_candidate_request(candidate, control, settings)


async def test_model_cancellation_clears_scope_without_fallback_or_retry(env):
    _, _, wrapper, _, model, service = await setup(env)
    entered = asyncio.Event()
    final_snapshot = []
    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()
    model.answer_customer_service = blocked
    async def run():
        try:
            await service.answer('question')
        finally:
            final_snapshot.append(wrapper._snapshot.get())
    task = asyncio.create_task(run())
    await entered.wait()
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert final_snapshot == [None] and wrapper._snapshot.get() is None


async def test_legacy_guard_cannot_wrap_actual_official_service(env):
    from ai_service.infrastructure.authoritative_knowledge import LegacyAnswerServiceGuard
    _, control, _, _, _, service = await setup(env)
    with pytest.raises(Exception, match='KNOWLEDGE_PUBLICATION_UNAVAILABLE'):
        LegacyAnswerServiceGuard(service, env[0], control)


@pytest.mark.parametrize('operation', ['INDEX', 'DELETE'])
async def test_all_legacy_mutation_entries_observe_live_mode(env, app_factory, auth_headers, operation):
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot
    from test_api import FakeKnowledgeEtlService, knowledge_etl_payload, knowledge_lease
    receipt, control, _, _, _, _ = await setup(env)
    settings = env[0]
    settings.customer_service_rag_enabled = True
    settings.rag_publication_enabled = False
    service = FakeKnowledgeEtlService()
    app = app_factory(knowledge_etl_service=service, knowledge_publication_client=control)
    control.current = ReadSnapshot.model_validate(DISABLED)
    body = knowledge_etl_payload() if operation == 'INDEX' else {
        'operation': 'DELETE', 'documentId': 'doc-1', 'documentVersion': 2,
        'lease': knowledge_lease('op-2', operation='DELETE')}
    path = '/internal/v1/customer-service/knowledge:' + ('etl' if operation == 'INDEX' else 'delete')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://python') as client:
        assert (await client.post(path, json=body, headers=auth_headers)).status_code == 200
        control.current = snapshot(receipt)
        assert (await client.post(path, json=body, headers=auth_headers)).status_code == 503
        control.error = True
        assert (await client.post(path, json=body, headers=auth_headers)).status_code == 503
    assert len(service.index_calls if operation == 'INDEX' else service.delete_calls) == 1
