"""新设施的严格批准与真实 SDK 序列化边界。"""
import pytest
import asyncio
from copy import deepcopy
import json
import httpx
from test_rag_cap_contracts import frozen_source, write
from test_rag_cap_experiment import prepared as cap_prepared
from ai_service.orchestration import rag_cap_contracts as cap
from ai_service.orchestration import rag_json_answer_contracts as contracts
from ai_service.orchestration import rag_json_answer_budget as budgets
from ai_service.orchestration import rag_json_answer_experiment as runner


@pytest.mark.parametrize('limit', [True, 180.0, '180', 179, 181])
def test_approval_exact_integer(limit):
    with pytest.raises(ValueError, match='APPROVAL_INVALID'):
        runner.validate_approval({'maxChatCalls': limit}, {})


async def prepared(frozen_source, tmp_path, monkeypatch):
    cfg, old_plan = await cap_prepared(frozen_source, tmp_path, monkeypatch)
    base = cap.load_frozen_inputs(cfg)
    common = [{'id': e['id'], 'question': e['question'].strip(),
               'contexts': old_plan['contexts'][e['id'] + ':cap1'],
               'actualMessages': contracts.messages(e['question'].strip(), old_plan['contexts'][e['id'] + ':cap1'])}
              for e in base.rubric['entries']]
    historical = {'schemaVersion': 'rag-cap-experiment/v1', 'studyId': cap.STUDY_ID,
                  'sourceReportSha256': base.source_report_sha256, 'sourceFilesSha256': base.source_files,
                  'rubricSha256': contracts.fingerprint(base.rubric), 'mappingSha256': contracts.fingerprint(base.mapping),
                  'config': cap.settings_manifest(cfg), 'results': []}
    for c in common:
        for arm in cap.ARMS:
            historical['results'].append({'id': c['id'], 'arm': arm, 'contexts': c['contexts'],
                                         'actualMessages': c['actualMessages']})
    path = tmp_path / 'historical.json'; write(path, historical)
    monkeypatch.setattr(contracts, 'SOURCE', path)
    monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', contracts.file_hash(path))
    monkeypatch.setattr(contracts, 'INPUT_SHA256', contracts.fingerprint(common))
    monkeypatch.setattr(runner, 'TMP', tmp_path)
    monkeypatch.setattr(runner, 'settings', lambda: cfg)
    monkeypatch.setattr(runner, 'AUTHORIZATION', tmp_path / 'new-anchor.jsonl')
    monkeypatch.setattr(budgets, 'AUTHORIZATION', tmp_path / 'new-anchor.jsonl')
    plan = await runner.prepare(tmp_path / 'json-prepared')
    return cfg, plan, contracts.load_frozen_inputs(cfg)


@pytest.mark.asyncio
async def test_prepare_pure(frozen_source, tmp_path, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('离线准备构造 SDK')
    monkeypatch.setattr(runner, '_runtime_model', forbidden)
    cfg, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    assert cfg.rag_answer_response_format == 'text'
    assert len(plan['schedule']) == len(plan['messages']) == len(plan['contexts']) == 180
    for key in plan['schedule'][::2]:
        id_ = key.rsplit(':', 1)[0]
        assert plan['messages'][id_ + ':text'] == plan['messages'][id_ + ':json_object']
    request = contracts.read_json(tmp_path / 'json-prepared/authorization-request.json')
    assert request['decision'] == 'pending'
    assert not runner.AUTHORIZATION.exists() and not (tmp_path / 'json-prepared/live').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['sourceReportSha256', 'inputSha256', 'sourceFilesSha256', 'sourceSnapshot',
    'rubricSha256', 'mappingSha256', 'config', 'sdkVersions', 'implementationFiles', 'evaluationBindings',
    'schedule', 'contexts', 'messages', 'liveOutput', 'maxChatCalls', 'studyId', 'schemaVersion'])
async def test_plan_drift_before_resources(frozen_source, tmp_path, monkeypatch, field):
    cfg, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    damaged = deepcopy(plan)
    damaged[field] = 'changed'
    with pytest.raises((ValueError, TypeError)):
        await runner.validate_plan(damaged, cfg)
    assert not runner.AUTHORIZATION.exists()


