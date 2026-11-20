"""合成输入验证冻结边界，不读取真实实验结果或调用模型。"""
from copy import deepcopy
import socket

import pytest

from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration import rag_qa_linked_cap_evaluation as subject


def fixture(content=None, question='如何使用？'):
    candidates, retrieved = [], []
    for i, doc in enumerate(('a', 'a', 'b', 'c')):
        text = content if content is not None else f'证据{i}。'
        candidate = dict(chunk_id=f'chunk{i}', document_id=doc, document_version=2,
                         chunk_index=i, content=text, source_name=doc+'.md', source_locator='说明 / 操作')
        candidates.append(candidate)
        retrieved.append(RetrievedChunk(chunk_id=candidate['chunk_id'], document_id=doc,
            document_version=2, current_document_version=2, chunk_index=i, content=text,
            file_name=doc+'.md', file_type='MD', source_locator=candidate['source_locator'],
            content_hash=subject.fingerprint(text), distance=1-i/10, score=1-i/10))
    ranked = [RerankedChunk(c, 4-i) for i,c in enumerate(retrieved)]
    baseline = subject.select_arm(question, retrieved, ranked, 1)
    after = dict(id='q', arm='after', stages=baseline['stages'], selectionDrops=baseline['selectionDrops'])
    report = dict(schemaVersion='rag-qa-linked-live-report/v1', status='completed',
                  results=[dict(id='q', arm='before'), after])
    rubric = dict(entries=[dict(id='q', question=question, requiredFacts=[dict(factId='f',
        basisType='public-document', statement='第三文档的证据', evidence=[dict(documentId='c', quote='证据3。')])])])
    return report, rubric, candidates, retrieved, ranked


def evaluate(report, rubric, candidates):
    return subject.evaluate(report, rubric, candidates, expected_cases=1, expected_facts=1)


def test_cap2_evicts_third_document_and_reports_fact_loss():
    report, rubric, candidates, _, _ = fixture()
    untouched = deepcopy((report, rubric, candidates))
    result = evaluate(report, rubric, candidates)
    case = result['cases'][0]
    assert [c['chunkId'] for c in case['arms']['cap1']['contexts']] == ['chunk0', 'chunk2', 'chunk3']
    assert [c['chunkId'] for c in case['arms']['cap2']['contexts']] == ['chunk0', 'chunk1', 'chunk2']
    assert case['change']['removedChunkIds'] == ['chunk3']
    assert case['change']['documentDiversityDelta'] == -1
    assert case['facts'][0]['arms']['cap1']['anyWholeQuoteSurvived']
    assert not case['facts'][0]['arms']['cap2']['anyWholeQuoteSurvived']
    assert subject.summarize(result)['comparison']['quoteLosses'] == 1
    assert result['newProviderCalls'] == 0
    assert (report, rubric, candidates) == untouched


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown'])
def test_report_row_set_exact(mutation):
    report, rubric, candidates, _, _ = fixture()
    if mutation == 'missing':
        report['results'].pop(0)
    elif mutation == 'duplicate':
        report['results'].append(deepcopy(report['results'][1]))
    else:
        report['results'][0]['id'] = 'unknown'
    with pytest.raises(ValueError, match='ROW_'):
        evaluate(report, rubric, candidates)


@pytest.mark.parametrize('stage,field,value', [
    ('retrieved', 'content', '替换正文'), ('retrieved', 'documentVersion', 3),
    ('retrieved', 'retrievalScore', float('nan')), ('retrieved', 'retrievalScore', True),
    ('reranked', 'rerankScore', float('inf')), ('reranked', 'retrievalScore', 42),
    ('filtered', 'content', '替换过滤正文'), ('thresholded', 'rerankScore', 42),
    ('selected', 'content', '基线漂移'), ('characterBudget', 'content', '预算漂移'),
    ('finalContext', 'content', '最终漂移'),
])
def test_tampering_rejected(stage, field, value):
    report, rubric, candidates, _, _ = fixture()
    report['results'][1]['stages'][stage][0][field] = value
    with pytest.raises(ValueError):
        evaluate(report, rubric, candidates)


@pytest.mark.parametrize('stage', ['retrieved', 'reranked'])
def test_unstable_order_rejected(stage):
    report, rubric, candidates, _, _ = fixture()
    records = report['results'][1]['stages'][stage]
    score = 'retrievalScore' if stage == 'retrieved' else 'rerankScore'
    records[1][score] = records[0][score]
    records[:2] = records[1::-1]
    with pytest.raises(ValueError):
        evaluate(report, rubric, candidates)


