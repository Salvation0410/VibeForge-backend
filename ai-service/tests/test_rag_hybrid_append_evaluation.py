"""第30轮独立追加选择的合成回归，不调用供应商。"""
from copy import deepcopy
from pathlib import Path
import pytest
from ai_service.orchestration import rag_hybrid_append_evaluation as subject
from test_rag_qa_linked_cap_evaluation import fixture


def inputs(content=None, question='如何使用？'):
    _, rubric, candidates, retrieved, ranked = fixture(content=content, question=question)
    baseline = subject.select_arm(question, retrieved, ranked, 1)
    frozen = dict(cases=[dict(id='q', question=question, arms=dict(cap1=baseline, cap2={}))])
    rubric['entries'][0]['requiredFacts'].append(dict(factId='gain', basisType='public-document',
        evidence=[dict(documentId='a', quote='证据1。')]))
    return frozen, rubric, candidates, retrieved, ranked


def evaluate(frozen, rubric, candidates):
    return subject.evaluate(frozen, rubric, candidates, expected_cases=1, expected_facts=2)


def test_append_preserves_three_document_prefix_and_adds_one():
    frozen, rubric, candidates, retrieved, ranked = inputs()
    result = evaluate(frozen, rubric, candidates)
    case = result['cases'][0]
    assert [c['chunkId'] for c in case['arms']['append1']['stages']['selected']] == ['chunk0', 'chunk2', 'chunk3', 'chunk1']
    assert case['change']['baselineSelectedPrefixPreserved']
    assert case['arms']['append1']['append']['addedChunkId'] == 'chunk1'
    assert case['change']['baselineFinalContextsPreserved']


def test_underfilled_baseline_only_adds_one_and_no_eligible():
    _, _, _, retrieved, ranked = inputs()
    arm = subject.select_append('问题', retrieved[:3], ranked[:3])
    assert len(arm['stages']['selected']) == 3
    assert arm['append']['baselineSelectedCount'] == 2
    arm = subject.select_append('问题', retrieved[2:], ranked[2:])
    assert arm['append']['reason'] == 'no_eligible_candidate'
    assert len(arm['stages']['selected']) == 2


@pytest.mark.parametrize('field', ['contexts', 'stages', 'selectionDrops', 'budgets', 'documentDiversity', 'byteTruncated', 'characterTruncated'])
def test_all_baseline_fields_reproduced(field):
    frozen, rubric, candidates, *_ = inputs()
    frozen['cases'][0]['arms']['cap1'][field] = None
    with pytest.raises(ValueError):
        evaluate(frozen, rubric, candidates)


def test_duplicate_questions_and_facts_rejected():
    frozen, rubric, candidates, *_ = inputs()
    rubric['entries'].append(deepcopy(rubric['entries'][0]))
    with pytest.raises(ValueError, match='CASE_'):
        evaluate(frozen, rubric, candidates)
    frozen, rubric, candidates, *_ = inputs()
    rubric['entries'][0]['requiredFacts'][1]['factId'] = 'f'
    with pytest.raises(ValueError, match='FACT_'):
        evaluate(frozen, rubric, candidates)


def test_quote_whitespace_normalized_in_all_stages():
    frozen, rubric, candidates, *_ = inputs(content='首句。\n\n第二句。\t末句。')
    for fact in rubric['entries'][0]['requiredFacts']:
        fact['evidence'] = [dict(documentId='a', quote='首句。 第二句。 末句。')]
    result = evaluate(frozen, rubric, candidates)
    for arm in subject.ARMS:
        assert all(v['anyWholeQuoteSurvived'] for v in result['cases'][0]['facts'][0]['arms'][arm]['stages'].values())


def test_integrity_detects_drift_and_paths(tmp_path):
    path = tmp_path / 'input'
    path.write_text('冻结', encoding='utf8')
    integrity = dict(unchanged=True, fileCount=1, sha256={str(path): subject.sha(path)})
    protected = subject.validate_integrity(integrity, tmp_path, expected_count=1)
    path.write_text('漂移', encoding='utf8')
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.verify_protected(protected)
    integrity['sha256'] = {str(tmp_path.parent / 'outside'): '0'*64}
    with pytest.raises(ValueError, match='PATH_INVALID'):
        subject.validate_integrity(integrity, tmp_path, expected_count=1)


