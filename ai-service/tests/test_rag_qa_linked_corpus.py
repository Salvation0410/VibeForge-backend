"""用合成语料验证来源边界，不用历史成绩调规则。"""
from copy import deepcopy
import hashlib
import json

import pytest

from ai_service.orchestration import rag_qa_linked_corpus as subject


def sample(tmp_path):
    root = tmp_path / 'source'
    (root / 'documents').mkdir(parents=True)
    body = '# 手册【当前功能】\n\n## 创建【当前功能】\n\n先创建记录，再等待生成。记录创建不等于完成。\n\n## 常见问法\n\n“怎么开始”“能查余额吗”。其他说明见 02。\n'
    (root / 'documents/d.md').write_text(body, encoding='utf8')
    manifest = dict(documents=[dict(id='d', path='documents/d.md', audience='user', version=1, sources=['backend:example'])])
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    links = dict(schemaVersion='rag-qa-links/v1', candidateVersion='qa-linked-20261120-v1',
        sourceManifestSha256=subject.sha(root / 'manifest.json'), reviewerType='agent', humanReview='pending',
        documents=[dict(documentId='d', sourceSha256=subject.sha(root / 'documents/d.md'), links=[
            dict(question='怎么开始', targets=['创建【当前功能】'], status='linked', reason='说明创建'),
            dict(question='能查余额吗', targets=[], status='unresolved', reason='没有说明'),
        ])])
    return root, links


def test_build_retains_source_and_unresolved_question(tmp_path):
    root, links = sample(tmp_path)
    result = subject.build(root, links)
    doc = result['documents'][0]
    assert '先创建记录，再等待生成。记录创建不等于完成。' in doc['text']
    assert '常见问法：“怎么开始”' in doc['text']
    assert '## 常见问法\n\n“能查余额吗”。' in doc['text']
    assert doc['navigationText'] == '其他说明见 02。'
    assert doc['sourceMetadata']['audience'] == 'user'
    assert doc['unresolvedQuestions'] == ['能查余额吗']
    assert doc['text'].endswith('\n') and not doc['text'].endswith('\n\n')
    assert result['summary']['linkedQuestions'] == 1
    assert result['summary']['unresolvedQuestions'] == 1
    assert result['summary']['liveMetrics'] is None


@pytest.mark.parametrize('mutation', [
    lambda x: x.update(sourceManifestSha256='0'*64),
    lambda x: x['documents'][0].update(sourceSha256='0'*64),
    lambda x: x['documents'].append(deepcopy(x['documents'][0])),
    lambda x: x['documents'][0]['links'].pop(),
    lambda x: x['documents'][0]['links'].append(deepcopy(x['documents'][0]['links'][0])),
    lambda x: x['documents'][0]['links'][0].update(question='伪造问法'),
    lambda x: x['documents'][0]['links'][0].update(targets=['其他文档 / 创建']),
    lambda x: x['documents'][0]['links'][0].update(targets=['常见问法']),
    lambda x: x['documents'][0]['links'][0].update(status='unresolved'),
    lambda x: x['documents'][0]['links'][0].update(extra='不可信字段'),
    lambda x: x.update(humanReview='passed'),
])
def test_invalid_mapping_rejected(tmp_path, mutation):
    root, links = sample(tmp_path)
    mutation(links)
    with pytest.raises(ValueError):
        subject.build(root, links)


def test_reject_question_separated_from_complete_answer(tmp_path):
    root, links = sample(tmp_path)
    with pytest.raises(ValueError, match='COLOCATION'):
        subject.build(root, links, chunk_size=25, overlap=0)


def test_no_question_section_is_byte_preserving(tmp_path):
    root, links = sample(tmp_path)
    path = root / 'documents/d.md'
    path.write_bytes('# FAQ\r\n\r\n## 问答\r\n\r\n先登录。\r\n'.encode())
    links['documents'][0].update(sourceSha256=subject.sha(path), links=[])
    result = subject.build(root, links)
    assert result['documents'][0]['text'].encode() == path.read_bytes()


def test_path_escape_rejected(tmp_path):
    root, links = sample(tmp_path)
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['documents'][0]['path'] = '../outside.md'
    (root / 'manifest.json').write_text(json.dumps(manifest))
    links['sourceManifestSha256'] = subject.sha(root/'manifest.json')
    with pytest.raises(ValueError):
        subject.build(root, links)


def test_fact_availability_uses_document_identity(tmp_path):
    root, links = sample(tmp_path)
    result = subject.build(root, links)
    rubric = dict(entries=[dict(id='q1', requiredFacts=[
        dict(factId='f1', basisType='public-document', evidence=[dict(documentId='d', quote='记录创建不等于完成。')]),
        dict(factId='f2', basisType='public-document', evidence=[dict(documentId='other', quote='记录创建不等于完成。')]),
    ])])
    facts = subject.observe_facts(result, rubric)
    assert facts['beforeAvailable'] == facts['afterAvailable'] == 1
    assert facts['cases'][0]['facts'][1]['after'] is False


def test_output_is_new_directory_only(tmp_path, monkeypatch):
    root, links = sample(tmp_path)
    rules = tmp_path / 'rules.json'
    rules.write_text(json.dumps(links))
    monkeypatch.setattr(subject, 'OUTPUT_ROOT', tmp_path / 'runs')
    output = tmp_path / 'runs/run1'
    subject.run(root, rules, output)
    before = (output / 'manifest.json').read_bytes()
    with pytest.raises(ValueError):
        subject.run(root, rules, output)
    assert (output / 'manifest.json').read_bytes() == before


def test_duplicate_json_key_rejected(tmp_path):
    path = tmp_path/'bad.json'
    path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):
        subject.read_json(path)


def test_question_text_is_not_new_answer_evidence(tmp_path):
    root, links = sample(tmp_path)
    result = subject.build(root, links)
    rubric = dict(entries=[dict(id='q1', requiredFacts=[dict(factId='f1',
        basisType='public-document', evidence=[dict(documentId='d', quote='怎么开始')])])])
    facts = subject.observe_facts(result, rubric)
    assert facts['beforeAvailable'] == facts['afterAvailable'] == 0
