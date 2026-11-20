"""答案单元实验只使用合成材料；不读取真实模型配置。"""
from copy import deepcopy
import json

import pytest

from ai_service.orchestration import rag_answer_unit_evaluation as unit


TEXT = '# 文档\n\n## 甲\n\n甲正文。\n\n常见问法：“甲？”\n\n## 中间\n\n中间原文。\n\n## 乙\n\n乙正文。\n'
GROUPS = [dict(title='合并', headings=['甲', '乙'])]


def chunk(key, content, locator='文档 / 甲', version=2):
    return dict(chunk_id=key, document_id='doc', document_version=version,
                chunk_index=0, content=content, source_name='doc.md', source_locator=locator)


def test_merge_preserves_bodies_and_first_position():
    result, provenance = unit.merge_sections(TEXT, GROUPS)
    assert result.index('## 合并') < result.index('## 中间')
    assert '甲\n\n甲正文。\n\n常见问法：“甲？”\n\n乙\n\n乙正文。\n' in result
    assert provenance[0]['sourceHeadings'] == ['甲', '乙']
    assert unit.merge_sections(TEXT, [])[0] == TEXT


@pytest.mark.parametrize('text,groups', [
    (TEXT, [dict(title='合并', headings=['甲', '未知'])]),
    (TEXT + '\n## 甲\n重复', GROUPS),
    (TEXT, GROUPS + [dict(title='另一个', headings=['乙', '中间'])]),
    (TEXT.replace('## 甲', '### 甲'), GROUPS),
    (TEXT, [dict(title='中间', headings=['甲', '乙'])]),
    (TEXT, [dict(title='合并', headings=['甲', '乙'], secret=True)]),
])
def test_merge_rejects_ambiguous_structure(text, groups):
    with pytest.raises(ValueError):
        unit.merge_sections(text, groups)


def test_mapping_requires_same_document_locator_and_whole_content():
    old = [chunk('doc:2:0', '甲正文。')]
    new = [chunk('doc:3:0', '甲\n甲正文。\n乙正文。', '文档 / 合并', 3)]
    provenance = {'doc': [dict(targetHeading='合并', sourceHeadings=['甲', '乙'])]}
    assert unit.map_chunks(old, new, provenance) == {'doc:2:0': 'doc:3:0'}
    new[0]['document_id'] = 'other'
    with pytest.raises(ValueError):
        unit.map_chunks(old, new, provenance)


def test_projection_deduplicates_without_backfill_and_never_emits_scores():
    new = [chunk('doc:3:0', '合并正文', version=3)]
    before = [dict(chunkId='old1'), dict(chunkId='old2')]
    result = unit.project_contexts('问题', before, {'old1': 'doc:3:0', 'old2': 'doc:3:0'}, new)
    assert len(result['mappedBeforeBudget']) == len(result['after']) == 1
    assert result['deduplicatedCount'] == 1
    assert 'score' not in str(result).lower()


def test_projection_records_budget_truncation():
    new = [chunk(f'doc:3:{i}', '字' * 1100, version=3) for i in range(3)]
    result = unit.project_contexts('问题', [dict(chunkId=f'o{i}') for i in range(3)],
        {f'o{i}': f'doc:3:{i}' for i in range(3)}, new)
    assert result['characterTruncated']
    assert sum(len(c['content']) for c in result['after']) <= 3000


def test_final_context_identity_and_content_are_exact():
    old = [chunk('doc:2:0', '甲\n\n正文')]
    record = unit.record(old[0])
    record['content'] = unit.clean(record['content'])
    unit.validate_contexts([record], old)
    for field, value in [('documentVersion', 3), ('locator', '其他'), ('content', '甲')]:
        bad = deepcopy(record)
        bad[field] = value
        with pytest.raises(ValueError):
            unit.validate_contexts([bad], old)


def test_nonpublic_fact_is_not_observed():
    facts = [dict(factId='f', basisType='evaluation-protocol', evidence=[])]
    assert unit.observe_facts(facts, [], [], {}) == []


def test_existing_output_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        unit.validate_output(tmp_path, tmp_path)


def test_network_is_disabled():
    import socket
    with unit.block_network() as audit:
        with pytest.raises(ValueError):
            socket.create_connection(('example.com', 443))
        assert audit['attempts'] == 1