def approval(plan):
    return {'schemaVersion': 'rag-json-answer-authorization/v1', 'studyId': contracts.STUDY_ID,
            'decision': 'approved', 'planSha256': contracts.fingerprint(plan), 'maxChatCalls': 180,
            'liveOutput': plan['liveOutput']}


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('decision', 'pending'), ('maxChatCalls', True), ('maxChatCalls', 180.0),
    ('maxChatCalls', 179), ('studyId', cap.STUDY_ID), ('planSha256', '0'*64), ('liveOutput', 'other'), ('extra', 1)])
async def test_approval_drift(frozen_source, tmp_path, monkeypatch, field, value):
    _, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    approved = approval(plan); approved[field] = value
    with pytest.raises(ValueError, match='APPROVAL_INVALID'):
        runner.validate_approval(approved, plan)


def response(content='{"answered":false,"answer":"","citedChunkIds":[]}', *, finish='stop', model='observed-model'):
    data = {'id': 'chatcmpl-mock', 'object': 'chat.completion', 'created': 1,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
    if model is not None:
        data['model'] = model
    return httpx.Response(200, json=data)


@pytest.mark.asyncio
async def test_real_sdk_180_mock_transports(frozen_source, tmp_path, monkeypatch):
    cfg, plan, frozen = await prepared(frozen_source, tmp_path, monkeypatch)
    cfg = cfg.model_copy(update={'model_api_key': 'isolated-test-key', 'model_base_url': 'http://mock.invalid/v1'})
    budget = budgets.JsonAnswerAuthorizationBudget(output=plan['liveOutput'], plan_sha256=contracts.fingerprint(plan), schedule=plan['schedule'])
    sent = []
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert budget.counts['chat'] == len(sent)
        if len(sent) % 2:
            context = json.loads(body['messages'][1]['content'])['contexts'][0]
            return response(json.dumps({'answered': True, 'answer': '合成有据答案', 'citedChunkIds': [context['chunkId']]}))
        return response()
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    assert model.adapters['text']._client is model.adapters['json_object']._client
    report = runner.new_report(frozen, plan, approval(plan))
    saves = []
    try:
        await runner.execute_answers(report, frozen, cfg, model, lambda: saves.append(deepcopy(report)))
        assert report['status'] == 'complete'
        assert len(sent) == budget.counts['chat'] == 180
        assert all(r['status'] == 'ok' and r['reserved'] and r['attemptedSend'] for r in report['results'])
        assert sum('response_format' in b for b in sent) == 90
        assert all(r['responseMetadata']['model'] == 'observed-model' for r in report['results'])
        assert sum(r['answered'] is True for r in report['results']) == 90
        assert len(saves[0]['results']) == 180 and all(r['status'] == 'not_run' for r in saves[0]['results'])
    finally:
        budget.close()
        assert await model.close() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['400', 'bad-json', 'unknown-citation', 'duplicate-citation', 'length', 'content_filter', 'oversize', 'invalid-http', 'missing-model'])
async def test_sdk_failure_observation_and_stop(frozen_source, tmp_path, monkeypatch, kind):
    cfg, plan, frozen = await prepared(frozen_source, tmp_path, monkeypatch)
    cfg = cfg.model_copy(update={'model_api_key': 'test', 'model_base_url': 'http://mock.invalid/v1'})
    budget = budgets.JsonAnswerAuthorizationBudget(output=plan['liveOutput'], plan_sha256='a'*64, schedule=plan['schedule'])
    sent = []
    def handler(request):
        sent.append(1)
        if kind == '400': return httpx.Response(400, json={'error': {'message': 'secret-error', 'type': 'bad'}})
        if kind == 'bad-json': return response('{bad')
        if kind == 'unknown-citation': return response('{"answered":true,"answer":"a","citedChunkIds":["unknown"]}')
        if kind == 'duplicate-citation':
            c = plan['contexts'][plan['schedule'][0]][0]['chunkId']
            return response(json.dumps({'answered': True, 'answer': 'a', 'citedChunkIds': [c,c]}))
        if kind in ('length', 'content_filter'): return response(finish=kind)
        if kind == 'oversize': return httpx.Response(200, content=b'x'*(contracts.MAX_RESPONSE_BYTES+1))
        if kind == 'invalid-http': return httpx.Response(200, content=b'not JSON')
        return response(model=None)
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    report = runner.new_report(frozen, plan, approval(plan))
    try:
        await runner.execute_answers(report, frozen, cfg, model, lambda: None)
        first = report['results'][0]
        assert first['reserved'] and first['attemptedSend'] and first['httpStatus'] == (400 if kind == '400' else 200)
        if kind in ('bad-json', 'unknown-citation', 'duplicate-citation'):
            assert first['errorCode'] == 'MODEL_PARSE_FAILED' and len(sent) == 3
        elif kind == 'missing-model':
            assert first['responseMetadata'].get('model') is None
        else:
            assert len(sent) <= 3 and report['status'] == 'stopped'
        if kind in ('length', 'content_filter'):
            assert first['rawResponse'] and first['responseMetadata']['finish_reason'] == kind
        if kind == '400': assert first['rawResponse'] is None and len(sent) == 1
        if kind == 'oversize': assert first['rawResponse'] is None and first['observedResponseBytes'] > contracts.MAX_RESPONSE_BYTES
    finally:
        budget.close(); await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['transport', 'row-save', 'save-failure', 'fsync'])