def test_actual_dependency_paths_exist_and_are_bound():
    paths = subject.dependency_paths(subject.ROOT)
    assert paths
    assert all(path.is_file() for path in paths)
    assert Path(subject.__file__).resolve() in [path.resolve() for path in paths]
    assert any(path.name == 'test_rag_hybrid_append_evaluation.py' for path in paths)
    assert any(path.name == 'rag_hybrid_cap_evaluation.py' for path in paths)


def test_fourth_context_enters_prompt_without_displacing_sources():
    frozen, rubric, candidates, *_ = inputs()
    case = evaluate(frozen, rubric, candidates)['cases'][0]
    assert len(case['arms']['append1']['contexts']) == 4
    assert case['change']['gainedFactIds'] == ['gain']
    assert case['change']['lostFactIds'] == []
    assert case['change']['documentDiversityDelta'] == 0


@pytest.mark.parametrize('count', [0, 1, 2, 3])
@pytest.mark.parametrize('content', [None, '正文'*1000, '首句。\n\t末句。'])
def test_experimental_character_adapter_equals_production_for_three_or_fewer(count, content):
    _, _, _, _, ranked = inputs(content=content)
    assert subject.bounded_append_contexts(ranked[:count]) == subject._bounded_contexts(ranked[:count])


def test_character_budget_exhaustion_drops_appended_chunk():
    frozen, rubric, candidates, *_ = inputs(content='正文'*1000)
    case = evaluate(frozen, rubric, candidates)['cases'][0]
    arm = case['arms']['append1']
    assert arm['budgets']['characterBudgetChars'] == 3000
    assert len(arm['contexts']) == 3
    assert case['change']['baselineFinalContextsPreserved']
    assert arm['append']['addedChunkId'] == 'chunk1'
    assert case['stageTransitions']['append1']['characterBudget']['droppedChunkIds'] == ['chunk1']


def test_prompt_overhead_can_shorten_original_contexts():
    frozen, rubric, candidates, retrieved, ranked = inputs(question='问'*5300)
    # 使用不同正文，避免独立内容去重掩盖真正的字节预算边界。
    from dataclasses import replace
    retrieved = [replace(c, content=str(i)+'证据'*100) for i, c in enumerate(retrieved)]
    ranked = [replace(r, chunk=c) for r, c in zip(ranked, retrieved)]
    baseline = subject.select_arm('问'*5300, retrieved, ranked, 1)
    arm = subject.select_append('问'*5300, retrieved, ranked)
    assert arm['byteTruncated']
    assert arm['contexts'][:len(baseline['contexts'])] != baseline['contexts']
    assert arm['budgets']['promptBytes'] <= 16384
    assert subject.stage_transitions(arm)['finalContext']['truncatedChunkIds']


def test_required_facts_only_read_after_every_selection(monkeypatch):
    frozen, rubric, candidates, *_ = inputs()
    calls = []
    original = subject.select_append
    def wrapped(*args):
        result = original(*args)
        calls.append('done')
        return result
    monkeypatch.setattr(subject, 'select_append', wrapped)
    class Guarded(dict):
        def __getitem__(self, key):
            if key == 'requiredFacts':
                assert calls == ['done']
            return super().__getitem__(key)
    rubric['entries'][0] = Guarded(rubric['entries'][0])
    evaluate(frozen, rubric, candidates)


def test_archive_expansion_is_exact_and_rejects_changed_body():
    frozen, _, candidates, *_ = inputs()
    chunks = {c['chunk_id']: dict(chunkId=c['chunk_id'], documentId=c['document_id'], documentVersion=c['document_version'], locator=c['source_locator'], content=c['content']) for c in candidates}
    archive = dict(schemaVersion='rag-hybrid-cap-validation-bundle/v1', chunksById=chunks, comparison=deepcopy(frozen))
    subject.validate_archive(archive, frozen, candidates)
    archive['comparison']['cases'][0]['question'] = '改写'
    with pytest.raises(ValueError, match='ARCHIVE_COMPARISON_DRIFT'):
        subject.validate_archive(archive, frozen, candidates)


def test_network_guard_and_settings_are_unused(monkeypatch):
    import socket
    from ai_service.config import Settings
    monkeypatch.setattr(Settings, '__init__', lambda *a, **k: pytest.fail('不得读取配置'))
    with subject.block_network() as audit:
        evaluate(*inputs()[:3])
        with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
            socket.create_connection(('localhost', 80))
    assert audit['attempts'] == 1


