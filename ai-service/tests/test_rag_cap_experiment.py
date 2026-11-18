"""离线准备、批准绑定与正式回答的发送前边界。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_service.models.base import CustomerServiceModelAnswer, CustomerServiceContext
from ai_service.orchestration import rag_cap_contracts as contracts
from ai_service.orchestration import rag_cap_experiment as runner
from ai_service.orchestration import rag_cap_budget as budget_module
from test_rag_cap_contracts import frozen_source, write


async def prepared(frozen_source, tmp_path, monkeypatch):
    _, cfg, _, _, replay_path = frozen_source
    # 先由正式 Fake 路径计算合成来源的 contexts，随后绑定为不可变前轮证据。
    frozen = contracts.load_frozen_inputs(cfg)
    contexts = await runner.prepare_contexts(frozen, cfg, check_replay=False)
    replay = contracts.read_json(replay_path)
    replay['cases'] = [{'id': e['id'], 'question': e['question'],
        'arms': {arm: contexts[e['id'] + ':' + arm] for arm in contracts.ARMS}}
        for e in frozen.rubric['entries']]
    write(replay_path, replay)
    monkeypatch.setattr(contracts, 'REPLAY_SHA256', contracts.fingerprint(replay))
    monkeypatch.setattr(runner, 'AUTHORIZATION', tmp_path / 'unused-anchor.jsonl')
    monkeypatch.setattr(budget_module, 'AUTHORIZATION', tmp_path / 'unused-anchor.jsonl')
    monkeypatch.setattr(runner, 'settings', lambda: cfg)
    monkeypatch.setattr(runner, 'TMP', tmp_path)
    plan = await runner.prepare(tmp_path / 'prepared')
    return cfg, plan


@pytest.mark.asyncio
async def test_prepare_never_constructs_sdk_or_anchor(frozen_source, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, '_runtime_model', lambda *a: pytest.fail('prepare 创建了 SDK'))
    cfg, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    assert len(plan['contexts']) == 180 and len(plan['schedule']) == 180
    assert plan['schedule'][:4] == ['q0:cap1', 'q0:cap2', 'q1:cap2', 'q1:cap1']
    request = contracts.read_json(tmp_path / 'prepared/authorization-request.json')
    assert request['decision'] == 'pending' and request['maxChatCalls'] == 180
    assert request['planSha256'] == contracts.fingerprint(plan)
    assert not runner.AUTHORIZATION.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['pending', 'extra', 'study', 'hash', 'bool', 'limit'])
async def test_invalid_approval_refused_before_resources(frozen_source, tmp_path, monkeypatch, damage):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    approval = contracts.read_json(tmp_path / 'prepared/authorization-request.json')
    approval['decision'] = 'approved'
    if damage == 'pending': approval['decision'] = 'pending'
    if damage == 'extra': approval['unknown'] = 1
    if damage == 'study': approval['studyId'] = 'other'
    if damage == 'hash': approval['planSha256'] = '0' * 64
    if damage == 'bool': approval['maxChatCalls'] = True
    if damage == 'limit': approval['maxChatCalls'] = 181
    path = tmp_path / 'approval.json'; write(path, approval)
    monkeypatch.setattr(runner, '_runtime_model', lambda *a: pytest.fail('无批准初始化 SDK'))
    with pytest.raises(ValueError, match='RAG_CAP_APPROVAL_INVALID'):
        await runner.run_live(tmp_path / 'prepared/plan.json', path)
    assert not runner.AUTHORIZATION.exists() and not (tmp_path / 'prepared/live').exists()


@pytest.mark.asyncio
async def test_guard_rejects_question_or_context_before_delegate():
    class Model:
        observation = {'providerCalled': False}
        def begin(self, key): pass
        async def answer_customer_service(self, *args): pytest.fail('冻结漂移触发底层模型')
    frozen = {'q:cap1': [{'chunkId': 'a', 'content': '原文'}]}
    model = runner.FrozenContextModel(Model(), {'q': '问题'}, frozen)
    model.begin('q:cap1')
    for question, text in [('变化', '原文'), ('问题', '变化')]:
        with pytest.raises(ValueError, match='RAG_CAP_CONTEXT_MISMATCH'):
            await model.answer_customer_service(question, [CustomerServiceContext('a', text)])


def approve(plan, tmp_path):
    value = {'schemaVersion': 'rag-cap-authorization/v1', 'studyId': contracts.STUDY_ID,
        'decision': 'approved', 'planSha256': contracts.fingerprint(plan),
        'maxChatCalls': 180, 'liveOutput': plan['liveOutput']}
    path = tmp_path / 'test-only-approved.json'
    write(path, value)
    return path


def fake_sdk(monkeypatch, *, mode='ok', closer_errors=False):
    import langchain_openai
    import ai_service.models.openai_compatible as adapter
    from langchain_core.messages import AIMessage
    from types import SimpleNamespace
    calls, closed, constructors = [], [], []
    cancelled = asyncio.CancelledError('原始取消')

    async def async_close():
        closed.append('async')
        if closer_errors:
            raise OSError('不得输出详情')

    def sync_close():
        closed.append('sync')
        if closer_errors:
            raise OSError('不得输出详情')

    class Chat:
        def __init__(self, **kwargs):
            constructors.append(kwargs)
            self.root_async_client = SimpleNamespace(close=async_close)
            self.root_client = SimpleNamespace(close=sync_close)

        async def ainvoke(self, messages):
            ledger_rows = [json.loads(line) for line in budget_module.AUTHORIZATION.read_text().splitlines()]
            # 发送替身只有看到已 fsync 的预占和全 180 项占位才能执行。
            reservation = ledger_rows[-1]
            assert reservation['kind'] == 'chat'
            report_path = json.loads(budget_module.AUTHORIZATION.read_text().splitlines()[0])['output']
            report = contracts.read_json(Path(report_path) / 'report.json')
            assert len(report['results']) == 180
            if not calls:
                assert all(r['status'] == 'not_run' for r in report['results'])
            payload = json.loads(messages[-1].content)
            calls.append((reservation['key'], payload))
            if mode == 'cancel':
                raise cancelled
            if mode == 'provider':
                raise RuntimeError('供应商私有错误')
            value = '坏JSON' if mode == 'json' else json.dumps({'answered': True,
                'answer': '合成回答', 'citedChunkIds': ['unknown' if mode == 'citation'
                    else payload['contexts'][0]['chunkId']]})
            return AIMessage(content=value,
                usage_metadata={'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25},
                response_metadata={'finish_reason': 'stop', 'unsafe': '不得保存'})

    monkeypatch.setattr(adapter, 'ChatOpenAI', lambda **kw: pytest.fail('基类 SDK 不得初始化'))
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', Chat)
    return calls, closed, constructors, cancelled


@pytest.mark.asyncio
async def test_live_180_reserved_before_send_and_no_new_retrieval_clients(frozen_source, tmp_path, monkeypatch):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    import openai
    from ai_service.models.reranker import LocalCrossEncoderReranker
    from ai_service.infrastructure import milvus_knowledge, spring_knowledge_publication
    forbidden = lambda *a, **kw: pytest.fail('答案实验构造了额外外部资源')
    monkeypatch.setattr(openai, 'AsyncOpenAI', forbidden)
    monkeypatch.setattr(LocalCrossEncoderReranker, 'create', forbidden)
    monkeypatch.setattr(milvus_knowledge, 'MilvusKnowledgeStore', forbidden)
    monkeypatch.setattr(spring_knowledge_publication, 'SpringKnowledgePublicationClient', forbidden)
    calls, closed, constructors, _ = fake_sdk(monkeypatch)
    report = await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
    assert report['status'] == 'complete'
    assert [key for key, _ in calls] == plan['schedule']
    assert len(calls) == 180 and closed == ['async', 'sync']
    assert len(constructors) == 1 and constructors[0]['max_retries'] == 0
    assert report['callsReserved'] == {'chat': 180, 'embeddingRequests': 0, 'queryInputs': 0}
    assert report['newEmbeddingCalls'] == report['newBgeCalls'] == report['productionIndexWrites'] == 0
    assert report['currentAuthoritativeSnapshotChecked'] is False
    assert all(r['status'] == 'ok' and r['providerCalled'] and len(r['actualMessages']) == 2
               and r['usage']['total_tokens'] == 25 and r['rawResponse']
               and r['responseMetadata'] == {'finish_reason': 'stop'} for r in report['results'])
    assert report['evaluationBindings'] == plan['evaluationBindings']
    # 换输出或重复启动都不能绕过固定锚点；原目录也不准恢复。
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW_UNDER_TMP'):
        await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['json', 'citation', 'provider'])
async def test_three_provider_called_errors_stop_with_180_denominator(frozen_source, tmp_path, monkeypatch, mode):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    calls, closed, _, _ = fake_sdk(monkeypatch, mode=mode)
    report = await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
    assert report['stopReason'] == 'THREE_CONSECUTIVE_CALL_FAILURES'
    assert len(calls) == report['callsReserved']['chat'] == 3
    assert sum(r['status'] == 'error' for r in report['results']) == 3
    assert sum(r['status'] == 'not_run' for r in report['results']) == 177
    assert all(sum(r['arm'] == arm for r in report['results']) == 90 for arm in contracts.ARMS)
    assert closed == ['async', 'sync']


@pytest.mark.asyncio
async def test_model_cancel_is_same_instance_and_not_refunded(frozen_source, tmp_path, monkeypatch):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    calls, closed, _, cancelled = fake_sdk(monkeypatch, mode='cancel')
    with pytest.raises(asyncio.CancelledError) as observed:
        await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
    assert observed.value is cancelled and len(calls) == 1 and closed == ['async', 'sync']
    report = contracts.read_json(tmp_path / 'prepared/live/report.json')
    assert report['status'] == 'cancelled' and report['results'][0]['errorCode'] == 'CANCELLED'
    assert report['callsReserved']['chat'] == 1
    assert sum(r['status'] == 'not_run' for r in report['results']) == 179


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['settings', 'implementation', 'input', 'context', 'schedule',
                                  'payment', 'annotations', 'plan-extra', 'anchor', 'output'])
async def test_drift_refused_before_sdk_anchor_or_send(frozen_source, tmp_path, monkeypatch, damage):
    cfg, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    source = frozen_source[0]
    if damage == 'settings':
        monkeypatch.setattr(runner, 'settings', lambda: cfg.model_copy(update={'model_temperature': .9}))
    if damage == 'implementation':
        monkeypatch.setattr(runner, 'implementation_manifest', lambda: {'changed': 'a' * 64})
    if damage == 'input':
        vectors = contracts.read_json(source / 'query-vectors.json'); vectors.reverse()
        write(source / 'query-vectors.json', vectors)
    if damage == 'context': plan['contexts']['q0:cap1'][0]['content'] += '漂移'
    if damage == 'schedule': plan['schedule'].reverse()
    if damage in {'payment', 'annotations'}:
        value = deepcopy(plan['evaluationBindings'])
        key = 'paymentClarification' if damage == 'payment' else 'originalAnnotations'
        value[key]['byteSha256'] = '0' * 64
        monkeypatch.setattr(runner, 'evaluation_bindings', lambda: value)
    if damage == 'plan-extra': plan['extra'] = True
    if damage == 'anchor': runner.AUTHORIZATION.write_text('隔离测试旧账本', encoding='utf8')
    if damage == 'output': (tmp_path / 'prepared/live').mkdir()
    write(tmp_path / 'prepared/plan.json', plan)
    monkeypatch.setattr(runner, '_runtime_model', lambda *a: pytest.fail('漂移后初始化 SDK'))
    with pytest.raises(ValueError, match='RAG_CAP_'):
        await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
    if damage != 'anchor': assert not runner.AUTHORIZATION.exists()
    if damage != 'output': assert not (tmp_path / 'prepared/live').exists()


@pytest.mark.asyncio
async def test_head_only_drift_does_not_invalidate_key_file_plan(frozen_source, tmp_path, monkeypatch):
    cfg, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    monkeypatch.setattr(runner, 'code_diagnostics', lambda: {'codeSha': 'different-head', 'dirty': True})
    assert runner.validate_plan(plan, cfg).vectors['q0'] == [1.0, 0.0]


@pytest.mark.asyncio
async def test_default_main_is_offline_no_sdk_or_anchor(frozen_source, tmp_path, monkeypatch, capsys):
    await prepared(frozen_source, tmp_path, monkeypatch)
    monkeypatch.setattr(runner, '_runtime_model', lambda *a: pytest.fail('默认入口创建 SDK'))
    monkeypatch.setattr('sys.argv', ['runner'])
    # main 自己使用 asyncio.run；此处在独立线程调用而不嵌套事件循环。
    await asyncio.to_thread(runner.main)
    assert "'providerCalls': 0" in capsys.readouterr().out
    assert not runner.AUTHORIZATION.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_save_and_all_closer_failures_still_attempt_every_resource(frozen_source, tmp_path, monkeypatch, cancelled):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    calls, closed, _, original = fake_sdk(monkeypatch, mode='cancel' if cancelled else 'json', closer_errors=True)
    original_budget = runner.CapAuthorizationBudget
    class Budget(original_budget):
        def close(self):
            super().close()
            closed.append('budget')
            raise OSError('禁止输出账本异常')
    monkeypatch.setattr(runner, 'CapAuthorizationBudget', Budget)
    original_write = runner.write_json
    def fail_final(path, value):
        if path.name == 'report.json' and 'finishedAtUtc' in value:
            raise OSError('禁止输出写盘异常')
        original_write(path, value)
    monkeypatch.setattr(runner, 'write_json', fail_final)
    if cancelled:
        with pytest.raises(asyncio.CancelledError) as observed:
            await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
        assert observed.value is original
    else:
        report = await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
        assert report['reportSaveFailures'] == ['before_cleanup', 'after_cleanup']
        assert report['cleanupErrors'] == ['budget', 'answer_model_async', 'answer_model_sync']
    assert closed == ['budget', 'async', 'sync']


@pytest.mark.asyncio
async def test_save_cancellation_after_row_replacement_marks_current_error(frozen_source, tmp_path, monkeypatch):
    cfg, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    frozen = runner.validate_plan(plan, cfg)
    report = runner.new_report(frozen, plan, {})
    original = asyncio.CancelledError('写盘时取消')
    class Model:
        observation = {'providerCalled': True}
        invalid_context = False
        pre_send_failure = False
        def begin(self, key): pass
        async def answer_customer_service(self, question, contexts):
            return CustomerServiceModelAnswer(False, '', ())
    saves = []
    def save():
        saves.append(1)
        if len(saves) > 1:
            raise original
    with pytest.raises(asyncio.CancelledError) as observed:
        await runner.execute_answers(report, frozen, cfg, Model(), save)
    assert observed.value is original
    assert report['results'][0]['status'] == 'error' and report['results'][0]['errorCode'] == 'CANCELLED'
    assert sum(r['status'] == 'not_run' for r in report['results']) == 179


@pytest.mark.asyncio
async def test_live_fsync_poison_stops_before_any_sdk_send(frozen_source, tmp_path, monkeypatch):
    _, plan = await prepared(frozen_source, tmp_path, monkeypatch)
    calls, closed, _, _ = fake_sdk(monkeypatch)
    original_append = budget_module.CapAuthorizationBudget._append
    def fail_reservation(self, record):
        if record['type'] == 'reserved':
            self._poisoned = True
            raise OSError('模拟 fsync 结果不确定')
        return original_append(self, record)
    monkeypatch.setattr(budget_module.CapAuthorizationBudget, '_append', fail_reservation)
    report = await runner.run_live(tmp_path / 'prepared/plan.json', approve(plan, tmp_path))
    assert report['stopReason'] == 'RAG_CAP_PRE_SEND_FAILED'
    assert calls == [] and closed == ['async', 'sync']
    assert sum(r['status'] == 'error' for r in report['results']) == 1
    assert sum(r['status'] == 'not_run' for r in report['results']) == 179


@pytest.mark.asyncio
async def test_new_study_save_failure_code_keeps_old_cleanup_compatibility():
    report = {'status': 'complete'}
    def failing_save():
        raise OSError('禁止输出原始异常')
    await runner._finish_run(report, failing_save, save_failure_code='RAG_CAP_REPORT_SAVE_FAILED')
    assert report['stopReason'] == 'RAG_CAP_REPORT_SAVE_FAILED'
    assert report['reportSaveFailures'] == ['before_cleanup', 'after_cleanup']