async def test_cancel_and_write_failures_stop_without_extra_send(frozen_source, tmp_path, monkeypatch, kind):
    import os
    cfg, plan, frozen = await prepared(frozen_source, tmp_path, monkeypatch)
    cfg = cfg.model_copy(update={'model_api_key': 'test', 'model_base_url': 'http://mock.invalid/v1'})
    budget = budgets.JsonAnswerAuthorizationBudget(output=plan['liveOutput'], plan_sha256='a'*64, schedule=plan['schedule'])
    sent = []
    def handler(request):
        sent.append(1)
        if kind == 'transport': raise asyncio.CancelledError()
        return response()
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    report = runner.new_report(frozen, plan, approval(plan))
    saves = []
    def save():
        saves.append(1)
        if len(saves) == 2 and kind in ('row-save', 'save-failure'):
            if kind == 'row-save': raise asyncio.CancelledError()
            raise OSError('隔离写回失败')
    if kind == 'fsync': monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError()))
    try:
        if kind == 'fsync':
            await runner.execute_answers(report, frozen, cfg, model, save)
            assert report['status'] == 'stopped' and not sent and budget.counts['chat'] == 0
        else:
            with pytest.raises(asyncio.CancelledError if kind != 'save-failure' else OSError):
                await runner.execute_answers(report, frozen, cfg, model, save)
            assert len(sent) == 1 and budget.counts['chat'] == 1
            if kind != 'save-failure':
                assert report['results'][0]['status'] == 'error' and report['results'][0]['errorCode'] == 'CANCELLED'
                assert report['status'] == 'cancelled'
        assert all(r['status'] == 'not_run' for r in report['results'][1:])
    finally:
        budget.close(); await model.close()


@pytest.mark.asyncio
async def test_constructor_failure_closes_http_client_without_base_constructor(frozen_source, tmp_path, monkeypatch):
    import langchain_openai
    from ai_service.models.openai_compatible import OpenAICompatibleModel
    cfg, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    monkeypatch.setattr(OpenAICompatibleModel, '__init__', lambda *a: pytest.fail('调用了基类构造器'))
    closed = []
    class Transport(httpx.AsyncBaseTransport):
        async def aclose(self): closed.append(1)
    def failed(**kwargs): raise RuntimeError('构造错误')
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', failed)
    with pytest.raises(RuntimeError):
        await runner._runtime_model(cfg, None, plan, transport=Transport())
    assert closed == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize('closer', ['budget', 'model', 'budget-save-failure'])
