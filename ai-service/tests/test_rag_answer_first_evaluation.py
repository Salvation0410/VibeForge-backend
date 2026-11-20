"""冻结规则与离线回放边界，不读取真实历史成绩。"""
from copy import deepcopy
import hashlib
import json
import socket

import pytest

from ai_service.orchestration import rag_answer_first_evaluation as subject


def chunk(key, doc, content, locator='说明 / 正文'):
    return dict(chunkId=key, documentId=doc, documentVersion=1,
                locator=locator, content=content)


def row_fixture():
    chunks = [chunk('q', 'd1', '“怎么使用”“如何开始”。', '说明 / 常见问法'),
              chunk('a', 'd1', '先创建应用。'), chunk('b', 'd1', '随后生成。'),
              chunk('c', 'd2', '其他说明。'), chunk('d', 'd3', '第三份说明。')]
    trace = []
    for i, c in enumerate(chunks):
        trace.append({k: c[k] for k in ('chunkId', 'documentId', 'documentVersion')} |
                     dict(rank=i + 1, length=len(c['content']), score=1 - i / 10,
                          scoreSource='reranker', kept=True))
    return dict(id='case', stages={'thresholded': chunks},
                trace={'thresholded': trace}, contexts=[chunks[i] for i in (0, 3, 4)])


@pytest.mark.parametrize('body', [
    '“怎么使用”“如何开始”。',
    '“怎么使用”\n“如何开始”。相关说明：平台见01；生成详见02。',
    '“是否支持”“能不能修改” 平台见01',
    '“必须登录吗”“必须填写标题吗”',
    '“怎么使用”“如何开始”。相关说明： 平台 见 07 ； 生成 详见 02 。',
])
def test_frozen_positive(body):
    assert subject.is_question_list(chunk('q', 'd', body, '说明 / 常见问法'))


@pytest.mark.parametrize('body,locator', [
    ('“怎么使用”', '常见问法'), ('“怎么使用”“操作入口”', '常见问法'),
    ('“怎么使用”“如何开始”。先点击创建。', '常见问法'),
    ('“怎么使用：点击按钮”“如何开始”', '常见问法'),
    ('“怎么使用”“如何开始”', 'FAQ'),
    ('“怎么使用”“如何开始”', '常见问法 / 回答'),
    ('“怎么\n使用”“如何开始”', '常见问法'),
    ('“怎么使用”“如何开始” 相关说明：平台见1', '常见问法'),
    ('“怎么使用”“如何开始”' * 11, '常见问法'),
    ('“必须登录”“必须填写标题”', '常见问法'),
    ('“怎么使用”“必须填写标题”', '常见问法'),
])
def test_mixed_unknown_and_outside_grammar_unchanged(body, locator):
    assert not subject.is_question_list(chunk('q', 'd', body, locator))


def test_partition_scores_identity_and_pair_differences():
    row = row_fixture()
    before = deepcopy(row)
    result = subject.compare_case(row, '如何使用？')
    assert row == before
    assert result['arms']['A0']['contexts'] == row['contexts']
    assert [c['chunkId'] for c in result['arms']['A1']['selected']] == ['q', 'a', 'c']
    assert [c['chunkId'] for c in result['arms']['A2']['selected']] == ['a', 'b', 'c']
    assert result['pairs']['A1_A2']['addedChunkIds'] == ['b']
    assert result['pairs']['A1_A2']['removedChunkIds'] == ['q']
    assert [c['score'] for c in result['candidates']] == [1, .9, .8, .7, .6]


def test_baseline_drift_fails_before_comparison():
    row = row_fixture()
    row['contexts'][0] = row['contexts'][0] | {'content': 'changed'}
    with pytest.raises(ValueError, match='BASELINE_DRIFT'):
        subject.compare_case(row, 'question')


