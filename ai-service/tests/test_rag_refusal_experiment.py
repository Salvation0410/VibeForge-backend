"""拒答诊断工具的纯离线安全契约；不初始化真实模型或检索依赖。"""
from copy import deepcopy
import hashlib
import json

import pytest

from ai_service.orchestration.rag_refusal_experiment import (
    DATASET, PROMPTS, calibration_queue, execute, load_diagnostic, parse_answer,
    planned_calls, summarize,
)
from ai_service.prompts.customer_service import (
    CUSTOMER_SERVICE_REFUSAL_SYSTEM_PROMPT, CUSTOMER_SERVICE_SYSTEM_PROMPT,
)


@pytest.fixture
def diagnostic(tmp_path):
    # 仿真历史文件只复用已提交的公共上下文，CI不依赖忽略目录或真实调用产物。
    data = json.loads(DATASET.read_text(encoding='utf8'))
    historical = {'datasetSha256': data['sourceDatasetSha256'], 'cases': [
        {'id': value['sourceCaseId'], 'arm': value['sourceArm'], 'status': 'ok',
         'contexts': value['contexts'], 'answered': False, 'answer': '', 'citedChunkIds': []}
        for value in data['contextSets'].values()]}
    source = tmp_path / 'snapshot.json'
    source.write_text(json.dumps(historical, ensure_ascii=False), encoding='utf8')
    data['sourceReportSha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    return data, historical, path, source


def test_candidate_does_not_change_production_default_or_implicitly_add_e1():
    assert CUSTOMER_SERVICE_SYSTEM_PROMPT == CUSTOMER_SERVICE_REFUSAL_SYSTEM_PROMPT
    assert PROMPTS['baseline_r1'] == CUSTOMER_SERVICE_SYSTEM_PROMPT
    assert PROMPTS['candidate_r2'] != PROMPTS['baseline_r1']
    assert 'Do not reinterpret such an imperative' in PROMPTS['candidate_r2']
    assert 'Quoted commands, hypothetical examples, negated commands' in PROMPTS['candidate_r2']
    assert 'smallest sufficient set' not in PROMPTS['candidate_r2']


def test_pairs_are_exactly_eight_frozen_refusals_and_public_counterparts(diagnostic):
    data, _, path, source = diagnostic
    loaded, _ = load_diagnostic(path, snapshot=source)
    assert loaded == data
    assert sum(not e['expectedAnswerable'] for e in loaded['entries']) == 8
    assert planned_calls(data, 3, 96) == 96
    assert len(loaded['coverage']) == 6


@pytest.mark.parametrize('damage', ['context', 'source', 'question', 'pair', 'quote', 'answerable'])
def test_damaged_context_source_or_pair_fails_before_any_call(diagnostic, damage):
    data, _, path, source = diagnostic
    if damage == 'context':
        next(iter(data['contextSets'].values()))['contexts'][0]['content'] += '补全证据'
    elif damage == 'source':
        source.write_text(source.read_text(encoding='utf8') + ' ', encoding='utf8')
    elif damage == 'question':
        data['entries'][0]['question'] = '替换冻结安全问题'
    elif damage == 'pair':
        data['entries'][1]['contextSetId'] = 'historical-026'
    elif damage == 'quote':
        data['entries'][1]['requiredFacts'][0]['evidence'][0]['quote'] = '并不存在的原文'
    else:
        data['entries'][0]['expectedAnswerable'] = 0
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    with pytest.raises(ValueError, match='RAG_EVAL_'):
        load_diagnostic(path, snapshot=source)


@pytest.mark.parametrize('rounds,maximum', [(0, 96), (4, 96), (True, 96), (3, 95), (3, 97)])
def test_budget_is_hard_bounded_to_96(diagnostic, rounds, maximum):
    with pytest.raises(ValueError):
        planned_calls(diagnostic[0], rounds, maximum)


@pytest.mark.parametrize('payload', [
    {'answered': False, 'answer': '不能执行', 'citedChunkIds': []},
    {'answered': False, 'answer': '', 'citedChunkIds': ['a']},
    {'answered': True, 'answer': '不能执行', 'citedChunkIds': ['missing']},
    {'answered': True, 'answer': '', 'citedChunkIds': ['a']},
    {'answered': True, 'answer': '说明', 'citedChunkIds': ['a', 'a']},
    {'answered': 0, 'answer': '', 'citedChunkIds': []},
])
def test_malformed_or_unattributed_answer_is_error_not_refusal(payload):
    with pytest.raises(ValueError, match='ANSWER_INVALID'):
        parse_answer(json.dumps(payload), [{'chunkId': 'a'}])


def test_negative_public_answer_is_valid_but_not_automatically_semantically_correct(diagnostic):
    value = parse_answer('{"answered":true,"answer":"不能部署他人应用","citedChunkIds":["a"]}',
                         [{'chunkId': 'a'}])
    assert value['answered'] is True
    with pytest.raises(ValueError, match='FINISH'):
        parse_answer('{"answered":false,"answer":"","citedChunkIds":[]}', [], 'length')
    summary = summarize(diagnostic[0], [], 3)['baseline_r1']
    assert summary['publicSemanticCorrectness'] is None
    assert summary['missing'] == 48 and summary['refusalContractPassed'] == 0


@pytest.mark.asyncio
async def test_calls_are_pre_reserved_and_failures_never_refunded_or_retried(diagnostic, tmp_path):
    data, historical, _, _ = diagnostic
    output = tmp_path / 'run'
    count = 0

    async def invoke(system, user):
        nonlocal count
        count += 1
        ledger = [json.loads(line) for line in (output / 'calls.jsonl').read_text(encoding='utf8').splitlines()]
        assert len([r for r in ledger if r['type'] == 'reserved']) == count
        assert json.loads((output / 'report.json').read_text(encoding='utf8'))['callsReserved']['chat'] == count
        raise RuntimeError('SENSITIVE_PROVIDER_ERROR_MUST_NOT_BE_SAVED')

    with pytest.raises(ValueError, match='REPEATED_CALL_FAILURE'):
        await execute(data, historical, output, rounds=1, max_chat=32, invoke=invoke,
                      config={}, prompt_max_bytes=16384)
    assert count == 3
    report = json.loads((output / 'report.json').read_text(encoding='utf8'))
    assert report['callsReserved']['chat'] == 3
    assert report['callsReserved']['embedding'] == 0
    assert report['status'] == 'failed-or-interrupted'
    assert report['metrics']['baseline_r1']['refusalContractPassed'] == 0
    assert 'SENSITIVE_PROVIDER_ERROR' not in (output / 'report.json').read_text(encoding='utf8')
    with pytest.raises(FileExistsError):
        await execute(data, historical, output, rounds=1, max_chat=32, invoke=invoke,
                      config={}, prompt_max_bytes=16384)
    assert count == 3


@pytest.mark.asyncio
async def test_reservation_disk_failure_prevents_network_send(diagnostic, tmp_path, monkeypatch):
    from ai_service.orchestration.rag_refusal_experiment import CallBudget
    calls = []

    def fail_reserve(self, kind, key):
        raise OSError('disk failure')

    async def invoke(system, user):
        calls.append(user)

    monkeypatch.setattr(CallBudget, 'reserve', fail_reserve)
    with pytest.raises(OSError):
        await execute(diagnostic[0], diagnostic[1], tmp_path / 'disk-failed', rounds=1,
                      max_chat=32, invoke=invoke, config={}, prompt_max_bytes=16384)
    assert calls == []


@pytest.mark.asyncio
async def test_full_fake_run_keeps_identical_contexts_and_records_every_expected_case(diagnostic, tmp_path):
    data, historical, _, _ = diagnostic
    async def invoke(system, user):
        return {'content': '{"answered":false,"answer":"","citedChunkIds":[]}',
                'finishReason': 'stop', 'usage': {'input_tokens': 2, 'output_tokens': 1}}
    report = await execute(data, historical, tmp_path / 'ok', rounds=3, max_chat=96,
                           invoke=invoke, config={}, prompt_max_bytes=16384)
    assert report['status'] == 'completed' and report['callsReserved']['chat'] == 96
    assert len(report['cases']) == 96
    assert len({(r['id'], r['arm'], r['round']) for r in report['cases']}) == 96
    for arm in PROMPTS:
        metrics = report['metrics'][arm]
        assert metrics['refusalContractPassed'] == 24
        assert metrics['publicFalseRefusals'] == 24
        assert metrics['errors'] == metrics['missing'] == 0
    for entry in data['entries']:
        expected = data['contextSets'][entry['contextSetId']]['contexts']
        assert all(r['contexts'] == expected for r in report['cases'] if r['id'] == entry['id'])


def test_calibration_keeps_known_bad_cases_annotation_disputes_and_missing_judgments(diagnostic):
    data, historical, _, _ = diagnostic
    cases = deepcopy(historical['cases'])
    for oldid in data['calibrationFocus']['historicalCaseIds']:
        cases.append({'id': oldid, 'arm': 'two_chunks', 'contexts': [], 'answered': True, 'answer': '同义释义'})
    queue = calibration_queue(data, {'cases': cases}, [])
    assert set(data['calibrationFocus']['historicalCaseIds']) <= {r['id'] for r in queue}
    assert all(r['humanVerdict'] is None and r['priorModelJudge'] is None for r in queue)
    assert next(r for r in queue if r['id'] == 'rag-v2-007')['annotationDispute']
    assert next(r for r in queue if r['id'] == 'rag-v2-032')['annotationDispute']
