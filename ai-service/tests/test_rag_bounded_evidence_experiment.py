"""有界证据实验的快照、预算与单变量边界；Fake不会访问真实模型。"""
import hashlib
import json

import numpy as np
import pytest

from ai_service.orchestration import rag_bounded_evidence_experiment as experiment


def rows(count=24):
    return [{'chunkId': f'a:{i}', 'documentId': 'a', 'locator': f'章节{i}',
             'content': f'事实{i}', 'rerankScore': 1-i/100} for i in range(count)]


@pytest.mark.parametrize('cap', [0, 4, True, 1.0, -1])
def test_invalid_document_quota_cannot_increase_final_budget(cap):
    with pytest.raises(ValueError, match='DOCUMENT_CAP'):
        experiment.selection_trial('问题', rows(), per_document=cap, max_bytes=16384)


def test_three_same_document_chunks_keep_separate_identities_and_final_limit():
    source = rows()
    trial = experiment.selection_trial('问题', source, per_document=3, max_bytes=16384)
    assert [r['chunkId'] for r in trial['final']] == ['a:0', 'a:1', 'a:2']
    assert [r['content'] for r in trial['final']] == ['事实0', '事实1', '事实2']
    assert experiment.selection_trial('问题', source, per_document=1, max_bytes=16384)['final'] == source[:1]


def test_title_inputs_are_explicit_new_version_and_do_not_rewrite_citation_body():
    chunks = rows(2)
    data = {'entries': [{'id': 'q', 'question': '问题'}]}
    plain = experiment.input_manifest(data, chunks)
    title = experiment.input_manifest(data, chunks, title_path=True)
    assert plain['inputVersion'] == 'body/v1'
    assert title['requiresNewEmbeddings'] is True
    assert title['inputsSha256'] != plain['inputsSha256']
    assert title['embeddingInputs'][0] == '标题路径：章节0\n正文：事实0'
    assert chunks[0]['content'] == '事实0'
    assert title['embeddingInputs'][-1] == '问题'


@pytest.mark.parametrize('damage', ['nan', 'norm', 'hash', 'shape'])
def test_mixed_corrupt_or_renormalized_vectors_are_rejected(damage):
    matrix = np.eye(3, dtype=np.float64)
    expected = hashlib.sha256(matrix.tobytes()).hexdigest()
    if damage == 'nan': matrix[0, 0] = np.nan
    if damage == 'norm': matrix *= 2
    if damage == 'hash': expected = '0'*64
    if damage == 'shape': matrix = matrix[:2]
    with pytest.raises(ValueError, match='VECTOR_SNAPSHOT'):
        experiment.validated_vectors(matrix, rows=3, dimension=3, expected_sha=expected)


@pytest.mark.parametrize('damage', ['duplicate', 'identity', 'body', 'nan', 'order', 'boolean'])
def test_reranking_must_be_complete_finite_ordered_and_preserve_source(damage):
    source = rows(3)
    ranked = [dict(r) for r in source]
    if damage == 'duplicate': ranked[1] = ranked[0]
    if damage == 'identity': ranked[0]['chunkId'] = 'forged'
    if damage == 'body': ranked[0]['content'] += '追加gold'
    if damage == 'nan': ranked[0]['rerankScore'] = float('nan')
    if damage == 'order': ranked.reverse()
    if damage == 'boolean': ranked[0]['rerankScore'] = True
    with pytest.raises(ValueError, match='RERANK_'):
        experiment.validate_ranked(source, ranked)


