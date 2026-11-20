"""冻结回答实验仅用合成响应，任何测试均不连接真实供应商。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from ai_service.models.base import CustomerServiceContext
from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt
from ai_service.orchestration import rag_hybrid_answer_experiment as exp


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(exp, 'RUN', tmp_path)
    monkeypatch.setattr(exp, 'PLAN', tmp_path / 'plan.json')
    monkeypatch.setattr(exp, 'ANCHOR', tmp_path / 'authorization.jsonl')
    settings = SimpleNamespace(model_api_key='synthetic', model_base_url='https://synthetic.invalid/v1',
        model_name='synthetic', model_temperature=.1, model_max_tokens=8192,
        rag_answer_timeout_seconds=60, rag_answer_response_format='text', rag_prompt_max_bytes=16384,
        model_context_window_tokens=65536, model_context_overhead_tokens=4096)
    slots = []
    for i, key in enumerate(exp.IDS):
        for arm in (('cap1', 'cap2') if i % 2 == 0 else ('cap2', 'cap1')):
            messages = [dict(role='system', content=CUSTOMER_SERVICE_SYSTEM_PROMPT),
                dict(role='user', content=customer_service_user_prompt('问题', [CustomerServiceContext('chunk', '证据')]))]
            slots.append(dict(key=f'{key}:{arm}', question='问题', contexts=[dict(chunkId='chunk', content='证据')],
                messages=messages, inputSha256=exp.fingerprint(messages)))
    monkeypatch.setattr(exp, 'load_inputs', lambda: deepcopy(slots))
    monkeypatch.setattr(exp, 'bindings', lambda: {'synthetic': 'digest'})
    return settings, exp.build_plan(settings)


def response(content=None, finish='stop'):
    return dict(choices=[dict(finish_reason=finish, message=dict(content=content or
        json.dumps(dict(answered=True, answer='回答', citedChunkIds=['chunk']))))],
        usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13, unsafe='omitted'))


async def run_stub(settings, plan, handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await exp.execute(settings, plan, exp.fingerprint(plan), client)


def test_prepare_offline_exclusive(fixture, monkeypatch):
    settings, plan = fixture
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: pytest.fail('prepare 联网'))
    assert exp.prepare(settings) == exp.fingerprint(plan)
    assert not exp.ANCHOR.exists()
    before = exp.PLAN.read_bytes()
    with pytest.raises(FileExistsError):
        exp.prepare(settings)
    assert exp.PLAN.read_bytes() == before


def test_budget_order_upper_bound_and_old_anchor(fixture):
    _, plan = fixture
    budget = exp.AnswerBudget(plan)
    with pytest.raises(ValueError, match='ORDER'):
        budget.reserve(plan['slots'][1])
    for slot in plan['slots']:
        budget.reserve(slot)
    with pytest.raises(ValueError, match='ORDER'):
        budget.reserve(plan['slots'][-1])
    budget.close()
    assert len(exp.ANCHOR.read_text(encoding='utf-8').splitlines()) == 9
    with pytest.raises(FileExistsError):
        exp.AnswerBudget(plan)


def test_invalid_schedule_cannot_create_anchor(fixture):
    _, plan = fixture
    plan['slots'][1] = plan['slots'][0]
    with pytest.raises(ValueError, match='SCHEDULE'):
        exp.AnswerBudget(plan)
    assert not exp.ANCHOR.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fmt', ['text', 'json_object'])
async def test_eight_requests_only_and_payload(fixture, fmt):
    settings, _ = fixture
    settings.rag_answer_response_format = fmt
    plan = exp.build_plan(settings)
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == '/v1/chat/completions'
        assert set(body) == {'model', 'messages', 'temperature', 'max_tokens', 'stream'} | (
            {'response_format'} if fmt == 'json_object' else set())
        assert body['stream'] is False
        assert len(exp.ANCHOR.read_text(encoding='utf-8').splitlines()) == len(requests) + 1
        return httpx.Response(200, json=response())
    report = await run_stub(settings, plan, handler)
    assert len(requests) == report['reservedOperations'] == 8
    assert report['status'] == 'completed'
    assert all(r['contractValid'] for r in report['results'])
    assert all(r['usage'] == dict(prompt_tokens=10, completion_tokens=3, total_tokens=13) for r in report['results'])


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['http', 'transport', 'cancel', 'json', 'citation', 'length', 'oversize'])
async def test_first_failure_stops_and_keeps_reservation(fixture, failure, monkeypatch):
    settings, plan = fixture
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'http':
            return httpx.Response(503)
        if failure == 'transport':
            raise httpx.ReadError('synthetic secret must not reach report')
        if failure == 'cancel':
            raise asyncio.CancelledError()
        if failure == 'oversize':
            monkeypatch.setattr(exp, 'MAX_RESPONSE_BYTES', 3)
        content = {'json': 'bad-json', 'citation': '{"answered":true,"answer":"x","citedChunkIds":["foreign"]}'}.get(failure)
        return httpx.Response(200, json=response(content, 'length' if failure == 'length' else 'stop'))
    with pytest.raises(BaseException):
        await run_stub(settings, plan, handler)
    report = exp.read_json(exp.RUN / 'report.json')
    assert len(calls) == report['reservedOperations'] == 1
    assert report['results'][0]['status'] == 'failed'
    assert all(r['status'] == 'not_run' for r in report['results'][1:])
    assert 'synthetic secret' not in json.dumps(report)
    with pytest.raises(FileExistsError):
        await run_stub(settings, plan, handler)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fsync_failure_during_reserve_never_sends(fixture, monkeypatch):
    settings, plan = fixture
    budget = exp.AnswerBudget(plan)
    calls = []
    monkeypatch.setattr(exp.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('fsync failed')))
    result = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        model = exp.FrozenAnswerModel(settings, client, budget, plan['slots'][0], result)
        with pytest.raises(OSError):
            await model.answer_customer_service('问题', [CustomerServiceContext('chunk', '证据')])
        with pytest.raises(ValueError, match='POISONED'):
            budget.reserve(plan['slots'][0])
    budget.close()
    assert not calls
    assert result['reservationState'] == 'unknown'
    assert exp.ANCHOR.exists()


@pytest.mark.asyncio
async def test_actual_messages_mismatch_never_reserves(fixture):
    settings, plan = fixture
    budget = exp.AnswerBudget(plan)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail('发送'))) as client:
        model = exp.FrozenAnswerModel(settings, client, budget, plan['slots'][0], {})
        with pytest.raises(ValueError, match='MESSAGES'):
            await model.answer_customer_service('被改的问题', [CustomerServiceContext('chunk', '证据')])
    assert budget.count == 0
    budget.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['config', 'input', 'binding', 'hash', 'authorization'])
async def test_live_rejects_drift_before_network(fixture, monkeypatch, change):
    settings, plan = fixture
    digest = exp.prepare(settings)
    if change == 'config':
        settings.model_temperature = .2
    elif change == 'input':
        monkeypatch.setattr(exp, 'load_inputs', lambda: [])
    elif change == 'binding':
        monkeypatch.setattr(exp, 'bindings', lambda: {'changed': 'digest'})
    elif change == 'hash':
        digest = 'bad'
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: pytest.fail('验证前联网'))
    with pytest.raises(ValueError):
        await exp.live(settings, digest, 'bad' if change == 'authorization' else exp.AUTHORIZATION)
    assert not exp.ANCHOR.exists()


@pytest.mark.asyncio
async def test_result_persistence_failure_stops(fixture, monkeypatch):
    settings, plan = fixture
    original = exp.durable_json
    def writer(path, value, **kwargs):
        if str(path).endswith('app-generation-005-cap1.json'):
            raise OSError('disk full')
        return original(path, value, **kwargs)
    monkeypatch.setattr(exp, 'durable_json', writer)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())
    with pytest.raises(OSError):
        await run_stub(settings, plan, handler)
    report = exp.read_json(exp.RUN / 'report.json')
    assert len(calls) == 1
    assert report['errorCode'] == 'ANSWER_PERSISTENCE_FAILED'
    assert report['reservedOperations'] == 1


@pytest.mark.parametrize('failure', ['flush', 'short_write'])
def test_ledger_write_failure_poisons_budget(fixture, failure):
    _, plan = fixture
    budget = exp.AnswerBudget(plan)
    stream = budget.stream
    class BrokenStream:
        closed = False
        def write(self, text):
            return 1 if failure == 'short_write' else stream.write(text)
        def flush(self):
            raise OSError('flush failure')
        def close(self):
            stream.close()
    budget.stream = BrokenStream()
    with pytest.raises(OSError):
        budget.reserve(plan['slots'][0])
    assert budget.count == 0
    assert budget.poisoned
    with pytest.raises(ValueError, match='POISONED'):
        budget.reserve(plan['slots'][0])
    budget.close()


@pytest.mark.parametrize('filename', ['authorization.jsonl', 'report.json', 'app-generation-005-cap1-raw.json'])
def test_prepare_rejects_old_outputs(fixture, filename):
    settings, _ = fixture
    (exp.RUN / filename).write_text('old', encoding='utf-8')
    with pytest.raises(ValueError, match='OUTPUT_EXISTS'):
        exp.prepare(settings)
    assert not exp.PLAN.exists()



def test_closed_budget_cannot_reserve(fixture):
    _, plan = fixture
    budget = exp.AnswerBudget(plan)
    budget.close()
    with pytest.raises(ValueError, match='POISONED'):
        budget.reserve(plan['slots'][0])


@pytest.fixture
def frozen_inputs(monkeypatch):
    """全合成归档仍保留90题、55绑定和四题八事实的真实结构约束。"""
    identities = [*exp.IDS, *(f'other-{i}' for i in range(86))]
    chunk = dict(chunkId='synthetic-chunk', documentId='document', documentVersion=1,
                 locator='synthetic', content='唯一可见证据')
    entries = [dict(id=key, question=f'问题 {key}', expectedAnswerable=True,
        requiredFacts=[dict(factId=f'{key}-{i}', text='禁止向模型泄露的评分标签')
                       for i in range(2 if key in exp.IDS else 0)]) for key in identities]
    rows = [dict(id=e['id'], question=e['question'], requiredFacts=deepcopy(e['requiredFacts']),
        arms={arm: dict(contexts=[deepcopy(chunk)], stages=dict(finalContext=[deepcopy(chunk)]))
              for arm in ('cap1', 'cap2')}) for e in entries]
    comparison = dict(cases=rows)
    compressed = deepcopy(comparison)
    for row in compressed['cases']:
        for arm in row['arms'].values():
            arm['contexts'] = [dict(chunkRef='synthetic-chunk')]
            arm['stages']['finalContext'] = [dict(chunkRef='synthetic-chunk')]
    integrity = dict(unchanged=True, fileCount=55, sha256={f'file-{i}': 'ok' for i in range(55)})
    archive = dict(schemaVersion='rag-hybrid-cap-validation-bundle/v1',
        inputIntegrity=deepcopy(integrity), comparison=compressed,
        chunksById={'synthetic-chunk': deepcopy(chunk)})
    documents = {exp.SOURCE_RESULT: archive, exp.INTEGRITY: integrity,
                 exp.COMPARISON: comparison, exp.RUBRIC: dict(entries=entries)}
    digests = {exp.SOURCE_RESULT: exp.SOURCE_SHA256, exp.COMPARISON: exp.COMPARISON_SHA256}
    monkeypatch.setattr(exp, 'read_json', lambda path: documents[path])
    monkeypatch.setattr(exp, 'sha', lambda path: digests.get(path, 'ok'))
    return archive, integrity, comparison, entries, digests


def test_frozen_inputs_expand_exactly_and_keep_fact_labels_private(frozen_inputs):
    slots = exp.load_inputs()
    assert [s['key'] for s in slots] == [
        'app-generation-005:cap1', 'app-generation-005:cap2',
        'app-generation-008:cap2', 'app-generation-008:cap1',
        'admin-knowledge-004:cap1', 'admin-knowledge-004:cap2',
        'deployment-download-009:cap2', 'deployment-download-009:cap1']
    assert len(slots) == 8
    assert '禁止向模型泄露' not in json.dumps(slots, ensure_ascii=False)
    assert 'requiredFacts' not in json.dumps(slots)
    assert all(s['contexts'][0]['content'] == '唯一可见证据' for s in slots)


@pytest.mark.parametrize('change', ['archive_hash', 'comparison_hash', 'archive_schema',
    'integrity_drift', 'integrity_unchanged', 'integrity_count', 'integrity_hash_count',
    'input_hash', 'archive_expansion', 'archive_reference', 'case_count', 'duplicate_case',
    'rubric_count', 'duplicate_rubric', 'different_ids', 'question', 'fact_count',
    'fact_drift', 'unanswerable', 'extra_arm', 'contexts_drift', 'empty_contexts',
    'duplicate_chunk', 'empty_content'])
def test_frozen_inputs_fail_closed(frozen_inputs, change):
    archive, integrity, comparison, entries, digests = frozen_inputs
    row = comparison['cases'][0]
    if change == 'archive_hash':
        digests[exp.SOURCE_RESULT] = 'changed'
    elif change == 'comparison_hash':
        digests[exp.COMPARISON] = 'changed'
    elif change == 'archive_schema':
        archive['schemaVersion'] = 'unknown'
    elif change == 'integrity_drift':
        integrity['sha256']['file-0'] = 'changed'
    elif change == 'integrity_unchanged':
        integrity['unchanged'] = False
        archive['inputIntegrity'] = deepcopy(integrity)
    elif change == 'integrity_count':
        integrity['fileCount'] = 54
        archive['inputIntegrity'] = deepcopy(integrity)
    elif change == 'integrity_hash_count':
        integrity['sha256'].pop('file-0')
        archive['inputIntegrity'] = deepcopy(integrity)
    elif change == 'input_hash':
        digests[exp.Path('file-0')] = 'changed'
    elif change == 'archive_expansion':
        archive['chunksById']['synthetic-chunk']['content'] = '变更'
    elif change == 'archive_reference':
        archive['comparison']['cases'][0]['arms']['cap1']['contexts'][0]['chunkRef'] = 'missing'
    elif change == 'case_count':
        comparison['cases'].pop()
    elif change == 'duplicate_case':
        comparison['cases'][-1] = deepcopy(row)
    elif change == 'rubric_count':
        entries.pop()
    elif change == 'duplicate_rubric':
        entries[-1] = deepcopy(entries[0])
    elif change == 'different_ids':
        entries[-1]['id'] = 'different'
    elif change == 'question':
        entries[0]['question'] = '变更'
    elif change == 'fact_count':
        entries[0]['requiredFacts'].pop()
    elif change == 'fact_drift':
        entries[0]['requiredFacts'][0]['text'] = '变更'
    elif change == 'unanswerable':
        entries[0]['expectedAnswerable'] = False
    elif change == 'extra_arm':
        row['arms']['cap3'] = deepcopy(row['arms']['cap1'])
    elif change == 'contexts_drift':
        row['arms']['cap1']['contexts'][0]['content'] = '变更'
    elif change == 'empty_contexts':
        row['arms']['cap1']['contexts'] = row['arms']['cap1']['stages']['finalContext'] = []
    elif change == 'duplicate_chunk':
        row['arms']['cap1']['contexts'] *= 2
        row['arms']['cap1']['stages']['finalContext'] *= 2
    elif change == 'empty_content':
        row['arms']['cap1']['contexts'][0]['content'] = ''
        row['arms']['cap1']['stages']['finalContext'][0]['content'] = ''
    # 结构场景同步合成归档，确保测试命中对应结构检查而非提前展开不等。
    if change in {'case_count', 'duplicate_case', 'extra_arm', 'contexts_drift',
                  'empty_contexts', 'duplicate_chunk', 'empty_content'}:
        archive['comparison'] = deepcopy(comparison)
    with pytest.raises((ValueError, KeyError)):
        exp.load_inputs()


@pytest.mark.asyncio
@pytest.mark.parametrize('name, expected', [('provider-model-v1', 'provider-model-v1'),
    ('x' * 128, 'x' * 128), ('x' * 129, None), ('bad\nname', None),
    ('', None), (None, None), (123, None), ({'name': 'model'}, None)])
async def test_response_model_is_bounded_observation(fixture, name, expected):
    settings, plan = fixture
    payload = response()
    payload['model'] = name
    report = await run_stub(settings, plan, lambda request: httpx.Response(200, json=payload))
    assert report['status'] == 'completed'
    assert all(r['responseModel'] == expected for r in report['results'])


@pytest.mark.asyncio
async def test_ninth_model_invocation_never_sends(fixture):
    settings, plan = fixture
    budget = exp.AnswerBudget(plan)
    for slot in plan['slots']:
        budget.reserve(slot)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail('第九次发送'))) as client:
        model = exp.FrozenAnswerModel(settings, client, budget, plan['slots'][0], {})
        with pytest.raises(ValueError, match='ORDER'):
            await model.answer_customer_service('问题', [CustomerServiceContext('chunk', '证据')])
    assert budget.count == 8
    budget.close()


def test_prepare_persists_no_api_key_or_endpoint(fixture):
    settings, _ = fixture
    settings.model_api_key = 'private-test-credential-do-not-persist'
    exp.prepare(settings)
    for path in (exp.PLAN, exp.RUN / 'freeze.json'):
        value = path.read_text(encoding='utf8')
        assert settings.model_api_key not in value
        assert settings.model_base_url not in value


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [[], None, {'choices': []}, {'choices': [1]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': 123}}]}])
async def test_invalid_response_shape_stops_after_one(fixture, payload):
    settings, plan = fixture
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps(payload))
    with pytest.raises(ValueError):
        await run_stub(settings, plan, handler)
    report = exp.read_json(exp.RUN / 'report.json')
    assert len(calls) == report['reservedOperations'] == 1
    assert report['results'][0]['errorCode'] == 'ANSWER_RESPONSE_INVALID'


@pytest.mark.parametrize('flag', ['true', 1, None])
def test_answerability_requires_explicit_true(frozen_inputs, flag):
    _, _, _, entries, _ = frozen_inputs
    entries[0]['expectedAnswerable'] = flag
    with pytest.raises(ValueError, match='QUESTION'):
        exp.load_inputs()



def test_bindings_cover_actual_round29_files_and_indirect_inputs(monkeypatch):
    """只模拟摘要与历史清单，保留真实路径生成，防止错误文件名被整体模拟遮住。"""
    indirect = exp.ROOT / 'synthetic-history-input.json'
    observed = []
    monkeypatch.setattr(exp, 'read_json', lambda path: {'sha256': {str(indirect): 'old'}})
    def hash_path(path):
        observed.append(path)
        return 'synthetic-sha'
    monkeypatch.setattr(exp, 'sha', hash_path)
    actual = exp.bindings()
    expected = {
        'docs/superpowers/plans/2026-11-20-rag-hybrid-answer-round29.md',
        'docs/superpowers/specs/2026-11-20-rag-hybrid-answer-round29-design.md',
        'ai-service/src/ai_service/orchestration/rag_hybrid_answer_experiment.py',
        'ai-service/tests/test_rag_hybrid_answer_experiment.py',
        'ai-service/src/ai_service/config.py',
        'ai-service/src/ai_service/models/base.py',
        'ai-service/src/ai_service/models/openai_compatible.py',
        'ai-service/src/ai_service/models/input_review.py',
        'ai-service/src/ai_service/prompts/customer_service.py',
        'ai-service/src/ai_service/orchestration/rag_hybrid_cap_evaluation.py',
    }
    assert expected <= actual.keys()
    assert all((exp.ROOT / name).is_file() for name in expected)
    assert not any(name.endswith('rag-hybrid-answer-validation.md') for name in actual)
    assert indirect in observed
    for path in (exp.COMPARISON, exp.INTEGRITY, exp.SOURCE_RESULT, exp.RUBRIC,
                 exp.RUN / 'frozen-review-basis.json', exp.RUN / 'pre-answer-evidence-review.json'):
        assert path in observed
        assert actual[path.relative_to(exp.ROOT).as_posix()] == 'synthetic-sha'