def test_order_only_change_remains_observable():
    # 未增删片段也可能改变模型读取次序，不能把它算作完全无变化。
    row = row_fixture()
    row['stages']['thresholded'] = row['stages']['thresholded'][:3]
    row['trace']['thresholded'] = row['trace']['thresholded'][:3]
    for i, (item, meta) in enumerate(zip(row['stages']['thresholded'], row['trace']['thresholded'])):
        item['documentId'] = meta['documentId'] = f'd{i}'
    row['contexts'] = deepcopy(row['stages']['thresholded'])
    result = subject.compare_case(row, 'question')
    pair = result['pairs']['A1_A2']
    assert pair['orderChanged'] is True
    assert pair['addedChunkIds'] == pair['removedChunkIds'] == []


@pytest.mark.parametrize('score', [float('nan'), float('inf'), True, '0.5'])
def test_invalid_score_rejected(score):
    row = row_fixture()
    row['trace']['thresholded'][0]['score'] = score
    with pytest.raises(ValueError, match='TRACE_INVALID'):
        subject.compare_case(row, 'question')


def test_budget_keeps_formal_limits():
    row = row_fixture()
    for c in row['stages']['thresholded']:
        c['content'] = '长' * 2000
    for m in row['trace']['thresholded']:
        m['length'] = 2000
    row['contexts'] = [row['stages']['thresholded'][i] | {'content': '长' * n}
                       for i, n in [(0, 1200), (3, 1200), (4, 600)]]
    result = subject.compare_case(row, 'question')
    for arm in result['arms'].values():
        assert arm['budgets']['finalChars'] == 3000
        assert arm['budgets']['promptBytes'] <= 16384
        assert len(arm['contexts']) == 3
        assert all(len(c['content']) <= 1200 for c in arm['contexts'])


