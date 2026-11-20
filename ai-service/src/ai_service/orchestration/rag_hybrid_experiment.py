"""第27轮独立混合检索实验：冻结旧向量，只新增有界本地BGE推理。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time

import numpy as np

from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_qa_linked_corpus import PRODUCT, ROOT, VERSION, read_json, sha, write_json
from ai_service.orchestration.rag_qa_linked_cap_evaluation import (
    CONFIG as SELECTION_CONFIG, load_candidates, safe_path, select_arm, validate_bindings, validate_report,
)
from ai_service.orchestration.rag_qa_linked_experiment import dense_top8
from ai_service.orchestration.rag_hybrid_retrieval import bm25_top, rrf_top
from ai_service.orchestration.rag_rerank_experiment import _local_model_manifest, snippet_survival

RUN = ROOT / 'tmp/rag-hybrid-20261120'
OLD_RUN = ROOT / 'tmp/rag-qa-linked-20261120'
CORPUS = PRODUCT / f'experiments/{VERSION}'
RUBRIC = PRODUCT / 'evaluation/rubric-20261118-v1.json'
ARMS = ('dense', 'hybrid')
CONFIG = dict(SELECTION_CONFIG, lexical='bm25-cjk-bigram-ascii-nfkc-v1', k1=1.2, b=0.75,
              lexicalTopK=8, fusion='equal-rrf', rrfK=60, fusionTopK=8,
              retrieval='local-exhaustive-cosine-f32', rerankBatchSize=4,
              rerankTimeoutSeconds=5, concurrency=1, automaticRetries=0,
              oomBatchRetries=0,
              bgeLimit=180, embeddingLimit=0, chatLimit=0, indexWrites=0, productionWrites=0)


def fail(code):
    raise ValueError('HYBRID_' + code)


def record(chunk):
    return dict(chunkId=chunk.chunk_id, documentId=chunk.document_id,
                documentVersion=chunk.document_version, locator=chunk.source_locator,
                content=chunk.content, retrievalScore=chunk.score)


def make_chunk(source, score):
    return RetrievedChunk(chunk_id=source['chunk_id'], document_id=source['document_id'],
        document_version=source['document_version'], current_document_version=source['document_version'],
        chunk_index=source['chunk_index'], content=source['content'], file_name=source['source_name'],
        file_type='MD', source_locator=source['source_locator'], content_hash=fingerprint(source['content']),
        distance=score, score=score)


def load_vectors(path):
    """旧向量单独有界读取；不能放宽通用8MiB文档读取限制。"""
    path = safe_path(path, ROOT)
    with path.open('rb') as stream:
        payload = stream.read(24 * 1024 * 1024 + 1)
    if len(payload) > 24 * 1024 * 1024:
        fail('VECTOR_FILE_TOO_LARGE')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail('VECTOR_DUPLICATE_KEY')
            result[key] = value
        return result
    vectors = json.loads(payload, object_pairs_hook=unique,
                         parse_constant=lambda _: fail('VECTOR_NONFINITE'))
    if not isinstance(vectors, dict) or set(vectors) != {'before', 'after', 'queries'}:
        fail('VECTOR_KEYS')
    for arm, count in [('before', 72), ('after', 63), ('queries', 90)]:
        rows = vectors[arm]
        if (not isinstance(rows, list) or len(rows) != count or any(
                not isinstance(row, list) or len(row) != 3072 or any(
                    type(x) not in (int, float) for x in row) for row in rows)):
            fail('VECTOR_SHAPE')
        matrix = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(matrix).all() or not np.isfinite(norms).all() or np.any(norms <= 0):
            fail('VECTOR_INVALID')
    return vectors


def verify_batches(plan, chunks, entries):
    """旧数组顺序必须对应当时已冻结的Embedding输入，不接受按新文本复用向量。"""
    for arm, texts in [('after', [c['content'] for c in chunks]),
                       ('queries', [e['question'].strip() for e in entries])]:
        expected = [dict(kind='embedding', key=f'{arm}:{start}', inputs=len(texts[start:start+32]),
                         inputSha256=fingerprint(texts[start:start+32])) for start in range(0, len(texts), 32)]
        actual = [row for row in plan['schedule'] if row['kind'] == 'embedding'
                  and row['key'].startswith(arm + ':')]
        if actual != expected:
            fail('EMBEDDING_INPUT_ORDER_CHANGED')


def load_inputs():
    old_plan_path = CORPUS / 'live-plan.json'
    old_result_path = PRODUCT / 'evaluation/results/qa-linked-retrieval-20261120-v1.json'
    report_path, ledger_path = OLD_RUN / 'live/report.json', OLD_RUN / 'authorization.jsonl'
    old_plan, expected = read_json(old_plan_path), read_json(old_result_path)
    protected = validate_bindings(old_plan, expected, report_path, ledger_path, ROOT)
    if any(old_plan['config'].get(k) != v for k, v in SELECTION_CONFIG.items()):
        fail('HISTORICAL_CONFIG_CHANGED')
    chunks, rubric = load_candidates(CORPUS), read_json(RUBRIC)
    historical = read_json(report_path)
    validate_report(historical, rubric, chunks, expected['planSha256'])
    entries = [dict(id=e['id'], question=e['question'].strip()) for e in rubric['entries']]
    verify_batches(old_plan, chunks, entries)
    vectors_path = OLD_RUN / 'live/vectors.json'
    vectors = load_vectors(vectors_path)
    old_rows = {row['id']: row for row in historical['results'] if row['arm'] == 'after'}
    for i, entry in enumerate(entries):
        current = [record(c) for c in dense_top8(chunks, vectors['after'], vectors['queries'][i])]
        if current != old_rows[entry['id']]['stages']['retrieved']:
            fail('HISTORICAL_DENSE_TOP8_CHANGED')
    sources = [old_plan_path, old_result_path, RUBRIC, vectors_path, Path(__file__),
        Path(__file__).with_name('rag_hybrid_retrieval.py'),
        Path(__file__).with_name('rag_qa_linked_cap_evaluation.py'),
        ROOT / 'docs/superpowers/specs/2026-11-20-rag-hybrid-round27-design.md',
        ROOT / 'docs/superpowers/plans/2026-11-20-rag-hybrid-round27.md']
    for path in sources:
        protected[str(safe_path(path, ROOT))] = sha(path)
    bindings = {str(Path(p).relative_to(ROOT)).replace('\\', '/'): digest for p, digest in protected.items()}
    return dict(chunks=chunks, entries=entries, vectors=vectors, historical=old_rows,
                bindings=bindings, oldBge=old_plan['bge'])


def retrieval_cases(inputs):
    """排序仅消费语料、问题和历史向量，公共事实标签不进入候选构建。"""
    chunks, vectors = inputs['chunks'], inputs['vectors']
    index = {c['chunk_id']: i for i, c in enumerate(chunks)}
    cases = []
    for i, entry in enumerate(inputs['entries']):
        start = time.perf_counter()
        dense = dense_top8(chunks, vectors['after'], vectors['queries'][i])
        dense_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        sparse_pairs = bm25_top(chunks, entry['question'])
        sparse = [make_chunk(chunks[j], score) for j, score in sparse_pairs]
        fused_pairs = rrf_top([index[c.chunk_id] for c in dense], [j for j, _ in sparse_pairs])
        hybrid = [make_chunk(chunks[j], score) for j, score in fused_pairs]
        union_ids = list(dict.fromkeys([index[c.chunk_id] for c in dense] + [j for j, _ in sparse_pairs]))
        # 并集仅诊断引文存在；不把混合量纲的分数解释为重排分数。
        union = [make_chunk(chunks[j], 0.0) for j in union_ids]
        cases.append(dict(id=entry['id'], question=entry['question'], arms=dict(dense=dense, hybrid=hybrid),
            diagnostic=dict(lexicalTop8=[record(c) for c in sparse], union=[record(c) for c in union]),
            retrievalMs=dict(dense=dense_ms, lexicalAndFusion=(time.perf_counter()-start)*1000)))
    return cases


def schedule_for(cases):
    return [dict(kind='bge', key=f'{case["id"]}:{arm}', inputSha256=fingerprint(
        dict(question=case['question'], chunks=[record(c) for c in case['arms'][arm]])))
        for i, case in enumerate(cases) for arm in (ARMS if i % 2 == 0 else ARMS[::-1])]


class Budget:
    """本轮固定180项预占；持久化未知后禁止继续，失败不退款。"""
    def __init__(self, anchor, plan):
        schedule = plan['schedule']
        if (len(schedule) != 180 or len({r['key'] for r in schedule}) != 180
                or any(set(r) != {'kind', 'key', 'inputSha256'} or r['kind'] != 'bge' for r in schedule)):
            fail('BUDGET_PLAN_INVALID')
        self.schedule, self.count, self.poisoned = schedule, 0, False
        self.stream = Path(anchor).open('x', encoding='utf8')
        try:
            self.append(dict(type='authorization', planSha256=fingerprint(plan), bgeLimit=180,
                             embeddingLimit=0, chatLimit=0, indexWrites=0))
        except BaseException:
            self.stream.close()
            raise

    def append(self, value):
        if self.poisoned:
            fail('PERSISTENCE_UNKNOWN')
        try:
            self.stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.poisoned = True
            raise

    def reserve(self, item):
        if self.count >= len(self.schedule) or item != self.schedule[self.count]:
            fail('BUDGET_SCHEDULE_MISMATCH')
        self.append(dict(type='reserved', ordinal=self.count, **item))
        self.count += 1

    def close(self):
        self.stream.close()


def unchanged(bindings):
    if any(sha(safe_path(ROOT / path, ROOT)) != digest for path, digest in bindings.items()):
        fail('INPUT_CHANGED')


class NoRetryAdapter:
    """复用正式评分函数，但本实验显存错误不触发隐藏缩批重试。"""
    def __init__(self, adapter, score_batch):
        self.adapter, self.score_batch = adapter, score_batch

    def compute_score(self, pairs):
        if not 1 <= len(pairs) <= 4:
            fail('BGE_BATCH_SIZE_CHANGED')
        raw = self.adapter._reranker
        detailed = raw.get_detailed_inputs(pairs)
        if len(detailed) != len(pairs):
            fail('BGE_PAIR_COUNT_CHANGED')
        return self.score_batch(raw, detailed, len(detailed), normalize=False)

    def close(self):
        self.adapter.close()


def strict_worker(connection, config):
    """仅子进程替换加载后的适配器，保持正式GPU锁、IPC及退出清理。"""
    from ai_service.models import reranker as module
    original = module._load_flag_reranker
    def load(*args, **kwargs):
        return NoRetryAdapter(original(*args, **kwargs), module._score_flag_batch)
    module._load_flag_reranker = load
    try:
        module._reranker_worker_entry(connection, config)
    finally:
        module._load_flag_reranker = original


async def create_reranker(settings):
    from ai_service.models.reranker import _ProcessReranker
    return await _ProcessReranker.start(settings, worker_target=strict_worker)


def prepare(settings):
    safe_path(RUN, ROOT / 'tmp')
    if (RUN / 'live-plan.json').exists() or (RUN / 'authorization.jsonl').exists():
        fail('PLAN_ALREADY_EXISTS')
    inputs = load_inputs()
    _, model = _local_model_manifest(settings)
    if model != inputs['oldBge']:
        fail('BGE_BASELINE_CHANGED')
    cases = retrieval_cases(inputs)
    plan = dict(schemaVersion='rag-hybrid-plan/v1', config=CONFIG, bge=model,
        authorization='用户要求直接加入混合检索对照；本轮限定180次本地BGE，外部模型与生产写入0',
        bindings=inputs['bindings'], schedule=schedule_for(cases), historicalDenseExactMatches=len(cases))
    unchanged(plan['bindings'])
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / 'live-plan.json').open('x', encoding='utf8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return fingerprint(plan)


async def execute(cases, reranker, budget, output, report):
    """每项落盘；首错停止，调用前的预占在任何失败路径保留。"""
    schedule = schedule_for(cases)
    for case in cases:
        case_index = len(report['results']) // 2
        for arm in (ARMS if case_index % 2 == 0 else ARMS[::-1]):
            retrieved = case['arms'][arm]
            reservation = schedule[len(report['results'])]
            report['phase'] = 'reservation'
            report['currentOperation'] = reservation['key']
            budget.reserve(reservation)
            report['phase'] = 'rerank'
            start = time.perf_counter()
            ranked = await reranker.rerank(case['question'], retrieved, top_n=len(retrieved))
            elapsed = (time.perf_counter() - start) * 1000
            report['phase'] = 'selection'
            start = time.perf_counter()
            result = select_arm(case['question'], retrieved, ranked, 1)
            row = dict(id=case['id'], arm=arm, **result, rerankMs=elapsed,
                       selectionMs=(time.perf_counter() - start) * 1000)
            report['phase'] = 'row_persistence'
            write_json(output / f'{case["id"]}-{arm}.json', row)
            report['results'].append(row)
            if len(report['results']) % 10 == 0:
                print('rerank_completed ' + str(len(report['results'])), flush=True)


def quote_failure(observed):
    """固定阶段定位仅针对整段引文，不是语义缺失判定。"""
    for stage, reason in [('retrieved', 'retrieval_quote_missing'), ('filtered', 'filter_quote_missing'),
                          ('selected', 'selection_quote_missing'),
                          ('characterBudget', 'character_budget_quote_missing'),
                          ('finalContext', 'prompt_budget_quote_missing')]:
        if not observed[stage]:
            return reason
    return 'survived'


def analyze(cases, report, rubric, historical):
    """推理完成后测量引用存活，所有标签读取均位于排序之后。"""
    rows = {(r['id'], r['arm']): r for r in report['results']}
    entries = {entry['id']: entry for entry in rubric['entries']}
    public_ids = [f['factId'] for entry in rubric['entries'] for f in entry['requiredFacts']
                  if f['basisType'] == 'public-document']
    if len(public_ids) != 130 or len(set(public_ids)) != 130:
        fail('PUBLIC_FACT_COUNT')
    output, changes = [], []
    for case in cases:
        entry = entries[case['id']]
        arms = {}
        for arm in ARMS:
            row = rows[(case['id'], arm)]
            arms[arm] = dict(row, facts=snippet_survival(entry, row['stages'],
                {c['document_id']: c['document_id'] for c in report['corpusIdentities']}))
        diagnostics = snippet_survival(entry, case['diagnostic'],
            {c['document_id']: c['document_id'] for c in report['corpusIdentities']})
        per_fact = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            observed = {arm: {stage: next(f['annotatedSupportSnippetSurvived'] for f in fs
                if f['factId'] == fact['factId']) for stage, fs in arms[arm]['facts']['stages'].items()}
                for arm in ARMS}
            change = ('gain' if not observed['dense']['finalContext'] and observed['hybrid']['finalContext']
                else 'loss' if observed['dense']['finalContext'] and not observed['hybrid']['finalContext']
                else 'unchanged')
            failure = {arm: quote_failure(observed[arm]) for arm in ARMS}
            per_fact.append(dict(factId=fact['factId'], observed=observed, change=change,
                                 quoteFailureByArm=failure))
            if change != 'unchanged':
                changes.append(dict(id=case['id'], factId=fact['factId'], change=change,
                    requiredFact=fact, observed=observed, quoteFailureByArm=failure, humanReview='pending'))
        old = historical[case['id']]['stages']
        current = arms['dense']['stages']
        output.append(dict(id=case['id'], question=case['question'], arms=arms,
            diagnostic=case['diagnostic'], diagnosticFacts=diagnostics, facts=per_fact,
            retrievalMs=case['retrievalMs'], historicalBaseline=dict(
                rerankOrderSame=[r['chunkId'] for r in old['reranked']] == [r['chunkId'] for r in current['reranked']],
                finalContextSame=old['finalContext'] == current['finalContext'],
                maxRerankScoreDelta=max(abs(a['rerankScore']-next(b['rerankScore'] for b in current['reranked']
                    if b['chunkId'] == a['chunkId'])) for a in old['reranked']))))
    summary = dict(publicFacts=130, cases=len(output), humanReview='pending', arms={})
    for arm in ARMS:
        stages = output[0]['arms'][arm]['facts']['stages']
        summary['arms'][arm] = dict(wholeQuoteSurvival={stage: sum(
            f['annotatedSupportSnippetSurvived'] for c in output
            for f in c['arms'][arm]['facts']['stages'][stage]) for stage in stages},
            totalPromptBytes=sum(c['arms'][arm]['budgets']['promptBytes'] for c in output),
            characterTruncatedCases=sum(c['arms'][arm]['characterTruncated'] for c in output),
            byteTruncatedCases=sum(c['arms'][arm]['byteTruncated'] for c in output),
            totalRerankMs=sum(c['arms'][arm]['rerankMs'] for c in output),
            totalSelectionMs=sum(c['arms'][arm]['selectionMs'] for c in output),
            quoteFailureCounts={reason: sum(f['quoteFailureByArm'][arm] == reason
                for c in output for f in c['facts']) for reason in (
                'retrieval_quote_missing', 'filter_quote_missing', 'selection_quote_missing',
                'character_budget_quote_missing', 'prompt_budget_quote_missing', 'survived')})
    summary.update(finalQuoteGains=sum(c['change'] == 'gain' for c in changes),
        finalQuoteLosses=sum(c['change'] == 'loss' for c in changes),
        changedContextCases=sum(c['arms']['dense']['contexts'] != c['arms']['hybrid']['contexts'] for c in output),
        historicalRerankOrderMatches=sum(c['historicalBaseline']['rerankOrderSame'] for c in output),
        historicalFinalContextMatches=sum(c['historicalBaseline']['finalContextSame'] for c in output),
        diagnosticQuoteSurvival={stage: sum(f['annotatedSupportSnippetSurvived'] for c in output
            for f in c['diagnosticFacts']['stages'][stage]) for stage in ('lexicalTop8', 'union')})
    return dict(schemaVersion='rag-hybrid-comparison/v1', summary=summary, cases=output, changes=changes,
        answerQuality=None, refusalQuality=None, humanReview='pending', newEmbeddingCalls=0,
        newChatCalls=0, productionWrites=0,
        boundary='重复开发集；历史真实向量加新本地BGE，不是Milvus/线上端到端或回答质量验收。')


async def live(settings, expected_hash):
    safe_path(RUN, ROOT / 'tmp')
    plan = read_json(RUN / 'live-plan.json')
    if fingerprint(plan) != expected_hash or plan['config'] != CONFIG:
        fail('PLAN_CHANGED')
    inputs = load_inputs()
    if plan['bindings'] != inputs['bindings']:
        fail('BINDINGS_CHANGED')
    cases = retrieval_cases(inputs)
    if plan['schedule'] != schedule_for(cases):
        fail('SCHEDULE_CHANGED')
    directory, model = _local_model_manifest(settings)
    if model != plan['bge'] or model != inputs['oldBge']:
        fail('BGE_CHANGED')
    output = RUN / 'live'
    if output.exists() or (RUN / 'authorization.jsonl').exists():
        fail('ALREADY_STARTED')
    output.mkdir()
    budget = Budget(RUN / 'authorization.jsonl', plan)
    report = dict(schemaVersion='rag-hybrid-live/v1', planSha256=expected_hash, status='running',
        phase='model_startup', results=[], corpusIdentities=[dict(document_id=c['document_id']) for c in inputs['chunks']],
        newEmbeddingCalls=0, newChatCalls=0, productionWrites=0)
    reranker = None
    try:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        cfg = settings.model_copy(update=dict(rag_reranker_model=str(directory), rag_reranker_device='cuda',
            rag_reranker_batch_size=4, rag_reranker_timeout_seconds=5, rag_reranker_workers=1,
            rag_reranker_max_concurrency=1))
        reranker = await create_reranker(cfg)
        await execute(cases, reranker, budget, output, report)
        report['phase'] = 'input_recheck'
        unchanged(plan['bindings'])
        report['phase'] = 'analysis'
        comparison = analyze(cases, report, read_json(RUBRIC), inputs['historical'])
        report['phase'] = 'summary_persistence'
        write_json(output / 'comparison.json', comparison)
        write_json(output / 'summary.json', comparison['summary'])
        write_json(output / 'reviews-template.json', dict(status='pending', reviewer=None,
            comparisonSha256=sha(output / 'comparison.json'), cases=[dict(id=c['id'],
            status='pending', semanticGain=None, semanticRegression=None, scopeNoise=None,
            notes='') for c in comparison['cases']]))
        report['status'] = 'completed'
        report['phase'] = 'completed'
    except BaseException as error:
        report['status'] = 'failed'
        report['failurePhase'] = report['phase']
        report['errorCode'] = 'CANCELLED' if isinstance(error, asyncio.CancelledError) else 'HYBRID_EXECUTION_FAILED'
        raise
    finally:
        report['reservedOperations'] = budget.count
        budget.close()
        try:
            if reranker is not None:
                await reranker.close()
        except BaseException:
            report['status'], report['cleanupError'] = 'failed', 'HYBRID_CLEANUP_FAILED'
            raise
        finally:
            write_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--live', action='store_true')
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    try:
        from ai_service.config import Settings
        settings = Settings(_env_file=ROOT / 'ai-service/.env')
        if not args.live:
            print(prepare(settings))
        elif not args.plan_sha256:
            parser.error('--live requires --plan-sha256')
        else:
            result = asyncio.run(live(settings, args.plan_sha256))
            print(json.dumps(dict(status=result['status'], completed=len(result['results']),
                                  reservedOperations=result['reservedOperations'])))
    except (Exception, KeyboardInterrupt):
        parser.exit(2, 'HYBRID_FAILED_CHECK_LOCAL_REPORT\n')


if __name__ == '__main__':
    main()
