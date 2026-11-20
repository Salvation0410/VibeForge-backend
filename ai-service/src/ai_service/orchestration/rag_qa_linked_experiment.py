"""第23轮真实Embedding/BGE对照；--prepare不发请求，--live使用固定新额度。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import math
import os
from pathlib import Path

import httpx
import numpy as np

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.reranker import LocalCrossEncoderReranker
from ai_service.orchestration.customer_service_rag import (
    _bounded_contexts, _filter_and_dedupe, _select_documents, _validate_ranked,
)
from ai_service.orchestration.rag_qa_linked_budget import Budget, fingerprint
from ai_service.orchestration.rag_qa_linked_corpus import PRODUCT, ROOT, VERSION, read_json, sha, write_json
from ai_service.orchestration.rag_rerank_experiment import _local_model_manifest, snippet_survival
from ai_service.prompts.customer_service import fit_customer_service_contexts

RUN = ROOT/'tmp/rag-qa-linked-20261120'
ANCHOR = RUN/'authorization.jsonl'
CORPUS = PRODUCT/f'experiments/{VERSION}'
RUBRIC = PRODUCT/'evaluation/rubric-20261118-v1.json'
ARMS = ('before', 'after')


def load_inputs():
    from ai_service.orchestration.rag_qa_linked_corpus import build
    result = build(PRODUCT, read_json(CORPUS/'associations.json'))
    manifest = read_json(CORPUS/'manifest.json')
    for row in manifest['documents']:
        item = next(d for d in result['documents'] if d['documentId'] == row['documentId'])
        if sha(CORPUS/row['sourceMetadata']['path']) != item['candidateSha256'] or row['candidateSha256'] != item['candidateSha256']:
            raise ValueError('QA_CANDIDATE_CHANGED')
    rubric = read_json(RUBRIC)
    entries = rubric['entries']
    chunks = {arm: [c for d in result['documents'] for c in d['chunks'][arm]] for arm in ARMS}
    if (len(entries) != 90 or len({e['id'] for e in entries}) != 90
            or [len(chunks[a]) for a in ARMS] != [72, 63]):
        raise ValueError('QA_EXPERIMENT_SIZE_CHANGED')
    batches = []
    for arm, texts in [(a, [c['content'] for c in chunks[a]]) for a in ARMS] + [('queries', [e['question'].strip() for e in entries])]:
        for start in range(0, len(texts), 32):
            batch = texts[start:start+32]
            batches.append((dict(kind='embedding', key=f'{arm}:{start}', inputs=len(batch),
                                 inputSha256=fingerprint(batch)), batch))
    order = [(e, a) for i, e in enumerate(entries) for a in (ARMS if i % 2 == 0 else ARMS[::-1])]
    return result, rubric, chunks, batches, order


def configuration(settings):
    if (settings.rag_embedding_model != 'openai:text-embedding-3-large'
            or settings.rag_embedding_dimension != 3072 or settings.rag_min_rerank_score is not None
            or settings.rag_reranker_model != 'BAAI/bge-reranker-v2-m3'
            or settings.rag_reranker_device != 'cuda' or not settings.closeai_api_key):
        raise ValueError('QA_EXPERIMENT_CONFIG_INVALID')
    return dict(embeddingModel=settings.rag_embedding_model, dimension=3072, topK=8, finalK=3,
        perDocument=1, minRerankScore=None, maxPromptBytes=16384, maxContextChars=1200,
        maxTotalContextChars=3000, batchSize=32, retrieval='local-exhaustive-cosine-f32',
        rerankBatchSize=4, rerankTimeoutSeconds=5, concurrency=1, automaticRetries=0,
        endpointSha256=fingerprint(settings.closeai_base_url), chatCalls=0, indexWrites=0)


def bindings():
    paths = [RUBRIC, PRODUCT/'manifest.json', PRODUCT/'evaluation/dataset.json',
        CORPUS/'associations.json', CORPUS/'manifest.json', ROOT/'ai-service/uv.lock',
        ROOT/'docs/superpowers/plans/2026-11-20-rag-qa-linked-corpus.md',
        *[PRODUCT/r['path'] for r in read_json(PRODUCT/'manifest.json')['documents']],
        *[CORPUS/r['sourceMetadata']['path'] for r in read_json(CORPUS/'manifest.json')['documents']]]
    paths.extend(ROOT/'ai-service/src/ai_service'/p for p in [
        'orchestration/rag_qa_linked_corpus.py', 'orchestration/rag_qa_linked_experiment.py',
        'orchestration/rag_qa_linked_budget.py', 'orchestration/document_etl.py',
        'orchestration/customer_service_rag.py', 'orchestration/rag_rerank_experiment.py',
        'models/reranker.py', 'prompts/customer_service.py'])
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in paths}


def prepare(settings):
    _, _, _, batches, order = load_inputs()
    cfg = configuration(settings)
    directory, model = _local_model_manifest(settings)
    plan = dict(schemaVersion='rag-qa-linked-live-plan/v1', studyId=VERSION,
        authorization='用户明确批准8次Embedding/225输入/180次BGE，Chat与生产索引写入0',
        config=cfg, bge=model, bindings=bindings(),
        schedule=[b for b, _ in batches] + [dict(kind='bge', key=f'{e["id"]}:{a}') for e, a in order],
        boundary='真实模型检索实验；本地穷举非Milvus、非权威发布、非生成回答验收。')
    RUN.mkdir(parents=True, exist_ok=True)
    write_json(RUN/'live-plan.json', plan)
    return fingerprint(plan)


def dense_top8(chunks, vectors, query):
    matrix, q = np.asarray(vectors, dtype=np.float32), np.asarray(query, dtype=np.float32)
    if matrix.ndim != 2 or q.ndim != 1 or matrix.shape != (len(chunks), len(q)):
        raise ValueError('QA_VECTOR_SHAPE')
    norms, qnorm = np.linalg.norm(matrix, axis=1), np.linalg.norm(q)
    if (not np.isfinite(matrix).all() or not np.isfinite(q).all() or not np.isfinite(norms).all()
            or not np.isfinite(qnorm) or np.any(norms <= 0) or qnorm <= 0):
        raise ValueError('QA_VECTOR_INVALID')
    scores = (matrix @ q) / (norms * qnorm)
    result = []
    for index in sorted(range(len(chunks)), key=lambda i: (-float(scores[i]), i))[:8]:
        c = chunks[index]
        result.append(RetrievedChunk(chunk_id=c['chunk_id'], document_id=c['document_id'],
            document_version=c['document_version'], current_document_version=c['document_version'],
            chunk_index=c['chunk_index'], content=c['content'],
            file_name=c['source_name'], file_type='MD', source_locator=c['source_locator'],
            content_hash=fingerprint(c['content']), distance=float(scores[index]), score=float(scores[index])))
    return result


async def embedding_batch(client, settings, texts, budget, reservation):
    if (reservation.get('kind') != 'embedding' or reservation.get('inputs') != len(texts)
            or reservation.get('inputSha256') != fingerprint(texts) or not 1 <= len(texts) <= 32):
        raise ValueError('QA_EMBEDDING_INPUT_CHANGED')
    budget.reserve(reservation)
    # 直接HTTP且不跟随重定向，避免SDK内部拆批或重试越过本轮账本。
    async with client.stream('POST', settings.closeai_base_url.rstrip('/')+'/embeddings',
            headers={'Authorization': 'Bearer '+settings.closeai_api_key},
            json={'model': 'text-embedding-3-large', 'input': texts, 'encoding_format': 'float', 'dimensions': 3072}) as response:
        if response.status_code != 200:
            raise ValueError('QA_EMBEDDING_HTTP_FAILED')
        data = bytearray()
        async for block in response.aiter_bytes():
            data.extend(block)
            if len(data) > 8*1024*1024:
                raise ValueError('QA_EMBEDDING_RESPONSE_TOO_LARGE')
    payload = json.loads(data)
    rows = payload.get('data')
    if (not isinstance(rows, list) or len(rows) != len(texts)
            or any(type(r.get('index')) is not int for r in rows)
            or sorted(r['index'] for r in rows) != list(range(len(texts)))):
        raise ValueError('QA_EMBEDDING_COUNT')
    vectors = [r['embedding'] for r in sorted(rows, key=lambda r:r['index'])]
    if any(not isinstance(v, list) or len(v) != 3072 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('QA_EMBEDDING_VECTOR')
    usage = payload.get('usage', {})
    usage = {k: usage[k] for k in ('prompt_tokens', 'total_tokens')
             if isinstance(usage, dict) and type(usage.get(k)) is int and usage[k] >= 0}
    return vectors, usage


def select_contexts(question, retrieved, ranked):
    candidates = _filter_and_dedupe(retrieved)
    ranked = _validate_ranked(ranked, candidates, len(candidates))
    selected, drops = _select_documents(ranked, 3, per_document_limit=1)
    character, by_id = _bounded_contexts(selected)
    fitted = fit_customer_service_contexts(question, character, max_bytes=16384)
    def records(chunks):
        return [dict(chunkId=c.chunk_id, documentId=c.document_id, documentVersion=c.document_version,
            locator=c.source_locator, content=c.content, retrievalScore=c.score) for c in chunks]
    stages = dict(retrieved=records(retrieved), filtered=records(candidates),
        reranked=[records([r.chunk])[0] | dict(rerankScore=r.score) for r in ranked], selected=records([r.chunk for r in selected]))
    stages['thresholded'] = list(stages['reranked'])
    stages['characterBudget'] = [dict(chunkId=c.chunk_id, documentId=by_id[c.chunk_id].document_id,
        documentVersion=by_id[c.chunk_id].document_version,
        locator=by_id[c.chunk_id].source_locator, content=c.content) for c in character]
    stages['finalContext'] = [dict(chunkId=key, documentId=by_id[key].document_id,
        documentVersion=by_id[key].document_version, locator=by_id[key].source_locator, content=content)
        for key, content in fitted or [] if content]
    return stages, drops


async def live(settings, expected_hash):
    plan = read_json(RUN/'live-plan.json')
    if fingerprint(plan) != expected_hash or plan['bindings'] != bindings() or plan['config'] != configuration(settings):
        raise ValueError('QA_LIVE_PLAN_CHANGED')
    directory, model = _local_model_manifest(settings)
    if model != plan['bge']:
        raise ValueError('QA_BGE_CHANGED')
    _, rubric, chunks, batches, order = load_inputs()
    if plan['schedule'] != [b for b,_ in batches]+[dict(kind='bge',key=f'{e["id"]}:{a}') for e,a in order]:
        raise ValueError('QA_SCHEDULE_CHANGED')
    output = RUN/'live'
    if output.exists():
        raise ValueError('QA_OUTPUT_EXISTS')
    output.mkdir()
    budget = Budget(ANCHOR, plan)
    report = dict(schemaVersion='rag-qa-linked-live-report/v1', planSha256=expected_hash,
        status='running', humanReview='pending', chatCalls=0, indexWrites=0, results=[], embeddingUsage=[])
    reranker = None
    try:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        cfg = settings.model_copy(update=dict(rag_reranker_model=str(directory), rag_reranker_batch_size=4,
            rag_reranker_timeout_seconds=5, rag_reranker_workers=1, rag_reranker_max_concurrency=1))
        reranker = await LocalCrossEncoderReranker.create(cfg)
        vectors = {a: [] for a in (*ARMS, 'queries')}
        async with httpx.AsyncClient(timeout=120, trust_env=False, follow_redirects=False,
                                     transport=httpx.AsyncHTTPTransport(retries=0)) as client:
            for reservation, texts in batches:
                batch, usage = await embedding_batch(client, settings, texts, budget, reservation)
                vectors[reservation['key'].split(':')[0]].extend(batch)
                report['embeddingUsage'].append(dict(key=reservation['key'], usage=usage))
                print('embedding_completed '+reservation['key'], flush=True)
        write_json(output/'vectors.json', vectors)
        queries = {e['id']: vectors['queries'][i] for i,e in enumerate(rubric['entries'])}
        for entry, arm in order:
            retrieved = dense_top8(chunks[arm], vectors[arm], queries[entry['id']])
            candidates = _filter_and_dedupe(retrieved)
            budget.reserve(dict(kind='bge', key=f'{entry["id"]}:{arm}'))
            ranked = await reranker.rerank(entry['question'].strip(), candidates, top_n=len(candidates))
            stages, drops = select_contexts(entry['question'].strip(), retrieved, ranked)
            row = dict(id=entry['id'], arm=arm, stages=stages, selectionDrops=drops,
                facts=snippet_survival(entry, stages, {c.document_id:c.document_id for c in retrieved}))
            report['results'].append(row)
            # 每题结果独立落盘；进程中断可观察已完成项，但没有自动续跑入口。
            write_json(output/(entry['id']+'-'+arm+'.json'), row)
            if len(report['results']) % 10 == 0:
                print('rerank_completed '+str(len(report['results'])), flush=True)
        if plan['bindings'] != bindings():
            raise ValueError('QA_INPUT_CHANGED')
        report['status'] = 'completed'
    except BaseException as error:
        report['status'] = 'failed'
        report['errorCode'] = 'CANCELLED' if isinstance(error, asyncio.CancelledError) else 'QA_LIVE_EXECUTION_FAILED'
        raise
    finally:
        report['reservedOperations'] = budget.count
        budget.close()
        try:
            if reranker is not None:
                await reranker.close()
        finally:
            write_json(output/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--live', action='store_true')
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    try:
        settings = Settings()
        if args.prepare:
            print(prepare(settings))
        elif not args.plan_sha256:
            parser.error('--live requires --plan-sha256')
        else:
            result = asyncio.run(live(settings, args.plan_sha256))
            print(json.dumps(dict(status=result['status'], cases=len(result['results']),
                reservedOperations=result['reservedOperations'])))
    except (Exception, KeyboardInterrupt):
        parser.exit(2, 'QA_LIVE_FAILED_CHECK_LOCAL_REPORT\n')


if __name__ == '__main__':
    main()
