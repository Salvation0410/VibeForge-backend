"""第28轮使用合成候选验证配额变化与冻结输入边界。"""
from copy import deepcopy
import socket
import json

import pytest

from ai_service.orchestration import rag_hybrid_cap_evaluation as subject
from test_rag_qa_linked_cap_evaluation import fixture


def inputs():
    old, rubric, candidates, _, _ = fixture()
    baseline = subject.select_arm(rubric['entries'][0]['question'],
        *subject.reconstruct(old['results'][1], candidates), 1)
    rows = [dict(id='q', arm=arm, **deepcopy(baseline)) for arm in ('dense', 'hybrid')]
    report = dict(schemaVersion='rag-hybrid-live/v1', status='completed', results=rows)
    rubric['entries'][0]['requiredFacts'].append(dict(factId='gain', basisType='public-document',
        evidence=[dict(documentId='a', quote='证据1。')]))
    return report, rubric, candidates


def evaluate(report, rubric, candidates):
    return subject.evaluate(report, rubric, candidates, expected_cases=1, expected_facts=2)


def test_cap_gain_and_global_eviction_have_separate_stage_diagnostics():
    result = evaluate(*inputs())
    case = result['cases'][0]
    assert result['baselineExactMatches'] == 1
    assert result['validatedOriginalRows'] == 2
    assert case['change']['removedChunkIds'] == ['chunk3']
    assert case['change']['addedChunkIds'] == ['chunk1']
    loss, gain = case['facts']
    assert loss['change'] == 'loss'
    assert gain['change'] == 'gain'
    assert loss['arms']['cap2']['quoteFailure'] == 'selection_quote_missing'
    assert gain['arms']['cap1']['stages']['retrieved']['anyWholeQuoteSurvived']
    assert result['summary']['comparison']['quoteLosses'] == 1
    assert result['summary']['comparison']['quoteGains'] == 1


@pytest.mark.parametrize('arm', ['dense', 'hybrid'])
@pytest.mark.parametrize('field', ['contexts', 'budgets', 'selectionDrops', 'documentDiversity', 'characterTruncated'])
def test_all_original_row_selection_fields_are_exact(arm, field):
    report, rubric, candidates = inputs()
    report['results'][0 if arm == 'dense' else 1][field] = None
    with pytest.raises(ValueError, match='BASELINE_DRIFT'):
        evaluate(report, rubric, candidates)


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown', 'missing'])
def test_row_identity_closed(mutation):
    report, rubric, candidates = inputs()
    if mutation == 'duplicate':
        report['results'].append(deepcopy(report['results'][0]))
    elif mutation == 'unknown':
        report['results'][0]['id'] = 'other'
    else:
        report['results'].pop()
    with pytest.raises(ValueError, match='ROW_'):
        evaluate(report, rubric, candidates)


def test_duplicate_fact_ids_and_empty_quote_rejected():
    report, rubric, candidates = inputs()
    rubric['entries'][0]['requiredFacts'][1]['factId'] = 'f'
    with pytest.raises(ValueError, match='FACT_'):
        evaluate(report, rubric, candidates)
    report, rubric, candidates = inputs()
    rubric['entries'][0]['requiredFacts'][0]['evidence'][0]['quote'] = ' '
    with pytest.raises(ValueError, match='FACT_'):
        evaluate(report, rubric, candidates)


def test_archive_expansion_preserves_overrides_and_rejects_unknown():
    chunk = dict(chunkId='a', documentId='doc', documentVersion=2, locator='位置', content='完整正文')
    assert subject.expand_archive([dict(chunkRef='a', contentOverride='完整', retrievalScore=.1)], {'a': chunk}) == [chunk | dict(content='完整', retrievalScore=.1)]
    with pytest.raises(ValueError, match='ARCHIVE_'):
        subject.expand_archive(dict(chunkRef='absent'), {'a': chunk})