def synthetic_corpus(tmp_path, monkeypatch, *, oversized=False):
    corpus = tmp_path/'source'
    (corpus/'documents').mkdir(parents=True)
    documents = []
    for identity, groups in unit.FROZEN_GROUPS.items():
        path = corpus/'documents'/f'{identity}.md'
        text = '# 合成文档\n\n'
        for index, heading in enumerate(groups[0]['headings']):
            body = f'合成来源{index}。' + ('长' * 600 if oversized else '')
            text += f'## {heading}\n\n{body}\n\n常见问法：“合成{index}？”\n\n'
        path.write_bytes(text.encode('utf8'))
        documents.append(dict(documentId=identity, candidateSha256=unit.sha(path),
            sourceMetadata=dict(id=identity, path=f'documents/{identity}.md')))
    manifest = dict(schemaVersion='rag-qa-candidate-manifest/v1', candidateVersion='qa-linked-20261120-v1',
                    documents=documents)
    unit.write_json(corpus/'manifest.json', manifest)
    digest = unit.sha(corpus/'manifest.json')
    monkeypatch.setattr(unit, 'SOURCE_HASH', digest)
    rules = dict(schemaVersion='rag-answer-unit-rules/v1', candidateVersion=unit.VERSION,
        sourceManifestSha256=digest, humanReview='pending',
        documents=[dict(documentId=k, groups=deepcopy(v)) for k, v in unit.FROZEN_GROUPS.items()])
    return corpus, rules


def test_build_uses_formal_chunks_without_settings_or_model(tmp_path, monkeypatch):
    from ai_service import config
    import httpx

    def denied(*args, **kwargs):
        pytest.fail('不得初始化配置或HTTP客户端')

    monkeypatch.setattr(config.Settings, '__init__', denied)
    monkeypatch.setattr(httpx.Client, '__init__', denied)
    monkeypatch.setattr(httpx.AsyncClient, '__init__', denied)
    corpus, rules = synthetic_corpus(tmp_path, monkeypatch)
    manifest, old, new = unit.build_candidate(corpus, rules, tmp_path/'candidate',
                                             expected_documents=3, expected_chunks=8)
    assert (len(old), len(new)) == (8, 3)
    assert len(manifest['oldToNew']) == 8
    assert all(c['document_version'] == 3 for c in new)
    assert set(manifest['oldToNew'].values()) == {c['chunk_id'] for c in new}
    assert all(c['content'] in next(n['content'] for n in new
        if n['chunk_id'] == manifest['oldToNew'][c['chunk_id']]) for c in old)


def test_build_rejects_oversized_merge_without_expanding_budget(tmp_path, monkeypatch):
    corpus, rules = synthetic_corpus(tmp_path, monkeypatch, oversized=True)
    with pytest.raises(ValueError, match='GROUP_EXCEEDS_SINGLE_CHUNK'):
        unit.build_candidate(corpus, rules, tmp_path/'candidate', expected_documents=3, expected_chunks=8)
    assert (tmp_path/'candidate').exists()