async def test_live_cleanup_cancel_preserves_and_closes_all(frozen_source, tmp_path, monkeypatch, closer):
    _, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    approval_path = tmp_path / 'isolated-approved.json'; write(approval_path, approval(plan))
    closed = []
    class Budget:
        counts = {'chat': 0, 'embeddingRequests': 0, 'queryInputs': 0}
        def __init__(self, **kwargs): pass
        def close(self):
            closed.append('budget')
            if closer.startswith('budget'): raise asyncio.CancelledError()
    class Model:
        async def close(self):
            closed.append('model')
            if closer == 'model': raise asyncio.CancelledError()
            return []
    async def runtime(*a): return Model()
    async def execute(report, *a): report['status'] = 'complete'
    monkeypatch.setattr(runner, 'JsonAnswerAuthorizationBudget', Budget)
    monkeypatch.setattr(runner, '_runtime_model', runtime)
    monkeypatch.setattr(runner, 'execute_answers', execute)
    if closer == 'budget-save-failure':
        original_write = runner.write_json
        saves = []
        def failing_final_save(path, value):
            if path.name == 'report.json':
                saves.append(1)
                if len(saves) == 2: raise OSError('收尾保存失败')
            return original_write(path, value)
        monkeypatch.setattr(runner, 'write_json', failing_final_save)
    with pytest.raises(asyncio.CancelledError):
        await runner.run_live(tmp_path / 'json-prepared/plan.json', approval_path)
    assert closed == ['budget', 'model']
    if closer != 'budget-save-failure':
        saved = contracts.read_json(tmp_path / 'json-prepared/live/report.json')
        assert saved['status'] == 'cancelled' and saved['stopReason'] == 'CANCELLED'


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['length', 'content_filter'])
async def test_json_sdk_parse_early_error_keeps_transport_observation(frozen_source, tmp_path, monkeypatch, finish):
    from openai import LengthFinishReasonError, ContentFilterFinishReasonError
    from ai_service.models.base import CustomerServiceContext
    cfg, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    cfg = cfg.model_copy(update={'model_api_key': 'test', 'model_base_url': 'http://mock.invalid/v1'})
    budget = budgets.JsonAnswerAuthorizationBudget(output=plan['liveOutput'], plan_sha256='a'*64, schedule=plan['schedule'])
    def handler(request):
        body = json.loads(request.content)
        return response(finish=finish) if 'response_format' in body else response()
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    try:
        for key in plan['schedule'][:2]:
            id_ = key.rsplit(':', 1)[0]
            entry = next(e for e in plan['messages'] if e == key)
            question = json.loads(plan['messages'][entry][1]['content'])['question']
            contexts = [CustomerServiceContext(c['chunkId'], c['content']) for c in plan['contexts'][key]]
            model.begin(key)
            if key.endswith(':text'):
                await model.answer_customer_service(question, contexts)
            else:
                with pytest.raises(LengthFinishReasonError if finish == 'length' else ContentFilterFinishReasonError):
                    await model.answer_customer_service(question, contexts)
                assert model.observation['reserved'] and model.observation['attemptedSend']
                assert model.observation['rawResponse'] and model.observation['usage']['input_tokens'] == 10
                assert model.observation['responseMetadata']['finish_reason'] == finish
        assert budget.counts['chat'] == 2
    finally:
        budget.close(); await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('createdAtUtc', 1), ('createdAtUtc', 'bad'),
    ('createdAtUtc', '2026-11-19T10:00:00+08:00'), ('createdAtUtc', '2026-11-19T10:00:00'),
    ('preparationCode', {'codeSha': 'bad', 'dirty': False}),
    ('preparationCode', {'codeSha': 'a'*40, 'dirty': 1}),
    ('preparationCode', {'codeSha': 'a'*40, 'dirty': False, 'extra': 1})])
async def test_plan_diagnostic_types_strict(frozen_source, tmp_path, monkeypatch, field, value):
    cfg, plan, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    plan[field] = value
    with pytest.raises(ValueError, match='PLAN_INVALID'):
        await runner.validate_plan(plan, cfg)
    assert not runner.AUTHORIZATION.exists()


def minimal_runtime_plan():
    contexts = [{'chunkId': 'c1', 'content': '合成片段'}]
    return {'contexts': {'q:text': contexts, 'q:json_object': contexts},
            'messages': {key: contracts.messages('合成问题', contexts) for key in ('q:text', 'q:json_object')}}


class CountingBudget:
    def __init__(self): self.keys = []
    def reserve_chat(self, key): self.keys.append(key)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['model', 'temperature', 'max_tokens', 'extra', 'messages', 'format'])
