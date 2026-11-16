"""路线B的只读证据实验；默认零模型调用，局部BGE评分须显式启用。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

from ai_service.orchestration.rag_evaluation_workflow import (
    DEFAULT_DATASET, ROOT, bounded_contexts, evidence_metrics, fingerprint,
    load_study, select_contexts,
)
from ai_service.orchestration.rag_snapshot_experiment import safe_output


def retrieval_inputs(chunks: list[dict], *, title_path: bool = False) -> list[str]:
    """标题实验改变真实模型输入；正文及引用身份始终保留原值。"""
    return [f"标题路径：{r['locator']}\n正文：{r['content']}" if title_path else r['content']
            for r in chunks]


def input_manifest(data: dict, chunks: list[dict], *, title_path: bool = False) -> dict:
    inputs = retrieval_inputs(chunks, title_path=title_path)
    questions = [e['question'] for e in data['entries']]
    return {'schemaVersion': 'rag-retrieval-inputs/v1',
            'inputVersion': 'title-path-body/v1' if title_path else 'body/v1',
            'datasetSha256': fingerprint(data), 'chunkIds': [r['chunkId'] for r in chunks],
            'caseIds': [e['id'] for e in data['entries']],
            'inputsSha256': fingerprint(inputs + questions),
            'embeddingInputs': inputs + questions,
            'requiresNewEmbeddings': title_path}


def validated_vectors(matrix, *, rows: int, dimension: int, expected_sha: str):
    import numpy as np

    # 哈希绑定原快照的顺序和数值，不归一化后掩盖损坏或混用标题向量。
    if (matrix.shape != (rows, dimension) or not np.isfinite(matrix).all()
            or not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-8)
            or hashlib.sha256(matrix.tobytes()).hexdigest() != expected_sha):
        raise ValueError('RAG_EVAL_VECTOR_SNAPSHOT_MISMATCH')
    return matrix


def dense_candidates(chunks: list[dict], matrix, case_index: int, *, top_k: int):
    import numpy as np

    if type(top_k) is not int or top_k not in (8, 16, 24):
        raise ValueError('RAG_EVAL_TOP_K_INVALID')
    scores = matrix[:len(chunks)] @ matrix[len(chunks) + case_index]
    order = np.argsort(-scores, kind='stable')[:top_k]
    return [{**chunks[int(i)], 'score': float(scores[int(i)])} for i in order]


def validate_ranked(retrieved: list[dict], ranked: list[dict]) -> None:
    expected = {r['chunkId']: r for r in retrieved}
    if (len(expected) != len(retrieved) or len(ranked) != len(retrieved)
            or {r['chunkId'] for r in ranked} != set(expected)):
        raise ValueError('RAG_EVAL_RERANK_IDENTITIES_INVALID')
    previous = math.inf
    for row in ranked:
        source = expected[row['chunkId']]
        score = row.get('rerankScore')
        if (any(row.get(key) != source.get(key)
                for key in ('chunkId', 'documentId', 'locator', 'content'))
                or type(score) not in (float, int) or not math.isfinite(score)
                or score > previous):
            raise ValueError('RAG_EVAL_RERANK_SNAPSHOT_INVALID')
        previous = score


def selection_trial(question: str, ranked: list[dict], *, per_document: int,
                    max_bytes: int) -> dict:
    # 此入口只有问题和检索结果，选择结束后才允许指标函数读取标注。
    if type(per_document) is not int or per_document not in (1, 2, 3):
        raise ValueError('RAG_EVAL_DOCUMENT_CAP_INVALID')
    selected = select_contexts(ranked, per_document=per_document, limit=3)
    return {'selected': selected, 'final': bounded_contexts(question, selected, max_bytes)}


def measured_case(entry, retrieved, ranked, chunks, *, cap, max_bytes):
    trial = selection_trial(entry['question'], ranked, per_document=cap, max_bytes=max_bytes)
    return {**trial, 'status': 'measured', 'metrics': {
        name: evidence_metrics(entry, rows, k=k, corpus_chunks=chunks)
        for name, rows, k in [('retrieved', retrieved, len(retrieved)),
                              ('reranked3', ranked, 3),
                              ('selected3', trial['selected'], 3), ('final3', trial['final'], 3)]}}


def summarize(data: dict, cases: list[dict]) -> dict:
    result = {}
    for arm in sorted({c['arm'] for c in cases}):
        rows = [c for c in cases if c['arm'] == arm]
        summary = {'expected': len(data['entries']), 'records': len(rows),
                   'measuredFinal': sum(c['status'] == 'measured' for c in rows), 'stages': {}}
        for stage in ('retrieved', 'reranked3', 'selected3', 'final3'):
            metrics = [c.get('metrics', {}).get(stage) for c in rows]
            valid = [m for m in metrics if m and m['applicable']]
            summary['stages'][stage] = {
                'measuredAnswerable': len(valid),
                'expectedAnswerable': sum(e['expectedAnswerable'] for e in data['entries']),
                'meanEvidenceCoverage': fmean(m['evidenceCoverage'] for m in valid) if valid else None,
                'completeEvidenceCases': sum(m['allRequiredEvidence'] for m in valid) if valid else None}
        result[arm] = summary
    return result


def load_snapshot(source: Path, data: dict, chunks: list[dict]):
    import numpy as np

    report = json.loads((source / 'report.json').read_text(encoding='utf8'))
    if (report.get('datasetSha256') != fingerprint(data)
            or report.get('expectedCaseIds') != [e['id'] for e in data['entries']]
            or report.get('chunkCount') != len(chunks)
            or report.get('config', {}).get('retrievalTopK') != 8):
        raise ValueError('RAG_EVAL_SOURCE_SNAPSHOT_MISMATCH')
    with np.load(source / 'vectors.npz', allow_pickle=False) as stored:
        matrix = validated_vectors(stored['vectors'], rows=len(chunks) + len(data['entries']),
                                   dimension=report['config']['embeddingDimension'],
                                   expected_sha=report['vectorSnapshotSha256'])
    records = [json.loads(line) for line in (source / 'retrieval.jsonl').read_text(encoding='utf8').splitlines()]
    by_id = {r['id']: r for r in records}
    if len(by_id) != len(records) or set(by_id) != set(report['expectedCaseIds']):
        raise ValueError('RAG_EVAL_SOURCE_CASES_INVALID')
    for index, entry in enumerate(data['entries']):
        record = by_id[entry['id']]
        actual = dense_candidates(chunks, matrix, index, top_k=8)
        if record['retrieved'] != actual:
            raise ValueError('RAG_EVAL_SOURCE_RETRIEVAL_MISMATCH')
        validate_ranked(actual, record['reranked'])
    return report, matrix, by_id


async def rerank_local(question, rows, reranker):
    from ai_service.infrastructure.milvus_knowledge import RetrievedChunk

    candidates = [RetrievedChunk(r['chunkId'], r['documentId'], 1, index, r['content'],
                  r['documentId'] + '.md', 'md', r['locator'],
                  hashlib.sha256(r['content'].encode()).hexdigest(),
                  r['score'], r['score'], True, 1) for index, r in enumerate(rows)]
    ranked = await reranker.rerank(question, candidates, top_n=len(candidates))
    by_id = {r['chunkId']: r for r in rows}
    result = [{**by_id[r.chunk.chunk_id], 'rerankScore': r.score} for r in ranked]
    validate_ranked(rows, result)
    return result


async def run(source: Path, output: Path, *, dataset: Path = DEFAULT_DATASET,
              local_rerank: bool = False, prepare_title_inputs: bool = False) -> dict:
    data, chunks = load_study(dataset)
    original, matrix, snapshots = load_snapshot(source, data, chunks)
    output = safe_output(output)
    output.mkdir(parents=True)
    report = {'schemaVersion': 'rag-bounded-evidence/v1', 'datasetSha256': fingerprint(data),
              'datasetVersion': data['datasetVersion'], 'diagnosticSet': True,
              'sourceVectorSha256': original['vectorSnapshotSha256'],
              'sourceRetrievalSha256': hashlib.sha256((source/'retrieval.jsonl').read_bytes()).hexdigest(),
              'implementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'finalTopK': 3, 'promptMaxBytes': original['config']['promptMaxBytes'],
              'rerankerModel': original['config']['reranker'],
              'rerankerDevice': original['config']['device'],
              'newChatCalls': 0, 'newEmbeddingCalls': 0, 'localRerankCalls': 0,
              'productionIndexWrites': 0, 'titleExperiment': 'not-run-requires-new-embeddings',
              'localRerankStatus': 'not-requested', 'cases': [], 'status': 'running'}
    if prepare_title_inputs:
        manifest = input_manifest(data, chunks, title_path=True)
        (output/'title-inputs.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        report['titleInputManifestSha256'] = fingerprint(manifest)
    reranker = None
    try:
        if local_rerank:
            from ai_service.config import Settings
            from ai_service.models.reranker import LocalCrossEncoderReranker, RerankerError

            cfg = Settings(_env_file=ROOT/'ai-service/.env')
            if cfg.rag_reranker_model != original['config']['reranker'] or cfg.rag_reranker_device != original['config']['device']:
                raise ValueError('RAG_EVAL_RERANK_CONFIG_MISMATCH')
            try:
                # 生产适配器持有进程级GPU独占锁；占用或模型不可用均不得抢占/降级。
                reranker = await LocalCrossEncoderReranker.create(cfg)
                report['localRerankStatus'] = 'available'
            except RerankerError as error:
                report['localRerankStatus'] = error.code
        for index, entry in enumerate(data['entries']):
            original_rows = snapshots[entry['id']]
            for cap in (1, 2, 3):
                report['cases'].append({'id': entry['id'], 'groupId': entry['groupId'],
                    'arm': f'document-cap-{cap}', 'variable': 'per-document-cap',
                    'retrievalTopK': 8, 'perDocument': cap,
                    **measured_case(entry, original_rows['retrieved'], original_rows['reranked'], chunks,
                                    cap=cap, max_bytes=report['promptMaxBytes'])})
            for top_k in (8, 16, 24):
                rows = dense_candidates(chunks, matrix, index, top_k=top_k)
                base = {'id': entry['id'], 'groupId': entry['groupId'], 'arm': f'dense-top-{top_k}',
                        'variable': 'dense-top-k', 'retrievalTopK': top_k, 'perDocument': 1,
                        'retrieved': rows}
                ranked = original_rows['reranked'] if top_k == 8 else None
                if reranker is not None:
                    # 扩召回对照的三组使用本轮同一实例，避免新旧模型别名变化混入单变量结果。
                    report['localRerankCalls'] += 1
                    ranked = await rerank_local(entry['question'], rows, reranker)
                if ranked is None:
                    base.update(status='final-not-tested-no-rerank', metrics={
                        'retrieved': evidence_metrics(entry, rows, k=top_k, corpus_chunks=chunks)})
                else:
                    base.update(reranked=ranked, **measured_case(entry, rows, ranked, chunks,
                                cap=1, max_bytes=report['promptMaxBytes']))
                report['cases'].append(base)
        report['status'] = 'completed'
    except BaseException:
        report['status'] = 'failed-or-interrupted'
        raise
    finally:
        if reranker is not None:
            await reranker.close()
        report['finishedAtUtc'] = datetime.now(timezone.utc).isoformat()
        report['summary'] = summarize(data, report['cases'])
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                     allow_nan=False)+'\n', encoding='utf8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--local-rerank', action='store_true')
    parser.add_argument('--prepare-title-inputs', action='store_true')
    args = parser.parse_args()
    report = asyncio.run(run(args.source, args.output, dataset=args.dataset,
                             local_rerank=args.local_rerank,
                             prepare_title_inputs=args.prepare_title_inputs))
    print(json.dumps({k: report[k] for k in ('status', 'newChatCalls', 'newEmbeddingCalls',
                                           'localRerankCalls', 'summary')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