def test_schedule_and_ledger_exact():
    report, rubric, _ = inputs()
    plan = dict(schedule=subject.schedule_for(report, rubric['entries']))
    report.update(planSha256=subject.fingerprint(plan), reservedOperations=2)
    ledger = [dict(type='authorization', planSha256=subject.fingerprint(plan), bgeLimit=2,
        embeddingLimit=0, chatLimit=0, indexWrites=0)] + [dict(type='reserved', ordinal=i, **s) for i,s in enumerate(plan['schedule'])]
    subject.validate_schedule(report, rubric['entries'], plan, ledger)
    ledger[1]['inputSha256'] = 'wrong'
    with pytest.raises(ValueError, match='LEDGER_'):
        subject.validate_schedule(report, rubric['entries'], plan, ledger)


@pytest.mark.parametrize('stage,field,value', [
    ('retrieved', 'content', '篡改正文'), ('retrieved', 'locator', '错误位置'),
    ('retrieved', 'retrievalScore', float('nan')), ('retrieved', 'retrievalScore', True),
    ('reranked', 'rerankScore', float('inf')), ('reranked', 'retrievalScore', 99),
    ('filtered', 'content', '篡改'), ('thresholded', 'rerankScore', 99),
    ('selected', 'content', '篡改'), ('characterBudget', 'content', '篡改'),
    ('finalContext', 'content', '篡改')])
def test_stage_tampering_rejected(stage, field, value):
    report, rubric, candidates = inputs()
    report['results'][1]['stages'][stage][0][field] = value
    with pytest.raises(ValueError):
        evaluate(report, rubric, candidates)


@pytest.mark.parametrize('stage,score', [('retrieved', 'retrievalScore'), ('reranked', 'rerankScore')])
def test_score_ties_must_retain_original_order(stage, score):
    report, rubric, candidates = inputs()
    rows = report['results'][1]['stages'][stage]
    rows[1][score] = rows[0][score]
    rows[:2] = rows[1::-1]
    with pytest.raises(ValueError):
        evaluate(report, rubric, candidates)


def test_facts_read_only_after_both_arms_finish(monkeypatch):
    report, rubric, candidates = inputs()
    selections = []
    original = subject.select_arm
    monkeypatch.setattr(subject, 'select_arm', lambda *args: (selections.append(args[-1]), original(*args))[1])
    class GuardedEntry(dict):
        def __getitem__(self, key):
            if key == 'requiredFacts':
                assert selections == [1, 1, 2]
            return super().__getitem__(key)
    rubric['entries'][0] = GuardedEntry(rubric['entries'][0])
    evaluate(report, rubric, candidates)


def test_nonpublic_facts_excluded_and_labels_cannot_change_contexts():
    report, rubric, candidates = inputs()
    first = evaluate(report, rubric, candidates)
    rubric['entries'][0]['requiredFacts'].append(dict(factId='private', basisType='private-state'))
    rubric['entries'][0]['requiredFacts'][0]['evidence'][0]['quote'] = '不存在'
    second = evaluate(report, rubric, candidates)
    assert first['cases'][0]['arms'] == second['cases'][0]['arms']
    assert len(second['cases'][0]['facts']) == 2
    assert second['cases'][0]['facts'][0]['arms']['cap1']['quoteFailure'] == 'retrieval_quote_missing'


def test_character_and_prompt_budget_boundaries():
    _, _, _, retrieved, ranked = fixture(content='正文' * 1000)
    arm = subject.select_arm('问题', retrieved, ranked, 2)
    assert [len(c['content']) for c in arm['contexts']] == [1200, 1200, 600]
    assert arm['characterTruncated']
    arm = subject.select_arm('问' * 3000, retrieved, ranked, 2)
    assert arm['byteTruncated'] and arm['budgets']['promptBytes'] <= 16384
    with pytest.raises(ValueError, match='PROMPT_BUDGET_INVALID'):
        subject.select_arm('问' * 6000, retrieved, ranked, 2)