def test_dense_candidates_use_vectors_only_stable_ties_and_top_budget():
    chunks = rows()
    matrix = np.eye(25)
    # 全部同分时按快照原顺序；不以gold或文档配额补候选。
    for top_k in (8, 16, 24):
        result = experiment.dense_candidates(chunks, matrix, 0, top_k=top_k)
        assert [r['chunkId'] for r in result] == [r['chunkId'] for r in chunks[:top_k]]
    with pytest.raises(ValueError):
        experiment.dense_candidates(chunks, matrix, 0, top_k=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('local', [False, True, 'unavailable'])
async def test_expanded_candidates_require_new_complete_rerank_and_fixed_final_budget(tmp_path, monkeypatch, local):
    from ai_service.models.reranker import LocalCrossEncoderReranker, RerankedChunk, RerankerError
    from ai_service.config import Settings

    chunks = rows()
    data = {'datasetVersion': 'test-diagnostic', 'entries': [
        {'id': 'q', 'groupId': 'g', 'question': '问题', 'expectedAnswerable': True,
         'requiredFacts': [{'id': 'f', 'evidence': [
             {'documentId': 'a', 'locator': '章节20', 'quote': '事实20'}]}]}]}
    matrix = np.eye(25)
    retrieved = experiment.dense_candidates(chunks, matrix, 0, top_k=8)
    original = {'vectorSnapshotSha256': 'fixed', 'config': {
        'promptMaxBytes': 16384, 'reranker': 'BAAI/bge-reranker-v2-m3', 'device': 'cuda'}}
    source = tmp_path/'source'
    source.mkdir()
    (source/'retrieval.jsonl').write_text('{}', encoding='utf8')
    monkeypatch.setattr(experiment, 'load_study', lambda _: (data, chunks))
    monkeypatch.setattr(experiment, 'load_snapshot', lambda *_: (
        original, matrix, {'q': {'retrieved': retrieved, 'reranked': retrieved}}))
    monkeypatch.setattr(experiment, 'safe_output', lambda path: path)
    calls = []

    class Fake:
        async def rerank(self, question, candidates, *, top_n):
            calls.append(len(candidates))
            return [RerankedChunk(c, 1-i/100) for i, c in enumerate(candidates)]

        async def close(self):
            calls.append('closed')

    async def create(_):
        if local == 'unavailable':
            raise RerankerError('CUSTOMER_SERVICE_RERANKER_UNAVAILABLE')
        return Fake()

    monkeypatch.setattr(LocalCrossEncoderReranker, 'create', create)
    monkeypatch.setattr('ai_service.config.Settings', lambda **_: Settings(
        _env_file=None, rag_reranker_model='BAAI/bge-reranker-v2-m3', rag_reranker_device='cuda'))
    report = await experiment.run(source, tmp_path/'output', local_rerank=local,
                                  prepare_title_inputs=True)
    assert report['newChatCalls'] == report['newEmbeddingCalls'] == 0
    expanded = [r for r in report['cases'] if r['arm'] in ('dense-top-16', 'dense-top-24')]
    assert report['titleExperiment'] == 'not-run-requires-new-embeddings'
    if local is True:
        assert calls == [8, 16, 24, 'closed']
        assert report['localRerankCalls'] == 3
        assert all(len(r['final']) <= 3 and len(r['final']) == 1 for r in expanded)
    else:
        assert calls == []
        assert all(r['status'] == 'final-not-tested-no-rerank' for r in expanded)
        assert all('final3' not in r['metrics'] for r in expanded)
        assert report['summary']['dense-top-24']['stages']['final3']['meanEvidenceCoverage'] is None
    saved = json.loads((tmp_path/'output/report.json').read_text(encoding='utf8'))
    assert saved['status'] == 'completed'


@pytest.mark.parametrize('damage', ['dataset', 'cases', 'retrieved', 'identity'])
def test_snapshot_binding_fails_before_reusing_ranked_results(tmp_path, damage):
    chunks = rows(8)
    data = {'entries': [{'id': 'q', 'question': '问题'}]}
    matrix = np.eye(9)
    retrieved = experiment.dense_candidates(chunks, matrix, 0, top_k=8)
    report = {'datasetSha256': experiment.fingerprint(data), 'expectedCaseIds': ['q'],
              'chunkCount': 8, 'config': {'retrievalTopK': 8, 'embeddingDimension': 9},
              'vectorSnapshotSha256': hashlib.sha256(matrix.tobytes()).hexdigest()}
    records = [{'id': 'q', 'retrieved': retrieved, 'reranked': [dict(r) for r in retrieved]}]
    if damage == 'dataset': report['datasetSha256'] = 'stale'
    if damage == 'cases': records *= 2
    if damage == 'retrieved': records[0]['retrieved'] = list(reversed(retrieved))
    if damage == 'identity': records[0]['reranked'][0]['documentId'] = 'other-generation'
    (tmp_path/'report.json').write_text(json.dumps(report), encoding='utf8')
    (tmp_path/'retrieval.jsonl').write_text('\n'.join(json.dumps(r) for r in records), encoding='utf8')
    np.savez_compressed(tmp_path/'vectors.npz', vectors=matrix)
    with pytest.raises(ValueError, match='RAG_EVAL_'):
        experiment.load_snapshot(tmp_path, data, chunks)