def synthetic_run_files(tmp_path, monkeypatch):
    frozen, rubric, candidates, *_ = inputs()
    frozen['summary'] = dict(arms=dict(cap2=dict(wholeQuoteSurvived=1)))
    product = tmp_path / 'doc/knowledge/product'
    archive_path = product / 'evaluation/results/hybrid-cap-selection-20261120-v1.json'
    comparison_path = tmp_path / 'tmp/rag-hybrid-cap-20261120/run-final/comparison.json'
    rubric_path = product / 'evaluation/rubric-20261118-v1.json'
    module_path = tmp_path / 'ai-service/src/ai_service/orchestration/rag_hybrid_append_evaluation.py'
    module_path.parent.mkdir(parents=True)
    module_path.write_text('冻结实验源码', encoding='utf8')
    paths = subject.dependency_paths(tmp_path)
    for path in [archive_path, comparison_path, rubric_path, *paths]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in paths:
        path.write_text('冻结依赖', encoding='utf8')
    subject.write_json(rubric_path, rubric)
    protected = {str(rubric_path): subject.sha(rubric_path)}
    for index in range(54):
        path = tmp_path / f'input{index}.bin'
        path.write_bytes(b'not-json-do-not-parse')
        protected[str(path)] = subject.sha(path)
    integrity = dict(unchanged=True, fileCount=55, sha256=protected)
    chunks = {c['chunk_id']: dict(chunkId=c['chunk_id'], documentId=c['document_id'], documentVersion=c['document_version'], locator=c['source_locator'], content=c['content']) for c in candidates}
    archive = dict(schemaVersion='rag-hybrid-cap-validation-bundle/v1', chunksById=chunks, comparison=deepcopy(frozen), inputIntegrity=integrity)
    subject.write_json(archive_path, archive)
    subject.write_json(comparison_path, frozen)
    subject.write_json(comparison_path.with_name('input-integrity.json'), integrity)
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    monkeypatch.setattr(subject, 'PRODUCT', product)
    monkeypatch.setattr(subject, 'ARCHIVE_SHA256', subject.sha(archive_path))
    monkeypatch.setattr(subject, 'COMPARISON_SHA256', subject.sha(comparison_path))
    monkeypatch.setattr(subject, 'load_candidates', lambda *a: candidates)
    original = subject.evaluate
    monkeypatch.setattr(subject, 'evaluate', lambda *a: original(*a, expected_cases=1, expected_facts=2))
    return archive_path, comparison_path, candidates


def test_run_persists_audit_bindings_and_detects_source_mutation(tmp_path, monkeypatch):
    _, _, candidates = synthetic_run_files(tmp_path, monkeypatch)
    output = tmp_path / 'tmp/new'
    result = subject.run(output)
    assert result['networkAudit'] == dict(enabled=True, attempts=0)
    assert result['bindings']['originalBindingCount'] == 55
    assert subject.read_json(output / 'input-integrity.json')['unchanged']
    summary = subject.read_json(output / 'summary.json')
    assert summary['bindings'] == result['bindings']
    assert summary['networkAudit'] == result['networkAudit']
    assert subject.read_json(output / 'reviews-template.json')['status'] == 'pending'
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW'):
        subject.run(output)
    monkeypatch.setattr(subject, 'load_candidates', lambda *a: ((tmp_path / 'input0.bin').write_bytes(b'drift'), candidates)[1])
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.run(tmp_path / 'tmp/drift')
    assert not (tmp_path / 'tmp/drift').exists()


@pytest.mark.parametrize('target', ['archive', 'comparison', 'integrity', 'dependency'])
def test_frozen_input_and_dependency_fail_closed(tmp_path, monkeypatch, target):
    archive, comparison, _ = synthetic_run_files(tmp_path, monkeypatch)
    if target == 'archive':
        archive.write_text('{}', encoding='utf8')
    elif target == 'comparison':
        comparison.write_text('{}', encoding='utf8')
    elif target == 'integrity':
        comparison.with_name('input-integrity.json').write_text('{}', encoding='utf8')
    else:
        # 不mock绑定器：缺失的真正规范路径必须在输出前阻断。
        (tmp_path / 'docs/superpowers/specs/2026-11-20-rag-hybrid-append-round30-design.md').unlink()
    with pytest.raises((ValueError, OSError)):
        subject.run(tmp_path / 'tmp/new')
    assert not (tmp_path / 'tmp/new').exists()