def test_multiline_quotes_survive_consistent_whitespace_normalization():
    _, rubric, candidates, retrieved, ranked = fixture(content='首句。\n\n第二句。\t末句。')
    baseline = subject.select_arm(rubric['entries'][0]['question'], retrieved, ranked, 1)
    report = dict(schemaVersion='rag-hybrid-live/v1', status='completed', results=[
        dict(id='q', arm=arm, **deepcopy(baseline)) for arm in ('dense', 'hybrid')])
    rubric['entries'][0]['requiredFacts'][0]['evidence'] = [dict(documentId='a', quote='首句。\n第二句。  末句。')]
    result = subject.evaluate(report, rubric, candidates, expected_cases=1, expected_facts=1)
    for arm in subject.ARMS:
        fact = result['cases'][0]['facts'][0]['arms'][arm]
        assert all(stage['anyWholeQuoteSurvived'] for stage in fact['stages'].values())
        assert fact['quoteFailure'] == 'survived'
        summary = result['summary']['arms'][arm]
        assert summary['totalPromptBytes'] == result['cases'][0]['arms'][arm]['budgets']['promptBytes']
        assert summary['totalFinalChars'] == result['cases'][0]['arms'][arm]['budgets']['finalChars']
        assert summary['quoteFailureCounts']['survived'] == 1
        assert sum(summary['quoteFailureCounts'].values()) == 1