async def test_actual_sdk_http_body_drift_before_reserve(damage):
    from ai_service.config import Settings
    from ai_service.models.base import CustomerServiceContext
    cfg = Settings(_env_file=None, model_api_key='test', model_base_url='http://mock.invalid/v1')
    plan, budget, sent = minimal_runtime_plan(), CountingBudget(), []
    def handler(request): sent.append(1); return response()
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    try:
        if damage == 'model': model._client.model_name = 'wrong'
        if damage == 'temperature': model._client.temperature = .7
        if damage == 'max_tokens': model._client.max_tokens = 3
        if damage == 'extra': model._client.model_kwargs = {'seed': 3}
        if damage == 'format': model._client.model_kwargs = {'response_format': {'type': 'json_object'}}
        model.begin('q:text')
        if damage == 'messages': model.boundary.expected_messages['q:text'][1]['content'] += '漂移'
        with pytest.raises(Exception):
            await model.answer_customer_service('合成问题', [CustomerServiceContext('c1', '合成片段')])
        assert not budget.keys and not sent and not model.observation['reserved'] and not model.observation['attemptedSend']
        assert model.pre_send_failure or model.invalid_context
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_invalid_key_and_second_actual_sdk_send_rejected():
    from ai_service.config import Settings
    from ai_service.models.base import CustomerServiceContext
    cfg = Settings(_env_file=None, model_api_key='test', model_base_url='http://mock.invalid/v1')
    plan, budget, sent = minimal_runtime_plan(), CountingBudget(), []
    def handler(request): sent.append(1); return response()
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError): model.begin('expired:text')
        assert not budget.keys and not sent
        model.begin('q:text')
        contexts = [CustomerServiceContext('c1', '合成片段')]
        await model.answer_customer_service('合成问题', contexts)
        assert len(budget.keys) == len(sent) == 1
        with pytest.raises(Exception): await model.answer_customer_service('合成问题', contexts)
        with pytest.raises(ValueError): model.begin('q:text')
        assert len(budget.keys) == len(sent) == 1
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_context_budget_before_sdk_and_reservation():
    from ai_service.config import Settings
    from ai_service.models.base import CustomerServiceContext
    cfg = Settings(_env_file=None, model_api_key='test', model_base_url='http://mock.invalid/v1')
    cfg = cfg.model_copy(update={'model_context_window_tokens': 1})
    plan, budget = minimal_runtime_plan(), CountingBudget()
    def forbidden(request): pytest.fail('模型窗口预算失败仍发送 HTTP')
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(forbidden))
    try:
        model.begin('q:text')
        with pytest.raises(Exception):
            await model.answer_customer_service('合成问题', [CustomerServiceContext('c1', '合成片段')])
        assert not budget.keys and not model.observation['reserved'] and not model.observation['attemptedSend']
        assert model.pre_send_failure
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('arm', ['text', 'json_object'])
@pytest.mark.parametrize('oversize', [False, True])
async def test_actual_sdk_gzip_response_entity_and_budget(arm, oversize):
    import gzip
    import hashlib
    from ai_service.config import Settings
    from ai_service.models.base import CustomerServiceContext
    cfg = Settings(_env_file=None, model_api_key='test', model_base_url='http://mock.invalid/v1')
    plan, budget, sent = minimal_runtime_plan(), CountingBudget(), []
    entity = b'x' * (contracts.MAX_RESPONSE_BYTES + 1) if oversize else response().content
    compressed = gzip.compress(entity)
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=compressed,
            headers={'content-encoding': 'gzip', 'content-length': str(len(compressed)),
                     'content-type': 'application/json'})
    model = await runner._runtime_model(cfg, budget, plan, transport=httpx.MockTransport(handler))
    try:
        model.begin('q:' + arm)
        if oversize:
            with pytest.raises(Exception):
                await model.answer_customer_service('合成问题', [CustomerServiceContext('c1', '合成片段')])
            assert model.observation['observationError'] == 'RESPONSE_TOO_LARGE'
            assert model.observation['responseCaptureComplete'] is False
        else:
            answer = await model.answer_customer_service('合成问题', [CustomerServiceContext('c1', '合成片段')])
            assert answer.answered is False
            assert model.observation['httpResponseBytes'] == len(entity)
            assert model.observation['httpResponseSha256'] == hashlib.sha256(entity).hexdigest()
            assert model.observation['responseCaptureComplete'] is True
        assert budget.keys == ['q:' + arm] and len(sent) == 1
        assert model.observation['reserved'] and model.observation['attemptedSend']
        assert ('response_format' in sent[0]) == (arm == 'json_object')
        assert sent[0]['messages'] == [{**m, 'role': 'user' if m['role'] == 'human' else m['role']}
                                        for m in plan['messages']['q:' + arm]]
    finally:
        await model.close()