def test_duplicate_rerank_and_drop_drift_rejected():
    report, rubric, candidates, _, _ = fixture()
    report['results'][1]['stages']['reranked'][1] = deepcopy(report['results'][1]['stages']['reranked'][0])
    with pytest.raises(ValueError):
        evaluate(report, rubric, candidates)
    report, rubric, candidates, _, _ = fixture()
    report['results'][1]['selectionDrops'] = {}
    with pytest.raises(ValueError, match='BASELINE_DRIFT'):
        evaluate(report, rubric, candidates)


def test_character_and_byte_budgets():
    _, _, _, retrieved, ranked = fixture(content='正文' * 1000)
    result = subject.select_arm('问题', retrieved, ranked, 2)
    assert result['characterTruncated']
    assert result['budgets']['characterBudgetChars'] == 3000
    assert [len(c['content']) for c in result['contexts']] == [1200, 1200, 600]
    result = subject.select_arm('问' * 3000, retrieved, ranked, 2)
    assert result['byteTruncated']
    assert result['budgets']['promptBytes'] <= 16384
    assert result['budgets']['finalChars'] < 3000
    with pytest.raises(ValueError, match='PROMPT_BUDGET_INVALID'):
        subject.select_arm('问' * 6000, retrieved, ranked, 2)


def test_fact_labels_do_not_affect_selection():
    report, rubric, candidates, _, _ = fixture()
    first = evaluate(report, rubric, candidates)
    rubric['entries'][0]['requiredFacts'][0]['evidence'] = [dict(documentId='a', quote='证据1。')]
    second = evaluate(report, rubric, candidates)
    assert first['cases'][0]['arms'] == second['cases'][0]['arms']


def test_binding_hash_and_file_drift(tmp_path):
    report, ledger, bound = [tmp_path/name for name in ('report.json', 'ledger.jsonl', 'input.txt')]
    for path in (report, ledger, bound):
        path.write_text('frozen', encoding='utf8')
    plan = dict(bindings={'input.txt': subject.sha(bound)})
    expected = dict(planSha256=subject.fingerprint(plan), reportSha256=subject.sha(report), ledgerSha256=subject.sha(ledger))
    assert subject.validate_bindings(plan, expected, report, ledger, tmp_path)
    for path in (report, ledger, bound):
        path.write_text('changed', encoding='utf8')
        with pytest.raises(ValueError):
            subject.validate_bindings(plan, expected, report, ledger, tmp_path)
        path.write_text('frozen', encoding='utf8')
    expected['planSha256'] = '0' * 64
    with pytest.raises(ValueError, match='SOURCE_HASH'):
        subject.validate_bindings(plan, expected, report, ledger, tmp_path)


def test_output_refuses_root_existing_escape_and_symlink(tmp_path):
    root = tmp_path / 'tmp'
    root.mkdir()
    fresh = root / 'new'
    assert subject.validate_output(fresh, root) == fresh
    for path in (root, tmp_path / 'escape', root / '..' / 'escape'):
        with pytest.raises(ValueError):
            subject.validate_output(path, root)
    fresh.mkdir()
    with pytest.raises(ValueError):
        subject.validate_output(fresh, root)
    link = root / 'linked'
    try:
        link.symlink_to(fresh, target_is_directory=True)
    except OSError:
        pytest.skip('当前Windows账户未授权创建符号链接')
    with pytest.raises(ValueError):
        subject.validate_output(link / 'new', root)


def test_network_guard_blocks_without_connecting():
    with subject.block_network() as audit:
        with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
            socket.create_connection(('localhost', 80))
    assert audit == dict(enabled=True, attempts=1)


def test_output_symlink_guard_without_platform_privilege(tmp_path, monkeypatch):
    root = tmp_path / 'tmp'
    link = root / 'linked'
    original = subject.Path.is_symlink
    monkeypatch.setattr(subject.Path, 'is_symlink', lambda p: p == link or original(p))
    with pytest.raises(ValueError, match='PATH_INVALID'):
        subject.validate_output(link / 'fresh', root)


