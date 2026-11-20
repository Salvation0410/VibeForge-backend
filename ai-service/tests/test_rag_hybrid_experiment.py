"""混合检索独立实验的预占、输入绑定和真实入口边界；全部使用Fake。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration import rag_hybrid_experiment as subject


def chunk(index):
    return dict(chunk_id=f'd{index}:2:0', document_id=f'd{index}', document_version=2,
                chunk_index=0, content=f'部署说明{index}', source_name=f'd{index}.md', source_locator='说明')


def cases():
    chunks = [subject.make_chunk(chunk(i), 1-i/10) for i in range(8)]
    return [dict(id=f'q{i}', question='部署', arms=dict(dense=chunks, hybrid=list(reversed(chunks))))
            for i in range(90)]


def plan():
    return dict(schedule=subject.schedule_for(cases()))


def test_budget_exclusive_exact_schedule_and_exhaustion(tmp_path):
    spec, anchor = plan(), tmp_path/'ledger'
    budget = subject.Budget(anchor, spec)
    with pytest.raises(ValueError, match='SCHEDULE'):
        budget.reserve(spec['schedule'][1])
    for row in spec['schedule']:
        budget.reserve(row)
    assert budget.count == 180
    with pytest.raises(ValueError, match='SCHEDULE'):
        budget.reserve(spec['schedule'][-1])
    budget.close()
    rows = [json.loads(line) for line in anchor.read_text(encoding='utf8').splitlines()]
    assert rows[0]['bgeLimit'] == 180 and rows[0]['chatLimit'] == 0
    assert [r['key'] for r in rows[1:]] == [r['key'] for r in spec['schedule']]
    with pytest.raises(FileExistsError):
        subject.Budget(anchor, spec)


def test_persistence_failure_poisoned_no_refund(tmp_path, monkeypatch):
    spec = plan()
    budget = subject.Budget(tmp_path/'ledger', spec)
    def failed(_):
        raise OSError('synthetic')
    monkeypatch.setattr(subject.os, 'fsync', failed)
    with pytest.raises(OSError):
        budget.reserve(spec['schedule'][0])
    with pytest.raises(ValueError, match='PERSISTENCE_UNKNOWN'):
        budget.reserve(spec['schedule'][0])
    assert budget.count == 0 and budget.poisoned
    budget.close()


@pytest.mark.parametrize('change', ['short', 'duplicate', 'kind'])
def test_invalid_budget_plan_rejected_before_file(tmp_path, change):
    spec = plan()
    if change == 'short':
        spec['schedule'].pop()
    elif change == 'duplicate':
        spec['schedule'][1] = spec['schedule'][0]
    else:
        spec['schedule'][0]['kind'] = 'embedding'
    with pytest.raises(ValueError, match='BUDGET_PLAN'):
        subject.Budget(tmp_path/'ledger', spec)
    assert not (tmp_path/'ledger').exists()


def test_schedule_alternates_and_binds_full_input():
    items = cases()
    original = subject.schedule_for(items)
    assert [r['key'] for r in original[:4]] == ['q0:dense', 'q0:hybrid', 'q1:hybrid', 'q1:dense']
    items[0]['question'] = '下载'
    assert subject.schedule_for(items)[0]['inputSha256'] != original[0]['inputSha256']
    items = cases()
    items[0]['arms']['dense'] = items[0]['arms']['dense'][::-1]
    assert subject.schedule_for(items)[0]['inputSha256'] != original[0]['inputSha256']


@pytest.mark.asyncio
async def test_full_fake_execute_pre_reserved_and_all_rows_saved(tmp_path):
    items, calls = cases(), []
    spec = dict(schedule=subject.schedule_for(items))
    anchor = tmp_path/'ledger'
    budget = subject.Budget(anchor, spec)
    class Fake:
        async def rerank(self, question, chunks, *, top_n):
            rows = [json.loads(x) for x in anchor.read_text(encoding='utf8').splitlines()]
            assert len(rows) == len(calls) + 2
            assert rows[-1]['type'] == 'reserved'
            assert question == '部署' and top_n == 8
            calls.append([c.chunk_id for c in chunks])
            return [RerankedChunk(c, 1-i/10) for i, c in enumerate(chunks)]
    report = dict(results=[])
    await subject.execute(items, Fake(), budget, tmp_path, report)
    budget.close()
    assert len(calls) == len(report['results']) == 180
    assert len(list(tmp_path.glob('q*.json'))) == 180
    assert all(len(r['contexts']) == 3 for r in report['results'])
    assert report['results'][2]['arm'] == 'hybrid'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_first_failure_stops_and_consumes_reservation(tmp_path, cancel):
    items, calls = cases(), []
    spec = dict(schedule=subject.schedule_for(items))
    budget = subject.Budget(tmp_path/'ledger', spec)
    class Fake:
        async def rerank(self, *args, **kwargs):
            calls.append(1)
            if cancel:
                raise asyncio.CancelledError()
            raise RuntimeError('synthetic')
    report = dict(results=[])
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await subject.execute(items, Fake(), budget, tmp_path, report)
    assert budget.count == len(calls) == 1 and report['results'] == []
    budget.close()


def test_batch_binding_rejects_text_or_order_change():
    chunks, entries = [chunk(i) for i in range(3)], [dict(question='部署'), dict(question='下载')]
    spec = dict(schedule=[dict(kind='embedding', key='after:0', inputs=3,
        inputSha256=subject.fingerprint([c['content'] for c in chunks])),
        dict(kind='embedding', key='queries:0', inputs=2, inputSha256=subject.fingerprint(['部署', '下载']))])
    subject.verify_batches(spec, chunks, entries)
    with pytest.raises(ValueError, match='INPUT_ORDER'):
        subject.verify_batches(spec, chunks[::-1], entries)
    with pytest.raises(ValueError, match='INPUT_ORDER'):
        subject.verify_batches(spec, chunks, entries[::-1])


@pytest.mark.parametrize('payload', ['{"after":[],"after":[]}', '{"before":NaN}', '{}',
    '{"before":[],"after":[],"queries":[]}'])
def test_vector_schema_fails_closed(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    path = tmp_path/'vectors.json'
    path.write_text(payload, encoding='utf8')
    with pytest.raises(ValueError):
        subject.load_vectors(path)


def test_bound_file_drift_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    path = tmp_path/'source'
    path.write_text('原文', encoding='utf8')
    bindings = {'source': subject.sha(path)}
    subject.unchanged(bindings)
    path.write_text('修改', encoding='utf8')
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.unchanged(bindings)


def test_retrieval_is_independent_of_fact_labels():
    inputs = dict(chunks=[chunk(i) for i in range(10)], entries=[dict(id='q0', question='部署')],
                  vectors=dict(after=[[1.0, i/10] for i in range(10)], queries=[[1.0, 0.0]]))
    original = subject.retrieval_cases(inputs)
    changed = deepcopy(inputs)
    changed['entries'][0]['requiredFacts'] = [{'evidence': '不要使用标签'}]
    alternative = subject.retrieval_cases(changed)
    assert original[0]['arms'] == alternative[0]['arms']
    assert len(original[0]['arms']['hybrid']) == 8
    assert len({c.chunk_id for c in original[0]['arms']['hybrid']}) == 8


@pytest.mark.asyncio
async def test_changed_plan_never_loads_model(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    run = tmp_path/'tmp'/'run'
    run.mkdir(parents=True)
    monkeypatch.setattr(subject, 'RUN', run)
    (run/'live-plan.json').write_text('{"config":{}}', encoding='utf8')
    monkeypatch.setattr(subject, 'load_inputs', lambda: pytest.fail('must reject before inputs/model'))
    with pytest.raises(ValueError, match='PLAN_CHANGED'):
        await subject.live(None, 'invalid')
    assert not (run/'authorization.jsonl').exists()


def test_quote_failure_identifies_first_loss_not_semantic_judgment():
    stages = dict(retrieved=True, filtered=True, selected=True, characterBudget=True, finalContext=True)
    assert subject.quote_failure(stages) == 'survived'
    assert subject.quote_failure(stages | dict(selected=False, finalContext=False)) == 'selection_quote_missing'
    assert subject.quote_failure(stages | dict(retrieved=False, selected=False)) == 'retrieval_quote_missing'
    assert subject.quote_failure(stages | dict(characterBudget=False)) == 'character_budget_quote_missing'
    assert subject.quote_failure(stages | dict(finalContext=False)) == 'prompt_budget_quote_missing'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'rerank', 'cleanup'])
async def test_complete_live_fake_report_and_reentry_rejected(tmp_path, monkeypatch, failure):
    from ai_service.config import Settings
    run = tmp_path/'tmp'/'run'
    run.mkdir(parents=True)
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    monkeypatch.setattr(subject, 'RUN', run)
    items = cases()
    for item in items:
        item['diagnostic'] = dict(lexicalTop8=[subject.record(c) for c in item['arms']['dense']],
                                  union=[subject.record(c) for c in item['arms']['dense']])
        item['retrievalMs'] = dict(dense=1.0, lexicalAndFusion=1.0)
    old = {}
    for item in items:
        dense = item['arms']['dense']
        result = subject.select_arm(item['question'], dense, [RerankedChunk(c, 1-i/10) for i,c in enumerate(dense)], 1)
        old[item['id']] = dict(stages=result['stages'])
    rubric = dict(entries=[dict(id=f'q{i}', question='部署', requiredFacts=[
        dict(factId=f'q{i}:{j}', basisType='public-document', evidence=[
            dict(documentId='d0', quote='部署说明0')]) for j in range(2 if i < 40 else 1)]) for i in range(90)])
    rubric_path = tmp_path/'rubric.json'
    rubric_path.write_text(json.dumps(rubric), encoding='utf8')
    monkeypatch.setattr(subject, 'RUBRIC', rubric_path)
    inputs = dict(chunks=[chunk(i) for i in range(8)], bindings={}, oldBge={}, historical=old)
    monkeypatch.setattr(subject, 'load_inputs', lambda: inputs)
    monkeypatch.setattr(subject, 'retrieval_cases', lambda _: items)
    monkeypatch.setattr(subject, '_local_model_manifest', lambda _: (tmp_path, {}))
    spec = dict(config=subject.CONFIG, bindings={}, bge={}, schedule=subject.schedule_for(items))
    (run/'live-plan.json').write_text(json.dumps(spec), encoding='utf8')
    calls = []
    class Fake:
        async def rerank(self, question, chunks, *, top_n):
            calls.append(1)
            if failure == 'rerank':
                raise RuntimeError('synthetic')
            return [RerankedChunk(c, 1-i/10) for i,c in enumerate(chunks)]
        async def close(self):
            if failure == 'cleanup':
                raise RuntimeError('synthetic')
    async def create(_):
        return Fake()
    monkeypatch.setattr(subject, 'create_reranker', create)
    settings = Settings(_env_file=None)
    if failure:
        with pytest.raises(RuntimeError):
            await subject.live(settings, subject.fingerprint(spec))
    else:
        await subject.live(settings, subject.fingerprint(spec))
    report = json.loads((run/'live/report.json').read_text(encoding='utf8'))
    assert report['status'] == ('failed' if failure else 'completed')
    assert report['reservedOperations'] == len(calls) == (1 if failure == 'rerank' else 180)
    if failure == 'rerank':
        assert report['failurePhase'] == 'rerank'
    else:
        result = json.loads((run/'live/comparison.json').read_text(encoding='utf8'))
        assert result['summary']['arms']['dense']['wholeQuoteSurvival']['finalContext'] == 130
        assert result['summary']['arms']['hybrid']['wholeQuoteSurvival']['finalContext'] == 0
        assert result['summary']['finalQuoteLosses'] == 130
        assert result['summary']['historicalFinalContextMatches'] == 90
        assert result['summary']['arms']['hybrid']['quoteFailureCounts']['selection_quote_missing'] == 130
        assert result['answerQuality'] is None and result['humanReview'] == 'pending'
    with pytest.raises(ValueError, match='ALREADY_STARTED'):
        await subject.live(settings, subject.fingerprint(spec))


def test_local_oom_not_retried_and_adapter_closes():
    from types import SimpleNamespace
    attempts, closed = [], []
    def score(raw, pairs, size, **kwargs):
        attempts.append(size)
        raise RuntimeError('CUDA out of memory')
    adapter = SimpleNamespace(_reranker=SimpleNamespace(get_detailed_inputs=lambda pairs: pairs),
                              close=lambda: closed.append(True))
    strict = subject.NoRetryAdapter(adapter, score)
    with pytest.raises(RuntimeError, match='out of memory'):
        strict.compute_score([['问题', '正文']] * 4)
    assert attempts == [4]
    with pytest.raises(ValueError, match='BATCH_SIZE'):
        strict.compute_score([['问题', '正文']] * 5)
    assert attempts == [4]
    strict.close()
    assert closed == [True]


def test_no_retry_adapter_preserves_scores_and_input_order():
    from types import SimpleNamespace
    pairs = [['问题', '正文1'], ['问题', '正文2']]
    def score(raw, detailed, size, **kwargs):
        assert detailed == pairs and size == 2 and kwargs == dict(normalize=False)
        return [1.2, -0.5]
    adapter = SimpleNamespace(_reranker=SimpleNamespace(get_detailed_inputs=lambda values: values))
    assert subject.NoRetryAdapter(adapter, score).compute_score(pairs) == [1.2, -0.5]