@pytest.mark.parametrize('drift', ['manifest-hash', 'document-hash', 'identity', 'rules'])
def test_build_rejects_hash_schema_and_identity_drift(tmp_path, monkeypatch, drift):
    corpus, rules = synthetic_corpus(tmp_path, monkeypatch)
    if drift == 'manifest-hash':
        with (corpus/'manifest.json').open('a') as stream:
            stream.write(' ')
    elif drift == 'document-hash':
        with next((corpus/'documents').iterdir()).open('a') as stream:
            stream.write('新增正文')
    elif drift == 'rules':
        rules['documents'][0]['groups'][0]['title'] = '未经批准的新标题'
    else:
        manifest = unit.read_json(corpus/'manifest.json')
        manifest['documents'][0]['sourceMetadata']['id'] = 'wrong'
        (corpus/'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
        monkeypatch.setattr(unit, 'SOURCE_HASH', unit.sha(corpus/'manifest.json'))
        rules['sourceManifestSha256'] = unit.SOURCE_HASH
    with pytest.raises(ValueError):
        unit.build_candidate(corpus, rules, tmp_path/'candidate', expected_documents=3, expected_chunks=8)


def test_linked_output_is_rejected(tmp_path, monkeypatch):
    original = type(tmp_path).is_symlink
    target = tmp_path/'linked'
    monkeypatch.setattr(type(tmp_path), 'is_symlink', lambda p: p == target or original(p))
    with pytest.raises(ValueError):
        unit.validate_output(target/'new', tmp_path)


def comparison_fixture():
    old = [chunk('doc:2:0', '甲正文。'), chunk('doc:2:1', '乙正文。', '文档 / 乙')]
    new = [chunk('doc:3:0', '甲\n甲正文。\n乙\n乙正文。', '文档 / 合并', 3)]
    contexts = [unit.record(c) for c in old]
    arm = dict(contexts=contexts, stages=dict(finalContext=contexts))
    comparison = dict(schemaVersion='rag-qa-linked-cap-selection/v1', sourceStudyId='qa-linked-20261120-v1',
        cases=[dict(id='q1', question='问题', arms={a: deepcopy(arm) for a in unit.ARMS})])
    rubric = dict(entries=[dict(id='q1', question='问题', requiredFacts=[
        dict(factId='f1', basisType='public-document', evidence=[dict(documentId='doc', quote='甲正文。')]),
        dict(factId='f2', basisType='evaluation-protocol', evidence=[])])])
    return comparison, rubric, old, new, {'doc:2:0': 'doc:3:0', 'doc:2:1': 'doc:3:0'}


def test_projection_observes_facts_after_mapping_and_aggregates_from_cases():
    comparison, rubric, old, new, mapping = comparison_fixture()
    result = unit.evaluate(comparison, rubric, old, new, mapping, expected_cases=1, expected_facts=1)
    assert result['liveRetrievalMetrics'] is None
    assert result['usage'] == dict(chat=0, embedding=0, bge=0, index=0, productionWrites=0)
    assert len(result['cases'][0]['facts']) == 1
    assert len(result['cases'][0]['unobservedNonPublicFacts']) == 1
    summary = unit.summarize(result)
    assert summary['arms']['cap1']['deduplicatedContexts'] == 1
    assert summary['arms']['cap2']['afterWholeQuoteAvailable'] == 1


@pytest.mark.parametrize('drift', ['duplicate', 'missing', 'question', 'unknown-id', 'version', 'locator', 'content'])
def test_projection_rejects_incomplete_or_drifted_comparison(drift):
    comparison, rubric, old, new, mapping = comparison_fixture()
    if drift == 'duplicate':
        comparison['cases'].append(deepcopy(comparison['cases'][0]))
    elif drift == 'missing':
        comparison['cases'] = []
    elif drift == 'question':
        comparison['cases'][0]['question'] = '不同问题'
    elif drift == 'unknown-id':
        comparison['cases'][0]['id'] = '未知'
    else:
        field = {'version': 'documentVersion', 'locator': 'locator', 'content': 'content'}[drift]
        comparison['cases'][0]['arms']['cap1']['contexts'][0][field] = 3 if drift == 'version' else '漂移'
    with pytest.raises(ValueError):
        unit.evaluate(comparison, rubric, old, new, mapping, expected_cases=1, expected_facts=1)


def test_duplicate_fact_identity_is_rejected():
    comparison, rubric, old, new, mapping = comparison_fixture()
    rubric['entries'][0]['requiredFacts'].append(deepcopy(rubric['entries'][0]['requiredFacts'][0]))
    with pytest.raises(ValueError, match='FACT_IDENTITY'):
        unit.evaluate(comparison, rubric, old, new, mapping, expected_cases=1, expected_facts=2)


def test_unmodified_document_bytes_are_identical(tmp_path, monkeypatch):
    corpus, rules = synthetic_corpus(tmp_path, monkeypatch)
    unchanged = corpus/'documents/unchanged.md'
    original = '# 原样\r\n\r\n## 原节\r\n\r\n原正文。\r\n'.encode('utf8')
    unchanged.write_bytes(original)
    manifest = unit.read_json(corpus/'manifest.json')
    manifest['documents'].append(dict(documentId='unchanged', candidateSha256=unit.sha(unchanged),
        sourceMetadata=dict(id='unchanged', path='documents/unchanged.md')))
    (corpus/'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    monkeypatch.setattr(unit, 'SOURCE_HASH', unit.sha(corpus/'manifest.json'))
    rules['sourceManifestSha256'] = unit.SOURCE_HASH
    result, old, new = unit.build_candidate(corpus, rules, tmp_path/'candidate',
                                           expected_documents=4, expected_chunks=9)
    assert (tmp_path/'candidate/documents/unchanged.md').read_bytes() == original
    assert result['documents'][-1]['changed'] is False