def test_binding_drift_and_path_boundaries(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ('report', 'ledger', 'vector.json')]
    for path in paths:
        path.write_text('无需解析的冻结字节', encoding='utf8')
    plan = dict(bindings={'vector.json': subject.sha(paths[2])})
    expected = dict(planSha256=subject.fingerprint(plan), reportSha256=subject.sha(paths[0]), ledgerSha256=subject.sha(paths[1]))
    subject.validate_bindings(plan, expected, *paths[:2], tmp_path)
    paths[2].write_text('漂移', encoding='utf8')
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        subject.validate_bindings(plan, expected, *paths[:2], tmp_path)
    with pytest.raises(ValueError):
        subject.validate_output(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        subject.validate_output(tmp_path.parent / 'escape', tmp_path)
    original = subject.Path.is_symlink
    link = tmp_path / 'linked'
    monkeypatch.setattr(subject.Path, 'is_symlink', lambda p: p == link or original(p))
    with pytest.raises(ValueError, match='PATH_INVALID'):
        subject.validate_output(link / 'new', tmp_path)


def test_no_settings_initialization_and_network_is_blocked(monkeypatch):
    from ai_service.config import Settings
    monkeypatch.setattr(Settings, '__init__', lambda *args, **kwargs: pytest.fail('不得加载配置'))
    with subject.block_network() as audit:
        evaluate(*inputs())
        with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
            socket.create_connection(('localhost', 80))
    assert audit['attempts'] == 1


def archive_for(report, candidates, rubric):
    return dict(schemaVersion='rag-hybrid-validation-bundle/v1', chunksById={c['chunk_id']:
        dict(chunkId=c['chunk_id'], documentId=c['document_id'], documentVersion=c['document_version'],
             locator=c['source_locator'], content=c['content']) for c in candidates},
        comparison=dict(cases=[dict(id='q', question=rubric['entries'][0]['question'],
            arms={row['arm']: dict(deepcopy(row), facts={}) for row in report['results']})]))


def test_archive_checks_entire_original_row_and_all_candidate_text():
    report, rubric, candidates = inputs()
    archive = archive_for(report, candidates, rubric)
    subject.validate_archive(archive, report, candidates, rubric['entries'])
    archive['comparison']['cases'][0]['arms']['dense']['budgets']['promptBytes'] += 1
    with pytest.raises(ValueError, match='ARCHIVE_REPORT_DRIFT'):
        subject.validate_archive(archive, report, candidates, rubric['entries'])
    archive = archive_for(report, candidates, rubric)
    archive['chunksById']['chunk0']['content'] = '篡改'
    with pytest.raises(ValueError, match='ARCHIVE_CHUNKS'):
        subject.validate_archive(archive, report, candidates, rubric['entries'])


def test_run_persists_new_package_and_detects_input_mutation(tmp_path, monkeypatch):
    report, rubric, candidates = inputs()
    product = tmp_path / 'doc/knowledge/product'
    plan_path = product / 'experiments/hybrid-20261120-v1/live-plan.json'
    report_path = tmp_path / 'tmp/rag-hybrid-20261120/live/report.json'
    ledger_path = report_path.parent.parent / 'authorization.jsonl'
    rubric_path = product / 'evaluation/rubric-20261118-v1.json'
    archive_path = product / 'evaluation/results/hybrid-retrieval-20261120-v1.json'
    module_path = tmp_path / 'ai-service/src/ai_service/orchestration/rag_hybrid_cap_evaluation.py'
    extras = [module_path, module_path.with_name('rag_answer_first_evaluation.py'),
        tmp_path / 'docs/superpowers/plans/2026-11-20-rag-hybrid-cap-round28.md',
        tmp_path / 'docs/superpowers/specs/2026-11-20-rag-hybrid-cap-round28-design.md']
    for path in [plan_path, report_path, rubric_path, archive_path, *extras]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in extras:
        path.write_text('冻结实现', encoding='utf8')
    bindings = {}
    for index in range(47):
        path = tmp_path / f'input{index}.bin'
        path.write_bytes(b'not-json-or-vectors')
        bindings[path.name] = subject.sha(path)
    plan = dict(config=subject.CONFIG, bindings=bindings, schedule=subject.schedule_for(report, rubric['entries']))
    plan_hash = subject.fingerprint(plan)
    subject.write_json(plan_path, plan)
    report.update(planSha256=plan_hash, reservedOperations=2)
    subject.write_json(report_path, report)
    ledger = [dict(type='authorization', planSha256=plan_hash, bgeLimit=2,
        embeddingLimit=0, chatLimit=0, indexWrites=0)] + [dict(type='reserved', ordinal=i, **s) for i,s in enumerate(plan['schedule'])]
    ledger_path.write_text('\n'.join(json.dumps(row) for row in ledger), encoding='utf8')
    subject.write_json(rubric_path, rubric)
    archive = archive_for(report, candidates, rubric)
    archive['audit'] = dict(planCanonicalSha256=plan_hash, reportSha256=subject.sha(report_path), ledgerSha256=subject.sha(ledger_path))
    subject.write_json(archive_path, archive)
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    monkeypatch.setattr(subject, 'PRODUCT', product)
    monkeypatch.setattr(subject, '__file__', str(module_path))
    monkeypatch.setattr(subject, 'PLAN_SHA256', plan_hash)
    monkeypatch.setattr(subject, 'ARCHIVE_SHA256', subject.sha(archive_path))
    monkeypatch.setattr(subject, 'load_candidates', lambda *args: candidates)
    original = subject.evaluate
    monkeypatch.setattr(subject, 'evaluate', lambda *args, **kwargs: original(*args, **kwargs, expected_cases=1, expected_facts=2))
    output = tmp_path / 'tmp/new'
    with subject.block_network() as audit:
        result = subject.run(output, network_audit=audit)
    assert result['networkAudit'] == dict(enabled=True, attempts=0)
    assert subject.read_json(output / 'reviews-template.json')['status'] == 'pending'
    assert subject.read_json(output / 'input-integrity.json')['unchanged']
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW'):
        subject.run(output)
    monkeypatch.setattr(subject, 'load_candidates', lambda *args: ((tmp_path / 'input0.bin').write_bytes(b'drift'), candidates)[1])
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.run(tmp_path / 'tmp/drift')
    assert not (tmp_path / 'tmp/drift').exists()