def test_output_and_bound_inputs_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    existing = tmp_path / 'tmp/existing'
    existing.mkdir(parents=True)
    for output in (existing, tmp_path / 'outside', tmp_path / 'tmp'):
        with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW_UNDER_TMP'):
            subject.validate_output(output)
    source = tmp_path / 'bad.json'
    source.write_text('{"secret":"raw-body","secret":2}')
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        subject.load_bound(source, hashlib.sha256(source.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='INPUT_HASH_OR_SIZE_INVALID'):
        subject.load_bound(source, '0' * 64)


def test_network_guard_records_and_blocks():
    with subject.block_network() as audit:
        with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
            socket.create_connection(('127.0.0.1', 1))
    assert audit == {'enabled': True, 'attempts': 1}


def test_production_does_not_import_offline_module():
    for path in (subject.ROOT / 'ai-service/src/ai_service').rglob('*.py'):
        if path.name != 'rag_answer_first_evaluation.py':
            assert 'import rag_answer_first_evaluation' not in path.read_text(encoding='utf8')


def report_fixture():
    """完整合成180行，保留正式analyze核验路径而不借用历史成绩。"""
    rubric = {'entries': []}
    results = []
    for index in range(90):
        key = f'case{index}'
        facts = [dict(factId=f'{key}-{n}', basisType='public-document',
                      evidence=[dict(documentId='d1', quote='先创建应用。')])
                 for n in range(2 if index < 40 else 1)]
        rubric['entries'].append(dict(id=key, question='如何使用？', requiredFacts=facts))
        base = row_fixture()
        base.update(id=key, status='not_run', sources=[])
        chunks = base['stages']['thresholded']
        ranked = deepcopy(base['trace']['thresholded'])
        for meta in ranked:
            meta.pop('kept')
        base['trace'].update(schemaVersion='customer-service-rag-trace/v1',
            configFingerprint=deepcopy(subject.TRACE_CONFIG),
            retrieved=deepcopy(ranked), filtered=deepcopy(base['trace']['thresholded']),
            reranked=deepcopy(ranked), selected=[m | {'kept': m['chunkId'] in {'q', 'c', 'd'}} for m in ranked])
        base['stages'].update(retrieved=deepcopy(chunks), reranked=deepcopy(chunks),
                              selected=deepcopy(base['contexts']))
        for stage in ('characterBudget', 'byteBudget'):
            base['stages'][stage] = deepcopy(base['contexts'])
            base['trace'][stage] = [m | {'kept': True, 'afterChars': m['length']}
                                   for m in ranked if m['chunkId'] in {'q', 'c', 'd'}]
        results.extend(deepcopy(base) | {'arm': arm} for arm in ('text', 'json_object'))
    report = dict(studyId='rag-json-answer-20261119-v1',
        schemaVersion='rag-json-answer-experiment/v1', results=results,
        config=deepcopy(subject.REPORT_CONFIG), rubricSha256=subject.fingerprint(rubric))
    mapping = {f'd{i}': (f'd{i}', 1) for i in range(1, 4)}
    return report, rubric, mapping


def test_full_synthetic_replay_without_network():
    report, rubric, mapping = report_fixture()
    with subject.block_network() as audit:
        result = subject.evaluate(report, rubric, mapping)
    assert audit['attempts'] == 0
    assert result['baselineExactMatches'] == 90
    assert result['publicFactCount'] == 130
    assert result['humanReview'] == 'pending'
    fact = result['cases'][0]['facts'][0]
    assert not fact['arms']['A0']['anyWholeQuoteSurvived']
    assert fact['arms']['A2']['anyWholeQuoteSurvived']


@pytest.mark.parametrize('mutation', [
    'arm_content', 'arm_score', 'score_stage', 'candidate_removed', 'config',
    'trace_config', 'unknown_stage', 'duplicate', 'tie_unstable', 'source', 'selected_score',
    'retrieved_score', 'retrieved_filtered_score',
])
def test_full_report_rejects_changed_or_incomplete_inputs(mutation):
    report, rubric, mapping = report_fixture()
    row = report['results'][0]
    if mutation == 'arm_content':
        report['results'][1]['stages']['thresholded'][0]['content'] = 'secret-body'
    elif mutation == 'arm_score':
        report['results'][1]['trace']['thresholded'][0]['score'] = .99
        report['results'][1]['trace']['reranked'][0]['score'] = .99
    elif mutation == 'score_stage':
        row['trace']['reranked'][0]['score'] = .99
    elif mutation == 'candidate_removed':
        # 从重排及之后所有阶段一致删除一个未选块，仍应发现filtered集合不完整。
        for stage in ('reranked', 'thresholded', 'selected'):
            row['trace'][stage] = [m for m in row['trace'][stage] if m['chunkId'] != 'b']
            for i, m in enumerate(row['trace'][stage]):
                m['rank'] = i + 1
        for stage in ('reranked', 'thresholded'):
            row['stages'][stage] = [c for c in row['stages'][stage] if c['chunkId'] != 'b']
    elif mutation == 'config':
        report['config']['rag_retrieval_top_k'] = 9
    elif mutation == 'trace_config':
        row['trace']['configFingerprint']['minRerankScore'] = 0
    elif mutation == 'unknown_stage':
        row['stages'].pop('selected')
    elif mutation == 'duplicate':
        report['results'][1] = deepcopy(row)
    elif mutation == 'tie_unstable':
        for stage in ('reranked', 'thresholded', 'selected'):
            for meta in row['trace'][stage]:
                meta['score'] = 1
        # 检索顺序颠倒，重排同分顺序未跟随。
        row['trace']['retrieved'][0:2] = reversed(row['trace']['retrieved'][0:2])
        row['stages']['retrieved'][0:2] = reversed(row['stages']['retrieved'][0:2])
    elif mutation == 'selected_score':
        row['trace']['selected'][0]['score'] = .99
    elif mutation == 'retrieved_score':
        report['results'][1]['trace']['retrieved'][0]['score'] = 999
    elif mutation == 'retrieved_filtered_score':
        report['results'][1]['trace']['retrieved'][0]['score'] = 999
        report['results'][1]['trace']['filtered'][0]['score'] = 999
    else:
        row['trace']['thresholded'][0]['scoreSource'] = 'distance'
    with pytest.raises(ValueError) as error:
        subject.evaluate(report, rubric, mapping)
    assert 'secret-body' not in str(error.value)


def test_run_refuses_input_mutation_before_mkdir(tmp_path, monkeypatch):
    report, rubric, mapping = report_fixture()
    rubric['freeze'] = dict(inputs=[], documents=[], sourceFiles=[])
    report['rubricSha256'] = subject.fingerprint(rubric)
    source, map_file, rubric_file = [tmp_path / name for name in ('report.json', 'map.json', 'rubric.json')]
    map_file.write_text('{}')
    rubric_file.write_text(json.dumps(rubric))
    report['sourceFilesSha256'] = {'document-map.json': subject._hash(map_file)}
    source.write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
    plan = tmp_path / 'docs/superpowers/plans/2026-11-20-rag-answer-first-selection.md'
    plan.parent.mkdir(parents=True)
    plan.write_text('frozen synthetic plan')
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    monkeypatch.setattr(subject, 'RUBRIC', rubric_file)
    monkeypatch.setattr(subject, 'load_rubric', lambda: rubric)
    monkeypatch.setattr(subject, 'load_document_mapping', lambda *args: mapping)
    evaluate = subject.evaluate
    def changed(*args):
        result = evaluate(*args)
        source.write_text('changed')
        return result
    monkeypatch.setattr(subject, 'evaluate', changed)
    output = tmp_path / 'tmp/new'
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.run(source, subject._hash(source), map_file, output)
    assert not output.exists()


def test_cli_error_does_not_leak_input(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, 'argv', ['evaluation', '--report', 'private-path',
        '--report-sha256', '0' * 64, '--document-map', 'private-map', '--output', 'private-output'])
    def bad(*args, **kwargs):
        raise ValueError('secret raw body and private-path')
    monkeypatch.setattr(subject, 'run', bad)
    with pytest.raises(SystemExit) as error:
        subject.main()
    assert error.value.code == 2
    assert capsys.readouterr().err == 'RAG_ANSWER_FIRST_INPUT_OR_OUTPUT_INVALID\n'


def test_run_writes_separate_pending_package_and_preserves_inputs(tmp_path, monkeypatch):
    report, rubric, mapping = report_fixture()
    rubric['freeze'] = dict(inputs=[], documents=[], sourceFiles=[])
    report['rubricSha256'] = subject.fingerprint(rubric)
    source, map_file, rubric_file = [tmp_path / name for name in ('report.json', 'map.json', 'rubric.json')]
    map_file.write_text('{}')
    rubric_file.write_text(json.dumps(rubric))
    report['sourceFilesSha256'] = {'document-map.json': subject._hash(map_file)}
    source.write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
    plan = tmp_path / 'docs/superpowers/plans/2026-11-20-rag-answer-first-selection.md'
    plan.parent.mkdir(parents=True)
    plan.write_text('frozen synthetic plan')
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    monkeypatch.setattr(subject, 'RUBRIC', rubric_file)
    monkeypatch.setattr(subject, 'load_rubric', lambda: rubric)
    monkeypatch.setattr(subject, 'load_document_mapping', lambda *args: mapping)
    before = {p: p.read_bytes() for p in (source, map_file, rubric_file)}
    output = tmp_path / 'tmp/new'
    with subject.block_network() as audit:
        subject.run(source, subject._hash(source), map_file, output, network_audit=audit)
    template = json.loads((output / 'reviews-template.json').read_text(encoding='utf8'))
    assert template['schemaVersion'] == 'rag-answer-first-evidence-review/v1'
    assert template['reviewer'] is None and template['status'] == 'pending'
    assert len(template['cases']) == 90
    summary = json.loads((output / 'summary.json').read_text(encoding='utf8'))
    assert summary['networkAudit'] == {'enabled': True, 'attempts': 0}
    assert summary['bindings']['ruleVersion'] == 'answer-first-question-list@2'
    assert all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW_UNDER_TMP'):
        subject.run(source, subject._hash(source), map_file, output)
