"""冻结回答实验仅用合成响应，任何测试均不连接真实供应商。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from ai_service.models.base import CustomerServiceContext
from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt
from ai_service.orchestration import rag_qa_linked_answer_experiment as exp


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
    assert len(exp.ANCHOR.read_text(encoding='utf-8').splitlines()) == 11
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
async def test_ten_requests_only_and_payload(fixture, fmt):
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
    assert len(requests) == report['reservedOperations'] == 10
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


@pytest.mark.parametrize('change', ['comparison', 'integrity', 'count', 'file'])
def test_source_hashes_fail_closed(monkeypatch, change):
    hashes = {f'file-{i}': 'ok' for i in range(44)}
    integrity = dict(unchanged=True, fileCount=44, sha256=hashes)
    source = dict(comparisonSha256='ok', integritySha256='ok')
    if change == 'count':
        integrity['fileCount'] = 43
    def fake_sha(path):
        if ((change == 'comparison' and path == exp.COMPARISON)
                or (change == 'integrity' and path == exp.INTEGRITY)
                or (change == 'file' and str(path) == 'file-0')):
            return 'changed'
        return 'ok'
    monkeypatch.setattr(exp, 'sha', fake_sha)
    monkeypatch.setattr(exp, 'read_json', lambda p: source if p == exp.SOURCE_RESULT else integrity)
    with pytest.raises(ValueError, match='ANSWER_'):
        exp.load_inputs()


def test_closed_budget_cannot_reserve(fixture):
    _, plan = fixture
    budget = exp.AnswerBudget(plan)
    budget.close()
    with pytest.raises(ValueError, match='POISONED'):
        budget.reserve(plan['slots'][0])
