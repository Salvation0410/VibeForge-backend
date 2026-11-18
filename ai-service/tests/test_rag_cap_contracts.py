"""构造完整隔离的 90 题来源，测试冻结链而不依赖本机私有 tmp。"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

import pytest

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk, IndexedDocument, MilvusKnowledgeStore
from ai_service.orchestration import rag_cap_contracts as contracts
from ai_service.orchestration.document_etl import parse_document, split_sections
from ai_service.orchestration.rag_rerank_contracts import PRODUCT, RUBRIC, read_json, fingerprint


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


@pytest.fixture
def frozen_source(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    cfg = Settings(_env_file=None, customer_service_rag_enabled=False,
        rag_publication_enabled=True, rag_embedding_dimension=2,
        rag_reranker_provider='local_cross_encoder', model_context_window_tokens=200000)
    # 只借用公开 rubric 结构生成隔离来源，不执行历史真实批次的源码字节 freeze。
    rubric = read_json(RUBRIC)
    for i, entry in enumerate(rubric['entries']):
        entry.update(id=f'q{i}', question=f'合成问题{i}')
    mapping = {'schemaVersion': 'product-knowledge-document-map/v1', 'documents': {}}
    rows, identities, receipt_docs = [], [], []
    for i, d in enumerate(rubric['freeze']['documents']):
        document_id = str(100 + i)
        mapping['documents'][d['documentId']] = {
            'documentId': document_id, 'documentVersion': d['version'], 'sha256': d['sha256']}
        identity = {'documentId': document_id, 'documentVersion': d['version'],
            'indexRevision': 1, 'activationRevision': 1, 'sha256': d['sha256'],
            'fileName': d['documentId'] + '.md', 'fileType': 'MD'}
        identities.append(identity)
        chunks = split_sections(parse_document(PRODUCT / d['path'], 'md'),
            document_id=document_id, document_version=d['version'],
            source_name=identity['fileName'], settings=cfg)
        receipt_docs.append({**identity, 'chunkCount': len(chunks)})
        identity_key = MilvusKnowledgeStore._identity(IndexedDocument(document_id, d['version'],
            '1', cfg.rag_embedding_model, identity['fileName'], 'md', d['sha256'], (), 1, 1))
        for c in chunks:
            rows.append({'recordType': 'chunk',
                'id': hashlib.sha256(f'{identity_key}\0{c.chunk_id}'.encode()).hexdigest(), 'chunkId': c.chunk_id,
                'documentId': document_id, 'documentVersion': d['version'],
                'chunkIndex': c.chunk_index, 'content': c.content, 'fileName': identity['fileName'],
                'fileType': 'md', 'sourceLocator': c.source_locator,
                'contentHash': hashlib.sha256(c.content.encode()).hexdigest(),
                'documentContentHash': d['sha256'], 'indexRevision': 1, 'activationRevision': 1,
                'etlVersion': '1', 'embeddingModelVersion': cfg.rag_embedding_model,
                'embeddingDimension': 2, 'schemaVersion': 1, 'revisionProtocol': 2, 'isActive': True,
                'embedding': [1.0, 0.0]})
    snapshot = {'enabled': True, 'epoch': 1, 'publicationId': 'bootstrap_' + 'a' * 32,
        'collectionName': 'frozen_collection', 'snapshotHash': 'a' * 64,
        'embeddingModelVersion': cfg.rag_embedding_model, 'embeddingDimension': 2, 'documents': identities}
    receipt = {'publicationId': snapshot['publicationId'], 'baseEpoch': 0,
        'operationId': snapshot['publicationId'], 'fence': 1,
        'candidateCollection': snapshot['collectionName'], 'snapshotHash': snapshot['snapshotHash'],
        'embeddingModelVersion': cfg.rag_embedding_model, 'embeddingDimension': 2, 'documents': receipt_docs}
    candidates = []
    # 两个同文档块确保 cap 唯一变量确实改变最终上下文。
    chosen = rows[:2] + [r for r in rows if r['documentId'] != rows[0]['documentId']][:6]
    for i, c in enumerate(chosen):
        candidates.append(asdict(RetrievedChunk(c['chunkId'], c['documentId'], c['documentVersion'],
            c['chunkIndex'], c['content'], c['fileName'], c['fileType'], c['sourceLocator'],
            c['contentHash'], i / 10, 1 - i / 10, True, c['documentVersion'],
            1, 1, c['documentContentHash'])))
    retrieval = {e['id']: {'retrieved': deepcopy(candidates), 'filtered': deepcopy(candidates),
        'ranked': [{'chunkId': c['chunk_id'], 'score': 10.0 - i} for i, c in enumerate(candidates)]}
        for e in rubric['entries']}
    vectors = [[1.0, i / 100] for i in range(90)]
    report = {'schemaVersion': 'rag-rerank-experiment/v1', 'studyId': 'rag-rerank-20261118-first-round-v1',
        'rubricSha256': fingerprint(rubric), 'mappingSha256': fingerprint(mapping),
        'corpusRowsSha256': fingerprint(rows), 'corpusChunks': 72,
        'queryVectorsSha256': fingerprint(dict(zip((e['id'] for e in rubric['entries']), vectors))),
        'retrieval': retrieval, 'retrievalFreezeSha256': fingerprint(retrieval),
        'snapshot': snapshot, 'receipt': receipt, 'config': contracts.settings_manifest(cfg)}
    for name, value in [('report.json', report), ('document-map.json', mapping),
                        ('publication-rows.json', rows), ('query-vectors.json', vectors),
                        ('retrieval-freeze.json', retrieval)]:
        write(source / name, value)
    replay = {'schemaVersion': 'rag-cap-evidence-replay/v1',
        'sourceReportFingerprint': fingerprint(report), 'rubricFingerprint': fingerprint(rubric), 'cases': []}
    replay_path = tmp_path / 'replay.json'
    write(replay_path, replay)
    monkeypatch.setattr(contracts, 'SOURCE', source)
    monkeypatch.setattr(contracts, 'REPLAY', replay_path)
    monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', fingerprint(report))
    monkeypatch.setattr(contracts, 'REPLAY_SHA256', fingerprint(replay))
    monkeypatch.setattr(contracts, 'load_rubric', lambda: deepcopy(rubric))
    return source, cfg, rubric, report, replay_path


def test_complete_loader_binds_id_to_vector_and_corpus(frozen_source):
    source, cfg, rubric, report, _ = frozen_source
    frozen = contracts.load_frozen_inputs(cfg)
    assert len(frozen.vectors) == 90 and frozen.vectors['q1'] == [1.0, .01]
    assert len(frozen.prepared) == 90
    assert frozen.source_report_sha256 == fingerprint(report)
    assert frozen.source_files['query-vectors.json'] == contracts.file_hash(source / 'query-vectors.json')


def test_isolated_fixture_builds_when_legacy_freeze_validation_is_forbidden(request, monkeypatch):
    from ai_service.orchestration import rag_rerank_contracts as legacy

    def forbidden(*args, **kwargs):
        pytest.fail('隔离 fixture 不得调用依赖历史源码字节换行的真实 freeze 校验')
    monkeypatch.setattr(legacy, 'load_rubric', forbidden)
    # 同时阻断历史实现已缓存的直接导入，防止函数别名掩盖边界回归。
    monkeypatch.setitem(globals(), 'load_rubric', forbidden)
    source, cfg, _, report, _ = request.getfixturevalue('frozen_source')
    frozen = contracts.load_frozen_inputs(cfg)
    assert len(frozen.prepared) == 90 and len(frozen.vectors) == 90
    assert frozen.source_report_sha256 == fingerprint(report)
    assert frozen.source_files['report.json'] == contracts.file_hash(source / 'report.json')


@pytest.mark.parametrize('damage', ['report', 'matrix-order', 'matrix-nan', 'mapping', 'rows',
                                  'retrieval-file', 'filtered', 'rank-order', 'identity', 'replay'])
def test_loader_rejects_broken_freeze_without_resources(frozen_source, monkeypatch, damage):
    source, cfg, _, report, replay_path = frozen_source
    name = 'report.json'
    if damage == 'report': report['corpusChunks'] = 999
    elif damage.startswith('matrix'):
        name = 'query-vectors.json'
        value = contracts.read_json(source / name)
        if damage == 'matrix-order': value[0], value[1] = value[1], value[0]
        else: value[0][0] = float('nan')
    elif damage == 'mapping':
        name = 'document-map.json'; value = contracts.read_json(source / name)
        next(iter(value['documents'].values()))['documentVersion'] = 2
    elif damage == 'rows':
        name = 'publication-rows.json'; value = contracts.read_json(source / name)
        value[0]['sourceLocator'] += '损坏'
    elif damage == 'replay':
        write(replay_path, {'changed': True})
        with pytest.raises(ValueError, match='RAG_CAP_'):
            contracts.load_frozen_inputs(cfg)
        return
    elif damage == 'retrieval-file':
        name = 'retrieval-freeze.json'; value = contracts.read_json(source / name)
        value['q0']['ranked'].reverse()
    else:
        # 同时更新输入自报哈希及测试 pin，证明深层身份/排序验证并非只靠哈希。
        if damage == 'filtered': report['retrieval']['q0']['filtered'].reverse()
        if damage == 'rank-order': report['retrieval']['q0']['ranked'].reverse()
        if damage == 'identity': report['retrieval']['q0']['retrieved'][0]['file_name'] = '冒名.md'
        report['retrievalFreezeSha256'] = fingerprint(report['retrieval'])
        write(source / 'retrieval-freeze.json', report['retrieval'])
        monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', fingerprint(report))
    write(source / name, report if name == 'report.json' else value)
    specific = {'filtered': 'FILTERED_CHANGED', 'rank-order': 'RANKED_CHANGED',
                'identity': 'RETRIEVAL_IDENTITY_CHANGED', 'matrix-order': 'QUERY_VECTOR_MAPPING_CHANGED'}
    with pytest.raises(ValueError, match='RAG_CAP_' + specific.get(damage, '')):
        contracts.load_frozen_inputs(cfg)


@pytest.mark.parametrize('damage', ['corpus-content', 'snapshot-identity', 'rank-missing', 'rank-duplicate'])
def test_deep_source_checks_reject_even_self_reported_rehashed_damage(frozen_source, monkeypatch, damage):
    source, cfg, _, report, _ = frozen_source
    if damage == 'corpus-content':
        rows = contracts.read_json(source / 'publication-rows.json')
        rows[0]['content'] += '伪造正文'
        rows[0]['contentHash'] = hashlib.sha256(rows[0]['content'].encode()).hexdigest()
        report['corpusRowsSha256'] = fingerprint(rows)
        write(source / 'publication-rows.json', rows)
    elif damage == 'snapshot-identity':
        report['snapshot']['documents'][0]['fileName'] = '伪造.md'
    else:
        ranked = report['retrieval']['q0']['ranked']
        if damage == 'rank-missing': ranked.pop()
        else: ranked[-1] = ranked[0]
        report['retrievalFreezeSha256'] = fingerprint(report['retrieval'])
        write(source / 'retrieval-freeze.json', report['retrieval'])
    monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', fingerprint(report))
    write(source / 'report.json', report)
    with pytest.raises(ValueError, match='RAG_CAP_'):
        contracts.load_frozen_inputs(cfg)


@pytest.mark.parametrize('field,value', [('chunkId', 'forged:1:0'), ('id', '0' * 64),
    ('fileName', '伪造.md'), ('indexRevision', 99), ('activationRevision', 99), ('fileType', 'txt')])
def test_every_canonical_chunk_identity_is_checked_outside_retrieval(
        frozen_source, monkeypatch, field, value):
    source, cfg, _, report, replay_path = frozen_source
    rows = contracts.read_json(source / 'publication-rows.json')
    target = rows[-1]
    retrieved_ids = {c['chunk_id'] for cached in report['retrieval'].values() for c in cached['retrieved']}
    assert target['chunkId'] not in retrieved_ids
    target[field] = value
    report['corpusRowsSha256'] = fingerprint(rows)
    report_pin = fingerprint(report)
    monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', report_pin)
    write(source / 'publication-rows.json', rows)
    write(source / 'report.json', report)
    # 重新绑定隔离 pin 和 replay，使负例只能由全语料身份核对拒绝。
    replay = contracts.read_json(replay_path)
    replay['sourceReportFingerprint'] = report_pin
    write(replay_path, replay)
    monkeypatch.setattr(contracts, 'REPLAY_SHA256', fingerprint(replay))
    with pytest.raises(ValueError, match='RAG_CAP_CANONICAL_CHUNK_IDENTITY_CHANGED'):
        contracts.load_frozen_inputs(cfg)