def test_run_writes_new_package_and_preserves_inputs(tmp_path, monkeypatch):
    report, rubric, candidates, _, _ = fixture()
    root, product = tmp_path, tmp_path / 'doc/knowledge/product'
    corpus = product / f'experiments/{subject.VERSION}'
    live = root / 'tmp/rag-qa-linked-20261120'
    plan_doc = root / 'docs/superpowers/plans/2026-11-20-rag-qa-linked-cap-selection.md'
    module = root / 'ai-service/src/ai_service/orchestration/rag_qa_linked_cap_evaluation.py'
    for path in (corpus, live / 'live', plan_doc.parent, module.parent, product / 'evaluation/results'):
        path.mkdir(parents=True, exist_ok=True)
    module.write_text('离线模块', encoding='utf8')
    module.with_name('rag_answer_first_evaluation.py').write_text('网络阻断', encoding='utf8')
    plan_doc.write_text('冻结计划', encoding='utf8')
    subject.write_json(corpus / 'associations.json', {})
    subject.write_json(product / 'evaluation/rubric-20261118-v1.json', rubric)
    ledger = live / 'authorization.jsonl'
    ledger.write_text('冻结账本', encoding='utf8')
    plan = dict(config=subject.CONFIG, bindings={str(plan_doc.relative_to(root)): subject.sha(plan_doc)})
    subject.write_json(corpus / 'live-plan.json', plan)
    report['planSha256'] = subject.fingerprint(plan)
    subject.write_json(live / 'live/report.json', report)
    subject.write_json(product / 'evaluation/results/qa-linked-retrieval-20261120-v1.json',
        dict(planSha256=subject.fingerprint(plan), reportSha256=subject.sha(live / 'live/report.json'), ledgerSha256=subject.sha(ledger)))
    # 合成63候选保留正式run数量门槛，只有前四个进入检索结果。
    padded = candidates + [candidates[0] | dict(chunk_id=f'unused{i}') for i in range(59)]
    monkeypatch.setattr(subject, 'ROOT', root)
    monkeypatch.setattr(subject, 'PRODUCT', product)
    monkeypatch.setattr(subject, '__file__', str(module))
    monkeypatch.setattr(subject, 'load_candidates', lambda *args: padded)
    actual_evaluate = subject.evaluate
    monkeypatch.setattr(subject, 'evaluate', lambda *args, **kwargs:
        actual_evaluate(*args, **kwargs, expected_cases=1, expected_facts=1))
    output = root / 'tmp/new-cap'
    with subject.block_network() as audit:
        result = subject.run(output, network_audit=audit)
    assert result['baselineExactMatches'] == 1
    assert audit['attempts'] == 0
    assert subject.read_json(output / 'input-integrity.json')['unchanged']
    assert subject.read_json(output / 'reviews-template.json')['status'] == 'pending'
    assert subject.read_json(output / 'summary.json')['comparison']['quoteLosses'] == 1
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW'):
        subject.run(output)
    monkeypatch.setattr(subject, 'load_candidates', lambda *args: (ledger.write_text('篡改', encoding='utf8'), padded)[1])
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        subject.run(root / 'tmp/drift')
    assert not (root / 'tmp/drift').exists()



def candidate_files(tmp_path):
    corpus = tmp_path / 'candidate'
    (corpus / 'documents').mkdir(parents=True)
    path = corpus / 'documents/guide.md'
    path.write_text('# 平台指南\n\n## 正文\n\n这是冻结的候选正文。\n', encoding='utf8')
    manifest = dict(schemaVersion='rag-qa-candidate-manifest/v1', candidateVersion=subject.VERSION,
        documents=[dict(documentId='guide', sourceMetadata=dict(id='guide', path='documents/guide.md'),
                        candidateSha256=subject.sha(path))])
    subject.write_json(corpus / 'manifest.json', manifest)
    return corpus, manifest, path


def test_candidate_parse_split_never_initializes_settings(tmp_path, monkeypatch):
    from ai_service.config import Settings
    corpus, _, _ = candidate_files(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('离线候选重建不得初始化Settings')
    monkeypatch.setattr(Settings, '__init__', forbidden)
    chunks = subject.load_candidates(corpus, expected_documents=1, expected_chunks=1)
    assert chunks[0]['document_id'] == 'guide'
    assert chunks[0]['document_version'] == 2
    assert chunks[0]['source_name'] == 'guide.md'
    assert '冻结的候选正文' in chunks[0]['content']


@pytest.mark.parametrize('kind', ['hash', 'path', 'duplicate', 'identity', 'count'])
def test_candidate_manifest_rejects_tampering(tmp_path, kind):
    corpus, manifest, path = candidate_files(tmp_path)
    expected_documents = 1
    if kind == 'hash':
        path.write_text('篡改正文', encoding='utf8')
    elif kind == 'path':
        manifest['documents'][0]['sourceMetadata']['path'] = '../outside.md'
    elif kind == 'duplicate':
        manifest['documents'].append(deepcopy(manifest['documents'][0]))
        expected_documents = 2
    elif kind == 'identity':
        manifest['documents'][0]['sourceMetadata']['id'] = 'other'
    else:
        expected_documents = 2
    (corpus / 'manifest.json').unlink()
    subject.write_json(corpus / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='CANDIDATE_'):
        subject.load_candidates(corpus, expected_documents=expected_documents, expected_chunks=1)
